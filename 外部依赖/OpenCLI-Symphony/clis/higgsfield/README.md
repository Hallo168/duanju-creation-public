# Higgsfield 视频适配器

复用**已登录的 OpenCLI 持久浏览器会话**，驱动 [higgsfield.ai/ai/video](https://higgsfield.ai/ai/video) 的真实网页界面，用 **Seedance 2.0 Unlimited** 文生视频，并轮询、下载成片。

> **为什么走浏览器而不是官方接口？**
> - 官方 API **不支持 Unlimited mode**（本项目的核心诉求）。
> - 站点内部接口 `/fnf/jobs/*` 背后是 **Cloudflare + DataDome** 人机验证，服务端直连会被 403 拦截。
> 因此提交必须由**真实浏览器里的前端自己发起**——这与仓库里 Symphony、Google Flow 两条链路是同一套架构。

---

## 前置条件

- 已构建的 OpenCLI：`<OpenCLI-Symphony>\dist\src\main.js`
- Chrome 安装 OpenCLI 浏览器扩展，并已登录 Higgsfield（Google 账号）
- 首次授权：
  ```bat
  cd /d <OpenCLI-Symphony>
  node dist/src/main.js higgsfield login
  ```
- 确认登录状态：
  ```bat
  node dist/src/main.js higgsfield whoami --window background
  ```

---

## 文生视频：提交 → 轮询 → 下载（一条命令）

```bat
cd /d <OpenCLI-Symphony>
set OPENCLI_BROWSER_COMMAND_TIMEOUT=1200

node dist/src/main.js higgsfield generate-video "A serene mountain lake at sunrise, slow cinematic aerial push-in, golden light, ultra realistic" ^
  --prepare-only false ^
  --unlimited true ^
  --wait ^
  --out "F:\输出\video.mp4" ^
  --timeout 1200 ^
  --window background
```

| 参数 | 作用 |
|---|---|
| `--prepare-only false` | **真正提交并扣积分**。默认 `true`：只配置合成器、不点生成（零成本预检） |
| `--unlimited true` | 强制打开 **Unlimited mode**；提交前会校验，没开就**拒绝提交** |
| `--wait` | 提交后轮询，直到视频渲染完成 |
| `--out <路径>` | 渲染完成后下载到本地（隐含 `--wait`） |
| `--timeout <秒>` | 等待渲染的最长时间，默认 1200（20 分钟） |
| `--prompt-file <文件>` | 从 UTF-8 文件读提示词。**长提示词（如分镜脚本）必须用它**，命令行参数有长度上限 |

**默认生成参数**：模型 `Seedance 2.0`（Unlimited）、时长 `15s`、分辨率 `720p`、比例 `9:16`。可用 `--duration/--resolution/--ratio` 覆盖；每项设置后都会**读回校验**，设错宁可报错也不会拿错参数去烧积分。

---

## 独立轮询 / 补下载（不重新提交、不花积分）

如果 `generate-video` 提交后中途断了，或你想单独等一个正在跑的任务并下载：

```bat
node dist/src/main.js higgsfield jobs --wait --out "F:\输出\video.mp4" --window background
```

`jobs` 也可只查状态：

```bat
node dist/src/main.js higgsfield jobs --limit 5 --window background
```

---

## 关键约束（实测）

- **并发上限 = 1**：plus 套餐同一时间只能有一个任务在渲染。已有任务在跑时再提交会收到 `429 rate_limit_reached`，命令会明确提示「并发上限，等当前任务完成再重试」——**不会盲目重试**。
- **Unlimited 判据**：开启后生成按钮文案是 `Generate Unlimited`；未开启时是 `Generate <积分数>`（如 `Generate 90 68`）。`generate-video` 会强制开启并校验。
- **审核失败会退积分**；结果面板会显示 `Failed · Credits refunded`。
- **渲染耗时**：15 秒视频约几分钟。
- **提交确认**：适配器在页面内挂了 XHR 探针，捕获前端真实的 `POST /fnf/jobs/v2/seedance_2_0` 响应，据此区分「已提交 / 被拒 / 结果未知」，结果未知时**不自动重试**（避免重复扣费）。

---

## 参考图（可选，目前半自动）

参考图要先上传进 Higgsfield 的「我的上传」库（经过人机审核），再**点选**进当前生成——**点选顺序 = 提示词里的「图片1 / 图片2 / …」**。这一步 UI 交互不稳定，当前推荐做法：

1. 在浏览器里手动上传并选好参考图（按提示词的图片顺序）；
2. **保持页面不刷新**；
3. 用 `--navigate false` 复用当前页面（不刷新、不碰图），只由适配器填提示词、提交、轮询、下载：
   ```bat
   node dist/src/main.js higgsfield generate-video --prompt-file "分镜提示词.txt" ^
     --navigate false --prepare-only false --unlimited true ^
     --wait --out "F:\输出\单元35.mp4" --window background
   ```

---

## 命令一览

| 命令 | 作用 |
|---|---|
| `login` | 打开登录页，等待完成授权 |
| `whoami` | 显示当前登录账号 |
| `generate-video` | 配置合成器并生成（默认 `--prepare-only` 不提交） |
| `jobs` | 查看 / 轮询 / 下载最近的生成 |
| `composer-options` | 只读探测合成器控件与选项（调试用，不扣积分） |

---

这是对 Higgsfield 网页流程的自动化封装，**不是** Higgsfield 官方稳定 API。站点改版、登录失效或触发风控时，需要重新登录或更新适配器。
