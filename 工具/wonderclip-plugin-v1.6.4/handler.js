/**
 * WonderClip 视频生成插件 —— 对接自建 WonderClip 后端（FastAPI，NewAPI 兼容）。
 *
 * 后端接口约定：
 *   - POST {base_url}/v1/videos     提交任务，返回 { task_id, status: "queued" }
 *          走 NewAPI openai-video(Sora) 协议：multipart/form-data，字段 prompt/model/seconds/size，
 *          扩展字段（resolution/aspect_ratio/参考素材）放在 metadata(JSON 字符串) 里透传。
 *   - GET  {base_url}/v1/videos/{id} 查询状态，返回 { task_id, status, progress, url, metadata }
 *           status 取值：queued / processing / completed / failed（NewAPI Sora 适配器认可的词表）
 *           url 优先为上游 OSS 签名直链（免 Header，可直接下载/播放），
 *           仅当无上游直链时才兜底为带 ?token= 的本地 /content 地址；
 *           metadata.original_url 为 OSS 直链。
 *   - GET  {base_url}/v1/videos/{id}/content  下载 mp4（支持 ?token= 或后台会话 Cookie）
 *
 * 鉴权：所有 /v1/* 接口都要求 Authorization: Bearer <api_key>，也支持 ?token=<api_key>。
 *       ctx.downloadToFile 不带 Header，用返回的 url（已带 ?token=）即可。
 *
 * 参考素材映射（后端 normalize_spec 实际识别的字段，务必对齐）：
 *   图片 → 同时下发 image(首张) + images(全部) + reference_images(全部)。
 *          后端会把所有图片统一转成「参考生视频 reference_to_video」（上游唯一可成功的图路径）。
 *          image/images 用于让 NewAPI 识别为图生/参考生并转发图片；reference_images 用于上游。
 *   视频/音频 → videos / audios（也是参考生视频 reference_to_video）
 *   画布传来的 referenceImages 可能是 [{url:"..."}] 或 ["..."]，统一提取纯 URL 字符串。
 */

console.log('[dadao 视频插件] v1.6.4 已加载')

const normalizeBaseUrl = (value) => String(value || '').trim().replace(/\/+$/, '')

const clampDuration = (d) => {
  const n = Number(d)
  if (!Number.isFinite(n)) return 5
  return Math.max(4, Math.min(30, Math.round(n)))
}

// NewAPI openai-video(Sora) 协议用 size 字段，格式为「宽x高」；画布只给 resolution/aspect_ratio，这里换算。
const sizeFromResolution = (res, ratio) => {
  const long = String(res || '720p').toLowerCase().startsWith('1080') ? '1080' : '720'
  const a = String(ratio || '16:9')
  const map = long === '1080'
    ? { '16:9': '1920x1080', '9:16': '1080x1920', '1:1': '1080x1080' }
    : { '16:9': '1280x720', '9:16': '720x1280', '1:1': '720x720' }
  return map[a] || map['16:9']
}

const sleep = (ms, signal) => new Promise((resolve, reject) => {
  const timer = setTimeout(resolve, ms)
  if (signal) signal.addEventListener('abort', () => { clearTimeout(timer); reject(new Error('已取消')) }, { once: true })
})

