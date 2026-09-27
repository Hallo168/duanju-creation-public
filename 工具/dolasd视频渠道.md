# Dolasd 视频渠道

用户于 2026-09-24 提供 `https://dolasd.xyz/v1/videos/generations` 的请求示例，指定新增渠道。统一登记在 `工具/api配置.json` 的 `providers.dolasd_video`；执行器为 `工具/dolasd_video_batch.py`。项目默认渠道仍由 `video_defaults` 决定，此渠道仅在用户点名时使用。

- 凭据：运行时读取 `DOLASD_API_KEY`，否则读取本机 `<本机路径>` 的 `KEY:` 行。配置、请求文件、账本和文档不保存密钥。
- 已核实的协议：`GET /v1/models`、`GET /v1/key/info`、`POST /v1/videos/generations`、`GET /v1/videos/{task_id}`、`GET /v1/videos/{task_id}/content`。Bearer 认证；提交可用 `multipart/form-data`，文字字段 `model`、`prompt`、`duration`、`size`，图片按顺序重复使用文件字段 `file`。站点文档也提供 JSON `reference_images`（公网 URL 或 data URL）方式；本执行器使用本地文件直传。
- 2026-09-24 只读核验：密钥启用，实时模型表含 `seedance-2.5`；模型列出 30 秒和 `720x1280`。接口没有公开报价端点，本渠道预检显示 `price: unknown`，不能把其他渠道价格套用过来。
- 执行器要求每次提交至少 1 张参考图，纯文字请求直接拒绝。服务端文档允许最多 30 张，每张不超过 15 MB，格式 JPEG/PNG/WEBP；本项目每单元最多挂载 9 张，执行器按这个上限校验并保持传图顺序。参考图生成效果、声音能力及成片效果尚未实测。
- `submit` 每个任务目录只允许一次创建请求；网络超时或服务端异常记为 `unknown_outcome`，先查原任务与计费记录，不自动重投。`poll` 查询原任务，完成后下载到以任务 ID 命名的独立文件，并检查尺寸、时长和完整解码。技术检查不代表画面验收。

只读检查：

```powershell
python 工具/dolasd_video_batch.py check
```

得到本批生成授权并核对费用后，把提示词保存为 UTF-8 文本，再提交并查询：

```powershell
python 工具/dolasd_video_batch.py submit --prompt-file "<提示词文件>" --reference "<图1>" --reference "<图2>" --task-dir "<本批独立任务目录>"
python 工具/dolasd_video_batch.py poll --task-dir "<同一任务目录>" --output-dir "<成片目录>" --watch
```

`submit` 不会自动轮询或重试；`poll` 不会创建新视频。输出文件名包含服务端任务 ID，已有文件不会覆盖。
