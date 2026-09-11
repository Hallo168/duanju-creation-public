# dadao 视频生成 API 使用说明

> 2026-09-03 用户新增通道，实测跑通并接入项目脚本 `工具/dadao_video_batch.py`；2026-09-05 用户指定为**默认视频通道**，模型 `wan3.0-video`。默认排序唯一出处＝`.claude/skills/fenjing/知识/视频生成规范.md` §一；技术协议见 `视频生成规范-备用通道.md`（V38–V40），本文件是配套实操步骤说明。

## 一、这是什么，风险是什么

- 直接调 API 生成视频（模型 `wan3.0-video`，阿里通义万相），不经画布，不需要用户自己的 sk-GuUV key。
- **风险提示（务必知悉）**：后端 `https://apishipin.dadaowushilibai.cn` 是一个公共写死 token 的非正规中转接口，token 硬编码在插件文件里、不是专属额度，随时可能限流、换 token 或断供，无法自行充值、无法查余额、无 SLA。用户已指定为默认通道；仍不承诺长期稳定，不可用时报告状态、等用户决定是否换通道。
- **模型家族与提示词适用域**：默认模型 `wan3.0-video` 是阿里通义万相，与 Symphony 的 Seedance 2.0 不同。本项目全部 `分镜提示词.md` 是按 Seedance 2.0 的官方约定写的（`图片N` 挂载编号语义、`{}`/`<>`/`()` 符号体系、F/T/Y 系列细则）。实测（83集单元1）显示这套提示词喂给 wan3.0-video 依然能生成、人物服装与年龄气质与参考图基本对应，但**不能假设两个模型的提示词理解方式完全等价**，尤其台词口型与音效对应关系未逐条验证，仅供参考质量抽查，不作为默认质量承诺。

## 二、图床桥接：为什么需要、怎么解决

dadao 的直连 JSON 接口**只接受公网可下载的 https 图片 URL** 做参考生视频（`image`/`images`/`reference_images` 字段），不接受本地文件、不接受 base64。Symphony 则使用本地文件绝对路径直挂；两通道的素材传递方式不同。

**已验证行为**（2026-09-03 实测）：
- 不带图片字段 → 纯文字生视频，返回 `task_type: "text_to_video"`。
- 带 `image`/`images`/`reference_images` 字段（url 数组）→ 参考生视频，返回 `task_type: "reference_to_video"`，后端会真的去下载这些 URL；下载失败（如遇防盗链）会在 `error.message` 里报 `下载素材 {url} HTTP 403` 这类错误，说明这一步是真实生效的，不是静默忽略。
- 用本集人物设定图/场景四宫格图的公网直链实测（单元1，5 张参考图），成品的人物服装、发型、年龄气质与对应参考图（R1方若兰/R2韩东/R3方小满）基本对应，场景（卷帘门修车铺）也大致吻合——图片参考确实起作用，质量比预期好。

**图床方案：imgbb.com 免费图床**，密钥从 `api配置.json` 顶层 `imgbb.api_key` 运行时读取，不在说明文档重复保存。

上传接口：`POST https://api.imgbb.com/1/upload?key=<api_key>`，multipart 字段 `image=@本地文件`，成功响应 `data.url` 即公网直链。`工具/dadao_video_batch.py` 已经把这一步自动化了，不需要手动上传。

## 三、脚本用法：`工具/dadao_video_batch.py`

### 3.1 提交（submit）

```bash
python 工具/dadao_video_batch.py submit \
  --story-root "stories/{集号}-{故事名}/分镜-{模式}" \
  --ledger "工具/运行垃圾/tasks/{集号}/dadao-ledger.jsonl" \
  --units 1-27 \
  --task-dir "工具/运行垃圾/tasks/{集号}" \
  --image-cache "工具/运行垃圾/tasks/{集号}/dadao_image_cache.json" \
  --version v1
```

自动流程（每个单元）：
1. 从 `分镜提示词.md` 对应「## 单元N」抓取「### 单元提示词」代码块正文（=提交给 dadao 的 `prompt`）、「视频挂载」行（=参考图清单）、「生成参数」行（=时长秒数）。
2. 挂载清单里每个素材名去 `资产图/` 目录找对应文件（按素材名+`.png`/`.jpg` 匹配）。
3. 每张图先算 sha256，查本地缓存（`--image-cache` 指的 JSON 文件）；缓存命中直接复用旧的公网 URL，未命中才真的调 imgbb 上传一次并写入缓存——**同一张图（人物设定图/四宫格图这类被几十个单元反复挂载的素材）全集只上传一次**，不会因为提交多个单元而重复占用 imgbb 额度。
4. 组装请求体 `{model, prompt, seconds, size, image, images, reference_images}`（`image`=第一张，`images`/`reference_images`=全部，三个字段都给，兼容后端不同解析路径），POST 到 `{base_url}/v1/videos`。
5. 把请求体存一份到 `--task-dir`（`dadao-unit{N}-{version}-request.json`，供事后核对），把返回的 `task_id` 连同提交时间追加写入账本（`--ledger` 指的 `.jsonl` 文件，一行一条 JSON）。

