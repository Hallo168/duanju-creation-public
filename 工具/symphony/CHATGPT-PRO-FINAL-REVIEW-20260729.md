# ChatGPT Pro 最终复核任务：Symphony Windows 便携分发包

## 背景和目标

项目把 TikTok Symphony Creative Studio 接入本地短剧批量生成流程。目标是在 Windows 新电脑上分发一个受控测试便携包：目标机不需要安装 Python、Node.js、uv、OpenCLI 或 ffprobe；源码入口使用 Nuitka standalone 编译；使用机器绑定的 Ed25519 离线许可证；登录态和许可证保存到 Windows Credential Manager；产品文件使用 Ed25519 完整性清单防篡改。

本轮请作为独立高级工程师做最终代码与证据复核。你的结论不是自动验收，Codex 会根据源码和测试独立判断。

## 当前架构和不可破坏边界

- HTTP 服务只监听 `127.0.0.1`。
- TikTok 登录态不得写入配置、日志、ZIP 或仓库，只能进入 Windows Credential Manager。
- 许可证私钥只存在发行端，产品包只能包含公钥。
- 未授权提交 Git、推送、PR、部署、数据库迁移、线上配置或真实用户数据操作。
- 不得发送 TikTok 视频生成请求或消耗积分。
- 新电脑仍需 Chrome；验证码、密码、Passkey 和两步验证由用户本人完成。
- 未购买 Authenticode 证书；这只是未签名的受控测试包，不能声称消除 SmartScreen。

## 需要复核的范围

1. `app/product_cli.py`、`app/batch_product_cli.py` 的编译入口和错误边界。
2. `app/licensing.py`、`app/session_store.py` 的 Credential Manager、机器码和许可证验证。
3. `app/integrity.py` 与 `app/license_admin.py` 的签名、路径越界、防删除清单和校验顺序。
4. `app/config.py` 与 `symphony_video_batch.py` 的便携路径和 `%LOCALAPPDATA%` 数据路径。
5. `tools/build_product.ps1` 的安全清理范围、Nuitka 构建、依赖装入、敏感信息排除、签名顺序、ZIP 和第三方许可证说明。
6. `tests/test_product_packaging.py` 及现有许可证、API、runner 测试是否覆盖关键失败路径。
7. README、STATUS 和便携构建报告是否准确，没有把模拟测试称为生产验证。

## 已执行证据

- 父仓库基线：`4b0ef653470d4870263b341b5a65b11b6058d685`
- Python 隔离测试：60 passed
- OpenCLI TypeScript typecheck：通过
- TikTok Symphony adapter：34 passed
- OpenCLI production build：通过
- 仅保留 Windows System32 PATH 时，包内两支 EXE、Node、OpenCLI、ffprobe 均可运行
- 包内代理 `/health`：HTTP 200/healthy
- 篡改测试副本的 `使用说明.txt` 后，EXE 退出码 1 并报告完整性异常
- 最终产品 ZIP：154,054,288 bytes
- SHA-256：`BAC76B4B470E75C3A0B301ED413EDEC8E2A0A5D1C6C85812AE6BA3D994FC4C80`
- 最终产品配置的 `api_key` 为空；未发现 `.env`、私钥、Cookie、Token 或凭据文件

## 明确交付物

请返回：

1. 按 P0/P1/P2 排序的具体缺陷；每项必须给出文件、位置、触发条件、影响和最小完整修正。
2. 若存在缺陷，提供可应用的 unified diff；不要重写无关代码。
3. 若没有阻断项，明确说明“未发现阻断受控测试分发的问题”，并列出仍未验证的风险。
4. 单独核对目标机是否确实不需要系统级 OpenCLI、Node.js 和 Python。
5. 单独核对完整性校验是否 fail-closed、许可证私钥是否可能进入产品包、构建脚本是否可能误删用户目录。

## 必须执行或静态核对的测试

- 静态检查所有附件源码和构建脚本。
- 核对测试是否覆盖：篡改、删除清单、错误机器码、过期许可证、批处理入口先校验后执行。
- 若附件足够，可运行不联网、不写外部状态的单元测试。
- 不得声称在干净 Windows VM、真实 TikTok 账号或 SmartScreen 上测试，除非确实有证据。

## 禁止操作和禁止声称

- 不要提交、推送、创建 PR 或部署。
- 不要访问真实 TikTok、发送生成请求、切换真实账号或消耗积分。
- 不要要求或输出密码、Cookie、Token、私钥、验证码或恢复码。
- 不要把 Nuitka/加密描述为绝对不可逆。
- 不要把 Ed25519 产品完整性签名称为 Windows Authenticode 代码签名。
- 不要把本机 PATH 隔离模拟称为真正的干净机验证。

## 验收标准

- 产品包不含源码入口、私钥或运行凭据。
- 目标机无需系统级 Python、Node.js、uv、OpenCLI、ffprobe。
- 安装目录可移动，运行数据写入 `%LOCALAPPDATA%\Symphony`。
- 删除或修改已签名文件时，两支入口在执行业务逻辑前拒绝运行。
- 错误机器、过期或损坏许可证无法放行业务请求。
- 构建脚本的递归清理只针对可证明的专用生成目录。
- 文档准确披露 Chrome、首次登录、SmartScreen、GPL ffprobe 和未做干净机验证等风险。
