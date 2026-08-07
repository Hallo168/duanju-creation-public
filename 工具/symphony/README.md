# TikTok Symphony 视频通道使用指南

本项目把 TikTok Symphony Creative Studio 接入短剧批量生成流程。日常操作入口是：

```text
<主仓库根>\工具\symphony_video_batch.py
```

HTTP 代理源码在：

```text
<主仓库根>\工具\symphony
```

这是唯一维护和启动的代理副本。旧目录 `<SymphonyProxy>` 已停用；不要从旧目录启动或修改代码。

当前能力、验证结果和未完成事项见 [STATUS.md](STATUS.md)。

## 便携分发包（新电脑）

受控测试包由以下命令构建：

```powershell
cd <主仓库根>\工具\symphony
.\tools\build_product.ps1
```

输出为 `dist-product.zip`。目标电脑不需要另外安装 Python、Node.js、uv、OpenCLI 或 ffprobe；这些运行时已经放入包内。目标电脑仍需安装 Chrome，并手动加载包内 `chrome-extension` 目录。首次 TikTok 登录、验证码、Passkey 和两步验证必须由用户本人完成。

解压后按 `使用说明.txt` 操作：

1. 运行 `查看机器码.cmd`，由发行方按机器码签发离线许可证。
2. 运行 `激活许可证.cmd` 导入许可证。
3. 运行 `首次登录.cmd` 完成 TikTok 登录。
4. 运行 `启动服务.cmd`，再使用 `运行批处理.cmd`。

许可证和登录态写入当前 Windows 用户的凭据管理器，运行数据写入 `%LOCALAPPDATA%\Symphony`，不写回安装目录。包内 `integrity.json` 使用 Ed25519 签名；任何已签名文件被修改或缺失时，程序拒绝启动。

这是未做 Windows 代码签名的受控测试版本。它不要求购买代码签名证书，但新电脑首次运行时可能出现 SmartScreen 警告；只能通过明确说明来源、核对发布方提供的 SHA-256，并在受控用户范围内分发。不能把应用自身的加密或完整性签名称为 Windows 可信发布者签名。

## 一、系统组成

| 组件 | 路径 | 作用 |
|---|---|---|
| 批量执行器 | `<主仓库根>\工具\symphony_video_batch.py` | 解析分镜、预算、热预存、跨账号调度、账本、轮询、下载和 ffprobe 核验 |
| HTTP 代理 | `<主仓库根>\工具\symphony` | 统一 API、直连 Symphony、账号切换、任务查询和下载 |
| OpenCLI 适配器 | `<OpenCLI-Symphony>` | 首次登录、新参考图首次上传、网页故障回退 |
| 本机配置 | `工具\symphony\symphony配置.json` | 端口、Node、OpenCLI、ffprobe 和默认账号 |
| 资产缓存 | `工具\symphony\data\asset_cache.json` | 按本地文件 SHA-256 记录可缓存使用的远端素材 URL |

日常批量生成不要直接调用 OpenCLI，也不要手工拼生成 API；应使用批量执行器，保证预算护栏、任务账本和下载核验都生效。

## 二、当前平台参数

| 项目 | 当前值 |
|---|---|
| 推荐模型 | `Dreamina Seedance 2.0` |
| 可选时长 | 5、10、12、15 秒 |
| 参考图 | 最多 9 张 |
| 计价 | 当前观测约 1 秒 1 积分；实际以平台返回的余额和计费结果为准 |
| 账号结构 | 主账号（一份独立登录态）下挂子账号；本机当前 2 个主账号共 15 个子账号（`main-a` 5 个、`main-b` 10 个），积分与渲染额度按子账号彼此独立 |
| 渲染并发 | 每个子账号最多约 5 条同时渲染 |
| 提交行为 | 平台可能接收超额排队，但本地执行器与代理实行硬节流：单账号达到 5 条即等待 |
| 输出 | 常见为 720×1280 或 1080×1920，必须用 ffprobe 核验 |

平台模型、时长、审核和排队行为可能变化。每个新批次仍须先运行：

