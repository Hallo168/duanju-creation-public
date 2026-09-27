# Flow Kit 彩铅渠道接入

彩铅生产约定唯一出处为 `.claude/skills/zichan/知识/资产生产规范.md` §〇；本文件记录本机操作与接口恢复方式。

## 启动与项目

1. 双击 `<本机路径>`；API 为 `http://127.0.0.1:8100`，控制台为 `http://127.0.0.1:5173`。
2. Chrome 加载 `<本机路径>` 中的 Flow Kit 0.5.2，保持已登录、能看到生成框的目标项目页。只保留该 Flow 项目标签，避免选择到旧账号或 404 页面。
3. `<本机路径>` 的 `FLOW_PROJECT_ID` 是服务默认项目；改动后重启服务。预检读取 `/health` 和 `/api/flow/status`，不读取旧 ST/AT 或 Flow2API 配置。

当前绑定项目为 `82e1ffdd-33a6-47fa-a9fa-019e782acde1`；这是本机状态，不是固定生产规则。切换项目后相应更新批次的 `expected_flow_project_id`。

## 统一入口

入口仍是 `python 工具/gen_grids_multi.py <任务文件> [普通资产并发]`。新彩铅任务指定 `provider: "flowkit_banana2"`，`refs` 仅一张本集同状态写实原图；旧 `flow2api_banana2` 任务名作为新渠道兼容别名。生产开关仍由 `工具/生产开关.json` 控制，本次接入没有全局启用彩铅。

配置位于 `工具/api配置.json` 的 `providers.flowkit_banana2`。适配器是 `工具/flowkit_image_client.py`，明确使用 NARWHAL；不会使用 Flow Kit 默认的其它图片型号，也不会回退到 Flow2API。

API 顺序：`POST /api/flow/upload-image-file` 上传单张本地图 → `POST /api/flow/edit-image`，以返回的素材 ID 作为 `source_media_id`（BASE_IMAGE），固定提示词、9:16、count=1 → 下载基础分辨率图片 → ffmpeg 解码转存 PNG。不会调用 2K/4K 导出。

## 账本与恢复

- 每个任务的操作 ID 绑定任务文件、输出文件名和源图 SHA-256；账本在 `工具/运行垃圾/tasks/flowkit-images/<操作ID>/`。
- `operation.json` 记录项目、原图、请求参数、上传和生成素材 ID；上传响应、编辑请求与响应、下载原文件和 `result.png` 均独立留存。
- 生成已完成时复用结果；下载失败时从原 URL／素材 ID 恢复，不再次调用模型。
- 上传或生成提交中断、结果未知时，重复执行会停止，先检查账本和 Flow 项目，不换操作 ID 盲重投。明确重试须作为经确认的新尝试保留版本。
- 已存在的最终资产仍由统一入口跳过；本次不重写旧剧集任务、素材或挂载。

## 实测范围

2026-09-26，经当前 Flow Kit 后端 1.3.1、扩展 0.5.2，在上述项目内用独立茶杯样图完成一次 NARWHAL 图生图。固定彩铅提示词经 UTF-8 JSON 发送，输出 PNG 为 768×1376。

证据：`工具/运行垃圾/tasks/flowkit-pencil-switch-20260926/evidence/`，生成素材 ID `c7274dfc-61e9-4361-8338-0670252215ce`。人物身份、服装和布局效果仍由用户验收。旧 Flow2API 与 1.3.2 桥接历史见 `工具/Flow新版彩铅接入.md`。
