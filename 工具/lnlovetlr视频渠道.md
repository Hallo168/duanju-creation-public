# 小宁API视频渠道

登记日期：2026-10-09；用户指定文档：https://video.lnlovetlr.cloud/docs。

## 项目登记

本次模型预设为 `工具/api配置.json` 的 `providers.lnlovetlr_sd25720mx_video`：
- API根地址：https://api.lnlovetlr.cloud
- 模型：`SD2.5720MX`，大小写按原ID。
- 默认：30秒、720p、9:16。
- 执行器：`工具/lnlovetlr_video_batch.py`。
- 凭据从登记的 `credential_file` 运行时读取；配置正文、作业、日志不放明文密钥。
- 保留已有 `lnlovetlr_video` 预设和项目默认通道，本预设仅用户点名时调用。

2026-10-09实时公开模型目录与本次Key权限交集均包含 SD2.5720MX。当前目录标价7元/次，实际扣费或退款以账户账单为准。

## 当前模型协议

`POST /v1/videos`，Bearer认证，UTF-8 JSON：
`model`、`prompt`、`duration:30`、`resolution:"720p"`、`ratio:"9:16"`；
图片按上传顺序放 `images`，未用的 `video_urls` 和 `audio_urls` 为空。

当前目录限30图、10视频、10音频、合计50个素材，提示词5000 UTF-16字符；项目单元仍按最多9张图片。模型固定30秒，首尾帧字段未开放。

参考图片通过 `POST https://video.lnlovetlr.cloud/api/workbench/reference` 上传：请求体为原图片二进制，Content-Type为实际image类型，X-Reference-Kind为image，Bearer认证。返回HTTPS URL后按原顺序发送；本地路径不能直接放images。上传回执与图片SHA-256按批次保存。

查询：`GET /v1/videos/{task_id}`。完成后按 `GET /v1/tasks/{task_id}/artifacts` 返回的成品列表下载；仅向API原域发送Bearer，跨域下载不转发密钥。状态、下载或创建超时均保留原请求和任务，不盲重发生成。

## 作业与执行

作业JSON包含 provider、model、unit、duration、resolution、ratio、prompt_file、references（按顺序的本地文件）、task_dir、output_dir。所有中文内容经UTF-8文件传递。

```
python -X utf8 工具/lnlovetlr_video_batch.py check --job 作业.json
python -X utf8 工具/lnlovetlr_video_batch.py submit --job 作业.json
python -X utf8 工具/lnlovetlr_video_batch.py poll --job 作业.json
```

每个task_dir只创建一次；已有账本时submit返回已有状态。poll只恢复原任务，成功视频保存一份并核对编码、尺寸、时长与完整解码。人物参考图选用服从本批用户授权，写实与彩铅不自动互换。

本次第138集《擦干净了就带你回家》单元11的记录在：
`工具/运行垃圾/tasks/138-擦干净了就带你回家-unit11-lnlovetlr-20261009-v1/`。
使用两张同状态写实人物设定图、S1场景、P2铁皮盒，共4图，原提示词未改。本次已受理仅证明提交链路，视频与真人参考效果尚待生成验证。