```bat
cd /d <主仓库根>
python 工具\video_provider_catalog.py
```

确认平台和完整模型名后再提交。不要把本页参数视为永久不变的官方契约。

## 三、首次配置

### 1. 环境

- Windows
- Python 3.11+
- Node.js 20+
- Chrome 与 OpenCLI 扩展
- `<OpenCLI-Symphony>` 已构建
- ffprobe 可用

### 2. 配置文件

只修改：

```text
<主仓库根>\工具\symphony\symphony配置.json
```

主要字段：

- `监听端口`：默认 8765，只绑定 `127.0.0.1`
- `node`：Node 可执行文件或 `node`
- `OpenCLI入口`：默认 `<OpenCLI-Symphony>/dist/src/main.js`
- `ffprobe`：成片分辨率核验工具
- `默认主账号`：默认 `main-a`；第二个主账号用命令行 `--main-account main-b` 覆盖
- `默认账号`：留空使用所选主账号的当前子账号，或填写 `main-a` 等子账号名

端口改动后，还要同步修改 `工具\api配置.json` 中 Symphony provider 的 `base_url`。

### 3. 首次登录

首次使用或登录失效时运行：

```bat
node <OpenCLI-Symphony>\dist\src\main.js tiktok-symphony direct-session -f json
```

登录、验证码、Passkey 或两步验证需要人工在 Chrome 中完成。会话随后加密保存到 Windows 凭据管理器，不应写入仓库、日志或配置文件。

## 四、启动与检查

启动：

```bat
cd /d <主仓库根>\工具\symphony
启动代理.cmd
```

也可以：

```bat
cd /d <主仓库根>\工具\symphony
uv run python launch.py
```

健康检查：

```bat
curl.exe http://127.0.0.1:8765/health
curl.exe http://127.0.0.1:8765/v1/models
```

Swagger：

```text
http://127.0.0.1:8765/docs
```

停止时结束实际监听端口的 Python 进程。只关闭外层 `uv` 窗口不一定能释放 8765；若修改代码后没有生效，先检查旧 Python 进程是否仍在监听。

## 五、标准批量流程

以下命令都从项目根目录执行：

```bat
cd /d <主仓库根>
```

示例变量：

```text
故事目录：stories\29-故事名
分镜文件：分镜-15秒\分镜提示词.md
单元：1-37
账本：工具\运行垃圾\tasks\29\symphony-29.jsonl
模型：Dreamina Seedance 2.0
```

分镜文件必须已经由用户审稿，并包含：

```yaml
fenjing_ready: true
```

老集兼容 `grids_ready: true`。只有用户明确放行部分单元时，才允许加 `--allow-partial`。

### 第一步：预检

```bat
python 工具\symphony_video_batch.py preflight ^
  --story-root "stories\29-故事名" ^
  --prompt-file "分镜-15秒\分镜提示词.md" ^
  --units 1-37 ^
  --model "Dreamina Seedance 2.0" ^
  --main-accounts all
```

预检会检查：

- 代理、许可证和直连登录态
- 模型、时长和参考图数量
- `fenjing_ready`
- 每单元参考图与未入库素材
- 账号池余额和在渲数量
- 本批总积分

记下最后给出的 `--confirm-credits` 数字。预检打印的吞吐时间只能作为低负载估算；平台繁忙时可能长时间排队，不能据此承诺出片时间。

### 第二步：热预存

发现 `UNCACHED` 时先运行：

```bat
python 工具\symphony_video_batch.py warmup ^
  --story-root "stories\29-故事名" ^
  --prompt-file "分镜-15秒\分镜提示词.md" ^
  --units 1-37 ^
  --warmup-timeout 900
```

热预存只上传参考图并写资产缓存，不点击生成按钮。它不会创建视频任务，也不验证最终生成能力。执行器会：

- 去重本批参考图
- 跳过已经缓存的文件
- 每批最多上传 9 张
- 最后重新读取缓存确认是否全部成功

