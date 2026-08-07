# ChatGPT2API 使用指南

面向日常使用，重点讲生图。部署细节见 [deployment.md](./deployment.md)。

---

## 1. 启动

需要**两个**服务：后端提供 API，前端提供面板。

### 后端

```bash
uv sync                    # 首次或依赖变更时执行
uv run main.py             # 默认监听 127.0.0.1:8000
```

**指定地址 / 端口**用环境变量 `CHATGPT2API_HOST`、`CHATGPT2API_PORT`。`uv run main.py` 启动时会做**单实例检查**：目标端口已被占用就**直接拒绝启动并退出**，从根上杜绝"同时跑两个后端、号池内存态对不上"：

```bash
# bash
CHATGPT2API_HOST=0.0.0.0 CHATGPT2API_PORT=8000 uv run main.py
# Windows PowerShell
$env:CHATGPT2API_HOST="0.0.0.0"; $env:CHATGPT2API_PORT="8000"; uv run main.py
```

> 直接用 `uvicorn main:app --host 0.0.0.0 --port 8000` 也能跑，但**不会**经过单实例检查。想要"只绑定一个后端"的保护，请用上面的 `uv run main.py`（或 `python main.py`）方式启动。

### 前端

```bash
cd web
npm install                # 或 bun install
npm run dev                # 监听 3000
```

打开 **http://localhost:3000/**（必须用 `http://`，直接输 `localhost:3000` 会被浏览器强跳 https 打不开）。

登录密钥 = `config.json` 里的 `auth-key`（默认 `chatgpt2api`）。**服务暴露到公网前务必改掉**，否则任何人都能用你的号池。

> Docker 部署不需要单独起前端，前端会被构建进镜像，面板和 API 同在 3000 端口。

### 先决条件：号池不能为空

面板 → 账号页导入 ChatGPT 账号，否则 API 能应答但出不了图。最省事的是 **access_token 导入**：一行一个 token 贴进去即可，无格式要求，导入后自动刷新出邮箱、订阅类型和额度。

---

## 2. 面板生图

打开 **http://localhost:3000/image/**

选模型 → 输入提示词 → 生成。支持文生图、图生图、多图组图编辑，可上传参考图。号池自动轮询账号，无需关心用的是哪个号。生成历史存在本地，可回看和删除。

---

## 3. API 生图

所有接口都需要请求头 `Authorization: Bearer <auth-key>`。

### 3.1 查看可用模型

```bash
curl http://127.0.0.1:8000/v1/models -H "Authorization: Bearer chatgpt2api"
```

**模型列表是从上游动态拉取的，以实际返回为准，不要照抄 README。** 生图统一用 `gpt-image-2`。

`codex-gpt-image-2` 只在满足**全部**两个条件时才会出现：账号 `source_type` 为 `codex`（只有走 CPA 号池导入才会打上此标记），且订阅类型为 Plus / Team / Pro。free 账号无法使用。

### 3.2 文生图

```bash
curl http://127.0.0.1:8000/v1/images/generations \
  -H "Content-Type: application/json" \
  -H "Authorization: Bearer chatgpt2api" \
  -d '{
    "model": "gpt-image-2",
    "prompt": "一只漂浮在太空里的猫",
    "n": 1
  }'
```

| 字段 | 说明 |
|:--|:--|
| `prompt` | 必填 |
| `model` | 默认 `gpt-image-2` |
| `n` | 生成数量，**只能是 1–4**，超范围报 400 |
| `size` | 可选 |
| `quality` | 默认 `auto` |
| `response_format` | 默认 `b64_json` |

---

## 4. 图生图与多参考图

统一走 `POST /v1/images/edits`。**多参考图就是传多张图**——接口对张数没有硬限制，传几张就用几张。

### 4.1 multipart 上传本地图（最常用）

**重复 `image` 字段即可传多张**，顺序即传入顺序：

```bash
curl http://127.0.0.1:8000/v1/images/edits \
  -H "Authorization: Bearer chatgpt2api" \
  -F "model=gpt-image-2" \
  -F "prompt=把第一张的人物放进第二张的场景里，保持第三张的色调" \
  -F "n=1" \
  -F "image=@./person.png" \
  -F "image=@./scene.png" \
  -F "image=@./palette.png"
```

以下字段**等价**，都会被收集进同一个参考图列表，可混用：

```
image   image[]   images   images[]   image_url   image_url[]
```

### 4.2 JSON 传图片链接

