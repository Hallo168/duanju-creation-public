"""Reusable Banana 2 colored-pencil client for the authenticated Flow browser bridge."""
import base64
import hashlib
from io import BytesIO
import json
from pathlib import Path
import time
import tomllib
import urllib.error
import urllib.request
import uuid
from urllib.parse import urlparse


class FlowBrowserImageClient:
    def __init__(self, base_url, settings_path):
        self.base_url = base_url.rstrip('/')
        parsed = urlparse(self.base_url)
        if parsed.hostname not in {'127.0.0.1', 'localhost', '::1'}:
            raise RuntimeError('New Flow browser transport is restricted to the local service')
        with open(settings_path, 'rb') as stream:
            self.settings = tomllib.load(stream)['global']
        self.opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
        self.auth = None
        self.project_id = None

    def api(self, path, body=None, timeout=290):
        headers = {'Content-Type': 'application/json'}
        if self.auth:
            headers['Authorization'] = 'Bearer ' + self.auth
        request = urllib.request.Request(self.base_url + path, data=None if body is None else json.dumps(body, ensure_ascii=False).encode('utf-8'), headers=headers)
        with self.opener.open(request, timeout=timeout) as response:
            return json.load(response)

    def command(self, name, payload=None, operation_id=None):
        body = {'command': name}
        if payload is not None:
            body['payload'] = payload
        if operation_id:
            body['operation_id'] = operation_id
        return self.api('/api/flow-browser/command', body)

    def preflight(self):
        login = self.api('/api/admin/login', {'username': self.settings['admin_username'], 'password': self.settings['admin_password']}, timeout=15)
        self.auth = login['token']
        try:
            status = self.api('/api/flow-browser/status', timeout=15)
        except urllib.error.HTTPError as exc:
            if exc.code == 404:
                return False  # Older Flow2API installation; existing transport remains available.
            raise
        bridge = status.get('bridge') or {}
        versions = bridge.get('versions') or []
        if bridge.get('connected_clients') != 1 or len(versions) != 1 or versions[0] not in {'1.3.0', '1.3.1', '1.3.2'}:
            raise RuntimeError('请打开已登录的FLOW页面并连接当前受支持的Token Updater扩展')
        self.adapter_version = versions[0]
        catalog = self.command('models').get('result') or {}
        if not catalog.get('success'):
            raise RuntimeError('New Flow model catalog unavailable')
        groups = catalog['data'][0][5]
        supported = any(usage[0] == 'NARWHAL' and 2 in usage[13][0] for group in groups for usage in group[1] or [])
        if not supported:
            raise RuntimeError('Nano Banana 2 portrait model is unavailable; no fallback')
        project = self.command('project_info').get('result') or {}
        if (not project.get('project_id') or not project.get('captcha_ready')) and self.adapter_version == '1.3.2':
            project = self.command('prepare_workspace', {}, 'prepare-' + uuid.uuid4().hex).get('result') or {}
        if not project.get('success'):
            raise RuntimeError(project.get('error') or '扩展未能准备FLOW工作页')
        if not project.get('project_id') or not project.get('captcha_ready'):
            raise RuntimeError('FLOW工作页未就绪；请更新至1.3.2以自动后台准备')
        self.project_id = project['project_id']
        self.project_preparation = project
        return True

    @staticmethod
    def save(path, value):
        temporary = path.with_suffix(path.suffix + '.tmp')
        temporary.write_text(json.dumps(value, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')
        temporary.replace(path)

    def download(self, url):
        parsed = urlparse(url)
        host = parsed.hostname or ''
        if parsed.scheme != 'https' or not (host.endswith('.googleusercontent.com') or host in {'flow-content.google', 'storage.googleapis.com', 'www.gstatic.com'}):
            raise RuntimeError('Unexpected Flow image download host')
        config = self.api('/api/proxy/config')
        proxy = config.get('media_proxy_url') if config.get('media_proxy_enabled') else config.get('proxy_url') if config.get('proxy_enabled') else None
        opener = urllib.request.build_opener(urllib.request.ProxyHandler({'https': proxy, 'http': proxy} if proxy else {}))
        with opener.open(urllib.request.Request(url, headers={'User-Agent': 'Mozilla/5.0'}), timeout=90) as response:
            return response.read(20000000)

    def generate(self, reference_path, operation_prefix, evidence_dir):
        if not self.project_id:
            raise RuntimeError('Run Flow browser preflight first')
        reference_path = Path(reference_path)
        data = reference_path.read_bytes()
        if not data.startswith(b'\x89PNG\r\n\x1a\n'):
            raise RuntimeError('The current Flow browser adapter requires one PNG source')
        evidence_dir = Path(evidence_dir)
        evidence_dir.mkdir(parents=True, exist_ok=True)
        source_sha256 = hashlib.sha256(data).hexdigest()
        upload = self.command('upload_reference', {'project_id': self.project_id, 'mime_type': 'image/png', 'image_b64': base64.b64encode(data).decode('ascii'), 'file_name': reference_path.name, 'source_sha256': source_sha256}, operation_prefix + '-upload')
        self.save(evidence_dir / 'upload.json', upload)
        uploaded = upload.get('result') or {}
        source_media = (uploaded.get('media') or {}).get('name')
        if not uploaded.get('success') or not source_media:
            raise RuntimeError('Flow source upload failed; response preserved')
        generation = self.command('generate_colored_pencil', {'project_id': self.project_id, 'reference_media_id': source_media, 'model_key': 'NARWHAL', 'seed': int(hashlib.sha256(operation_prefix.encode()).hexdigest()[:8], 16) % 2147483647, 'batch_id': operation_prefix}, operation_prefix + '-generate')
        self.save(evidence_dir / 'generation.json', generation)
        generated = generation.get('result') or {}
        media = generated.get('media') or []
        if not generated.get('success') or len(media) != 1:
            raise RuntimeError('Flow image generation did not return one image; response preserved, no retry')
        media = media[0]
        for _ in range(20):
            if media.get('url'):
                break
            response = self.command('get_media', {'media_id': media['name']})
            self.save(evidence_dir / 'media.json', response)
            media = response.get('result', {}).get('media') or media
            if not media.get('url'):
                time.sleep(3)
        if not media.get('url'):
            raise RuntimeError('Generated media exists but URL is not ready; recover without regenerating')
        original = self.download(media['url'])
        (evidence_dir / 'image-original.bin').write_bytes(original)
        from PIL import Image
        with Image.open(BytesIO(original)) as image:
            image.load()
            width, height = image.size
            if min(width, height) < 500 or max(width, height) > 1800 or abs(width / height - 9 / 16) > 0.02:
                raise RuntimeError('Unexpected Flow portrait size; raw image preserved')
            buffer = BytesIO()
            image.save(buffer, format='PNG')
            png = buffer.getvalue()
        self.save(evidence_dir / 'verification.json', {'format': 'PNG', 'width': width, 'height': height, 'sha256': hashlib.sha256(png).hexdigest(), 'source_sha256': source_sha256, 'model_key': 'NARWHAL', 'adapter_version': getattr(self, 'adapter_version', None), 'project_id': self.project_id, 'media_id': media['name']})
        return png
