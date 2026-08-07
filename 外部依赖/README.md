# 外部依赖（vendor 收编，2026-08-02）

本目录是三个外部项目的**源码收编**，自此以项目内这份为唯一维护版（F 盘原件停用不删，作离线备份）。目的：主仓库克隆到新机器后，不再依赖 F 盘散装项目。

| 目录 | 是什么 | 上游 | 许可证 | 本项目怎么用 |
|---|---|---|---|---|
| `OpenCLI-Symphony/` | 浏览器自动化框架＋TikTok Symphony 适配器（含本地补丁 `clis/tiktok-symphony/account.js`） | github.com/jackwener/opencli（npm `@jackwener/opencli`） | Apache-2.0（LICENSE 已随源码保留） | `工具/symphony` 代理以子进程调用 `dist/src/main.js`，仅首次登录/新图入库/浏览器兜底时用 |
| `chatgpt2api/` | 普通资产生图后端（账号池代理） | github.com/basketikun/chatgpt2api | MIT | 常驻服务监听 `127.0.0.1:8000`，`工具/gen_grids_multi.py` 走 HTTP 调用 |
| `flow2api/` | 彩铅参考图专用 Banana 2 通道（含本地补丁 `extension/background.js`、`extension/manifest.json`） | github.com/TheSmallHanCat/flow2api | MIT | 常驻服务监听 `127.0.0.1:8001`，仅彩铅参考图派生用 |

三份 LICENSE 均已保留——收编第三方开源代码时保留其版权与许可声明是许可证的要求，也是它们出现在这里的唯一原因；与 `工具/symphony` 自研代码的归属无关。

## 不入 git 的部分（见本目录 .gitignore）

- **运行环境**：`node_modules/`、`.venv/`、`web/.next/` —— 新机器用 `安装.py` 重建
- **凭据与运行时数据**：`chatgpt2api/config.json`（账号池与密钥）、`chatgpt2api/data/`、`flow2api/config/setting.toml`（api_key 等）、`flow2api/data/`、浏览器会话目录 —— 这些只存在于本机磁盘，**任何情况下不得提交**；新机器需要自行配置（chatgpt2api 参考其 `.env.example` 与 README 重建 config.json 账号池；flow2api 参考 `config/setting_example.toml`）

## 新机器安装

```
python 外部依赖/安装.py        # 依次：npm ci（OpenCLI）→ uv sync（chatgpt2api）→ uv venv+pip（flow2api）
```

前置：Node.js（含 npm）、[uv](https://docs.astral.sh/uv/)。装完再补上面说的凭据文件即可。

## 日常启动

- chatgpt2api：双击 `启动chatgpt2api.cmd`（端口 8000）；flow2api：双击 `启动flow2api.cmd`（端口 8001）
- **默认隐藏窗口后台跑（2026-08-02 裁定）**：双击后屏幕上不出现任何窗口，日志落各自 `logs/server.out.log`／`server.err.log`；要看实时输出就直接跑各目录 `.venv\Scripts\python.exe main.py`
- OpenCLI 无需常驻，由 `工具/symphony` 代理按需拉起（入口路径在 `工具/symphony/symphony配置.json`；代理本身的 `启动代理.cmd` 同样默认隐藏，前台调试用 `启动代理-前台.cmd`）

## 与上游同步

三份拷贝均不含 `.git`（chatgpt2api 原件本就无 git；flow2api 原件是浅克隆且带未提交本地补丁）。如需追上游新版本：手动 diff 上游仓库，**先确认本地补丁不被覆盖**（OpenCLI 的 account.js 两处补丁、flow2api 的 extension 改动），再选择性合入。