```bash
curl http://127.0.0.1:8000/v1/images/edits \
  -H "Content-Type: application/json" \
  -H "Authorization: Bearer chatgpt2api" \
  -d '{
    "model": "gpt-image-2",
    "prompt": "把这几张合成一张海报",
    "images": [
      {"image_url": "https://example.com/a.png"},
      {"image_url": "https://example.com/b.png"},
      "https://example.com/c.png"
    ]
  }'
```

`images` 数组里每一项可以是：

| 写法 | 示例 |
|:--|:--|
| 对象 + `image_url` | `{"image_url": "https://..."}` |
| 对象 + `url` | `{"url": "https://..."}` |
| 嵌套对象（OpenAI 风格） | `{"image_url": {"url": "https://..."}}` |
| 裸字符串 URL | `"https://..."` |
| data URL | `"data:image/png;base64,iVBOR..."` |
| 裸 base64 字符串 | `"iVBORw0KGgo..."` |
| 对象 + base64 | `{"b64_json": "iVBOR...", "filename": "a.png", "mime_type": "image/png"}` |

数组可嵌套，会被自动展平。**`file_id` 不支持**，传了直接报 400，请改用 `image_url`。

### 4.3 混合本地文件和链接

multipart 模式下 `image_url` 字段可以和 `image` 文件字段同时出现：

```bash
curl http://127.0.0.1:8000/v1/images/edits \
  -H "Authorization: Bearer chatgpt2api" \
  -F "model=gpt-image-2" \
  -F "prompt=把本地这张人物换成链接里的背景" \
  -F "image=@./person.png" \
  -F "image_url=https://example.com/bg.png"
```

表单里的 `images` 字段还可以直接写成 JSON 字符串：

```bash
-F 'images=[{"image_url":"https://example.com/a.png"},{"image_url":"https://example.com/b.png"}]'
```

### 4.4 局部重绘（mask）

用 `mask` / `mask[]` 字段传遮罩。**mask 的透明区域（低 alpha）= 要改的地方，不透明区域 = 保留**。mask 会按 alpha 通道合成进原图，并自动缩放到原图尺寸。

```bash
curl http://127.0.0.1:8000/v1/images/edits \
  -H "Authorization: Bearer chatgpt2api" \
  -F "model=gpt-image-2" \
  -F "prompt=把天空换成星空" \
  -F "image=@./photo.png" \
  -F "mask=@./mask.png"
```

多图时 mask 按下标与图片一一对应；mask 数量少于图片时，多出来的图片复用**最后一张** mask。

### 4.5 限制

- **单张图片上限 50MB**，无论上传还是 URL 下载，超出报 400。
- URL 必须是 `http` / `https`，且返回的必须是图片类型。
- 远程图片下载超时 60 秒，走号池代理设置。
- `n` 同样限制 1–4。

---

## 5. 接入第三方客户端

Cherry Studio、New API 等填：

- **API 地址**：`http://127.0.0.1:8000/v1`
- **密钥**：`config.json` 里的 `auth-key`

---

## 6. 常见问题 / 排障速查

先按症状定位，再看对应小节。日志文件在 `data/logs.jsonl`，可直接搜关键词。

| 症状 | 最可能原因 | 处理 | 日志关键词 / 自检命令 |
|:--|:--|:--|:--|
| 打开 `http://localhost:3000` 没反应、连接被拒 | 前端或后端进程没起来 | 启动缺的那个服务（§1） | `netstat -ano \| grep -E ":3000\|:8000"` |
| 面板能开但账号列表为空 / 找不到号池 | 内存态过期，或两个后端抢 8000 端口 | 关掉多余进程后重启后端 | `netstat -ano \| grep ":8000"` |
| 生图报 `no available image quota` / 号池中没有可用账号 | 账号全被限流(额度=0)，或被删/标异常到 0 | 等额度恢复、补号，或改自动删除策略 | 面板号池页看「可用账号」数 |
| 账号越用越少、悄悄没了 | `auto_remove_invalid_accounts=true` 自动删失效号 | 见「账号被自动清掉了」 | `自动移除异常账号`、`暂缓标记异常账号` |
| 生图一直失败 / 403 | 撞 Cloudflare | 启用 WARP（见下） | `403` |

---

### 打开网页没反应 / 服务连不上

`localhost:3000` 打不开、`curl` 直接 connection refused，几乎都是**服务没在跑**（不是页面卡住）。这个项目要**同时**起两个进程：前端 3000、后端 8000（开发模式下前端的接口会打到 `127.0.0.1:8000`）。先查端口：

