# TikTok Symphony 视频通道项目现状

更新时间：2026-07-29
规范源码：`.\工具\symphony`
批量入口：`.\工具\symphony_video_batch.py`

本文只记录当前仍有效的实现、验证和风险。历史事故只保留结论，不再混入日常使用步骤。

## 一、结论

项目已经具备可用的短剧批量生成闭环：

```text
分镜审稿
→ preflight
→ warmup
→ submit
→ 任务账本
→ poll
→ 原片下载
→ ffprobe 核验
```

对已成功缓存且缓存仍有效的参考图，账号查询、账号切换、生成提交、任务轮询和原片下载都可以使用直连接口，不需要启动浏览器。

当前已知仍依赖 Chrome 的场景包括：

1. 首次登录、验证码或登录态更新
2. 新参考图首次上传入库
3. 直连接口改版后的浏览器故障回退

## 二、组件状态

| 组件 | 状态 | 说明 |
|---|---|---|
| 项目内 HTTP 代理 | 可用 | 唯一规范副本，只绑定 `127.0.0.1:8765` |
| 批量执行器 | 可用 | 支持 preflight、warmup、submit、poll |
| 直连登录态 | 可用 | 加密保存于 Windows 凭据管理器 |
| 直连账号切换 | 可用 | Cookie 切换后调用账户接口二次验证 |
| 账号池查询 | 已优化 | 最多 5 个独立客户端并发；支持白名单 |
| 参考图缓存 | 可用 | 按文件 SHA-256 记录可缓存使用的资源 URL |
| 直连视频提交 | 可用 | Seedance 2.0 Reference-to-Video |
| 直连任务查询 | 可用 | 按任务实际账号查询 |
| 直连原片下载 | 可用 | 校验 assetId，拒绝静默交付 360×640 预览流 |
| 新图 warmup | 可用 | 隐藏真实 Chrome，非 headless |
| 真 headless | 未实现 | 可实验，但不是推荐终局 |
| 官方 Symphony API | 未接入 | 官方已提供 API 页面，当前账号权限与能力覆盖未核实 |
| 网页私有上传直连 | 未实现 | 候选三段链路已识别，字段合同尚未捕获完整 |

## 三、最新验证

### 1. 自动测试

| 门禁 | 结果 |
|---|---|
| 隔离环境 `pytest` | 60 passed |
| Python compileall | 通过 |
| OpenCLI Symphony adapter | 34 passed |
| TypeScript typecheck | 通过 |
| OpenCLI production build | 通过 |
| OpenCLI 生产依赖审计 | 0 vulnerabilities（`--omit=dev`） |

代理测试有 1 个既有 Starlette/httpx 弃用警告。

OpenCLI 全量 adapter 基线曾得到 4,771 passed、11 failed；失败集中在 Instagram、Twitter、Suno 和小宇宙的 Windows/POSIX 路径断言，不涉及 Symphony。本项目专项门禁为 34 passed。

### 2. 账号池性能

原账号池接口逐账号串行切换并查询；5 账号情况下，根据当时的调用路径估算约产生 32 次远端 HTTP 请求。当前实现：

- 账号目录只获取一次
- 每个 worker 创建独立 `SymphonyDirectClient`
- 每个 worker 使用独立 httpx client、CookieJar 和 session 副本
- 最多 5 账号并发
- 白名单在代理侧下推，未选账号不查询余额和在渲数
- 单账号失败不影响其他账号
- 返回顺序稳定

模拟测试：

| 模式 | 耗时 |
|---|---:|
| 串行 | 0.651–0.654 秒 |
| 并发 | 0.172–0.199 秒 |
| 加速 | 3.28×–3.79× |

这只证明并发模型；真实 TikTok 账号池的限流和实际耗时尚未重新测量。

### 3. 真实 warmup

2026-07-29 经用户授权发送过一次真实 warmup：

- 测试图：6,391 bytes，无用户素材
- SHA-256：`B28967C5E7B8ADA50F0897F812F47462E6A9BFA2660BAB0F5D230B60B5BE2673`
- 耗时：6.32 秒
- 返回状态：`prepared`
- 页面参考图数量：1
- 本地资产缓存：登记成功
- 返回并登记了可缓存使用的资源 URL，域名为 `p19-creative-tool-sg.ibyteimg.com`
- 没有点击生成按钮
- 没有返回生成任务号

