# ZM 视频渠道

用户2026-10-09指定保存的渠道名称为 **ZM**，网站为 https://model.aihubkit.com/pricing。网站当前公开状态接口确认 API 根地址为 https://apimodel.aihubkit.com；官方文档为 https://86bdknokqu.apifox.cn。

统一配置登记 `providers.zm_video`，专用执行器 `工具/zm_video_batch.py`；仅用户点名时使用，项目全局默认视频渠道保持原配置。凭据只保存在本机私有运行目录，以KEY字段读取，不写进提示词、账本或公开文档。

- 模型：`seedance-2.5_k1`。当前Key模型列表已确认有权限；时长、分辨率、画幅和素材能力按实时目录与官方文档核对。
- 创建：`POST /v1/videos`，JSON含model、prompt、duration、resolution、ratio、referenceImages、referenceAudios、referenceVideos。图片为公网可读HTTPS地址并保持顺序。
- 查询：`GET /v1/videos/{task_id}`；成品：`GET /v1/videos/{task_id}/content`。成品可能307到外部OSS；仅向本API域发授权头，外部媒体URL不转发Key。
- 参考图：官方临时文件上传需要另一个用户访问令牌，当前生成Key对此接口无权限。本项目现有素材托管服务提供原PNG可读地址，上传原文件不改脸、不转换为彩铅；生成请求只发ZM。
- 费用：当前720p、无参考视频的实时计费表达式为`usd(0.69 * seconds * units)`，与模型说明文字的宣传价不同。请求前记录实时表达式、Key余额和估价，实际扣费以平台账单为准。
- 当前本集补镜：8秒、720p、9:16，仅老韩写实人物设定图+S1公司门口场景图；未修改其他17单元。
- 一次创建，成功后保留任务号；超时、5xx、无任务号记unknown_outcome，不自动重发。完成后下载原任务并核对720×1280、时长、H.264/AAC与完整解码。画面与对白实际效果另行验收。

2026-10-09首次实际调用：8秒补镜已取得任务号，输入为写实人物参考图，随后平台报`service_quota_unavailable`（服务额度暂时不可用，请联系管理员处理）。Key权限与余额预检通过不代表服务端生成额度可用；本次未生成可下载视频，真实人脸生成效果尚未验证。

后续用户指定先试`wan3.0-video-prime`、失败再用`sd-2.5-z`：WAN同样8秒、写实人脸2张参考的调用已生成并下载，实际720×1280、H.264/AAC、约8.034秒，完整解码通过。备用sd-2.5-z在当次Key模型列表中无权限，未调用。执行器按本次模型的实时计费表达式计算预算，不套用seedance-2.5_k1价格；此单次试用不改变默认模型。