账本文件已存在且非空时会拒绝提交（防止误操作重复计费/重复占用）；确需追加新一批单元，换一个新的 `--ledger` 路径即可，`poll` 时各账本分别轮询、下载目标目录相同（按单元号命名不会冲突）。

`fenjing_ready` 不是 `true` 时会拒绝提交，除非加 `--allow-partial`（仅用户明确点名放行部分单元时用）。

### 3.2 轮询下载（poll）

```bash
python 工具/dadao_video_batch.py poll \
  --story-root "stories/{集号}-{故事名}/分镜-{模式}" \
  --ledger "工具/运行垃圾/tasks/{集号}/dadao-ledger.jsonl" \
  --interval 20 --timeout 3600
```

每隔 `--interval` 秒查一次账本里全部未完结任务的状态（`GET /v1/videos/{task_id}`）；`completed`/`succeeded` 就下载到 `{story_root}/成片/{单元号}单元-dadao.mp4` 并把结果追加进账本，`failed` 记录失败原因，超时未完成的在最后统一报告。可以随时中断重跑，已完成/已失败的单元不会重复处理。

单条任务实测约 4 分钟完成，`progress` 字段全程可能显示 0 直到跳到 100，这是正常现象，不代表卡死；判断卡死的唯一依据是**等足够久（文档建议 ≥15 分钟）仍无 completed/failed**。

### 3.3 时长与画幅

- 项目固定 15 秒模式 → `seconds: 15`（dadao 支持 4/5/10/15/20/30，15 是常见档位之一，不会被夹紧改值）。
- 项目竖屏 9:16 → `size: "720x1280"`（脚本默认值，`--size` 可覆盖，如需 1080p 竖屏改 `"1080x1920"`）。

## 四、配置位置

`工具/api配置.json`：
- 顶层 `imgbb`：`{kind, api_key, upload, result_field, note}`，图床凭据与用法说明。
- `providers.dadao_video`：`{kind, base_url, api_key, model, note}`，dadao 接口本身的凭据（当前 token 硬编码在插件与本文件里，是公共 token，不是专属申请的密钥；`api配置.json` 里存一份只是跟其他 provider 保持同一套读取逻辑，环境变量 `DADAO_API_KEY` 可覆盖）。

## 五、原始接口参考（供排障，日常不需要手写）

### 提交

```
POST {base_url}/v1/videos
Content-Type: application/json
Authorization: Bearer wc-api-9f3e2a7c8b41d6e50f2a4c9b

{
  "model": "wan3.0-video",
  "prompt": "…",
  "seconds": 15,
  "size": "720x1280",
  "image": "https://i.ibb.co/xxxx/xxx.png",
  "images": ["https://i.ibb.co/xxxx/xxx.png", "…"],
  "reference_images": ["https://i.ibb.co/xxxx/xxx.png", "…"]
}
```

返回：`{id, task_id, object: "video", status: "queued", progress: 0}`

### 查询

```
GET {base_url}/v1/videos/{task_id}
Authorization: Bearer wc-api-9f3e2a7c8b41d6e50f2a4c9b
```

`status` 取值：`queued` / `processing` / `completed`（`url` 字段=成品直链，阿里云 OSS 签名链接会过期，完成后尽快下载）/ `failed`（`error.message` 给失败原因）。

### 踩坑记录（沿用自原文档）

1. Body 必须是 JSON，不是 multipart 表单；用 `-F` 表单提交会报 `Input should be a valid dictionary`。curl 直接用 `-d '{...}'` 在 Windows Git Bash 下若 prompt 含中文，容易被 shell 转义搞乱，改用 `--data-binary @文件.json` 更稳。
2. `progress` 一直是 0 不代表卡死，实测约 4 分钟从提交到 `completed`，最后直接跳到 100。
3. `url` 是带签名的私有 OSS 链接，有 `Expires` 过期时间，`completed` 后尽快下载（脚本 `poll` 已自动做）。
4. `seconds` 若填非整数或超出 4~30 范围会被后端夹紧改值。
5. 参考图 URL 必须是后端能实际下载到的地址；有防盗链/反爬限制的图床（如直接用 Wikipedia 原图链接）会返回 `HTTP 403` 下载失败——这是本文件改用 imgbb 的直接原因。

## 六、风险提示（原文保留）

- 这是一个公共写死 token 的非正规中转接口，不是正规可充值 API，随时可能失效。
- 不能看余额、不能充值、无 SLA。不要为这个接口单独付费充值。
- 默认通道已由用户改为 dadao（2026-09-05）；Symphony、Guagu 保留为用户点名的备选，默认排序以 `视频生成规范.md` §一 为准，不因失败自动切换。