测试前后余额快照反向变化，无法精确归因；可确认的是代码走 `prepare-only`，没有进入 `submitComposer()`。

## 四、当前真实运行语义

### 1. 后台运行

- `backend=direct`：全程直连，绝不启动浏览器
- `backend=auto`：优先直连；只有在提交前确认无法直连时才允许浏览器回退
- `backend=browser`：强制隐藏 Chrome

隐藏 Chrome 是后台窗口，不是真 headless。

### 2. 渲染并发和平台排队

- 每个子账号约 5 条同时渲染
- 横向容量＝子账号数 × 5；当前 2 个主账号共 15 个子账号，合计约 75 条
- 账号池接口一次只认一个主账号；跨主账号要用执行器的 `--main-accounts all`（按主账号逐个查再合并成一个池）
- 平台虽可能在满载后接收任务进入队列，但本地把 5 条作为用户配置的硬提交阀
- 批量执行器与代理双层限流；按在渲数、余额分摊账号，全池满额时等待
- 需要控制后台积压时才加 `--throttle`

低负载下 15 秒片曾约 6 分钟；繁忙时提交到出片曾约 49 分钟。项目不再承诺固定吞吐公式。

### 3. 计费安全

- 正式提交必须提供精确的 `--confirm-credits`
- 明确被平台拒绝时记录 `rejected`
- 结果不确定时记录 `unknown_outcome` 并停止整批
- 未确认前禁止自动重试、浏览器回退或重复提交
- 已有非空账本时禁止普通重复提交，必须显式 `--resume`

### 4. 下载安全

- 原片下载优先使用直连接口
- 请求 assetId 与返回 URL 中的 vid 必须一致
- 360×640 播放转码流不能作为正式成片
- 下载完成后必须运行 ffprobe
- 文件身份、时长和分辨率检查不能替代人工观片

## 五、已解决的关键问题

1. 下载等待错误回捞上一条历史记录，导致不同 assetId 得到相同文件。
2. 网页下载按钮漏传 vid，导出长期停在 0%。
3. 参考图 DOM 计数翻倍，可能少挂图却误判成功。
4. 首次 drop 被页面吞掉但没有报错。
5. 提交前缺少最终参考图数量断言。
6. 低清播放流被误当作原片。
7. 网页账号切换偶发菜单失败，无法作为无人值守主路径。
8. 全账号池查询串行，重复账号目录和身份请求。
9. 账号白名单只在批处理本地过滤，未下推到代理。
10. （2026-08-02）warmup 落在已限额子账号的 workspace 上报「无法切换到模型」：代理 `/v1/assets/warmup` 新增 `account` 字段，进面板前先 `account --use` 切到执行器挑好的未限额子账号（`pick_warmup_target` 已选到子账号粒度）。**同时修了 ./OpenCLI-Symphony\clis\tiktok-symphony\account.js 两处**：①账号菜单改为悬停触发，点击会把刚弹开的菜单又关上——openAccountMenu 现在先纯悬停等 8 秒、失败才回退真点击；②中文界面下菜单行是全角冒号「ID：」，三处正则改为 `ID[:：]`。**这两处补丁在上游项目里，若 F 盘项目被更新/重装会丢，需按本条复查**。

11. （2026-08-02）池视图不含提交闸门的短时保留，批量调度器视图偏乐观白吃 429：`/v1/direct/pool` 与 `/v1/direct/generating-count` 现在回报 `reserved`/`effective`（与提交口同一把 AccountCapacityGate 对账，平台计数跟上后按增量回收），执行器把 reserved 折进在渲数记账。同时给执行器加了 `submit --watch` 一条龙（提交完自动轮询下载；`unknown_outcome` 停批时不进入轮询）。跨进程并发提交的最终仲裁仍是代理提交口的容量闸门（fail-closed，429 不建任务不扣分），**不需要**执行器侧文件锁。

目前主路径均有对应代码修复或安全替代方案。

## 六、未完成和已知风险

### P1：新图首次上传仍依赖 Chrome

当前 warmup 需要页面加载、base64 分块、`File/DataTransfer/drop` 和网页上传组件。多 MB PNG 会产生大量浏览器桥往返。

目标架构：

```text
官方 Symphony API
→ 可验证的 DirectAssetUploader
→ BrowserUploader 回退
```

正式实现前必须确认授权、账号隔离、上传 token 生命周期、签名、超时后的未知状态和缓存作用域。

### P1：prepare-only 协议探针提前返回