当前新图首次入库仍使用隐藏的真实 Chrome；不是 headless。对已成功缓存且缓存仍有效的相同文件，可按 SHA-256 命中缓存，并使用 `--backend direct` 全程不启动浏览器。

2026-07-29 使用 6,391-byte 无用户数据测试图完成过一次真实 warmup：6.32 秒、状态 `prepared`、缓存登记成功。这个数字只证明链路可用，不能代表多 MB 参考图的耗时。

### 第三步：提交

热预存完成后，推荐强制直连：

```bat
python 工具\symphony_video_batch.py submit ^
  --story-root "stories\29-故事名" ^
  --prompt-file "分镜-15秒\分镜提示词.md" ^
  --units 1-37 ^
  --model "Dreamina Seedance 2.0" ^
  --main-accounts all ^
  --backend direct ^
  --confirm-credits 555 ^
  --ledger "工具\运行垃圾\tasks\29\symphony-29.jsonl"
```

把 `555` 换成预检给出的真实数字。

提交规则：

- `--confirm-credits` 必须与这次实际待提交单元的总时长完全一致
- 成功提交后立即把任务号、账号和 backend 追加到账本
- 默认不等渲染空位，尽快提交到平台队列
- `--main-accounts all` 在全部主账号的全部子账号间分摊任务（当前 15 个），并自动把 `--accounts` 视为 `all`
- `--main-accounts main-a` 或 `--main-account main-b --accounts all` 只用一个主账号
- `--accounts main-a,main-b` 在选中的主账号里只使用指定子账号
- 两个都不传时使用 `--account` 或配置文件默认账号（单账号模式）
- 轮询不需要账号参数：账本逐单元记了 `main_account` 与 `account`，poll 按单元各自路由
- 想控制后台积压时加 `--throttle --max-inflight 5`

`5` 是单账号同时渲染数，不是提交闸。默认模式下超出部分会在平台排队；排队时状态长期不变不等于失败。
`--max-inflight 5` 是本地调度节流阈值，不代表平台提交限制；默认提交路径允许超过这个数量，由平台排队。

### 第四步：轮询、下载和核验

```bat
python 工具\symphony_video_batch.py poll ^
  --story-root "stories\29-故事名" ^
  --prompt-file "分镜-15秒\分镜提示词.md" ^
  --units 1-37 ^
  --ledger "工具\运行垃圾\tasks\29\symphony-29.jsonl" ^
  --interval 20 ^
  --timeout 7200
```

执行器按账本里每个单元实际使用的账号查询任务，完成后下载到：

```text
stories\<故事>\分镜-15秒\成片\<单元>单元-symphony.mp4
```

随后用配置文件中的 ffprobe 核验分辨率。若拿到 360×640 播放转码流，会报错，不会把它当成正式原片。

## 六、续跑与异常处理

### 续跑

账本非空时，普通 `submit` 会拒绝重复提交。续跑必须使用同一个账本并加：

```text
--resume
```

执行器会跳过最后状态为：

- `submitted`
- `succeeded`
- `unknown_outcome`

只重试明确没有提交成功的单元。续跑时 `--confirm-credits` 必须改为“本次剩余单元”的积分，而不是原整批总积分。

### `direct_rejected`

平台明确拒绝，代理认为没有创建任务。执行器记录 `rejected` 后继续处理其他单元。不要凭错误码臆测触发词，应保存平台原始 code/message 后再决定换图或改稿。

### `direct_outcome_unknown`

请求可能已经提交并扣分，但响应未能确认。系统会：

- 禁止自动浏览器回退
- 禁止盲目重试
- 写入 `unknown_outcome`
- 停止整批

此时先在平台任务列表和积分记录中人工核对，再更新账本结论。

### 平台排队

繁忙时曾实测提交到出片约 49 分钟；低负载下 15 秒片也曾约 6 分钟。不要用固定公式承诺耗时，也不要因为 `render=2` 长时间不变就重复提交。

## 七、backend 怎么选

