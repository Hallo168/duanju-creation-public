# Symphony 发布就绪审查

审查日期：2026-07-29  
源码基线：父仓库 `4b0ef653470d4870263b341b5a65b11b6058d685`；`工具/symphony/` 与 `工具/symphony_video_batch.py` 当前未被 Git 跟踪。

## 结论

当前版本只能用于开发机和受控内测，不能直接分发给一台干净的 Windows 电脑，更不具备公开销售、批量安装或无人协助安装条件。

核心生成、账号池、许可证和完整性校验代码已经具备基础，但发行包尚未形成可运行闭环。

## 已实测的 P0 阻断

### 1. 默认构建命令必然失败

`tools/build_product.ps1` 默认输出到旧目录：

```text
./tiktok-symphony-proxy\dist-product
```

同一脚本又要求输出目录必须位于当前项目目录内。直接执行：

```powershell
.\tools\build_product.ps1
```

立即得到 `OutputDir must be a dedicated directory inside ...`，没有进入编译。

### 2. 显式指定合法目录后仍无法编译

执行：

```powershell
.\tools\build_product.ps1 -OutputDir .\dist-product -OpenCliRoot ./OpenCLI-Symphony
```

Nuitka 在当前中文 Windows 环境读取环境文件时发生：

```text
UnicodeDecodeError: 'gbk' codec can't decode byte 0xad
```

没有生成 `SymphonyLocal.exe`。

### 3. OpenCLI 发行内容不完整

构建脚本只复制：

- `dist/`
- `package.json`
- `cli-manifest.json`

没有复制 `node_modules/` 和 `clis/`。按这一结构建立隔离副本后：

```text
node dist/src/main.js tiktok-symphony --help
```

实测立即失败：

```text
ERR_MODULE_NOT_FOUND: Cannot find package 'commander'
```

当前目录规模：

- OpenCLI `dist/`：约 2.1 MB
- `clis/`：约 9.6 MB
- `node_modules/`：约 196.6 MB

此外，客户机仍需要可用的 Node.js、Chrome 和 OpenCLI 浏览器扩展；当前包没有完整解决这些依赖。

### 4. 产品启动器没有使用包内 OpenCLI

生成的 `启动服务.cmd` 只运行：

```bat
runtime\proxy\SymphonyLocal.exe serve
```

没有设置：

- `OPENCLI_NODE_PATH`
- `OPENCLI_CLI_PATH`
- `SYMPHONY_ASSET_CACHE`
- `SYMPHONY_LICENSE_PUBLIC_KEY`

而 `app/config.py` 的 OpenCLI 默认路径仍是：

```text
./OpenCLI-Symphony\dist\src\main.js
```

所以即使编译成功，换机器后 warmup、首次登录和浏览器回退也会寻找不存在的开发机路径。

### 5. 实际业务入口没有进入发行包

用户日常入口 `工具/symphony_video_batch.py` 没有被构建或复制。当前候选包只有本地 HTTP 代理，不包含完整的：

- preflight
- warmup
- submit
- poll
- resume
- ffprobe 核验
- `api配置.json` 联动

这与当前“短剧批量生成软件”的实际使用范围不一致。

## P1 发布缺口

- 可写数据仍可能落到安装目录；若安装在 `Program Files`，普通用户可能无法写入资产缓存。运行数据应迁移到 `%LOCALAPPDATA%\Symphony`。
- 没有客户机可直接执行的“查看机器码、导入许可证、查看状态”辅助入口和安装说明。
- 没有在无 Python、无 Node、无 uv 的干净 Windows VM 中验证。
- 没有验证首次安装、首次登录、Chrome 扩展安装、登录失效恢复和卸载。
- 完整性清单会验证已登记文件，但仍需验证运行期数据、升级文件与签名文件之间的边界。
- `compiled_bundle_root().parents[2]` 在当前计划的 `runtime\proxy\SymphonyLocal.exe` 层级下成立；只有发行目录布局改变时才需要改为向上查找 `integrity.json`。

## P2 产品化缺口

- 没有 Authenticode 代码签名，可能触发 SmartScreen。
- 没有 MSI、Inno Setup 或其他安装/卸载器。
- 没有版本升级、配置迁移和失败回滚方案。
- OpenCLI 使用 Apache-2.0，但当前构建脚本没有复制其 `LICENSE`；第三方依赖许可证清单也未生成。
- TikTok/Chrome 扩展及网页私有接口的对外分发边界仍需单独确认。

## 达到可分发所需的最小顺序

1. 修复构建脚本默认目录和 UTF-8/Nuitka 构建。
2. 确定产品范围：仅 HTTP 代理，还是包含批量执行器的完整软件。
3. 形成自包含 OpenCLI 运行时，包含 Node、生产依赖、`clis/tiktok-symphony`、扩展和许可证文件。
4. 让启动器只使用相对路径，并把可写数据放入 `%LOCALAPPDATA%`。
5. 把批量执行器、ffprobe、配置模板和首次激活入口纳入产品。
6. 生成签名清单和最终 ZIP/安装器。
7. 在干净 Windows VM 完成无 Python、无 Node、无 uv 的安装、激活、启动和 Mock smoke test。
8. 在单独授权下完成人工登录、无扣费 warmup；计费生成必须另行授权，不能用 Mock 冒充。
9. 补安装、升级、回滚、卸载和 SmartScreen/代码签名验证。

## 审查附件

文件：

```text
.\工具\运行垃圾\tasks\symphony-distribution-review-20260729-1519.zip
```

- 文件数：28
- 大小：127,427 bytes
- SHA-256：`3471BB31F2ED5D65ABFEB3FBCDBD57D3A5C00DFA4F4C24EDB6374A5C45C5A45C`
- 密钥扫描：未发现凭据型赋值
- 不包含资产缓存、运行日志、测试许可证、登录态或发行私钥

ChatGPT Pro 独立审查：

```text
https://chatgpt.com/c/6a69ad07-8c10-83ee-915e-2cb3e0196529
```

外部审查结论同样为“不可公开分发，只能受控内测”。最终判断以本地构建和隔离运行证据为准。
