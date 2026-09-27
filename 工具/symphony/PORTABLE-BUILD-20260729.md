# Symphony 便携分发构建与验收记录

日期：2026-07-29  
用途：未签 Windows 代码签名的受控测试分发  
父仓库基线：`4b0ef653470d4870263b341b5a65b11b6058d685`

## 交付目标

目标电脑无需预装 Python、Node.js、uv、OpenCLI 或 ffprobe。便携包内置两支 Nuitka standalone EXE、OpenCLI 生产依赖、Node.js、ffprobe 和 Chrome 扩展。目标电脑只需安装 Chrome、加载扩展、激活机器绑定许可证，并由用户本人完成 TikTok 登录。

## 安全边界

- 离线许可证使用 Ed25519 签名并绑定机器码。
- 许可证和 TikTok 登录态保存到 Windows 凭据管理器。
- `integrity.json` 使用 Ed25519 签名并记录每个分发文件的大小与 SHA-256。
- 已签名文件被修改、缺失或清单签名无效时，编译后的入口拒绝运行。
- 产品配置仅包含 `127.0.0.1` 本地代理，不包含 API Key、Cookie、Token、私钥或源环境配置。
- 私钥只用于发行端签发许可证和签署构建，不进入产品包。

## 构建结果

- 产品目录：`<项目根目录>\工具\symphony\dist-product`
- 产品 ZIP：`<项目根目录>\工具\symphony\dist-product.zip`
- 完整性清单覆盖：3,672 个文件
- ZIP 文件数：3,673
- 当前构建脚本固定使用 `<本机路径>` 作为纯英文编译中间目录，以规避 MSVC 链接器对中文路径的错误解码。
- 构建脚本保存为带 BOM 的 UTF-8，确保 Windows PowerShell 5.1 正确解析中文快捷文件名和说明。

最终 ZIP 大小和 SHA-256 以本报告末尾“最终制品”及同目录构建输出为准。

## 独立验收

| 检查 | 结果 |
|---|---|
| Python 隔离测试 | 60 passed |
| OpenCLI TypeScript typecheck | 通过 |
| Symphony adapter 专项测试 | 34 passed |
| OpenCLI production build | 通过 |
| OpenCLI 生产依赖审计 | 0 vulnerabilities |
| `SymphonyLocal.exe machine` | 通过 |
| `SymphonyBatch.exe --help` | 通过，中文正常 |
| 包内 Node + OpenCLI `--help` | 通过 |
| 包内 ffprobe `-version` | 通过 |
| 仅保留 Windows System32 PATH 的依赖模拟 | 通过 |
| 包内代理 `/health` | HTTP 200，`healthy` |
| 篡改签名文件后运行 | 拒绝，退出码 1，报告文件大小异常 |
| ZIP SHA-256 二次计算 | 与构建输出一致 |

没有把 Mock 测试称为生产验证。此次分发验收没有发送新的视频生成请求，也没有消耗 TikTok 积分。

## 未验证风险

1. 尚未在真正干净的新电脑或 Windows VM 上完成端到端安装。
2. 尚未在新电脑上完成 Chrome 扩展加载、TikTok 首次登录和真实视频生成。
3. 未做 Windows Authenticode 代码签名，SmartScreen 可能警告。
4. 当前 ffprobe 为 gyan.dev GPLv3 构建；扩大到公开或商业外部分发前必须完成许可证义务复核，或更换符合分发策略的构建。
5. 本地编译和完整性校验提高逆向与篡改成本，但不能承诺绝对不可破解。

## ChatGPT Pro 最终复核

对话：`https://chatgpt.com/c/6a69ad07-8c10-83ee-915e-2cb3e0196529`

Pro 复核原始结论为：“未发现阻断受控测试分发的问题”，P0 为 0。它建议清单缺失时不再猜目录，以及把构建机固定路径参数化；两项均已落地并复验。

Codex 没有直接接受它对构建误删的泛化担忧：`Resolve-SafeChild()` 已要求输出严格位于专用父目录下，项目根本身不能通过检查。Codex 另外发现并修复了 Pro 未指出的 `js-yaml` 生产依赖高危拒绝服务漏洞。

最终增量复核包：

- 文件：`<项目根目录>\工具\运行垃圾\tasks\symphony-portable-final-delta-20260729.zip`
- 大小：58,789 bytes
- SHA-256：`0E8C9C11A00432A4657C8BAA24FD9F269178545E55484149767D443EABC34696`

Pro 第二次结论：新增 P0 为 0，新增 P1 为 0；当前可以作为 Windows 便携受控内测候选，但仍不是公开商业发行版。

## 版本控制与发布状态

当前只有本地修改和本地构建。未提交 Git、未推送远程、未创建 PR、未部署、未迁移数据库、未修改线上配置。

## 最终制品

最终构建完成后记录：

- ZIP 大小：154,054,288 bytes
- SHA-256：`BAC76B4B470E75C3A0B301ED413EDEC8E2A0A5D1C6C85812AE6BA3D994FC4C80`