| backend | 行为 | 推荐场景 |
|---|---|---|
| `direct` | 只走直连；缺登录态或资产缓存就失败 | 热预存完成后的正式批量生成 |
| `auto` | 优先直连；在确认尚未提交时可回退隐藏 Chrome | 少量新图或日常兼容模式 |
| `browser` | 强制使用隐藏 Chrome 页面路径 | 直连改版后的故障排查 |

计费请求一旦进入结果未知状态，`auto` 也不会回退浏览器，以免重复扣分。

## 八、账号与账号池

本机已保存两个独立 TK 主账号登录态。`main_account` 选择完整登录态，`account`
选择该主账号下的子账号；长期并行时必须同时显式传这两个字段，避免同名子账号混淆。

| 主账号别名 / OpenCLI Profile | 子账号数 |
|---|---:|
| `main-a` | 5 |
| `main-b` | 10 |

代理的账号池接口 `/v1/direct/pool` 一次只认一个 `main_account`（一个主账号一份登录态）。
批量执行器的 `--main-accounts` 在这之上做合并：按主账号逐个查再拼成一个池，一条命令即可
把整批摊到全部 15 个子账号。

```bat
:: 整集大批量：全部主账号 × 全部子账号，池并发 15×5＝75
python 工具\symphony_video_batch.py preflight ... --main-accounts all
python 工具\symphony_video_batch.py submit ... --main-accounts all --confirm-credits 555

:: 只用其中一个主账号（小批、试跑、给另一个主账号留余量）
python 工具\symphony_video_batch.py submit ... --main-account main-b --accounts all --confirm-credits 555
```

池内账号的唯一标识是「主账号/子账号」这一对，不是子账号名——两个主账号下可以出现同名
子账号。账本、日限标记和轮询路由全部按这一对记录，`ROUTE`／`SUBMITTED` 日志也打完整的
`main-b/4`。轮询不需要任何账号参数，它从账本里的 `main_account + account` 精确回到
原登录态。

资产缓存 `asset_cache.json` 是一张全局 SHA-256→URL 表，没有按主账号分区，**跨主账号可直接
复用**：2026-08-01 实测 38 集 U23 在 `main-b/9` 下使用 5 张全部由 `main-a` 时期上传的
缓存图，`backend=direct` 提交成功。因此 `warmup` 只需在任一主账号下做一次，新增主账号不必
重新入库。

`warmup` 走网页端，因此**必须落在一个当天还没被限额的主账号上**。额度用尽的账号进不去创作
面板，OpenCLI 会报「无法切换到 Dreamina Seedance 2.0」——看着像模型问题，其实是账号额度；
缓存写不成，后续提交就全部返回 `direct_unavailable: reference image(s) are not in the direct
asset cache`。执行器已按日限标记自动挑选，打印 `WARMUP_SKIP` / `WARMUP_MAIN`；`warmup` 同时
接受 `--main-account` 与 `--main-accounts`。

**每个子账号每天 30 条生成上限**（2026-08-01 实测：`main-a` 的 5 个子账号当天各提交 30 条后
全部返回 `10001114 User Generation Day Limit`）。上限按子账号计，与积分余额无关——余额充足
也会被拒。触发后执行器写 `data\账号限制.json`，次日早 8 点自动失效。整机日产能＝子账号数 × 30。

查看两个主账号及已保存的子账号快照：

```bat
curl.exe http://127.0.0.1:8765/v1/direct/main-accounts
```

分别刷新并列出两个主账号的子账号：

```bat
curl.exe "http://127.0.0.1:8765/v1/direct/accounts?main_account=main-a"
curl.exe "http://127.0.0.1:8765/v1/direct/accounts?main_account=main-b"
```

查询第二个主账号中子账号 `4` 的余额：

```bat
curl.exe "http://127.0.0.1:8765/v1/direct/credits?main_account=main-b&account=4"
```

持久切换第一个主账号里的 `main-a`：

```bat
curl.exe -X POST "http://127.0.0.1:8765/v1/direct/accounts/main-a/activate?main_account=main-a"
```

正式生成请求示例：

