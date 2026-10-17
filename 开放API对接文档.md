# 视频生成开放 API 对接文档

> 本文档面向第三方开发者,介绍如何通过 API Key 调用视频生成服务:提交任务 → 异步出片 → 轮询或 Webhook 获取结果。

---

## 1. 概述

- **接入地址(Base URL)**:`{BASE_URL}`(由我们提供,例如 `https://api.your-domain.com`)
- **接口前缀**:所有接口都在 `/openapi/v1` 下
- **鉴权方式**:API Key(Bearer Token),由我们为你签发
- **计费方式**:按成功出片的条数扣除积分,任务失败自动退还
- **交互模型**:创建任务是**异步**的 —— 创建后立即返回任务 `id`(HTTP 202),随后你可以**轮询**任务状态,或配置 **Webhook** 在出片后收到回调

一次典型对接的调用顺序:

```
GET  /openapi/v1/video-generations/models      查询可用模型
POST /openapi/v1/video-generations/estimate    (可选)预估费用
POST /openapi/v1/video-generations/references   (可选)上传参考图/音频
POST /openapi/v1/video-generations              创建任务 → 拿到 id
GET  /openapi/v1/video-generations/{id}         轮询直到 succeeded/failed
                                                 (或改用 Webhook 被动接收)
```

---

## 2. 鉴权

每个请求都必须带上 API Key。支持以下任意一种请求头(三选一):

```
Authorization: Bearer <API_KEY>
x-api-key: <API_KEY>
x-goog-api-key: <API_KEY>
```

- 缺少 Key → `HTTP 401`
- Key 无效或已被吊销 → `HTTP 401`
- 请妥善保管 Key,泄露后请联系我们吊销并重新签发

---

## 3. 通用响应格式

所有成功响应都是统一信封,`code = 0` 表示成功:

```json
{
  "code": 0,
  "message": "success",
  "data": { }
}
```

判断成功的标准:**HTTP 状态码为 2xx 且 `code == 0`**。失败时 `code` 为非 0,`message` 为可读的错误说明。

常见 HTTP 状态:

| 状态码 | 含义 |
|---|---|
| 200 | 请求成功 |
| 202 | 创建任务已受理(异步处理中) |
| 400 | 参数错误(详见 `message`) |
| 401 | 未鉴权 / Key 无效 |
| 429 | 触发限流,响应头带 `Retry-After`(秒),稍后重试 |
| 5xx | 服务端异常 |

---

## 4. 接口详解

### 4.1 查询可用模型

发现当前你有权使用的模型、分辨率与单价。

```
GET /openapi/v1/video-generations/models
```

响应 `data` 为数组:

```json
{
  "code": 0,
  "message": "success",
  "data": [
    {
      "model": "seedance-2.0",
      "resolution": "720p",
      "type": "image_to_video",
      "credit_cost": 2.5,
      "producible": 4530
    }
  ]
}
```

| 字段 | 说明 |
|---|---|
| `model` | 模型标识,创建任务时传入 |
| `resolution` | 分辨率,如 `480p` / `720p` / `1080p` |
| `type` | `text_to_video`(文生视频)/ `image_to_video`(图生视频) |
| `credit_cost` | 每条产出消耗的积分 |
| `producible` | 当前可生产的大致条数(参考值) |

> 也可用等价接口 `GET /openapi/v1/video-generations/capabilities`,返回 `{"models": [...]}`。
> 请以本接口返回的 `model` + `resolution` 组合为准来提交任务。
> 提交时 `model` 不区分大小写、连字符与空格,例如 `seedance-2.0`、`seedance2.0`、`Seedance 2.0` 视为同一模型。

---

### 4.2 预估费用

不创建任务,仅返回预计消耗的积分。

```
POST /openapi/v1/video-generations/estimate
Content-Type: application/json
```

请求体与创建任务相同(见 4.4)。响应:

```json
{
  "code": 0,
  "message": "success",
  "data": {
    "model": "seedance-2.0",
    "resolution": "720p",
    "ratio": "16:9",
    "estimated_cost": 2.5
  }
}
```

---

### 4.3 上传参考素材

图生视频、参考音频所需的素材,**必须先通过本接口上传**,拿到 `asset://` 句柄后再在创建任务时引用。**v1 不支持直接传外链 URL。**

