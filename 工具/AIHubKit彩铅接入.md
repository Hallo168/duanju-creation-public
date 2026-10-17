# AIHubKit 彩铅接入

2026-10-11 用户指定：`https://apimodel.aihubkit.com`，模型 `gemini-3.1-flash-image-preview`。配置与密钥从 `工具/api配置.json` 的 `providers.aihubkit_gemini_image` 读取；彩铅默认路由取 `colored_pencil_defaults.provider`。

沿用资产规范：唯一参考图为同角色、同状态写实人物图；9:16、1K；提示词逐字为「不改变图片，把图片风格转换成素描彩铅画」。普通资产仍用内置生图；全局彩铅开关不因本次换通道而改变。

入口为 `python -X utf8 工具/gen_grids_multi.py <任务json> 0`。任务结构沿用既有格式，新任务写 `provider: "aihubkit_gemini_image"`。已提交的旧任务先恢复与对账，不因换通道重新提交。

适配器 `工具/aihubkit_image_client.py`：先以 Bearer 认证读取 `/v1/models`，再使用 Gemini 原生 `POST /v1beta/models/gemini-3.1-flash-image-preview:generateContent`；`contents.parts` 中单张原图用 `inlineData`，`generationConfig.imageConfig` 固定 `aspectRatio: "9:16"`、`imageSize: "1K"`，`responseModalities` 为 `TEXT` 与 `IMAGE`。响应从 `candidates.content.parts.inlineData` 取图，解码转存 PNG；不换模型、不自动重发。

请求、响应、原图哈希及状态留在 `工具/运行垃圾/tasks/aihubkit-images/<操作ID>/`，密钥不写入请求或账本。结果未知时先对账；已保存响应的解码与落盘失败只恢复同一响应，不再次调用模型；现有成图跳过。

本轮只读验证：`/v1/models` 返回 HTTP 200，指定模型存在，`supported_endpoint_types` 为 `gemini`、`openai`。本轮未提交生图，实际生成与画面质量尚未验证。
