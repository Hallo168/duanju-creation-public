import argparse
import copy
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from PIL import Image
import canvas_video_batch as canvas
import guagu_video_batch as guagu
import yfai_video_batch as yfai
from test_yfai_video_batch import MODEL


class StoryboardContractTests(unittest.TestCase):
    def setUp(self):
        base = Path(__file__).resolve().parent / '运行垃圾/tmp'
        base.mkdir(parents=True, exist_ok=True)
        self.temp = tempfile.TemporaryDirectory(prefix='storyboard-30-', dir=base)
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        (self.root / '资产图').mkdir()
        (self.root / '分镜-30秒').mkdir()
        for i in range(1, 11):
            Image.new('RGB', (8, 8), (i, 0, 0)).save(self.root / f'资产图/R{i}.png')
        self.args = argparse.Namespace(story_root=str(self.root), prompt_file='分镜-30秒/分镜提示词.md', units='1', model='live-model', resolution='720p')

    def put(self, images=9, seconds=30, mode=30):
        mounts = '；'.join(f'R{i}.png' for i in range(1, images + 1))
        references = '，'.join(f'图片{i}' for i in range(1, images + 1))
        self.text = f'---\nmode: {mode}秒\nfenjing_ready: true\n---\n## 单元1（{seconds}s）\n视频挂载：{mounts}\n生成参数：{seconds}秒，9:16\n### 单元提示词\n```\n{references}。甲{{台词逐字保留。}}\n```\n'
        (self.root / self.args.prompt_file).write_text(self.text, encoding='utf-8')

    def test_nine_images_preserve_thirty_seconds_and_order(self):
        self.put()
        prompt, refs, seconds = canvas.unit_inputs(self.root, self.text, 1)
        self.assertEqual(seconds, 30)
        self.assertEqual([p.name for p in refs], [f'R{i}.png' for i in range(1, 10)])
        with patch('builtins.print'):
            prepared = guagu.prepare_units(self.root, [1], self.args.prompt_file, None, False)
        self.assertEqual(prepared[1]['duration'], 30)
        self.assertEqual(prepared[1]['prompt'], prompt)

    def test_ten_images_block_before_upload(self):
        self.put(10)
        with self.assertRaisesRegex(ValueError, '上限为9张'):
            canvas.mount_tokens(canvas.unit_section(self.text, 1), 1)
        with self.assertRaisesRegex(ValueError, '上限为9张'):
            yfai.local_inputs(self.args)

    def test_thirty_mode_rejects_fifteen_parameter_and_preserves_legacy(self):
        self.put(seconds=15)
        with self.assertRaisesRegex(ValueError, '必须固定30秒'):
            guagu.prepare_units(self.root, [1], self.args.prompt_file, None, False)
        self.put(seconds=15, mode=15)
        self.assertEqual(canvas.unit_inputs(self.root, self.text, 1)[2], 15)

    def test_yfai_uses_authored_thirty_and_rejects_unsupported_model(self):
        self.put()
        item = yfai.local_inputs(self.args)['items'][0]
        self.assertEqual(item['duration'], 30)
        urls = [f'https://example.test/{i}.png' for i in range(1, 10)]
        with self.assertRaisesRegex(RuntimeError, '不支持 duration=30'):
            yfai.build_params(MODEL, item, urls)
        model = copy.deepcopy(MODEL)
        model['param_schema']['duration']['enum'] = ['30']
        model['param_schema']['images']['max_items'] = 9
        params = yfai.build_params(model, item, urls)
        self.assertEqual(params['duration'], '30')
        self.assertEqual(params['images'], urls)


if __name__ == '__main__':
    unittest.main()
