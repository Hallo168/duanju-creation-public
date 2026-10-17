#!/usr/bin/env python3
"""MEAICC/New API video adapter.

Reads the user supplied token at runtime, uploads ordered reference images to
the channel MinIO service, creates one existing-story video task, and polls it
without ever retrying a POST automatically.
"""
from __future__ import annotations
import argparse, hashlib, json, mimetypes, msvcrt, os, re, shutil, subprocess, sys, time, uuid
from datetime import datetime, timezone
from pathlib import Path
from urllib import error, parse, request

ROOT = Path(__file__).resolve().parent.parent
MINIO_BASE = 'https://minioapi.meaicc.com'
UA = 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/140.0.0.0 Safari/537.36'

class NoRedirect(request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None

OPENER = request.build_opener(NoRedirect)

def now():
    return datetime.now(timezone.utc).isoformat()

def sha(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()

def save(path, value):
    path = Path(path); path.parent.mkdir(parents=True, exist_ok=True)
    temp = path.with_name(path.name + '.tmp')
    temp.write_text(json.dumps(value, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')
    os.replace(temp, path)

def read_json(path):
    return json.loads(Path(path).read_text(encoding='utf-8-sig'))

def append(path, row):
    path = Path(path); path.parent.mkdir(parents=True, exist_ok=True)
    with path.open('a', encoding='utf-8', newline='\n') as f:
        f.write(json.dumps({'at': now(), 'platform': 'meaicc', **row}, ensure_ascii=False) + '\n')
        f.flush(); os.fsync(f.fileno())

def rows(path):
    path = Path(path)
    return [json.loads(x) for x in path.read_text(encoding='utf-8').splitlines() if x.strip()] if path.exists() else []

def channel_config():
    from video_provider_catalog import read_config, token_for
    provider = read_config()['providers']['meaicc_video']
    return provider['base_url'].rstrip('/'), token_for(provider)

def clean(token, value):
    raw = json.dumps(value, ensure_ascii=False) if not isinstance(value, str) else value
    return raw.replace(token, '[REDACTED]')

def call(base, token, method, path, payload=None, timeout=60, headers_extra=None):
    url = path if path.startswith('http') else base.rstrip('/') + path
    headers = {'Authorization': 'Bearer ' + token, 'Accept': 'application/json', 'User-Agent': UA,
               'Origin': base, 'Referer': base.rstrip('/') + '/'}
    if headers_extra: headers.update(headers_extra)
    body = None
    if payload is not None:
        body = json.dumps(payload, ensure_ascii=False).encode('utf-8')
        headers['Content-Type'] = 'application/json'
    try:
        response = OPENER.open(request.Request(url, data=body, headers=headers, method=method), timeout=timeout)
    except error.HTTPError as exc:
        response = exc
    with response:
        status = response.code
        raw = response.read(12_000_000).decode('utf-8', 'replace')
    try: data = json.loads(raw)
    except ValueError: data = {'raw': raw[:4000]}
    return status, data

def local_item(args):
    # Reuse the project's prompt boundary and ordered mount resolver.
    sys.path.insert(0, str(ROOT / '工具'))
    import mjnewapi_video_batch as mj
    story = Path(args.story_root)
    if not story.is_absolute(): story = ROOT / story
    source = story / args.prompt_file
    markdown = source.read_text(encoding='utf-8-sig')
    section = mj.unit_section(markdown, args.unit)
    if re.search(r'(?m)^视频挂载：', section):
        if not re.search(r'(?m)^fenjing_ready:\s*true\s*$', markdown):
            raise RuntimeError('fenjing_ready必须为true')
        prompt = mj.video_prompt(section, args.unit)
        mounts = re.findall(r'(?m)^- 图片(\d+)＝([^\r\n（]+)（[^）]*）', section)
        if not mounts:
            mount_line = re.search(r'(?m)^视频挂载：([^\r\n]+)$', section)
            if mount_line:
                mounts = re.findall(r'(?:^|[；;]\s*)图片(\d+)\s*[=＝]\s*([^；;\r\n]+)', mount_line.group(1))
        if not mounts or len(mounts) > 9 or [int(n) for n, _ in mounts] != list(range(1, len(mounts) + 1)):
            raise RuntimeError(f'单元{args.unit}挂载数量或顺序不符')
        refs = []
        asset_root = (story / '资产图').resolve()
        for index, filename in mounts:
            asset = Path(filename.strip())
            if not asset.is_absolute():
                asset = (story / '资产图' / asset) if len(asset.parts) == 1 else (story / asset)
            asset = asset.resolve()
            if not asset.is_relative_to(asset_root) or not asset.is_file() or not 0 < asset.stat().st_size <= 20 * 1024 * 1024:
                raise RuntimeError(f'单元{args.unit}参考图不可用：{asset.name}')
            refs.append({'index': int(index), 'path': str(asset), 'sha256': sha(asset), 'bytes': asset.stat().st_size})
        used = set(map(int, re.findall(r'(?:图片|图)(\d+)', prompt)))
        if used != set(range(1, len(refs) + 1)):
            raise RuntimeError(f'单元{args.unit}提示词图序号与挂载不一致')
        duration = int(re.search(r'(?m)^生成参数：(\d+)秒', section)[1])
        ratio = re.search(r'(?m)^生成参数：[^\r\n]*?(\d+:\d+)', section)[1]
        return {'unit': args.unit, 'prompt': prompt, 'prompt_sha256': hashlib.sha256(prompt.encode('utf-8')).hexdigest(),
                'duration': duration, 'aspect_ratio': ratio, 'resolution': args.resolution, 'references': refs}
    ns = argparse.Namespace(
        story_root=args.story_root, prompt_file=args.prompt_file, units=str(args.unit),
        model=args.model, resolution=args.resolution.lower(), task_dir=Path(args.task_dir),
    )
    items = mj.local_inputs(ns)
    if len(items) != 1: raise RuntimeError('本次只允许一个单元')
    return items[0]

def live_preflight(args, item):
    base, token = channel_config()
    status, pricing = call(base, token, 'GET', '/api/pricing')
    if status != 200: raise RuntimeError(f'价格目录 HTTP {status}: ' + clean(token, pricing)[:700])
    plist = pricing.get('data', []) if isinstance(pricing, dict) else []
    price = next((x for x in plist if isinstance(x, dict) and x.get('model_name') == args.model), None)
    if not price: raise RuntimeError('实时价格目录没有完整模型名 ' + args.model)
    status, config = call(base, token, 'GET', '/create/js/config.js')
    if status != 200: raise RuntimeError(f'视频配置 HTTP {status}: ' + clean(token, config)[:500])
    config_text = config.get('raw', '') if isinstance(config, dict) else str(config)
    if args.model in ('w-3', 'w3-p-c1', 'w3-c1') and args.resolution.upper() == '1080P' and not re.search(r"['\"]" + re.escape(args.model) + r"['\"]\s*:\s*\[[^\]]*1080P", config_text):
        raise RuntimeError('实时视频配置未确认所选模型支持1080P')
    if item['duration'] != 30: raise RuntimeError('本次单元必须按30秒发送')
    if item['aspect_ratio'] != '9:16': raise RuntimeError('本次单元必须按9:16发送')
    if len(item['references']) < 1 or len(item['references']) > 9: raise RuntimeError('参考图数量不在1-9张范围')
    try: unit_price = float(price.get('model_price'))
    except Exception: raise RuntimeError('实时价格不是有效数字')
    report = {
        'checked_at': now(), 'provider': 'meaicc', 'base_url': base, 'model': args.model,
        'unit': args.unit, 'duration': item['duration'], 'resolution': args.resolution,
        'aspect_ratio': item['aspect_ratio'], 'billing_mode': 'per_call', 'model_price_yuan': unit_price,
        'budget_yuan': unit_price, 'reference_count': len(item['references']),
        'prompt_sha256': item['prompt_sha256'],
        'reference_sha256s': [x['sha256'] for x in item['references']],
        'catalog_model': {k: v for k, v in price.items() if k not in ('key', 'token')},
    }
    save(Path(args.task_dir) / 'preflight.json', report)
    print(json.dumps({'preflight': 'passed', 'model': args.model, 'unit': args.unit,
                      'duration': item['duration'], 'resolution': args.resolution,
                      'ratio': item['aspect_ratio'], 'reference_count': len(item['references']),
                      'budget_yuan': unit_price}, ensure_ascii=False), flush=True)
    return base, token, report

def upload(args, item, base, token):
    task = Path(args.task_dir); cache_path = task / 'materials.json'
    cache = read_json(cache_path) if cache_path.exists() else {}
    if args.model in ('w3-p-c1', 'w3-c1'):
        from video_reference_utils import imgbb_key, imgbb_upload, load_cache
        host_cache_path = task.parent / 'imgbb-cache.json'
        host_cache = load_cache(host_cache_path)
        key = imgbb_key()
        for index, ref in enumerate(item['references'], 1):
            digest = ref['sha256']
            image = Path(ref['path'])
            view_url = imgbb_upload(image, key, host_cache, host_cache_path)
            if not view_url.startswith('https://'):
                raise RuntimeError(f'参考图{index}没有HTTPS图床地址')
            if cache.get(digest, {}).get('host') == 'imgbb' and cache[digest].get('view_url') == view_url:
                continue
            cache[digest] = {'view_url': view_url, 'host': 'imgbb', 'uploaded_at': now(),
                             'source_sha256': digest, 'filename': image.name, 'bytes': ref['bytes'], 'index': index}
            save(cache_path, cache)
            print(json.dumps({'hosted_reference': index, 'unit': args.unit, 'host': 'imgbb'}, ensure_ascii=False), flush=True)
        return cache
    for index, ref in enumerate(item['references'], 1):
        digest = ref['sha256']
        if digest in cache: continue
        filename = Path(ref['path']).name
        query = '/api/get-upload-url?filename=' + parse.quote(filename)
        status, sign = call(MINIO_BASE, token, 'GET', query, timeout=60)
        if status != 200: raise RuntimeError(f'参考图{index}签名 HTTP {status}: ' + clean(token, sign)[:700])
        upload_url = sign.get('uploadUrl'); view_url = sign.get('viewUrl')
        if not upload_url or not view_url: raise RuntimeError(f'参考图{index}签名响应缺少地址')
        if parse.urlsplit(view_url).scheme != 'https': raise RuntimeError('参考图返回非HTTPS地址')
        content_type = mimetypes.guess_type(filename)[0] or 'application/octet-stream'
        headers = {'Content-Type': content_type, 'User-Agent': UA}
        try:
            response = OPENER.open(request.Request(upload_url, data=Path(ref['path']).read_bytes(), headers=headers, method='PUT'), timeout=180)
        except error.HTTPError as exc: response = exc
        with response:
            if response.code not in (200, 201, 204):
                raise RuntimeError(f'参考图{index}直传 HTTP {response.code}')
        cache[digest] = {'view_url': view_url, 'uploaded_at': now(), 'source_sha256': digest,
                         'filename': filename, 'bytes': ref['bytes'], 'index': index}
        save(cache_path, cache)
        print(json.dumps({'uploaded_reference': index, 'unit': args.unit, 'bytes': ref['bytes']}, ensure_ascii=False), flush=True)
    return cache

def submit(args):
    task = Path(args.task_dir); ledger = Path(args.ledger)
    if rows(ledger): raise RuntimeError('账本已有记录，禁止重复创建任务')
    item = local_item(args)
    base, token, report = live_preflight(args, item)
    cache_path = task / 'materials.json'
    if not cache_path.exists(): raise RuntimeError('尚未上传参考图')
    cache = read_json(cache_path)
    media = []
    for ref in item['references']:
        entry = cache.get(ref['sha256'])
        if not entry: raise RuntimeError('缺少参考图上传结果: ' + Path(ref['path']).name)
        media.append({'type': 'reference_image', 'url': entry['view_url']})
    body = {
        'model': args.model,
        'input': {'prompt': item['prompt'], 'media': media},
        'parameters': {'resolution': args.resolution, 'ratio': item['aspect_ratio'],
                       'duration': item['duration'], 'prompt_extend': False, 'watermark': False},
    }
    save(task / 'request-meta.json', {'model': args.model, 'unit': args.unit,
         'duration': item['duration'], 'resolution': args.resolution, 'aspect_ratio': item['aspect_ratio'],
         'prompt_sha256': item['prompt_sha256'], 'reference_sha256s': [x['sha256'] for x in item['references']]})
    attempt = str(uuid.uuid4())
    base_row = {'unit': args.unit, 'model': args.model, 'attempt_id': attempt,
                'duration': item['duration'], 'resolution': args.resolution, 'aspect_ratio': item['aspect_ratio'],
                'prompt_sha256': item['prompt_sha256'], 'reference_sha256s': [x['sha256'] for x in item['references']],
                'billing_mode': report['billing_mode'], 'unit_price_yuan': report['model_price_yuan'],
                'budget_yuan': report['budget_yuan']}
    append(ledger, {**base_row, 'status': 'submitting'})
    status, data = call(base, token, 'POST', '/v1/videos', body, timeout=600)
    save(task / 'create-response.json', data)
    if status not in (200, 201, 202):
        state = 'submit_failed' if 400 <= status < 500 else 'unknown_outcome'
        append(ledger, {**base_row, 'status': state, 'http_status': status, 'error': clean(token, data)[:1500]})
        raise RuntimeError(f'视频创建 HTTP {status}: ' + clean(token, data)[:800])
    task_id = data.get('task_id') or data.get('id')
    if not task_id:
        append(ledger, {**base_row, 'status': 'unknown_outcome', 'error': '响应缺少任务号'})
        raise RuntimeError('创建响应没有任务号，禁止重投')
    state = data.get('status', 'PENDING')
    append(ledger, {**base_row, 'task_id': str(task_id), 'status': state, 'progress': data.get('progress')})
    print(json.dumps({'submitted_unit': args.unit, 'task_id': str(task_id), 'status': state}, ensure_ascii=False), flush=True)

def download_url(base, token, url, target):
    current = url
    for _ in range(6):
        headers = {'Accept': '*/*', 'User-Agent': UA}
        if parse.urlsplit(current).netloc == parse.urlsplit(base).netloc:
            headers['Authorization'] = 'Bearer ' + token
        try: response = OPENER.open(request.Request(current, headers=headers), timeout=180)
        except error.HTTPError as exc: response = exc
        with response:
            if response.code in (301, 302, 303, 307, 308):
                current = parse.urljoin(current, response.headers.get('Location', ''))
                if parse.urlsplit(current).scheme != 'https': raise RuntimeError('下载重定向不是HTTPS')
                continue
            if response.code != 200: raise RuntimeError('视频下载 HTTP ' + str(response.code))
            content_type = response.headers.get('Content-Type', '')
            if 'json' in content_type:
                payload = json.loads(response.read(2_000_000).decode('utf-8', 'replace'))
                current = payload.get('object') or payload.get('url') or payload.get('video_url')
                if not current: raise RuntimeError('下载响应缺少视频地址')
                continue
            with Path(target).open('wb') as f: shutil.copyfileobj(response, f, 1024 * 1024)
            return
    raise RuntimeError('视频下载重定向过多')

def save_video_without_overwrite(source, out, unit_label, task_id):
    source, out = Path(source), Path(out)
    version = re.sub(r'[^A-Za-z0-9_-]', '_', str(task_id))
    candidates = [out / f'{unit_label}.mp4', out / f'{unit_label}-meaicc-{version}.mp4']
    for target in candidates:
        try:
            with target.open('xb') as dest, source.open('rb') as src:
                shutil.copyfileobj(src, dest, 1024 * 1024)
            return target
        except FileExistsError:
            if sha(target) == sha(source):
                return target
    raise RuntimeError('同任务版本文件已存在不同内容，禁止覆盖')

def technical_finalize(args, row, detail, base, token):
    sys.path.insert(0, str(ROOT / '工具'))
    import mjnewapi_video_batch as mj
    task = Path(args.task_dir); story = Path(args.story_root).resolve()
    out = story / args.prompt_file
    out = out.parent / '成片'; out.mkdir(parents=True, exist_ok=True)
    unit_label = f'{args.unit}单元'
    stamp = f'{unit_label}-meaicc-' + uuid.uuid4().hex
    downloads = task / 'downloads'; downloads.mkdir(parents=True, exist_ok=True)
    raw_path = downloads / (stamp + '-download.mp4')
    object_url = detail.get('object') or detail.get('video_url') or detail.get('url')
    if not object_url: raise RuntimeError('成功状态缺少 object 视频地址')
    download_url(base, token, object_url, raw_path)
    if not raw_path.exists() or raw_path.stat().st_size == 0: raise RuntimeError('下载视频为空')
    info = mj.probe(raw_path)
    video = next((x for x in info.get('streams', []) if x.get('codec_type') == 'video'), {})
    audio = next((x for x in info.get('streams', []) if x.get('codec_type') == 'audio'), {})
    duration = float(info.get('format', {}).get('duration', 0))
    source = raw_path
    if not (video.get('codec_name') == 'h264' and audio.get('codec_name') == 'aac'):
        converted = downloads / (stamp + '-converted.mp4')
        ffmpeg = mj.tool('ffmpeg')
        proc = subprocess.run([ffmpeg, '-nostdin', '-v', 'error', '-n', '-i', str(raw_path),
                               '-c:v', 'libx264', '-pix_fmt', 'yuv420p', '-c:a', 'aac', '-movflags', '+faststart', str(converted)],
                              capture_output=True, creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0), timeout=240)
        if proc.returncode: raise RuntimeError('H.264/AAC转码失败')
        source = converted; info = mj.probe(source)
        video = next((x for x in info.get('streams', []) if x.get('codec_type') == 'video'), {})
        audio = next((x for x in info.get('streams', []) if x.get('codec_type') == 'audio'), {})
        duration = float(info.get('format', {}).get('duration', 0))
    if video.get('codec_name') != 'h264' or audio.get('codec_name') != 'aac': raise RuntimeError('成片不是H.264/AAC')
    expected_size = {'720P': (720, 1280), '1080P': (1080, 1920)}.get(str(row.get('resolution') or args.resolution).upper())
    if expected_size is None: raise RuntimeError('未配置本任务分辨率的技术核验尺寸')
    if (video.get('width'), video.get('height')) != expected_size: raise RuntimeError(f'成片尺寸不符: {video.get("width")}x{video.get("height")}，预期{expected_size[0]}x{expected_size[1]}')
    if abs(duration - 30) > 0.75: raise RuntimeError(f'成片时长不符: {duration:.3f}s')
    ffmpeg = mj.tool('ffmpeg')
    decode = subprocess.run([ffmpeg, '-nostdin', '-v', 'error', '-xerror', '-err_detect', 'explode', '-i', str(source), '-f', 'null', '-'],
                            capture_output=True, creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0), timeout=180)
    if decode.returncode: raise RuntimeError('成片完整解码失败')
    target = save_video_without_overwrite(source, out, unit_label, row['task_id'])
    moves = []
    report = {'unit': args.unit, 'task_id': row['task_id'], 'status': 'succeeded', 'output_path': str(target),
              'video_codec': video.get('codec_name'), 'audio_codec': audio.get('codec_name'),
              'width': video.get('width'), 'height': video.get('height'), 'duration': duration,
              'frame_rate': video.get('avg_frame_rate'), 'bytes': target.stat().st_size,
              'sha256': sha(target), 'full_decode': True, 'archive_manifest': str(task / 'final-file-archive.json')}
    save(task / 'technical-report.json', report); save(task / 'final-file-archive.json', moves)
    append(Path(args.ledger), report)
    print(json.dumps(report, ensure_ascii=False), flush=True)

def poll_once(args):
    task = Path(args.task_dir); ledger = Path(args.ledger)
    all_rows = rows(ledger)
    latest = next((r for r in reversed(all_rows) if r.get('task_id')), None)
    if not latest: raise RuntimeError('账本没有任务号，禁止创建新任务')
    if latest.get('status') == 'succeeded' and (task / 'technical-report.json').exists(): return 0
    base, token = channel_config()
    status, detail = call(base, token, 'GET', '/v1/videos/' + parse.quote(str(latest['task_id']), safe=''), timeout=90)
    if status != 200: raise RuntimeError(f'查询任务 HTTP {status}: ' + clean(token, detail)[:700])
    save(task / 'last-response.json', detail)
    state = str(detail.get('status') or '').upper()
    progress = detail.get('progress')
    if state == 'SUCCEEDED':
        if not any(r.get('status') == 'succeeded' and r.get('technical') for r in all_rows):
            technical_finalize(args, latest, detail, base, token)
        return 0
    if state.startswith('FAILED') or state in ('ERROR', 'CANCELED', 'CANCELLED'):
        message = detail.get('message') or detail.get('error') or state
        append(ledger, {'unit': args.unit, 'task_id': latest['task_id'], 'status': 'failed', 'error': clean(token, message)[:1500]})
        raise RuntimeError('平台任务终止: ' + clean(token, message)[:900])
    if state != latest.get('status') or progress != latest.get('progress'):
        append(ledger, {'unit': args.unit, 'task_id': latest['task_id'], 'status': detail.get('status'), 'progress': progress})
    print(json.dumps({'unit': args.unit, 'task_id': latest['task_id'], 'status': detail.get('status'), 'progress': progress}, ensure_ascii=False), flush=True)
    return 3

def watcher(args):
    task = Path(args.task_dir); lock = (task / 'watcher.lock').open('a+b')
    lock.seek(0); lock.write(b'0'); lock.flush(); lock.seek(0)
    try: msvcrt.locking(lock.fileno(), msvcrt.LK_NBLCK, 1)
    except OSError: return
    start = time.monotonic(); errors = 0
    while time.monotonic() - start < 86400:
        try:
            write_state(task, 'running')
            outcome = poll_once(args)
            if outcome == 0:
                report = read_json(task / 'technical-report.json')
                write_state(task, 'completed', **{k: v for k, v in report.items() if k != 'status'})
                return
            errors = 0
        except Exception as exc:
            errors += 1; write_state(task, 'retrying', error=str(exc), consecutive_errors=errors)
            if '平台任务终止' in str(exc) or errors >= 10:
                write_state(task, 'failed' if '平台任务终止' in str(exc) else 'needs_attention', error=str(exc)); return
        time.sleep(30)
    write_state(task, 'needs_attention', message='等待已达24小时，未自动重投')

def write_state(task, status, **extra):
    save(Path(task) / 'watcher-status.json', {'status': status, 'pid': os.getpid(), 'updated_at': now(), **extra})

def launch_watcher(args):
    task = Path(args.task_dir); log = (task / 'watcher.log').open('ab', buffering=0)
    cmd = [sys.executable, '-X', 'utf8', str(Path(__file__).resolve()), 'watch',
           '--story-root', args.story_root, '--prompt-file', args.prompt_file, '--unit', str(args.unit),
           '--model', args.model, '--resolution', args.resolution, '--task-dir', str(task), '--ledger', str(args.ledger)]
    child = subprocess.Popen(cmd, cwd=str(ROOT), stdin=subprocess.DEVNULL, stdout=log, stderr=log,
                             creationflags=subprocess.CREATE_NO_WINDOW | subprocess.DETACHED_PROCESS | subprocess.CREATE_NEW_PROCESS_GROUP,
                             close_fds=True)
    print(json.dumps({'background_pid': child.pid, 'interval_seconds': 30, 'log': str(task / 'watcher.log')}, ensure_ascii=False))

def main():
    p = argparse.ArgumentParser(description='MEAICC视频生成与轮询')
    p.add_argument('mode', choices=['preflight', 'upload', 'submit', 'poll', 'watch'])
    p.add_argument('--story-root', default='stories/101-她不是贼'); p.add_argument('--prompt-file', default='分镜-30秒/分镜提示词.md')
    from video_provider_catalog import read_config
    defaults = read_config()['video_defaults']
    p.add_argument('--unit', type=int, default=2); p.add_argument('--model', default=defaults['model']); p.add_argument('--resolution', default=defaults['resolution'])
    p.add_argument('--task-dir', required=True); p.add_argument('--ledger'); p.add_argument('--watch', action='store_true'); p.add_argument('--launch', action='store_true')
    args = p.parse_args(); args.task_dir = str(Path(args.task_dir).resolve())
    Path(args.task_dir).mkdir(parents=True, exist_ok=True)
    args.ledger = str(Path(args.ledger or (Path(args.task_dir) / 'meaicc-tasks.jsonl')).resolve())
    try:
        if args.mode == 'watch':
            if args.launch: launch_watcher(args)
            else: watcher(args)
            return 0
        item = local_item(args)
        base, token, _ = live_preflight(args, item)
        if args.mode == 'preflight': return 0
        if args.mode == 'upload': upload(args, item, base, token); return 0
        if args.mode == 'submit': submit(args); return 0
        if args.mode == 'poll':
            if args.watch:
                watcher(args); return 0
            return poll_once(args)
    except Exception as exc:
        print('ERROR ' + str(exc), flush=True); return 1

if __name__ == '__main__': raise SystemExit(main())