`generate-video.js` 在上传前启动网络捕获，但 `prepare-only` 在读取捕获结果之前返回。因此真实 warmup 已成功，探针却没有输出上传交易结构。

修复应先补 Mock Page 测试，再移动脱敏捕获读取逻辑；不需要为了修探针重复发送真实请求。

### P1：真实账号池性能尚未复测

并发实现已通过模拟测试，但真实 TikTok 环境可能有：

- 每账号或每登录限流
- 网络抖动
- 长期连接池行为差异
- 会话刷新与账号目录变化

不得把 3.28×–3.79× 模拟结果宣传为真实生产加速。

### P2：平台排队不可预测

任务长时间保持排队状态不等于失败。不得因等待时间超过历史均值就重投。

### P2：平台审核误判

真实人物、公众人物、内容安全等审核可能拒绝任务。审核原因应按平台原始返回记录，不应由代码臆测。

### P2：人工画面质检

自动流程只能确认：

- 任务状态
- assetId
- 文件大小
- 哈希
- 分辨率
- 时长

人物一致性、剧情、动作、口型和画面质量仍需人工观看。

## 七、下一步优先级

1. 修复 `prepare-only` 协议探针的提前返回，并补纯 Mock 回归测试。
2. 只读确认当前账号能否使用官方 Symphony API、sandbox，以及是否覆盖 Seedance 2.0 多参考图。
3. 若官方 API 不覆盖，再进行一次单独授权的脱敏上传协议观察。
4. 为上传链路设计 `AssetUploader` 抽象和 `unknown_upload_state`。
5. 在不提交视频的前提下真实复测优化后的 `/v1/direct/pool` 耗时和限流。
6. 清理 OpenCLI 全量测试中的跨平台路径基线失败，但不与 Symphony 功能改动混在同一补丁。

## 八、源码与交付状态

- 父仓库当前 commit：`4b0ef653470d4870263b341b5a65b11b6058d685`
- `工具\symphony\` 和 `工具\symphony_video_batch.py` 在父仓库中当前仍为未跟踪文件

## 九、便携分发状态

**2026-08-02 用户裁定：许可证强制校验默认关闭**（个人使用不需要防拷贝锁）。`app/main.py` 现只在显式传入 `license_manager`（测试/受保护构建）或设 `SYMPHONY_LICENSE_ENFORCE=1` 时拦截；签发工具、校验器、三个 `/v1/license/*` 端点全部保留，`/v1/models` 在未强制时回显 `license.code=not_enforced`。恢复分发时打开环境变量开关即可，下述便携包机制不受影响。同日起 OpenCLI 源码收编至 `外部依赖/OpenCLI-Symphony`（唯一维护版），`symphony配置.json` 的 OpenCLI 入口已指向项目内。

2026-07-29 已完成 Windows 受控测试便携包：

- Nuitka standalone 编译 `SymphonyLocal.exe` 和 `SymphonyBatch.exe`
- 内置 OpenCLI 生产依赖、Node.js、ffprobe 和 Chrome 扩展
- 目标电脑无需安装 Python、Node.js、uv、OpenCLI 或 ffprobe
- 机器绑定 Ed25519 离线许可证
- 登录态和许可证使用 Windows 凭据管理器
- Ed25519 完整性清单覆盖 3,672 个文件
- 已模拟只保留系统 PATH，包内 EXE、Node、OpenCLI、ffprobe 均可直接运行
- 已实际启动包内代理并得到 `/health` 200
- 已修改测试副本中的签名文件，程序正确拒绝启动

仍未验证：

- 尚未在一台真正干净、没有开发环境的新电脑或 Windows VM 中走完首次安装
- 尚未在新电脑上完成真实 TikTok 登录和视频生成
- ffprobe 当前来自 GPLv3 构建，只适合当前受控内部测试；扩大对外分发前须完成许可证合规复核
- 未购买 Windows 代码签名证书，因此 SmartScreen 仍可能警告
- 本轮及此前修改都只存在于本地
- 未提交 Git
- 未推送远程
- 未创建 PR
- 未部署
- 未修改线上配置

## 九、证据文档

- [使用指南](README.md)
- [账号池性能优化验收](PERFORMANCE-OPTIMIZATION-20260729.md)
- [热预存后台化研究与真实测试](WARMUP-BACKEND-RESEARCH-20260729.md)
- `.\knowledge\视频生成规范.md`
