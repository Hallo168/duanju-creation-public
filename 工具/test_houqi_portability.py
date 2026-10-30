"""后期分享版资源寻址与隔离草稿回归测试；不访问用户草稿。"""
import copy
import importlib.util
import json
import os
import re
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / '工具/剪映'))
import 环境

def load(name, relative):
    spec = importlib.util.spec_from_file_location(name, ROOT / relative)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module

def draft_data():
    content = json.dumps({'text': '原字幕', 'styles': [{'range': [0, 3]}]}, ensure_ascii=False)
    return {'duration': 30_000_000, 'materials': {'texts': [
        {'id': 'sub', 'type': 'subtitle', 'content': content, 'base_content': content, 'recognize_text': '原识别'},
        {'id': 'title', 'type': 'text', 'content': '保留标题'},
    ], 'audios': []}, 'tracks': [{'id': 'sub-track', 'type': 'text', 'segments': [
        {'id': 'sub-segment', 'material_id': 'sub', 'target_timerange': {'start': 0, 'duration': 2_000_000}},
    ]}]}

def make_draft(base):
    draft = base / '隔离草稿'
    timeline = draft / 'Timelines/main'
    timeline.mkdir(parents=True)
    (draft / 'Timelines/project.json').write_text('{"main_timeline_id":"main"}', encoding='utf-8')
    for path in (draft / 'draft_content.json', timeline / 'draft_content.json'):
        path.write_text(json.dumps(draft_data(), ensure_ascii=False), encoding='utf-8')
    return draft