```
POST /openapi/v1/video-generations/references
Content-Type: multipart/form-data
```

表单字段:

| 字段 | 必填 | 说明 |
|---|---|---|
| `file` | 是 | 素材文件 |
| `kind` | 否 | 素材用途提示(如 `image` / `audio`) |

限制:单文件 ≤ **20MB**;支持类型:图片 `jpeg/png/webp/gif`、音频 `mp3/wav/mp4(m4a)`、视频 `mp4`。

响应:

```json
{
  "code": 0,
  "message": "success",
  "data": {
    "id": 12345,
    "url": "asset://12345",
    "type": "image_url",
    "role": "reference_image",
    "mime_type": "image/png",
    "size_bytes": 204800
  }
}
```

拿到的 `url`(形如 `asset://12345`)用于 4.4 的 `content` 中。

---

### 4.4 创建生成任务

```
POST /openapi/v1/video-generations
Content-Type: application/json
```

**幂等**:建议带上 `Idempotency-Key` 或 `X-Request-ID` 请求头。相同幂等键的重复请求(如网络重试)只会创建一个任务,不会重复扣费。

请求体字段:

| 字段 | 类型 | 必填 | 说明 |
|---|---|---|---|
| `model` | string | 是 | 模型标识,取自 `/models` |
| `prompt` | string | 否 | 顶层提示词(也可放进 `content` 的 text 片段) |
| `resolution` | string | 否 | 分辨率,默认 `720p` |
| `ratio` | string | 否 | 画幅比 `16:9`/`9:16`/`1:1`/`4:3`/`3:4`,默认 `16:9` |
| `duration` | integer | 否 | 时长(秒) |
| `generate_audio` | boolean | 否 | 是否生成 AI 配音,默认 `true`(部分模型可能不支持关闭) |
| `content` | array | 否 | 富输入:文本 + 参考素材片段(见第 5 节) |
| `webhook_url` | string | 否 | 本次任务的回调地址,覆盖 Key 的默认回调 |

**文生视频 vs 图生视频由输入自动判定**:`content` 中**不含图片素材** → 文生视频;**含至少一张图片素材** → 图生视频。请确保对应的 `model + resolution + 类型`组合在 `/models` 中可用,否则返回"模型不可用或无权限"。

> `resolution` / `ratio` / `duration` 的可选值随模型而定(例如某些模型仅支持 `16:9`/`9:16`、时长仅 `5`/`10`/`15` 秒);传入模型不支持的组合可能导致生成失败。建议先用 `/models` 确认,拿不准的值先小量试跑。

最简请求(文生视频):

```json
{
  "model": "seedance-2.0",
  "prompt": "夕阳下的海边,海浪缓缓拍打礁石,电影质感",
  "resolution": "720p",
  "ratio": "16:9",
  "duration": 5
}
```

图生视频(先上传首帧图拿到 `asset://`):

```json
{
  "model": "seedance-2.0",
  "resolution": "720p",
  "content": [
    { "type": "text", "text": "镜头缓缓推进,人物微笑" },
    { "type": "image_url", "role": "first_frame", "image_url": { "url": "asset://12345" } }
  ]
}
```

响应(HTTP **202**):

```json
{
  "code": 0,
  "message": "accepted",
  "data": {
    "id": 90001,
    "request_id": "your-idempotency-key",
    "status": "queued",
    "model": "seedance-2.0",
    "resolution": "720p",
    "ratio": "16:9",
    "duration": 5,
    "progress": 0,
    "estimated_cost": 2.5,
    "billing_status": "pending",
    "created_at": "2026-07-20 10:30:00"
  }
}
```

记下 `data.id`,用于后续轮询 / 匹配 Webhook。

---

### 4.5 查询任务

```
GET /openapi/v1/video-generations/{id}
```

响应 `data` 为任务对象(字段见第 8 节)。出片成功时:

```json
{
  "code": 0,
  "message": "success",
  "data": {
    "id": 90001,
    "status": "succeeded",
    "progress": 100,
    "model": "seedance-2.0",
    "resolution": "720p",
    "estimated_cost": 2.5,
    "final_cost": 2.5,
    "billing_status": "charged",
    "result": {
      "video_url": "https://.../xxxx.mp4",
      "thumbnail_url": "https://.../xxxx.jpg"
    },
    "created_at": "2026-07-20 10:30:00"
  }
}
```

