"""YFAI protocol and paid-request safety tests. All network calls are fakes."""
import argparse
import copy
import json
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from PIL import Image
import yfai_video_batch as y
from video_provider_catalog import compact_yfai_model, token_for, video_model_rows


MODEL = {"id": "live-video", "category": "video", "available": True, "max_prompt_chars": 30000, "param_schema": {
    "duration": {"type": "enum", "enum": ["5", "15"]},
    "resolution": {"type": "enum", "enum": ["720p"]},
    "aspect_ratio": {"type": "enum", "enum": ["9:16"]},
    "mode": {"type": "enum", "enum": ["reference", "text-to-video"]},
    "quality": {"type": "enum", "enum": ["标准", "快速"], "default": "标准"},
    "images": {"type": "upload", "max_items": 2},
    "count": {"type": "int", "min": 1, "max": 50, "default": 10}}}


class FakeClient:
    base = "https://yf.test"
    def __init__(self):
        self.calls = []
        self.create_error = None
        self.quote_value = 2
        self.task_result = {"id": "task-real-id", "status": "pending"}
        self.model = copy.deepcopy(MODEL)
        self.available = 100

    def clean(self, value):
        return str(value)

    def call(self, name, payload=None, **kwargs):
        self.calls.append((name, copy.deepcopy(payload), kwargs))
        if name == "models":
            return {"models": [self.model]}
        if name == "balance":
            return {"credits": {"available": self.available}}
        if name == "estimate":
            return {k: self.quote_value for k in ("effective_total_rmb", "max_effective_total_rmb", "required_freeze_rmb")}
        if name == "dry_run":
            p = payload["params"]
            return {"would_call_vendor": False, "reference_check": {"present_in_vendor_body": True},
                    "wire": {"vendor": "test", "body": json.dumps({"seconds": p["duration"], "ratio": p["aspect_ratio"], "resolution": p["resolution"], "prompt": p["prompt"], "image_urls": p.get("images", []), "n": 1})}}
        if name == "files":
            index = sum(n == "files" for n, _, _ in self.calls)
            return {"url": f"https://cdn.test/ref{index}.png"}
        if name == "submit":
            if self.create_error:
                raise self.create_error
            return self.task_result
        if name == "detail":
            return self.task_result
        raise AssertionError(name)


