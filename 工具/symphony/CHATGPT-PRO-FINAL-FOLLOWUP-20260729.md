# ChatGPT Pro 最终复核后的修正与复验

同一对话中上一份源码复核完成后，Codex 独立审计又发现并修正了以下事项：

1. OpenCLI 生产依赖 `js-yaml 4.2.0` 命中 GHSA-52cp-r559-cp3m。已最小升级到 `js-yaml 4.3.0`，更新 `package.json` 和 `package-lock.json`。
2. 接受 Pro 的建议：`compiled_bundle_root()` 找不到 `integrity.json` 时直接抛出 `integrity_manifest_missing`，不再返回 `parents[2]` 猜测目录。
3. 接受 Pro 的建议：构建脚本新增 `-CompilerRoot` 参数，并支持 `SYMPHONY_BUILD_ROOT`；`F:\CodexBuild` 只作为发行机构建机默认值。
4. 新增测试：主 EXE 在读取机器码前校验完整性、缺失清单 fail-closed、编译 bundle root 不猜目录。

最终复验：

- Python：60 passed
- OpenCLI production `npm audit --omit=dev`：0 vulnerabilities
- TypeScript typecheck：通过
- Symphony adapter：34 passed
- OpenCLI production build：通过
- 两支 Nuitka EXE 从源码完整重编译成功
- 真实编译 EXE 缺失 `integrity.json`：退出码 1，提示“安装包完整性清单缺失，拒绝启动”
- 最终便携 EXE、包内 Node/OpenCLI/ffprobe：仅 System32 PATH 冒烟通过
- 最终包内代理 `/health`：HTTP 200/healthy

最终产品制品：

- 文件：`<主仓库根>\工具\symphony\dist-product.zip`
- 大小：154,054,288 bytes
- SHA-256：`BAC76B4B470E75C3A0B301ED413EDEC8E2A0A5D1C6C85812AE6BA3D994FC4C80`

请只复核本增量包中的变更是否引入新的 P0/P1 阻断项。不得真实访问 TikTok、提交生成、提交 Git、推送或部署。若没有，请简洁确认“未发现新的受控测试分发阻断项”，并保留干净机、SmartScreen、首次登录和 ffprobe GPL 合规等未验证风险。