> `POST /openapi/v1/video-generations/{id}/refresh` 与本接口等价,直接返回当前状态(我们已自动跟踪出片进度,无需你催)。

**轮询建议**:间隔 5~10 秒查询一次,直到 `status` 变为 `succeeded` / `failed` / `canceled`。如已配置 Webhook,可不轮询。

---

### 4.6 任务列表

```
GET /openapi/v1/video-generations?page=1&page_size=20&status=succeeded
```

| 查询参数 | 默认 | 说明 |
|---|---|---|
| `page` | 1 | 页码 |
| `page_size` | 20 | 每页条数(上限 100) |
| `status` | 空 | 可选,按状态过滤:`queued`/`running`/`succeeded`/`failed`/`canceled` |

响应 `data` 为任务对象数组,按创建时间倒序。

---

### 4.7 取消任务

```
POST /openapi/v1/video-generations/{id}/cancel
```

**仅当任务仍在排队(`queued`)时可取消**;已开始生成的任务不可取消。响应返回该任务的最新状态。

---

## 5. content[] 富输入格式

`content` 是一个片段数组,每个片段是文本或一个参考素材。支持两种写法(嵌套对象或简写):

**文本片段:**
```json
{ "type": "text", "text": "提示词内容" }
```

**图片片段**(两种等价写法):
```json
{ "type": "image_url", "role": "first_frame", "image_url": { "url": "asset://12345" } }
{ "type": "image_url", "role": "first_frame", "url": "asset://12345" }
```

**音频片段:**
```json
{ "type": "audio_url", "role": "reference_audio", "audio_url": { "url": "asset://67890" } }
```

`role` 取值:

| role | 用途 |
|---|---|
| `first_frame` | 首帧参考图 |
| `last_frame` | 尾帧参考图 |
| `reference_image` | 参考图 |
| `reference_audio` | 参考音频 |

> 素材 `url` 必须是 `/references` 上传返回的 `asset://<id>` 句柄。**v1 暂不支持参考视频(`reference_video`)与外链 URL。**

---

## 6. 任务状态与计费状态

**`status`(任务状态):**

| 值 | 含义 |
|---|---|
| `queued` | 已受理,排队中 |
| `running` | 生成中 |
| `succeeded` | 出片成功,`result` 可用 |
| `failed` | 失败(已自动退款) |
| `canceled` | 已取消 |

**`billing_status`(计费状态):**

| 值 | 含义 |
|---|---|
| `pending` | 已冻结积分,等待结算 |
| `charged` | 已扣费(出片成功) |
| `refunded` | 已退款(失败/取消) |

`progress` 为粗略进度:`queued=0`、`running=50`、`succeeded=100`。

---

## 7. Webhook 回调

配置回调后,任务进入**终态**(成功/失败/取消)时,我们会向你的地址 POST 一条通知,省去轮询。

**配置方式**(二选一):
- 创建任务时传 `webhook_url`(仅对该任务生效)
- 或联系我们为你的 Key 配置默认回调地址

**要求**:回调地址必须是**公网可达**的 `http(s)` 地址(内网、回环、云元数据地址会被拒绝)。你的接口收到后应尽快返回 `2xx`。

**请求头:**

| 头 | 说明 |
|---|---|
| `X-Guagu-Event` | 事件名:`video_generation.succeeded` / `.failed` / `.canceled` |
| `X-Guagu-Delivery` | 本次投递的唯一 ID(可用于去重) |
| `X-Guagu-Signature` | 签名,格式 `sha256=<hex>`,用于验签(见下) |

**请求体:**

```json
{
  "event": "video_generation.succeeded",
  "data": {
    "id": 90001,
    "status": "succeeded",
    "progress": 100,
    "result": { "video_url": "https://.../xxxx.mp4", "thumbnail_url": "https://.../xxxx.jpg" },
    "final_cost": 2.5,
    "billing_status": "charged",
    "created_at": "2026-07-20 10:30:00"
  }
}
```

`data` 与查询接口返回的任务对象结构一致。

**签名验证**(强烈建议,防伪造):

签名密钥 = 你的明文 API Key 的 SHA-256 十六进制串;签名 = 用该密钥对**原始请求体**做 HMAC-SHA256。伪代码:

