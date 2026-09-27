import io
import json
from pathlib import Path
import runpy
import sys
import tempfile
import types
import unittest
import urllib.error
from unittest.mock import patch

from flowkit_image_client import FlowKitImageClient, PENCIL_PROMPT

PROJECT = "82e1ffdd-33a6-47fa-a9fa-019e782acde1"
SOURCE_ID = "11111111-1111-4111-8111-111111111111"
OUTPUT_ID = "22222222-2222-4222-8222-222222222222"
PNG = b"\x89PNG\r\n\x1a\n" + b"x" * 2048


class FakeClient(FlowKitImageClient):
    def __init__(self):
        super().__init__("http://127.0.0.1:8100")
        self.calls = []
        self.remote_project = PROJECT
        self.connected = True
        self.cooldown = False
        self.edit_error = None
        self.download_error = None

    def _request(self, path, payload=None, **kwargs):
        self.calls.append((path, payload, kwargs))
        if path == "/health":
            return {"status": "ok", "extension_connected": self.connected}
        if path == "/api/flow/status":
            return {"connected": self.connected, "transport": "batch", "flow_project_id": self.remote_project, "generation_throttle": {"cooldown_active": self.cooldown}}
        if path.endswith("upload-image-file"):
            return {"media_id": SOURCE_ID, "project_id": PROJECT}
        if path.endswith("edit-image"):
            if self.edit_error:
                raise self.edit_error
            return {"complete": True, "media": [{"name": OUTPUT_ID, "image": {"generatedImage": {"fifeUrl": "https://flow-content.google/image/test"}}}]}
        if path.endswith(OUTPUT_ID):
            return {"image": {"fifeUrl": "https://flow-content.google/image/refreshed"}}
        raise AssertionError(path)

    def _download(self, url):
        if self.download_error:
            error, self.download_error = self.download_error, None
            raise error
        return b"jpeg-bytes"

    @staticmethod
    def _png(data, evidence):
        (evidence / "result.png").write_bytes(PNG)
        return PNG, 768, 1376


class FlowKitClientTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.source = self.root / "R1-写实参考.png"
        self.source.write_bytes(PNG)
        self.evidence = self.root / "evidence"
        self.client = FakeClient()
        self.client.preflight()

    def test_single_reference_upload_and_fixed_edit_contract(self):
        self.assertEqual(self.client.generate(self.source, "pencil-test", self.evidence), PNG)
        uploads = [c for c in self.client.calls if c[0].endswith("upload-image-file")]
        self.assertEqual(len(uploads), 1)
        raw = uploads[0][2]["raw"]
        self.assertEqual(raw.count(b'name="file"'), 1)
        self.assertIn(PNG, raw)
        self.assertIn(PROJECT.encode(), raw)
        edits = [c for c in self.client.calls if c[0].endswith("edit-image")]
        self.assertEqual(len(edits), 1)
        body = edits[0][1]
        self.assertEqual((body['image_model'], body['aspect_ratio'], body['count']), ('NARWHAL', '9:16', 1))
        self.assertEqual(body['source_media_id'], SOURCE_ID)
        self.assertEqual(body['prompt'], PENCIL_PROMPT)
        self.assertNotIn('reference_media_ids', body)
        self.client.generate(self.source, "pencil-test", self.evidence)
        self.assertEqual(len([c for c in self.client.calls if c[0].endswith('edit-image')]), 1)

    def test_unknown_submission_is_not_repeated(self):
        self.client.edit_error = TimeoutError('submission outcome unknown')
        with self.assertRaises(TimeoutError):
            self.client.generate(self.source, 'pencil-test', self.evidence)
        with self.assertRaisesRegex(RuntimeError, 'reconciliation'):
            self.client.generate(self.source, 'pencil-test', self.evidence)
        self.assertEqual(len([c for c in self.client.calls if c[0].endswith('edit-image')]), 1)

    def test_download_recovery_does_not_regenerate(self):
        self.client.download_error = OSError('network offline')
        with self.assertRaises(OSError):
            self.client.generate(self.source, 'pencil-test', self.evidence)
        self.assertEqual(self.client.generate(self.source, 'pencil-test', self.evidence), PNG)
        self.assertEqual(len([c for c in self.client.calls if c[0].endswith('edit-image')]), 1)

    def test_expired_url_uses_media_lookup(self):
        self.client.download_error = urllib.error.HTTPError('url', 403, 'expired', {}, io.BytesIO())
        self.assertEqual(self.client.generate(self.source, 'pencil-test', self.evidence), PNG)
        self.assertTrue(any(c[0].endswith(OUTPUT_ID) for c in self.client.calls))

    def test_changed_project_stops_before_upload(self):
        self.client.remote_project = '33333333-3333-4333-8333-333333333333'
        with self.assertRaisesRegex(RuntimeError, 'project changed'):
            self.client.generate(self.source, 'pencil-test', self.evidence)
        self.assertFalse(any(c[0].endswith('upload-image-file') for c in self.client.calls))

    def test_disconnected_extension_and_wrong_model_rejected(self):
        self.client.connected = False
        with self.assertRaisesRegex(RuntimeError, 'not connected'):
            self.client.preflight()
        with self.assertRaises(ValueError):
            FlowKitImageClient('http://localhost', model='GEM_PIX_2')

    def test_changed_source_not_reused(self):
        self.client.generate(self.source, 'pencil-test', self.evidence)
        self.source.write_bytes(PNG + b'changed')
        with self.assertRaisesRegex(RuntimeError, 'identity changed'):
            self.client.generate(self.source, 'pencil-test', self.evidence)

    def test_server_cooldown_stops_before_submission(self):
        self.client.cooldown = True
        with self.assertRaisesRegex(RuntimeError, 'cooldown'):
            self.client.generate(self.source, 'pencil-test', self.evidence)
        self.assertFalse(any(c[0].endswith('upload-image-file') for c in self.client.calls))


