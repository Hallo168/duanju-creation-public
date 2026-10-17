"""QS async videos: durable requests and explicit idempotent recovery."""
from pathlib import Path
from urllib import request, error, parse
import argparse
import hashlib
import json
import os
import re
import time
import uuid

from video_provider_catalog import read_config, token_for, model_rows
from video_reference_utils import imgbb_upload, imgbb_key, load_cache
from lnlovetlr_video_batch import save, append, latest, now
from zm_video_batch import NoRedirect, download
from dolasd_video_batch import verify

PROVIDER = 'qsqs_video'
MODEL = 'LEO-WAN3-480P'


class Client:
    def __init__(self):
        self.cfg = read_config()['providers'][PROVIDER]
        if self.cfg.get('disabled') or self.cfg.get('enabled') is False:
            raise RuntimeError('QS 视频渠道已停用')
        self.base = self.cfg['base_url'].rstrip('/')
        self.key = token_for(self.cfg)
        if not self.key:
            raise RuntimeError('QS 视频密钥缺失')
        self.opener = request.build_opener(NoRedirect)

    def clean(self, value):
        return str(value).replace(self.key, '[REDACTED]')

    def call(self, method, path, raw=None, idempotency_key=None):
        headers = {'Authorization': 'Bearer '+self.key,
                   'Accept': 'application/json', 'User-Agent': 'Mozilla/5.0'}
        if raw is not None:
            headers['Content-Type'] = 'application/json; charset=utf-8'
        if idempotency_key:
            headers['Idempotency-Key'] = idempotency_key
        req = request.Request(self.base+path, data=raw, headers=headers, method=method)
        try:
            with self.opener.open(req, timeout=300 if method == 'POST' else 60) as response:
                status, content = response.code, response.read(3_000_000)
        except error.HTTPError as exc:
            status, content = exc.code, exc.read(3_000_000)
            exc.close()
        content = self.clean(content.decode('utf-8', 'replace'))
        try:
            result = json.loads(content)
        except ValueError:
            result = {'message': content[:1000]}
        return status, result


def get(client, path):
    http, data = client.call('GET', path)
    if http != 200:
        raise RuntimeError(f'QS 查询 {path} HTTP {http}: {client.clean(data)[:500]}')
    return data


def check(client, duration, ratio, resolution):
    if not 15 <= duration <= 30 or ratio != '9:16' or resolution.lower() != '480p':
        raise RuntimeError('本渠道预设为 15–30 秒、9:16、480p；不自动改时长或分辨率')
    permissions = model_rows(get(client, '/v1/models'))
    if MODEL not in [r.get('id') for r in permissions]:
        raise RuntimeError('当前 Key 无 LEO-WAN3-480P 权限')
    models = model_rows(get(client, '/api/workbench-models'))
    row = next((r for r in models if r.get('api_model', r.get('id')) == MODEL), None)
    if not row or not row.get('enabled'):
        raise RuntimeError('LEO-WAN3-480P 当前未开放')
    bounds = re.fullmatch(r'(\d+)-(\d+)', str(row.get('duration')))
    if not bounds or not int(bounds[1]) <= duration <= int(bounds[2]) or resolution.lower() != row.get('resolution'):
        raise RuntimeError('实时模型参数与本次请求不符')
    pricing = next((r for r in model_rows(get(client, '/api/pricing')) if r.get('model_name') == MODEL), None)
    expression = pricing.get('billing_expr', '') if pricing else ''
    rate = re.fullmatch(r'u\("seconds"\)\s*\*\s*([0-9.]+)', expression)
    if not rate:
        raise RuntimeError('实时计费表达式无法确认: '+expression)
    return {'at': now(), 'provider': PROVIDER, 'model': MODEL, 'permission_confirmed': True,
            'duration': duration, 'ratio': ratio, 'resolution': resolution.lower(), 'capabilities': row,
            'billing_expr': expression, 'price_per_second_cny': float(rate[1]),
            'estimate_cny': round(duration*float(rate[1]), 4)}


def https_url(value):
    if parse.urlsplit(value).scheme != 'https':
        raise RuntimeError('参考素材须为公网 HTTPS 地址')
    return value