```
signing_secret = hex(sha256(你的明文APIKey))
expected       = "sha256=" + hex(hmac_sha256(key=signing_secret, msg=原始请求体字节))
比对 expected 与请求头 X-Guagu-Signature(建议用常数时间比较)
```

Python 示例:

```python
import hashlib, hmac

def verify(raw_body: bytes, header_sig: str, api_key: str) -> bool:
    signing_secret = hashlib.sha256(api_key.encode()).hexdigest()
    expected = "sha256=" + hmac.new(
        signing_secret.encode(), raw_body, hashlib.sha256).hexdigest()
    return hmac.compare_digest(expected, header_sig or "")
```

Node.js 示例:

```js
const crypto = require('crypto');

function verify(rawBody, headerSig, apiKey) {
  const secret = crypto.createHash('sha256').update(apiKey).digest('hex');
  const expected = 'sha256=' + crypto.createHmac('sha256', secret).update(rawBody).digest('hex');
  const a = Buffer.from(expected), b = Buffer.from(headerSig || '');
  return a.length === b.length && crypto.timingSafeEqual(a, b);
}
```

**重试**:投递失败(非 2xx / 超时)会按指数退避重试,最多 8 次(30s、60s、120s…,上限 6 小时)。请对同一 `X-Guagu-Delivery` 做幂等处理。

---

## 8. 任务对象字段速查

| 字段 | 类型 | 说明 |
|---|---|---|
| `id` | number | 任务 ID |
| `request_id` | string | 你提交的幂等键(如有) |
| `status` | string | 见第 6 节 |
| `model` | string | 模型标识 |
| `resolution` | string | 分辨率 |
| `ratio` | string | 画幅比 |
| `duration` | number | 时长(秒) |
| `progress` | number | 粗略进度 0/50/100 |
| `estimated_cost` | number | 预估积分 |
| `final_cost` | number | 实际扣费(成功=单价,失败/取消=0) |
| `billing_status` | string | 见第 6 节 |
| `result.video_url` | string | 成品视频地址(成功时) |
| `result.thumbnail_url` | string | 封面图地址(成功时) |
| `fail_reason` | string | 失败原因(失败时) |
| `created_at` | string | 创建时间(北京时间 `yyyy-MM-dd HH:mm:ss`) |

> 为 `null` 的字段会被省略,不出现在响应中。

---

## 9. 端到端示例(curl)

```bash
BASE="{BASE_URL}"
KEY="<API_KEY>"

# 1) 查询可用模型
curl -s "$BASE/openapi/v1/video-generations/models" \
  -H "Authorization: Bearer $KEY"

# 2) 创建文生视频任务(带幂等键)
curl -s -X POST "$BASE/openapi/v1/video-generations" \
  -H "Authorization: Bearer $KEY" \
  -H "Content-Type: application/json" \
  -H "Idempotency-Key: my-unique-key-0001" \
  -d '{
        "model": "seedance-2.0",
        "prompt": "夕阳下的海边,海浪拍打礁石,电影质感",
        "resolution": "720p",
        "ratio": "16:9",
        "duration": 5
      }'
# → 返回 { "code":0, "data":{ "id":90001, "status":"queued", ... } }

# 3) 轮询任务(每 5~10s 一次,直到终态)
curl -s "$BASE/openapi/v1/video-generations/90001" \
  -H "Authorization: Bearer $KEY"
# → status=succeeded 时,data.result.video_url 即成品
```

图生视频:先 `POST /references` 上传首帧图拿到 `asset://<id>`,再把它放进创建请求的 `content` 里(见 4.4 / 第 5 节)。

---

## 10. 常见问题

- **提示"模型不可用或无权限"**:`model + resolution + 类型`组合不在你的可用范围。请以 `/models` 返回的组合为准;图生视频需在 `content` 里带图片素材。
- **参考素材报错**:素材必须先经 `/references` 上传,再用返回的 `asset://` 句柄引用,不能直接传外链。
- **收到 429**:触发限流,按响应头 `Retry-After` 秒数等待后重试。
- **任务一直 `queued`**:产能繁忙时会排队,配置 Webhook 可在出片后第一时间收到通知。
- **重复提交**:务必使用 `Idempotency-Key`,避免网络重试造成重复扣费。