class YfaiTests(unittest.TestCase):
    def setUp(self):
        temporary_root = y.ROOT / "工具/运行垃圾/tmp"
        temporary_root.mkdir(parents=True, exist_ok=True)
        self.tmp = tempfile.TemporaryDirectory(prefix="yfai-test-", dir=temporary_root)
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.story = self.root / "story"
        (self.story / "资产图").mkdir(parents=True)
        Image.new("RGB", (16, 16), "red").save(self.story / "资产图/R1.png")
        Image.new("RGB", (16, 16), "blue").save(self.story / "资产图/S1.png")
        self.markdown = '---\nfenjing_ready: true\n---\n## 单元1（测试）\n视频挂载：R1.png；S1.png\n生成参数：15秒，9:16\n### 单元提示词\n```\n图片1为人物，图片2为场景。甲：{台词逐字保留。}\n```\n修复记录：这里不能进入请求\n'
        (self.story / "分镜-15秒").mkdir()
        self.prompt = self.story / "分镜-15秒/分镜提示词.md"
        self.prompt.write_text(self.markdown, encoding="utf-8")
        self.args = argparse.Namespace(story_root=str(self.story), prompt_file="分镜-15秒/分镜提示词.md", model="live-video", units="1", resolution="720p", plan=str(self.root / "plan.json"), ledger=str(self.root / "ledger.jsonl"), task_dir=str(self.root / "tasks"), output_suffix="-yfai-test", timeout=0.01, interval=5)
        self.client = FakeClient()

    def plan(self):
        with patch("builtins.print"):
            return y.make_plan(self.client, self.args)

    def submit(self):
        with patch("builtins.print"):
            y.submit(self.client, self.args)

    def test_external_credentials_raw_and_ambiguous(self):
        key = self.root / "key.txt"
        key.write_text("\ufeffsk-test-token\n", encoding="utf-8")
        p = {"credential_file": str(key), "credential_format": "sk_token"}
        self.assertEqual(token_for(p), "sk-test-token")
        key.write_text("KEY: sk-first\nKEY: sk-second", encoding="utf-8")
        with self.assertRaisesRegex(RuntimeError, "唯一"):
            token_for(p)
        with patch.dict(os.environ, {"YFAI_UNIT_TEST_KEY": "sk-env"}):
            self.assertEqual(token_for({**p, "credential_env_override": "YFAI_UNIT_TEST_KEY"}), "sk-env")

    def test_catalog_category_filters_out_non_video(self):
        self.assertEqual(video_model_rows({"models": [MODEL, {"id": "image", "category": "image"}]}), [MODEL])

    def test_large_catalog_descriptions_do_not_truncate_workbench_json(self):
        row = copy.deepcopy(MODEL)
        row["description"] = "long description " * 100000
        row["param_schema"]["quality"]["options"] = [{"value": "标准", "label": "verbose label " * 100000}]
        result = compact_yfai_model(row)
        self.assertLess(len(json.dumps(result)), 10000)
        self.assertEqual(result["param_schema"]["quality"]["enum"], ["标准", "快速"])

    def test_preflight_is_free_preserves_dialogue_and_exact_enum(self):
        plan = self.plan()
        self.assertNotIn("files", [c[0] for c in self.client.calls])
        self.assertNotIn("submit", [c[0] for c in self.client.calls])
        params = plan["checks"][0]["params"]
        self.assertEqual(params["duration"], "15")
        self.assertEqual(params["quality"], "标准")
        self.assertEqual(params["count"], 1)
        self.assertEqual(params["prompt"], "图片1为人物，图片2为场景。甲：{台词逐字保留。}")

    def test_unsupported_parameters_rejected_without_paid_request(self):
        for change in ({"duration": {"enum": ["5"]}}, {"images": {"max_items": 1}}, {"mode": {"enum": ["first-frame"]}}):
            with self.subTest(change=change):
                self.client.model = copy.deepcopy(MODEL)
                self.client.model["param_schema"].update(change)
                with self.assertRaises(RuntimeError):
                    self.plan()
        self.assertNotIn("submit", [c[0] for c in self.client.calls])

    def test_preflight_blocks_missing_ready_or_mount_number(self):
        for md in (self.markdown.replace("true", "false"), self.markdown.replace("图片2为", "图片3为")):
            self.prompt.write_text(md, encoding="utf-8")
            with self.assertRaises(RuntimeError):
                self.plan()
        self.assertEqual(self.client.calls, [])

    def test_low_balance_no_submission(self):
        self.client.available = 1
        with self.assertRaisesRegex(RuntimeError, "余额不足"):
            self.plan()
        self.assertNotIn("submit", [c[0] for c in self.client.calls])

    def test_submit_flat_payload_order_idempotency_and_double_submit_barrier(self):
        self.plan()
        self.submit()
        sent = next(p for n, p, _ in self.client.calls if n == "submit")
        self.assertNotIn("params", sent)
        self.assertEqual(sent["images"], ["https://cdn.test/ref1.png", "https://cdn.test/ref2.png"])
        self.assertTrue(sent["client_request_id"].startswith("yfai-"))
        self.assertEqual(y.ledger_rows(self.args.ledger)[-1]["task_id"], "task-real-id")
        with self.assertRaisesRegex(RuntimeError, "账本"):
            self.submit()
        self.args.ledger = str(self.root / "another-ledger.jsonl")
        with self.assertRaisesRegex(RuntimeError, "已用于提交"):
            self.submit()
        self.assertEqual(sum(n == "submit" for n, _, _ in self.client.calls), 1)

    def test_timeout_and_missing_id_never_retry_creation(self):
        for mode in ("timeout", "missing_id"):
            with self.subTest(mode=mode):
                self.args.plan = str(self.root / f"plan-{mode}.json")
                self.args.ledger = str(self.root / f"ledger-{mode}.jsonl")
                self.client.create_error = TimeoutError("network lost") if mode == "timeout" else None
                self.client.task_result = {} if mode == "missing_id" else {"id": "real"}
                self.plan()
                count = sum(n == "submit" for n, _, _ in self.client.calls)
                with self.assertRaisesRegex(RuntimeError, "禁止自动重投"):
                    self.submit()
                self.assertEqual(sum(n == "submit" for n, _, _ in self.client.calls), count + 1)
                self.assertEqual(y.ledger_rows(self.args.ledger)[-1]["status"], "unknown_outcome")

    def test_changed_prompt_reference_and_price_block_creation(self):
        self.plan()
        self.prompt.write_text(self.markdown.replace("逐字", "全部"), encoding="utf-8")
        with self.assertRaisesRegex(RuntimeError, "变化"):
            self.submit()
        self.prompt.write_text(self.markdown, encoding="utf-8")
        self.client.quote_value = 5
        with self.assertRaisesRegex(RuntimeError, "费用超过"):
            self.submit()
        self.client.quote_value = 2
        Image.new("RGB", (16, 16), "green").save(self.story / "资产图/R1.png")
        with self.assertRaisesRegex(RuntimeError, "变化"):
            self.submit()
        self.assertNotIn("submit", [c[0] for c in self.client.calls])

    def test_dry_run_rejects_dropped_or_reordered_references(self):
        params = self.plan()["checks"][0]["params"]
        response = self.client.call("dry_run", {"params": params})
        for urls in (params["images"][:1], list(reversed(params["images"]))):
            body = json.loads(response["wire"]["body"])
            body["image_urls"] = urls
            response["wire"]["body"] = json.dumps(body)
            with patch.object(self.client, "call", return_value=response):
                with self.assertRaisesRegex(RuntimeError, "数量或顺序"):
                    y.dry_run(self.client, "live-video", params)

    def test_needs_review_and_downloading_are_not_final(self):
        for data in ({"status": "needs_review", "is_final": False}, {"status": "processing", "stage": "downloading", "is_final": False}, {"status": "completed", "is_final": False, "output_url": "https://cdn.test/a.mp4"}):
            self.assertEqual(y.task_state(data), "processing")
        self.assertEqual(y.task_state({"status": "failed", "is_final": True}), "failed")
        with self.assertRaises(RuntimeError):
            y.task_state({"status": "completed", "is_final": True, "result_url": "https://cdn.test/a.mp4"})

    def test_unsupported_free_dry_run_is_disclosed_but_other_failures_stop(self):
        params = self.plan()["checks"][0]["params"]
        with patch.object(self.client, "call", side_effect=y.ApiError(501, "unsupported", "dry_run_transport_not_instrumented")):
            result = y.dry_run(self.client, "live-video", params)
            self.assertFalse(result["verified"])
            self.assertIn("暂不支持", result["warning"])
        with patch.object(self.client, "call", side_effect=y.ApiError(503, "outage")):
            with self.assertRaises(y.ApiError):
                y.dry_run(self.client, "live-video", params)

    def test_poll_download_only_after_final_and_uses_latest_row(self):
        self.plan()
        self.submit()
        self.client.task_result = {"status": "completed", "is_final": True, "output_url": "https://cdn.test/a.mp4", "actual_cost_micro_usd": 123}
        with patch.object(y, "collect", return_value={"status": "succeeded", "full_decode": True}) as collect, patch("builtins.print"):
            self.assertEqual(y.poll(self.client, self.args), 0)
            collect.assert_called_once()
            self.assertEqual(y.poll(self.client, self.args), 0)
            collect.assert_called_once()
        self.assertEqual(y.ledger_rows(self.args.ledger)[-1]["actual_cost_micro_usd"], 123)

    def test_exclusive_lock_blocks_competing_process(self):
        with y.exclusive(self.args.ledger):
            with self.assertRaisesRegex(RuntimeError, "执行锁"):
                with y.exclusive(self.args.ledger):
                    self.fail("acquired duplicate lock")

    def test_authorization_not_sent_to_download_host(self):
        class Response:
            code = 200
            def __enter__(self): return self
            def __exit__(self, *args): pass
            def read(self, size):
                if getattr(self, "sent", False): return b""
                self.sent = True
                return b"video"
        with patch.object(y.request, "build_opener") as factory:
            factory.return_value.open.return_value = Response()
            y.download("https://cdn.test/result.mp4", self.root / "video.part")
            req = factory.return_value.open.call_args.args[0]
            self.assertIsNone(req.get_header("Authorization"))

    def test_collected_media_is_really_decoded_and_wrong_geometry_is_blocked(self):
        source = self.root / "upstream-fixture.mp4"
        encoded = y.subprocess.run([y.tool("ffmpeg"), "-nostdin", "-v", "error", "-f", "lavfi", "-i", "color=c=black:s=720x1280:r=1:d=15", "-f", "lavfi", "-i", "anullsrc=r=8000:cl=mono", "-t", "15", "-c:v", "libx264", "-preset", "ultrafast", "-c:a", "aac", str(source)], capture_output=True, timeout=40, creationflags=getattr(y.subprocess, "CREATE_NO_WINDOW", 0))
        self.assertEqual(encoded.returncode, 0, encoded.stderr.decode("utf-8", "replace"))
        row = {"unit": 1, "output_suffix": "-yfai-test", "resolution": "720p", "aspect_ratio": "9:16", "duration": 15}
        with patch.object(y, "download", side_effect=lambda url, target: y.shutil.copyfile(source, target)):
            result = y.collect(self.args, row, {"output_url": "https://cdn.test/video.mp4"})
        self.assertTrue(result["full_decode"])
        self.assertEqual((result["width"], result["height"]), (720, 1280))
        self.assertEqual(result["audio_codec"], "aac")
        with self.assertRaisesRegex(RuntimeError, "不符"):
            y.collect(self.args, {**row, "resolution": "480p"}, {"output_url": "https://cdn.test/video.mp4"})


if __name__ == "__main__":
    unittest.main()