async function generateVideo(ctx) {
  const { input, config, onProgress, signal } = ctx
  const doFetch = ctx.fetch || fetch
  const baseUrl = normalizeBaseUrl(config.base_url)
  const apiKey = String(config.api_key || '').trim()
  const headers = { Authorization: `Bearer ${apiKey}` }

  // 直连 wonderclip 后端的兜底配置（绕开 new-api 吃 url / 重写 task_id）。
  // 公开版不提供后端令牌；需要直连时填写自己的 wonderclip_token。
  const DEFAULT_DB_BASE = 'https://apishipin.dadaowushilibai.cn'
  const DEFAULT_DB_TOKEN = ''
  const dbBaseUrl = normalizeBaseUrl(config.wonderclip_base_url) || DEFAULT_DB_BASE
  const dbToken = String(config.wonderclip_token || '').trim() || DEFAULT_DB_TOKEN

  // 画布侧生成的稳定任务标识：new-api 不认识不改写，用它回查真实 OSS 直链。
  const genId = () => (Date.now().toString(36) + '-' + Math.random().toString(36).slice(2, 10))
  let clientJobId = ''

  // 0) 断点续轮询：宿主重开后带回上次任务 id，跳过提交避免重复生成。
  let taskId = String(input.resumeTaskId || '').trim()
  if (!taskId) {
    onProgress(0, '提交视频任务')

    // 1) 提交任务（NewAPI openai-video/Sora 协议：multipart/form-data，字段为 prompt/model/seconds/size）。
    //    后端 normalize_spec 需要的扩展字段（resolution/aspect_ratio/参考素材）统一塞进 metadata 透传。
    const fd = new FormData()
    fd.append('model', input.model || 'wan3.0-video')
    fd.append('prompt', input.prompt)
    fd.append('seconds', String(clampDuration(input.duration)))
    fd.append('size', sizeFromResolution(input.resolution, input.aspectRatio))

    // 参考素材：单图 → image_to_video（图生视频），多图/视频/音频 → reference_to_video
    // 打印参考图原始对象，定位画布给的真实可加载 URL 字段（jiai.lizier.cc 不可达，需找 OSS 直链字段）。
    try { console.log('【参考图原始对象】', JSON.stringify(input.referenceImages)) } catch (e) { console.warn('【参考图原始对象打印失败】', e) }
    // 自动识别对象里「可抓取」的 URL：优先 OSS/对象存储直链，其次任意 https，避免取到 jiai 这种内部域名。
    const isHttp = (s) => typeof s === 'string' && /^https?:\/\//i.test(s)
    const isOss = (s) => /aliyuncs\.com|amazonaws\.com|oss-|ice-ai-saas|\/userupload\//i.test(String(s))
    const pickUrl = (r) => {
      if (typeof r === 'string') return r
      if (!r || typeof r !== 'object') return ''
      const keys = ['publish_url','publishUrl','presigned_url','presignedUrl','oss_url','ossUrl','media_url','mediaUrl','video_url','videoUrl','videoSourceUrl','source_url','sourceUrl','loadable_url','loadableUrl','raw_url','rawUrl','full_url','fullUrl','download_url','downloadUrl','file_url','fileUrl','object_url','objectUrl','asset_url','assetUrl','url','source','src','image','img','poster','thumbnail','preview','content']
      for (const k of keys) { const v = r[k]; if (typeof v === 'string' && isHttp(v) && isOss(v)) return v; if (v && typeof v === 'object' && isHttp(v.url) && isOss(v.url)) return v.url }
      for (const k of keys) { const v = r[k]; if (typeof v === 'string' && isHttp(v)) return v; if (v && typeof v === 'object' && isHttp(v.url)) return v.url }
      return ''
    }
    const extractUrl = pickUrl
    const refImages = (input.referenceImages || []).map(extractUrl).filter(Boolean)
    console.log('【参考图提取URL】', JSON.stringify(refImages))
    const refVideos = (input.referenceVideos || []).map(extractUrl).filter(Boolean)
    const refAudios = (input.referenceAudios || []).map(extractUrl).filter(Boolean)

    // 大体积参考素材（尤其画布给的 data:base64）直接塞进 metadata 会把 body 撑爆，
    // 触发 NewAPI 的「multipart 消息过大」。这里先经 wonderclip 后端转存成 OSS 短 URL，
    // metadata 里只放 https 短链，让画布→NewAPI 的 multipart 始终保持极小。
    const toOssUrl = async (u) => {
      if (/^https?:\/\//i.test(u)) return u
      if (!dbBaseUrl) return ''   // 未配直连地址时丢弃，避免大 base64 进 body
      try {
        const r = await doFetch(`${dbBaseUrl}/v1/references/upload`, {
          method: 'POST',
          headers: { 'Content-Type': 'application/json', ...(dbToken ? { Authorization: `Bearer ${dbToken}` } : {}) },
          body: JSON.stringify({ source: u }),
          signal
        })
        if (r.ok) { const j = await r.json(); if (j && j.url) return j.url }
        console.warn('【参考图转OSS未返回URL→已丢弃】', u.slice(0, 80), await r.text())
      } catch (e) { console.warn('【参考图转OSS失败→已丢弃】', u.slice(0, 80), e) }
      return ''   // 转存失败丢弃该图，绝不让大 base64 进入 multipart body
    }
    const ossImages = (await Promise.all(refImages.map(toOssUrl))).filter(Boolean)
    const ossVideos = (await Promise.all(refVideos.map(toOssUrl))).filter(Boolean)
    const ossAudios = (await Promise.all(refAudios.map(toOssUrl))).filter(Boolean)

    // 扩展参数放进 metadata（JSON 字符串），供后端 normalize_spec 解析：
    //   同时下发 image / images / reference_images，让 NewAPI 识别图生/参考生并转发图片。
    const meta = {
      duration: clampDuration(input.duration),
      resolution: String(input.resolution || '720p').toUpperCase(),
      aspect_ratio: input.aspectRatio || '16:9'
    }
    if (ossImages.length) {
      meta.reference_images = ossImages
      meta.images = ossImages
      meta.image = ossImages[0]
    }
    if (ossVideos.length) meta.videos = ossVideos
    if (ossAudios.length) meta.audios = ossAudios
    // 画布侧任务标识：new-api 转发 multipart 时原样透传到 wonderclip，供回查真实 OSS 直链。
    clientJobId = genId()
    meta.client_job_id = clientJobId
    fd.append('metadata', JSON.stringify(meta))
    // 顶层字段保险：部分 new-api 适配器转发 multipart 时可能丢弃 metadata 内的未知键，
    // 这里同时在顶层放一个 client_job_id，后端 normalize_spec 会优先读顶层（data.get("client_job_id")）。
    fd.append('client_job_id', clientJobId)

    const submitResp = await doFetch(`${baseUrl}/v1/videos`, {
      method: 'POST',
      headers, // 不手动设 Content-Type，交给 FormData 自动带 multipart boundary
      body: fd,
      signal
    })
    const submitText = await submitResp.text()
    if (!submitResp.ok) throw new Error(`提交任务失败 (${submitResp.status}): ${submitText.slice(0, 300)}`)
    const submitJson = JSON.parse(submitText)
    taskId = submitJson?.task_id || submitJson?.id
    if (!taskId) throw new Error('未解析到任务 ID')
    // 立即上报任务 id 给宿主持久化：退出重开后带回 resumeTaskId 续轮询。
    ctx.onTaskCreated?.(taskId)
  }

  // 2) 轮询任务状态，直到完成。
  const pollTimeoutMs = (Number(config.poll_timeout) || 1800) * 1000
  const start = Date.now()
  let mediaUrl = ''
  while (Date.now() - start < pollTimeoutMs) {
    await sleep(5000, signal)
    const pollResp = await doFetch(`${baseUrl}/v1/videos/${taskId}`, { headers, signal })
    const pollText = await pollResp.text()
    if (!pollResp.ok) throw new Error(`查询任务失败 (${pollResp.status}): ${pollText.slice(0, 300)}`)
    const pollJson = JSON.parse(pollText)
    const status = String(pollJson?.status || '').toLowerCase()

    if (status === 'completed' || status === 'succeeded') {
      console.log('【轮询返回完整JSON】', pollJson)
      // 可接受的成品地址：优先上游 OSS 签名直链；也兼容后端兜底的 /content?token=（均免 Header 可直接下载）。
      let videoUrl = pollJson?.url || pollJson?.output?.url || pollJson?.metadata?.original_url || pollJson?.content?.video_url || pollJson?.video_url
      console.log('【解析得到videoUrl】', videoUrl)

      // 关键兜底：new-api Sora 适配器会吃掉 url / 重写 task_id，导致上面拿不到可裸下载地址。
      // 这里用画布侧 client_job_id 直连 wonderclip 后端，取真实 OSS 签名直链（免 Header 可下载）。
      if (dbBaseUrl && clientJobId) {
        try {
          const dbResp = await doFetch(`${dbBaseUrl}/v1/videos/by_client/${encodeURIComponent(clientJobId)}`, {
            headers: dbToken ? { Authorization: `Bearer ${dbToken}` } : {},
            signal
          })
          if (dbResp.ok) {
            const dbJson = JSON.parse(await dbResp.text())
            const dbUrl = dbJson?.url || dbJson?.output?.url || dbJson?.metadata?.original_url || dbJson?.content?.video_url || dbJson?.video_url
            if (dbUrl && /^https?:\/\//i.test(dbUrl)) {
              videoUrl = dbUrl
              console.log('【兜底by_client拿到真实OSS直链】', videoUrl)
            }
          } else {
            console.warn('【兜底by_client失败】', dbResp.status, await dbResp.text())
          }
        } catch (e) {
          console.warn('【兜底by_client异常】', e)
        }
      }

      // 相对路径则补全 base_url；后端无 output_url 时兜底为 /v1/videos/{id}/content?token=<key>
      if (videoUrl && !/^https?:\/\//i.test(videoUrl)) {
        videoUrl = `${baseUrl}${videoUrl.startsWith('/') ? '' : '/'}${videoUrl}`
      } else if (!videoUrl) {
        videoUrl = `${baseUrl}/v1/videos/${encodeURIComponent(taskId)}/content${apiKey ? `?token=${encodeURIComponent(apiKey)}` : ''}`
      }

      if (!/^https?:\/\//i.test(videoUrl)) {
        throw new Error('获取视频失败：未解析到可下载的视频地址 url')
      }
      onProgress(95, '下载视频')
      // 交给画布宿主免鉴权下载，不进入任何鉴权 blob/FileReader 逻辑。
      mediaUrl = await ctx.downloadToFile(videoUrl, 'mp4')
      break
    }
    if (status === 'failed') {
      throw new Error(`任务失败：${pollJson?.error?.message || pollJson?.error || '未知错误'}`)
    }
    const p = Number(pollJson?.progress) || 0
    onProgress(Math.min(90, 30 + Math.floor(p * 0.6)), `生成中：${status === 'processing' ? '生成中' : '排队中'}`)
  }

  if (!mediaUrl) {
    throw new Error('轮询超时，没有拿到视频资源')
  }
  onProgress(100, '完成')
  return { mediaUrl, taskId }
}

// 统一入口：本插件仅支持视频生成。
export async function generate(ctx) {
  if (ctx.input.type === 'image') throw new Error('本插件仅支持视频生成')
  if (!String(ctx.config.base_url || '').trim()) throw new Error('未配置接口地址')
  if (!String(ctx.config.api_key || '').trim()) throw new Error('未配置 API Key')
  if (!String(ctx.input.resumeTaskId || '').trim() && !String(ctx.input.prompt || '').trim()) {
    throw new Error('提示词不能为空')
  }
  return generateVideo(ctx)
}

/**
 * 可选：处理配置面板的自定义动作（如「测试连通性」）。
 * 需要时在 manifest.json 的 config 里加对应按钮即可；不支持时可整段删除。
 */
export async function handleAction(action, data, ctx) {
  if (action === 'test_connection') {
    return { ok: true, message: `已读取配置 base_url=${ctx.config.base_url || '(空)'}` }
  }
  return { ok: false, error: `未知动作: ${action}` }
}
