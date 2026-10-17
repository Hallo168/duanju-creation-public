# QS 视频渠道

2026-10-10 用户指定新增 https://qsqs.org，模型 `LEO-WAN3-480P`。文档：https://qsqs.org/docs.html。

统一配置为 `工具/api配置.json` 的 `providers.qsqs_video`，执行器为 `工具/qsqs_video_batch.py`。按需使用，项目默认渠道保持现有配置。密钥存放在配置引用的本机私有凭据文件，可用 `QSQS_VIDEO_API_KEY` 覆盖。

- 预设：15–30 秒、480p、9:16，竖屏尺寸按渠道说明为 480×854。实时目录支持 10 图、5 视频、5 音频；项目图片挂载上限仍为 9 张。
- 费用：当前实时表达式 `u("seconds") * 0.07`，即 ¥0.07/秒，15 秒约 ¥1.05、30 秒约 ¥2.10；执行器每次提交前重新查询。
- 创建：`POST /v1/videos`，JSON 使用 `model`、`prompt`、`seconds`、`aspect_ratio`、`resolution`、`reference_image_urls`、`reference_video_urls`、`reference_audio_urls`、`generate_audio`。
- 参考图片按 `--reference` 顺序传入，本地文件使用现有 imgbb 托管并缓存，也可直接传公网 HTTPS 地址。视频和音频参考使用公网 HTTPS 地址。图片上传只在提交时执行。
- 查询：`GET /v1/videos/{task_id}`；下载：`GET /v1/videos/{task_id}/content`，跨域媒体下载不传 API 密钥。
- 提交前保存完整请求、摘要和 UUID 幂等键。状态不明时，显式 `submit --resume` 使用原请求和原 `Idempotency-Key` 恢复；已有任务号则直接返回原记录，后续使用 `poll`，不自动另建任务。

不产生视频费用的预检：

```text
python -X utf8 工具/qsqs_video_batch.py check --duration 30
python -X utf8 工具/video_provider_catalog.py --provider qsqs_video --json
```

已授权单元的提交与查询示例（替换路径与单元号）：

```text
python -X utf8 工具/qsqs_video_batch.py submit --unit 1 --duration 30 --prompt-file 提示词.txt --reference 人物.png --reference 场景.png --task-dir 工具/运行垃圾/tasks/集号-QS/U1
python -X utf8 工具/qsqs_video_batch.py poll --task-dir 工具/运行垃圾/tasks/集号-QS/U1 --output-dir stories/对应故事/分镜-30秒/成片 --watch
```

2026-10-10 接入验证：Key 的模型权限、公开能力表和实时计费查询通过，离线检查覆盖请求字段、中文正文、参考图顺序、提交超时恢复的幂等键与请求一致性。未提交真实视频；服务端生成、实际扣费、对白及画面效果尚未验证。完成下载后执行器核对尺寸、时长、音视频与完整解码；这些检查不代表创作验收。
