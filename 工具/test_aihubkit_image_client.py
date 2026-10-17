import base64
import json
from pathlib import Path
import tempfile
import unittest

from aihubkit_image_client import AIHubKitImageClient, MODEL, PENCIL_PROMPT

PNG = b"\x89PNG\r\n\x1a\n" + b"x" * 2048


class FakeClient(AIHubKitImageClient):
    def __init__(self):
        super().__init__('https://apimodel.aihubkit.com', api_key='secret-test-key')
        self.calls = []
        self.error = None
        self.conversion_error = None

    def _request(self, path, payload=None, **kwargs):
        self.calls.append((path, payload))
        if path == '/v1/models':
            return {'data': [{'id': MODEL}]}
        if self.error:
            raise self.error
        return {'candidates': [{'content': {'parts': [
            {'text': 'result'}, {'inlineData': {'mimeType': 'image/png',
             'data': base64.b64encode(PNG).decode('ascii')}}]}}]}

    def _png(self, data, evidence):
        if self.conversion_error:
            raise self.conversion_error
        (evidence / 'result.png').write_bytes(data)
        return data, 768, 1376


class AIHubKitTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.source = self.root / 'R1-写实.png'
        self.source.write_bytes(PNG)
        self.evidence = self.root / 'evidence'
        self.client = FakeClient()

    def test_native_single_reference_request_and_result_reuse(self):
        self.client.preflight()
        self.assertEqual(self.client.generate(self.source, 'test', self.evidence), PNG)
        path, body = self.client.calls[-1]
        self.assertEqual(path, '/v1beta/models/' + MODEL + ':generateContent')
        parts = body['contents'][0]['parts']
        self.assertEqual(len([p for p in parts if 'inlineData' in p]), 1)
        self.assertEqual(base64.b64decode(parts[0]['inlineData']['data']), PNG)
        self.assertEqual(parts[1]['text'], PENCIL_PROMPT)
        self.assertEqual(body['generationConfig']['imageConfig'], {'aspectRatio': '9:16', 'imageSize': '1K'})
        self.client.generate(self.source, 'test', self.evidence)
        self.assertEqual(len([c for c in self.client.calls if c[1]]), 1)
        for file in self.evidence.glob('*.json'):
            self.assertNotIn('secret-test-key', file.read_text(encoding='utf-8'))

    def test_unknown_submission_stops_without_duplicate(self):
        self.client.error = TimeoutError('outcome unknown')
        with self.assertRaises(TimeoutError):
            self.client.generate(self.source, 'test', self.evidence)
        with self.assertRaisesRegex(RuntimeError, 'reconciliation'):
            self.client.generate(self.source, 'test', self.evidence)
        self.assertEqual(len(self.client.calls), 1)

    def test_conversion_failure_recovers_response_without_generation(self):
        self.client.conversion_error = OSError('conversion interrupted')
        with self.assertRaises(OSError):
            self.client.generate(self.source, 'test', self.evidence)
        self.client.conversion_error = None
        self.assertEqual(self.client.generate(self.source, 'test', self.evidence), PNG)
        self.assertEqual(len(self.client.calls), 1)

    def test_no_image_response_is_not_retried(self):
        with self.assertRaisesRegex(RuntimeError, 'exactly one image'):
            self.client._image_bytes({'candidates': [{'content': {'parts': [{'text': 'no image'}]}}]})


if __name__ == '__main__':
    unittest.main(verbosity=2)
