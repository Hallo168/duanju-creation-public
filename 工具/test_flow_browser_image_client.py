from io import BytesIO
from pathlib import Path
import tempfile
import unittest
from unittest.mock import Mock

from PIL import Image
from flow_browser_image_client import FlowBrowserImageClient


class ReusableFlowClientTests(unittest.TestCase):
    def client(self):
        client = FlowBrowserImageClient.__new__(FlowBrowserImageClient)
        client.project_id = 'project-sample'
        return client

    def test_full_client_returns_png_with_source_and_generation_evidence(self):
        client = self.client()
        image = BytesIO()
        Image.new('RGB', (768, 1376)).save(image, format='JPEG')
        client.download = Mock(return_value=image.getvalue())
        client.command = Mock(side_effect=[{'result': {'success': True, 'media': {'name': 'source-id'}}}, {'result': {'success': True, 'media': [{'name': 'generated-id', 'url': 'https://flow-content.google/image/generated-id'}]}, 'cached': True}])
        with tempfile.TemporaryDirectory() as directory:
            source = Path(directory) / 'source.png'
            source.write_bytes(b'\x89PNG\r\n\x1a\nsource-fixture')
            evidence = Path(directory) / 'evidence'
            output = client.generate(source, 'operation-sample', evidence)
            self.assertTrue(output.startswith(b'\x89PNG\r\n\x1a\n'))
            self.assertTrue((evidence / 'image-original.bin').is_file())
            self.assertTrue((evidence / 'verification.json').is_file())
            calls = client.command.call_args_list
            self.assertEqual([call.args[0] for call in calls], ['upload_reference', 'generate_colored_pencil'])
            self.assertEqual(calls[1].args[1]['reference_media_id'], 'source-id')
            self.assertEqual(calls[1].args[1]['model_key'], 'NARWHAL')

    def test_failed_generation_is_saved_and_never_silently_retried(self):
        client = self.client()
        client.command = Mock(side_effect=[{'result': {'success': True, 'media': {'name': 'source-id'}}}, {'result': {'success': False, 'code': 'RPC_FAILURE'}}])
        with tempfile.TemporaryDirectory() as directory:
            source = Path(directory) / 'source.png'
            source.write_bytes(b'\x89PNG\r\n\x1a\nsource-fixture')
            evidence = Path(directory) / 'evidence'
            with self.assertRaises(RuntimeError):
                client.generate(source, 'operation-sample', evidence)
            self.assertEqual(client.command.call_count, 2)
            self.assertTrue((evidence / 'generation.json').is_file())

    def test_non_png_reference_is_rejected_before_any_network_action(self):
        client = self.client()
        client.command = Mock()
        with tempfile.TemporaryDirectory() as directory:
            source = Path(directory) / 'source.jpg'
            source.write_bytes(b'not-png')
            with self.assertRaises(RuntimeError):
                client.generate(source, 'operation-sample', Path(directory) / 'evidence')
            client.command.assert_not_called()

    def test_disconnected_browser_does_not_claim_preflight_success(self):
        client = self.client()
        client.settings = {'admin_username': 'local-user', 'admin_password': 'local-test'}
        client.api = Mock(side_effect=[{'token': 'local-auth'}, {'bridge': {'connected_clients': 0, 'versions': []}}])
        with self.assertRaises(RuntimeError):
            client.preflight()

    def test_home_page_preflight_prepares_workspace_automatically(self):
        client = self.client()
        client.settings = {'admin_username': 'local-user', 'admin_password': 'local-test'}
        client.api = Mock(side_effect=[{'token': 'local-auth'}, {'bridge': {'connected_clients': 1, 'versions': ['1.3.2']}}])
        usage = ['NARWHAL'] + [None] * 12 + [[[1, 2, 3]]]
        group = ['Nano Banana 2', [usage], None, 'narwhal']
        config = [None] * 5 + [[group]]
        client.command = Mock(side_effect=[
            {'result': {'success': True, 'data': [config]}},
            {'result': {'success': True, 'project_id': None, 'captcha_ready': False}},
            {'result': {'success': True, 'project_id': 'workspace-sample', 'captcha_ready': True, 'background_prepared': True}},
        ])
        self.assertTrue(client.preflight())
        self.assertEqual(client.project_id, 'workspace-sample')
        self.assertEqual([call.args[0] for call in client.command.call_args_list], ['models', 'project_info', 'prepare_workspace'])


if __name__ == '__main__':
    unittest.main()
