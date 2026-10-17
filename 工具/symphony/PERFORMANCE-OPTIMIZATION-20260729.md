# Symphony 账号池查询性能优化验收记录

日期：2026-07-29
范围：`.\工具\symphony` 与 `.\工具\symphony_video_batch.py`
状态：仅本地修改；未提交、未推送、未部署、未访问真实用户数据。

## 基线与外部审查

- 父仓库基线 commit：`4b0ef653470d4870263b341b5a65b11b6058d685`
- 规范源码目录在父仓库中当前为未跟踪目录，现有其他工作区改动未覆盖。
- 交给 ChatGPT Pro 的安全源码包：
  - 路径：`.\工具\运行垃圾\tasks\symphony-speed-review-20260729-1042.zip`
  - 大小：266,018 bytes
  - SHA-256：`5D06391D29209C592E12401DE0B013CC30F56BE42305F087020354773FD42EC2`
  - 条目数：53
  - 上传前密钥扫描：通过
- ChatGPT Pro 对话：
  - <https://chatgpt.com/c/6a696ac0-f5fc-83e8-b92f-fbdb23dc7bae>
- Codex 候选审查包 SHA-256：
  - `78F20E05714FF2547E87496153FB2D4202370C68102447A9A54CC5F426BFDBD6`
- Pro 最终结论：无阻断问题；真实 TikTok 环境耗时与限流仍需另行验证。

## 问题与实现

原 `/v1/direct/pool` 对 5 个账号串行执行账号切换、身份确认、余额查询和在渲数查询，
约需 32 次远端操作；现场耗时约 14 秒。

本次实现：

1. 账号目录只获取一次，不再为每个 worker 重复扫描账号列表。
2. 最多 5 个独立 `SymphonyDirectClient` 并发查询；每个 worker 拥有独立
   `httpx.Client`、CookieJar 与 session 副本，避免跨账号 Cookie 污染。
3. 切换账号后仍调用 `current_account()` 验证身份；异常时按原 domain/path
   回滚 selector Cookie。
4. 已验证身份后直接查询余额，去掉同一 worker 中多余的身份请求。
5. API 支持重复 `accounts` 参数和 CSV；未知账号返回结构化 422，单账号失败不影响其余账号。
6. 批处理脚本把 `--accounts` 白名单 URL 编码后下推到代理，未选账号不再查询余额和在渲数。
7. 返回顺序保持账号目录顺序；未改动 OpenCLI warmup 的 128 KiB browser bridge 分块协议。

## 独立验证

### Symphony

- `uv run pytest`：51 passed，1 个既有 Starlette/httpx 弃用警告。
- `uv run python -m compileall -q app tests`：通过。
- 并发端点性能测试连续 5 次通过。
- 模拟 5 账号、每个远端阶段 40 ms：
  - 串行：0.651–0.654 秒
  - 并发：0.172–0.199 秒
  - 加速：3.28×–3.79×
- 覆盖：真实 FastAPI 端点、稳定顺序、独立 factory/client、Cookie 隔离、
  白名单与中文账号、未知账号、单账号故障隔离、批处理 URL 编码。

### OpenCLI-Symphony

- `npx vitest run --project adapter clis/tiktok-symphony`：34 passed。
- `npm run typecheck`：通过。
- `npm run build`：通过，manifest 1,289 entries。
- `npm run test:adapter` 全量：4,771 passed，11 failed。失败均在 Instagram、
  Twitter、Suno、小宇宙的 Windows/POSIX 路径断言，不涉及 Symphony，本次未修改。

### 安全

- 最终 6 个变更源码/测试文件的常见凭据模式扫描：无命中。
- 未打包或持久化 `.env`、Token、Cookie、私钥、数据库、浏览器状态或运行状态。

## ChatGPT Pro 纠错记录

1. 初版把约 32 次远端操作误算为 21 次，并误判 `PoolScheduler` 没有本地记账。
2. 首次补丁不是合法 unified diff，并引用不存在的 `splitBase64`。
3. 第二次交付的 ZIP 含缓存；测试只是线程池占位；Cookie 未保留原 domain/path、
   无回滚；余额调用仍有多余身份请求。该交付未采用。
4. 对 Codex 候选的首次复核误判 factory 隔离与测试覆盖不足。补充行级证据和对象
   identity 断言后，Pro 撤销两项阻断意见，最终结论为无阻断问题。

## 尚未验证

- 没有对真实 TikTok 账号池发请求，因此不能宣称现场 14 秒会降到具体数值。
- 未验证平台在 5 账号同时查询时的真实限流、网络抖动和长时间连接池行为。
- warmup 本次保持现有 128 KiB 分块，未扩大 browser bridge payload；其速度未变化。
- 全量 OpenCLI 适配器的 11 个跨平台路径失败属于既有外部基线问题。
