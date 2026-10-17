# Symphony 热预存后台化研究

日期：2026-07-29  
性质：源码与官方资料研究；未调用真实 TikTok 账号、未修改上传逻辑、未生产验证。

## 结论

可以把生成流程做成后台运行，但应区分三种层次：

1. 当前 `--window background` 已经能无人值守运行，但底层仍是带窗口能力的真实 Chrome。
2. 真 headless Chrome 可以实验，但仍依赖页面、DOM、登录 profile 和反自动化环境，
   不能消除现有 base64/browser bridge 往返，长期收益有限。
3. 纯 HTTP API 是正确终局。优先确认官方 Symphony API 是否已向当前账号开放并覆盖
   Seedance 2.0 Reference-to-Video；不足时才研究当前网页使用的私有上传链路。

推荐顺序：

`官方 Symphony API → 已验证的 DirectAssetUploader → BrowserUploader 回退`

## 源码已证实

- `/v1/assets/warmup` 调用 `generate-video --prepare-only true`。
- `--window background` 是隐藏真实 Chrome，不是 headless。
- 图片被转成 base64，以 128 KiB 分块多次通过 `page.evaluate` 送入页面。
- 页面内构造 `File`、`DataTransfer` 和 `drop` 事件触发上传。
- 协议探针能识别以下候选链路：
  - `POST /creative_bff_i18n/api/cue/upload/token`
  - `PUT upload.byteoversea.com/file`
  - `POST /creative_bff_i18n/api/cue/save_to_my_library`
- 上传后得到 `creative-tool-sg.ibyteimg.com` 稳定 URL，并按文件 SHA-256 写本地缓存。
- `SymphonyDirectClient` 已具备会话加载、账号切换、生成、查询和下载能力，但没有上传实现。

## 官方资料已证实

- TikTok 官方页面公开提供 Symphony API，并称其支持以图片/视频等原始资产进行视频生成。
- 官方页面链接了开发文档、SDK 和 sandbox。
- Symphony Creative Studio 官方帮助页说明 Reference-to-Video 支持多张图片/视频参考输入。

尚未证实：

- 当前 TikTok for Business 账号是否已有 Symphony API 权限。
- 官方 API 是否覆盖当前 Seedance 2.0 多参考图、15 秒及相同积分/账号池语义。
- 官方 API 的授权方式、价格、限流和素材上传端点。
- 当前网页私有上传接口的请求字段、签名、token 生命周期和账号绑定方式。

## 推荐架构

定义统一 `AssetUploader`：

```text
upload_reference(path, account) -> stable_asset_url
```

实现优先级：

1. `OfficialSymphonyUploader`
2. `DirectAssetUploader`（仅在脱敏协议探索和合同测试完成后启用）
3. `BrowserAssetUploader`（保留当前 warmup 作为兼容回退）

缓存键至少包含：

- 文件 SHA-256
- 账号标识或已证明可跨账号复用的作用域
- 上传协议版本

## 最小验证计划

1. 只读确认当前账号是否能访问官方 Symphony API 文档、控制台或 sandbox。
2. 核对官方 API 是否支持 Reference-to-Video、Seedance 2.0、多参考图和 15 秒。
3. 若官方 API 不覆盖，使用现有脱敏协议探针做一次受控上传观察：
   只记录 method、path、header 名称、字段名和响应结构，不保存凭据或签名值。
4. 用 MockTransport 实现并验证三段上传合同。
5. 增加 `backend=auto|official|direct|browser`，默认逐级回退。
6. 最后才做一张非敏感测试图的真实验证，并明确区分模拟结果与真实结果。

## 安全与验收门禁

- Cookie、Authorization、临时上传凭据、签名和完整预签名 URL 不得写日志。
- 每个账号使用独立客户端、session 和上传授权。
- PUT 超时或响应丢失属于 `unknown_upload_state`，不得盲目重传。
- 同文件缓存命中不得再次上传；不同文件不得错误复用 URL。
- direct 失败可以回退 browser，但已进入未知写入状态时禁止自动回退造成重复素材。
- 原始参考图不得被压缩覆盖；派生优化图必须写到独立目录并记录来源 hash。
- 必跑单元测试、合同测试、浏览器回退测试、凭据泄漏扫描和缓存隔离测试。

## 参考

- TikTok Symphony API：
  https://ads.tiktok.com/creative/creativeCenter/tools/api
- TikTok 官方 Reference-to-Video 使用说明：
  https://ads.tiktok.com/help/article/how-to-generate-videos-with-symphony-creative-studio?lang=en
- ChatGPT Pro 研究对话：
  https://chatgpt.com/c/6a696ac0-f5fc-83e8-b92f-fbdb23dc7bae

## 真实 warmup 验证

验证时间：2026-07-29  
授权范围：用户明确授权发送一次真实请求。

测试资产：

- 文件：`.\工具\运行垃圾\tasks\symphony-warmup-probe-20260729\codex-warmup-probe.png`
- 内容：Codex 生成的无用户数据测试图
- 大小：6,391 bytes
- SHA-256：`B28967C5E7B8ADA50F0897F812F47462E6A9BFA2660BAB0F5D230B60B5BE2673`

执行：

- OpenCLI `generate-video --prepare-only true`
- 模式：Reference-to-Video
- 模型：Dreamina Seedance 2.0
- 时长配置：5 秒
- 窗口：background
- site session：persistent
- 协议探针：`OC_SYMPHONY_PROTOCOL_PROBE=1`

结果：

- 退出码：0
- 总耗时：6.32 秒
- 页面确认参考图数量：1
- 返回状态：`prepared`
- `assetId`：null
- 本地资产缓存登记：成功
- 稳定 URL 域名：`p19-creative-tool-sg.ibyteimg.com`
- 未点击生成按钮，未返回生成任务号。
- 没有发送第二次上传请求。

余额只读快照：

- 上传前：账号 `main-b`，credits 1760，weeklySpent 240
- 上传后：账号 `main-b`，credits 1775，weeklySpent 225

余额增加、周消耗减少不是扣费形态，说明测试窗口内存在平台刷新、退款或其他外部状态变化；
不能用这两个快照精确归因本次上传费用。可确认的安全证据是 `prepare-only` 返回
`prepared`、`assetId=null`，且源码在该分支不会调用 `submitComposer()`。

### 发现的协议探针缺陷

`generate-video.js` 在上传前启动网络捕获，但 `prepare-only` 在
`readNetworkCapture()` 之前直接返回。因此本次真实上传虽成功，探针没有输出
`upload/token → PUT → save_to_my_library` 的交易结构。

修复方向：把脱敏网络捕获读取移入 `prepare-only` 返回前，或统一放入安全的 finally
收尾逻辑。该修复可以先用 Mock Page 测试，不需要再次发送真实上传请求。