def prepare(client, args, folder):
    prompt = args.prompt_file.read_text(encoding='utf-8-sig').strip()
    if not prompt:
        raise RuntimeError('视频提示词为空')
    report = check(client, args.duration, args.ratio, args.resolution)
    limits = report['capabilities']
    counts = (len(args.reference), len(args.reference_video_url), len(args.reference_audio_url))
    maximums = (min(9, int(limits['max_image_refs'])), int(limits['max_video_refs']), int(limits['max_audio_refs']))
    if any(count > maximum for count, maximum in zip(counts, maximums)):
        raise RuntimeError('参考素材数量超出项目或模型上限')
    videos = [https_url(url) for url in args.reference_video_url]
    audios = [https_url(url) for url in args.reference_audio_url]
    sources = [value if value.startswith('https://') else Path(value).resolve() for value in args.reference]
    for source in sources:
        if isinstance(source, Path) and not source.is_file():
            raise RuntimeError('参考图片不存在: '+str(source))
    cache_file = folder/'reference-uploads.json'
    cache = load_cache(cache_file)
    images, metadata = [], []
    for source in sources:
        if isinstance(source, Path):
            digest = hashlib.sha256(source.read_bytes()).hexdigest()
            url = imgbb_upload(source, imgbb_key(), cache, cache_file)
            metadata.append({'path': str(source), 'sha256': digest, 'url': url})
        else:
            url = source
            metadata.append({'url': url})
        images.append(https_url(url))
    payload = {'model': MODEL, 'prompt': prompt, 'seconds': str(args.duration),
               'aspect_ratio': args.ratio, 'resolution': args.resolution.lower(),
               'reference_image_urls': images, 'reference_video_urls': videos,
               'reference_audio_urls': audios, 'generate_audio': True}
    raw = json.dumps(payload, ensure_ascii=False).encode('utf-8')
    (folder/'request.json').write_bytes(raw)
    save(folder/'preflight.json', report)
    save(folder/'request-metadata.json', {'unit': args.unit, 'references': metadata,
         'prompt_sha256': hashlib.sha256(prompt.encode('utf-8')).hexdigest(),
         'request_sha256': hashlib.sha256(raw).hexdigest()})
    return raw


def submit(client, args):
    folder = args.task_dir
    folder.mkdir(parents=True, exist_ok=True)
    ledger = folder/'qsqs-tasks.jsonl'
    if ledger.exists():
        previous = latest(ledger)
        if previous.get('task_id'):
            print(json.dumps({'existing': True, **previous}, ensure_ascii=False))
            return
        if not args.resume or previous.get('status') not in ('submitting', 'unknown_outcome'):
            raise RuntimeError('已有提交记录；状态不明时用 submit --resume 恢复原请求')
        if previous.get('base_url') != client.base:
            raise RuntimeError('原任务渠道地址已变化')
        raw = (folder/'request.json').read_bytes()
        if hashlib.sha256(raw).hexdigest() != previous['request_sha256']:
            raise RuntimeError('原请求文件已变化，不能恢复')
        identity = previous['idempotency_key']
    else:
        if not args.prompt_file:
            raise RuntimeError('提交需要 --prompt-file')
        raw = prepare(client, args, folder)
        identity = str(uuid.uuid4())
        append(ledger, {'provider': PROVIDER, 'base_url': client.base, 'unit': args.unit,
              'duration': args.duration, 'status': 'submitting', 'idempotency_key': identity,
              'request_sha256': hashlib.sha256(raw).hexdigest()})
    try:
        http, reply = client.call('POST', '/v1/videos', raw, identity)
    except Exception as exc:
        append(ledger, {'status': 'unknown_outcome', 'error': client.clean(exc)[:1000]})
        raise RuntimeError('提交结果不明；原请求和幂等键已保存，用 --resume 恢复') from None
    save(folder/'create-response.json', reply)
    task = reply.get('data', reply)
    task_id = task.get('id') or task.get('task_id')
    if http not in (200, 201, 202) or not task_id:
        append(ledger, {'status': 'submit_rejected' if 400 <= http < 500 else 'unknown_outcome',
                       'http': http, 'error': client.clean(reply)[:1000]})
        raise RuntimeError(f'QS 提交 HTTP {http}: {client.clean(reply)[:500]}')
    append(ledger, {'task_id': str(task_id), 'status': task.get('status', 'processing'), 'error': None})
    print(json.dumps(latest(ledger), ensure_ascii=False))