class HouqiPortabilityTests(unittest.TestCase):
    def test_nested_relative_resources_from_another_directory(self):
        value = {'path': './工具/剪映/模板/字幕-资源/字体.ttf', 'nested': json.dumps({'font': {'path': '工具/剪映/模板/字幕-资源/字体.ttf'}}), 'absolute': (ROOT / 'known.ttf').as_posix()}
        with tempfile.TemporaryDirectory() as directory:
            previous = Path.cwd()
            try:
                os.chdir(directory)
                resolved = 环境.展开模板资源(value)
            finally:
                os.chdir(previous)
        expected = (ROOT / '工具/剪映/模板/字幕-资源/字体.ttf').as_posix()
        self.assertEqual(resolved['path'], expected)
        self.assertEqual(json.loads(resolved['nested'])['font']['path'], expected)
        self.assertEqual(resolved['absolute'], value['absolute'])
        self.assertTrue(value['path'].startswith('./'))

    def test_subtitle_template_keeps_original_timing_and_other_text(self):
        subtitles = load('subtitle_write_test', '工具/字幕回写.py')
        template = json.loads((ROOT / '工具/剪映/模板/字幕样式-156.json').read_text(encoding='utf-8-sig'))
        template = 环境.展开模板资源(template)
        data = draft_data()
        original_time = copy.deepcopy(data['tracks'][0]['segments'][0]['target_timerange'])
        self.assertEqual(subtitles.改一份(data, {0: '修正字幕'}, template), 1)
        material = data['materials']['texts'][0]
        self.assertEqual(json.loads(material['content'])['text'], '修正字幕')
        self.assertEqual(json.loads(material['content'])['styles'][0]['range'], [0, 4])
        self.assertTrue(Path(material['font_path']).is_file())
        self.assertEqual(material['recognize_text'], '原识别')
        self.assertEqual(data['materials']['texts'][1]['content'], '保留标题')
        self.assertEqual(data['tracks'][0]['segments'][0]['target_timerange'], original_time)

    def test_subtitle_count_mismatch_refuses_changes(self):
        subtitles = load('subtitle_count_test', '工具/字幕回写.py')
        data = draft_data()
        original = copy.deepcopy(data)
        with self.assertRaises(SystemExit):
            subtitles.改一份(data, {})
        self.assertEqual(data, original)

    def test_character_card_runs_outside_project_and_keeps_both_documents(self):
        with tempfile.TemporaryDirectory() as directory:
            base = Path(directory)
            draft = make_draft(base)
            plan = base / 'plan.json'
            plan.write_text(json.dumps({'draft': str(draft), 'track_name': '隔离人物卡', 'template': '工具/剪映/模板/人物介绍-142.json', 'characters': [{'name': '测试人物', 'identity': '测试身份', 'start_us': 3_000_000}]}, ensure_ascii=False), encoding='utf-8')
            command = [sys.executable, '-X', 'utf8', str(ROOT / '工具/剪映/写入人物介绍.py'), str(plan)]
            original = (draft / 'draft_content.json').read_bytes()
            subprocess.run(command + ['--dry-run'], cwd=base, capture_output=True, check=True)
            self.assertEqual((draft / 'draft_content.json').read_bytes(), original)
            subprocess.run(command, cwd=base, capture_output=True, check=True)
            for path in 环境.草稿文件(draft):
                data = json.loads(path.read_text(encoding='utf-8'))
                self.assertEqual(data['tracks'][-1]['name'], '隔离人物卡')
                self.assertEqual(data['materials']['texts'][0]['recognize_text'], '原识别')
                for resource in data['materials']['text_templates'][0]['resources']:
                    self.assertTrue(Path(resource['path']).exists(), resource['path'])
                    self.assertTrue(Path(resource['path']).is_absolute())
                dependencies = Path(data['materials']['text_templates'][0]['path']) / 'depends'
                if dependencies.is_file():
                    for resource in re.findall(r'(?m)^\d+\\path=([^\r\n]+)', dependencies.read_text(encoding='utf-8')):
                        self.assertTrue(Path(resource).exists(), resource)
                        self.assertTrue(Path(resource).is_absolute())
                self.assertEqual({json.loads(item['content'])['text'] for item in data['materials']['texts'][-2:]}, {'测试人物', '测试身份'})

    def test_audio_plan_dry_run_and_same_track_replacement(self):
        audio = load('audio_write_test', '工具/剪映/写入音轨.py')
        with tempfile.TemporaryDirectory() as directory:
            draft = make_draft(Path(directory))
            plan = {'track_name': '隔离音效', 'segments': [{'audio': str(ROOT / '配乐库/音效/咚咚.mp3'), 'start': 0, 'end': 1, 'volume': 0.1}]}
            original = (draft / 'draft_content.json').read_bytes()
            self.assertTrue(audio.apply_plan(draft, plan, dry_run=True)['dry_run'])
            self.assertEqual((draft / 'draft_content.json').read_bytes(), original)
            audio.apply_plan(draft, plan)
            audio.apply_plan(draft, plan)
            for path in 环境.草稿文件(draft):
                data = json.loads(path.read_text(encoding='utf-8'))
                self.assertEqual(len([t for t in data['tracks'] if t.get('name') == '隔离音效']), 1)
                self.assertEqual(data['materials']['texts'][0]['recognize_text'], '原识别')

    def test_index_accepts_ffmpeg_and_ffprobe_on_path(self):
        index = load('library_index_test', '工具/配乐/配乐库索引.py')
        with tempfile.TemporaryDirectory() as directory:
            with patch.object(index, 'LIBRARY', Path(directory)), patch.object(index, 'INDEX_JSON', Path(directory) / 'none.json'), patch.object(index, 'FFMPEG', Path('ffmpeg')), patch.object(index, 'FFPROBE', Path('ffprobe')), patch.object(index.shutil, 'which', return_value='available'):
                self.assertEqual(index.scan()['曲目'], [])

    def test_every_indexed_audio_asset_exists(self):
        index = json.loads((ROOT / '配乐库/索引.json').read_text(encoding='utf-8-sig'))
        for track in index['曲目']:
            self.assertTrue((ROOT / '配乐库' / track['文件']).is_file(), track['文件'])

if __name__ == '__main__':
    unittest.main()