```json
{
  "main_account": "main-b",
  "account": "4",
  "backend": "direct",
  "prompt": "……",
  "refs": ["G:\\短剧创作\\……\\角色.jpg"],
  "mode": "reference",
  "model": "Dreamina Seedance 2.0",
  "duration": 15,
  "wait": false
}
```

查询全池：

```bat
curl.exe "http://127.0.0.1:8765/v1/direct/pool?main_account=main-a"
curl.exe "http://127.0.0.1:8765/v1/direct/pool?main_account=main-b"
```

只查部分账号，支持重复参数和 CSV：

```bat
curl.exe "http://127.0.0.1:8765/v1/direct/pool?accounts=main-a&accounts=main-b"
curl.exe "http://127.0.0.1:8765/v1/direct/pool?accounts=main-a,legacy-a"
```

账号池接口会先取一次账号目录，再为每个账号创建独立客户端并发查询余额和在渲数；单账号失败不会影响其余结果，返回顺序保持账号目录顺序。

## 九、HTTP API 摘要

| 方法 | 路径 | 是否可能打开浏览器 | 是否可能扣积分 |
|---|---|---:|---:|
| GET | `/health` | 否 | 否 |
| GET | `/v1/models` | 否 | 否 |
| GET | `/v1/direct/main-accounts` | 否 | 否 |
| GET | `/v1/direct/accounts?main_account=...` | 否 | 否 |
| POST | `/v1/direct/accounts/{account}/activate` | 否 | 否 |
| GET | `/v1/direct/credits` | 否 | 否 |
| GET | `/v1/direct/pool` | 否 | 否 |
| GET | `/v1/direct/generating-count` | 否 | 否 |
| GET | `/v1/direct/tasks/{task_id}` | 否 | 否 |
| POST | `/v1/assets/warmup` | 是，新图首次上传 | 否 |
| POST | `/v1/videos/generations` | 取决于 backend | **是** |
| POST | `/v1/assets/{asset_id}/download` | 直连失败且允许回退时可能 | 否 |

成功响应统一为：

```json
{"ok": true, "data": {}}
```

失败响应统一为：

```json
{"ok": false, "error": {"code": "...", "message": "...", "details": {}}}
```

## 十、性能与后台化现状

### 账号池查询

`GET /v1/direct/pool` 已从逐账号串行改为最多 5 个独立客户端并发查询，并支持账号白名单下推。模拟 5 账号测试为：

- 串行：0.651–0.654 秒
- 并发：0.172–0.199 秒
- 加速：3.28×–3.79×

这是 MockTransport 结果，不等于真实 TikTok 网络耗时。

### 热预存

当前浏览器上传会把文件转成 base64，以 128 KiB 分块通过浏览器桥传入页面。大 PNG 会产生大量往返；优先使用不覆盖原图的派生 1024px JPEG。

长期方向：

1. 优先确认官方 Symphony API 权限和 Reference-to-Video 覆盖
2. 实现可验证的 HTTP `AssetUploader`
3. 保留当前 BrowserUploader 回退

官方 API、网页私有直传和真 headless 目前都没有接入生产路径。详细研究见 [WARMUP-BACKEND-RESEARCH-20260729.md](WARMUP-BACKEND-RESEARCH-20260729.md)。

## 十一、测试

代理：

```bat
cd /d <主仓库根>\工具\symphony
uv run pytest
uv run python -m compileall -q app tests
```

OpenCLI Symphony 专项：

```bat
cd /d <OpenCLI-Symphony>
npx vitest run --project adapter clis/tiktok-symphony
npm run typecheck
npm run build
```

当前结果：

- 代理：51 passed，1 个 Starlette/httpx 既有弃用警告
- OpenCLI Symphony：34 passed
- TypeScript 类型检查：通过
- OpenCLI 构建：通过

测试不会替代真实平台验证，也不能替代成片观片质检。

## 十二、相关文档

- [项目现状](STATUS.md)
- [账号池性能优化验收](PERFORMANCE-OPTIMIZATION-20260729.md)
- [热预存后台化研究与真实测试](WARMUP-BACKEND-RESEARCH-20260729.md)
- `<主仓库根>\knowledge\视频生成规范.md`