def poll(client, args):
    ledger = args.task_dir/'qsqs-tasks.jsonl'
    record = latest(ledger)
    task_id = record.get('task_id')
    if not task_id:
        raise RuntimeError('本地原任务没有任务号；先恢复原提交')
    if record.get('base_url') != client.base:
        raise RuntimeError('原任务渠道地址已变化')
    if record.get('status') == 'downloaded':
        print(json.dumps(record, ensure_ascii=False))
        return 0
    deadline = time.monotonic()+args.timeout
    while True:
        reply = get(client, '/v1/videos/'+parse.quote(task_id, safe=''))
        task = reply.get('data', reply)
        status = str(task.get('status', 'unknown')).lower()
        save(args.task_dir/'last-status.json', task)
        append(ledger, {'status': status, 'progress': task.get('progress'), 'error': task.get('error')})
        print(json.dumps({'task_id': task_id, **task}, ensure_ascii=False), flush=True)
        if status in ('completed', 'complete'):
            output = args.output_dir or args.task_dir
            output.mkdir(parents=True, exist_ok=True)
            target = output/(str(record['unit'])+'单元-QS-'+task_id+'.mp4')
            temporary = target.with_suffix('.part')
            if not target.exists():
                if temporary.exists():
                    raise RuntimeError('上次部分下载已保留，需恢复本地文件后再下载')
                download(client, task_id, temporary)
                report = verify(temporary, int(record['duration']), '480x854')
                os.replace(temporary, target)
            else:
                report = verify(target, int(record['duration']), '480x854')
            save(args.task_dir/'technical-report.json', report)
            append(ledger, {'status': 'downloaded', 'output_path': str(target.resolve()), 'technical_verified': True})
            print(json.dumps(latest(ledger), ensure_ascii=False))
            return 0
        if status in ('failed', 'error', 'cancelled', 'canceled'):
            raise RuntimeError('QS 任务失败: '+client.clean(task.get('error') or task.get('message')))
        if not args.watch or time.monotonic() >= deadline:
            return 3
        time.sleep(max(3, args.interval))


def main():
    parser = argparse.ArgumentParser(description='QS LEO-WAN3-480P 视频执行器')
    parser.add_argument('mode', choices=('check', 'submit', 'poll'))
    parser.add_argument('--duration', type=int, default=30)
    parser.add_argument('--resolution', default='480p')
    parser.add_argument('--ratio', default='9:16')
    parser.add_argument('--unit', default='1')
    parser.add_argument('--prompt-file', type=Path)
    parser.add_argument('--reference', action='append', default=[])
    parser.add_argument('--reference-video-url', action='append', default=[])
    parser.add_argument('--reference-audio-url', action='append', default=[])
    parser.add_argument('--task-dir', type=Path)
    parser.add_argument('--output-dir', type=Path)
    parser.add_argument('--resume', action='store_true')
    parser.add_argument('--watch', action='store_true')
    parser.add_argument('--interval', type=int, default=5)
    parser.add_argument('--timeout', type=int, default=1800)
    args = parser.parse_args()
    client = Client()
    if args.mode == 'check':
        print(json.dumps(check(client, args.duration, args.ratio, args.resolution), ensure_ascii=False, indent=2))
        return 0
    if not args.task_dir:
        parser.error('需要 --task-dir')
    args.task_dir = args.task_dir.resolve()
    if args.mode == 'submit':
        submit(client, args)
        return 0
    return poll(client, args)


if __name__ == '__main__':
    try:
        raise SystemExit(main())
    except Exception as exc:
        print('ERROR '+str(exc)[:1000], flush=True)
        raise SystemExit(1)
