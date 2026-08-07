# Google Flow 图生图接口

这条链路复用已经登录 Google Flow 的 OpenCLI 持久浏览器会话。它会把本地图片上传并附加为参考图，然后按提示词生成派生图片。

## 正式生成并保存原图

```powershell
$body = @{
  prompt = "保持主体与构图，派生成中国水墨工笔插画，宣纸纹理，高细节，不添加文字"
  project_id = "e890840e-c2ff-4705-a82f-f15d804e9872"
  reference_path = "F:\素材\参考图.png"
  wait = $true
  timeout = 600
  out = "F:\素材\派生结果.jpg"
} | ConvertTo-Json

Invoke-RestMethod `
  -Method Post `
  -Uri "http://127.0.0.1:8765/v1/images/generations" `
  -ContentType "application/json" `
  -Body $body
```

`reference_path` 支持 PNG、JPEG、WebP。也可以改传 `uploaded_reference`，值为 Flow 素材库中的准确文件名；两者必须且只能传一个。

## 不扣积分的预检

先把 `prepare_only` 设为 `true`，接口只上传参考图并填入提示词，不点击创建：

```json
{
  "prompt": "派生成水墨风格",
  "project_id": "e890840e-c2ff-4705-a82f-f15d804e9872",
  "reference_path": "F:\\素材\\参考图.png",
  "prepare_only": true,
  "wait": false
}
```

## 单独下载已有资产

```powershell
$body = @{
  project_id = "e890840e-c2ff-4705-a82f-f15d804e9872"
  out = "F:\素材\结果.jpg"
  timeout = 120
} | ConvertTo-Json

Invoke-RestMethod `
  -Method Post `
  -Uri "http://127.0.0.1:8765/v1/images/<assetId>/download" `
  -ContentType "application/json" `
  -Body $body
```

这是对 Flow 网页流程的自动化封装，不是 Google 公布并承诺稳定的 Flow API。Google 改版、登录失效或触发风控时，需要重新登录或更新适配器。