```bash
netstat -ano | grep -E ":3000|:8000"
```

哪个没有 `LISTENING` 就按 §1 启动哪个。两个都在、仍打不开时，确认用的是 `http://` 而不是被浏览器强跳的 `https://`。

### 面板里账号列表突然空了，但没删过号

账号池**只在进程启动时从磁盘加载一次**，之后全程走内存（`account_service.list_accounts()` 读的是内存态）。如果磁盘上 `data/accounts.json` 有号但面板显示 0，说明当前进程的内存态过期了——**重启后端即可恢复**。

最常见的触发原因：**同时跑了两个后端且都绑在 8000 端口**。Windows 允许重复绑定，导入请求可能被路由到 A 进程（写了磁盘 + 更新了 A 的内存），而对外服务的是 B 进程（内存仍是空的）。**启动前先确认端口没被占用**：

```bash
netstat -ano | grep ":8000"
```

> 用 `uv run main.py` / `python main.py` 启动的新版后端已带**单实例检查**：端口被占时第二个后端会直接退出，不会再偷偷并绑造成这个问题（见 §1）。

### 生图一直失败或报 403

多半是撞上 Cloudflare。此时才需要启用 WARP / FlareSolverr：

```bash
cp .env.example .env
docker compose -f docker-compose.warp.yml up -d --build
```

默认 `config.json` 里 `proxy_runtime.enabled` 为 `false`，即未启用。

### 提交后很久没反应

正常现象。`image_poll_initial_wait_secs` 默认 10 秒（提交后先等 10 秒才开始轮询），`image_poll_timeout_secs` 默认 120 秒。首图较慢，超时后可继续等待。

### 账号被自动清掉了

`config.json` 里 `auto_remove_invalid_accounts` 默认为 `true`，配合每 5 分钟一轮的自动刷新（`refresh_account_interval_minute`），token 失效的账号**会被真正从磁盘删除**。想留后悔余地就改成 `false`，或定期在面板导出账号备份。

> 注意：导出要求账号同时具备 `access_token`、`refresh_token`、`id_token`。只用 access_token 导入的账号不满足此条件，也无法自动重登恢复——想要完整三件套请用面板的 OAuth 登录方式导入。

### 号池为空 / 生图报 no available image quota / 找不到号池

一个账号只有**同时**满足「状态正常 + 额度 > 0」才算可用。取号时一个都凑不齐，接口就报 `no available image quota`，面板显示「号池中没有可用账号或所有账号均被限流」。三类原因，处理各不同：

1. **账号都在，但全被限流**（`status=限流`、`quota=0`）——free 号每日额度很紧，用完得等 `restore_at`（UTC）重置。只能等，或多备几个号错峰用。
2. **账号被删空了**——失效号被自动删除（见上一节），越用越少直到为 0。
3. **磁盘有号但进程读不到**——内存态过期 / 两个后端抢 8000 端口（见「账号列表突然空了」），重启后端即可。进程每次启动都会从 `data/accounts.json` 重新加载，只要磁盘有号，重启后一定回来。

> **能不能彻底不再出现？** 要分开看：
> - **本工具自己造成的空池**（自动删号删到 0、双进程内存不同步）——**可以根治**：把 `auto_remove_invalid_accounts` 设为 `false`（失效号只标「异常」不再删，磁盘不会被清空），并保证只跑一个后端。
> - **上游事实造成的空池**（OpenAI 作废 free 号 token、free 每日额度耗尽）——**无法根除**，只能缓解：号池养大让额度错峰、改用 Plus/Team/Pro 且导入完整三件套（可自动重登恢复）、或接 CPA / Sub2API 号池按需补号。
>
> 一句话：想让「找不到号池」几乎不再发生 = **关掉自动删号 + 定期备份/补号 + 只跑单个后端**，三件一起做。

### 额度什么时候恢复

账号刷新日志里的 `restore_at` 就是重置时间（UTC）。free 账号生图额度按天恢复，且额度很紧。

---

## 7. 使用须知

本项目基于对 ChatGPT 官网接口的逆向实现，仅供个人学习与技术研究。存在账号被限制、临时封禁或永久封禁的风险，**请勿使用重要账号或高价值账号**。严禁商业用途、批量滥用及任何违反 OpenAI 服务条款或当地法律法规的行为。完整免责声明见 [README](../README.md)。