class UnifiedRoutingTests(unittest.TestCase):
    def run_plan(self, provider, enabled):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source = root / 'R1.png'; source.write_bytes(PNG)
            plan = root / 'tasks.json'
            output_name = 'R1-彩铅参考图.png' if provider is None else 'R1-pencil.png'
            plan.write_text(json.dumps({'output_dir':str(root/'out'), 'tasks':[{'id':'R1P','file':output_name,'provider':provider,'refs':[str(source)],'size':'9:16'}]}), encoding='utf-8')
            config = {'providers': {'flowkit_banana2': {'base_url':'http://127.0.0.1:8100','model':'NARWHAL','aspect_ratio':'9:16','image_size':'1K','prompt':PENCIL_PROMPT,'concurrency':1}}}
            actual_open = open
            calls=[]
            def fake_open(file,*args,**kwargs):
                if str(file).endswith('生产开关.json'):return io.StringIO(json.dumps({'colored_pencil_enabled':enabled}))
                if str(file).endswith('api配置.json'):return io.StringIO(json.dumps(config))
                if str(file).endswith('setting.toml'):raise AssertionError('Old Flow2API credentials must not be read')
                return actual_open(file,*args,**kwargs)
            class Client:
                def __init__(self,base,**kwargs):calls.append(('init',base,kwargs))
                def preflight(self,expected=None):calls.append(('preflight',expected))
                def generate(self,*args):calls.append(('generate',args[0]));return PNG
            module=types.ModuleType('flowkit_image_client');module.FlowKitImageClient=Client
            with patch('builtins.open',side_effect=fake_open),patch.dict(sys.modules,{'flowkit_image_client':module}),patch.object(sys,'argv',['gen_grids_multi.py',str(plan),'0']):
                try:runpy.run_path(str(Path(__file__).with_name('gen_grids_multi.py')),run_name='__main__')
                except SystemExit as exc:self.assertEqual(exc.code,0)
            return calls,(root/'out'/output_name).exists()

    def test_new_and_legacy_provider_both_use_flowkit(self):
        for provider in ['flowkit_banana2','flow2api_banana2',None]:
            with self.subTest(provider=provider):
                calls,exists=self.run_plan(provider,True)
                self.assertTrue(exists)
                self.assertEqual(calls[0][1],'http://127.0.0.1:8100')
                self.assertEqual(sum(c[0]=='generate' for c in calls),1)

    def test_paused_pencil_does_not_probe_or_submit(self):
        for provider in ['flowkit_banana2','flow2api_banana2',None]:
            calls,exists=self.run_plan(provider,False)
            self.assertEqual(calls,[])
            self.assertFalse(exists)


if __name__ == '__main__':
    unittest.main(verbosity=2)
