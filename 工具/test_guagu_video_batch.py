"""Offline request-contract checks. No provider requests or video generation."""

import argparse
import contextlib
import io
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

import guagu_video_batch as guagu


class GuaguResolutionTests(unittest.TestCase):
    def args(self, root, model, resolution):
        return argparse.Namespace(
            model=model, resolution=resolution, base_url=None, story_root=str(root),
            units="1", ledger=str(root / "ledger.jsonl"), prompt_file="prompt.md",
            grid_dir=None, allow_partial_grids=False, duration=None, concurrency=1,
            version="test", output_suffix="-test",
        )

    def test_selected_resolution_reaches_request_and_ledger(self):
        fixture_root = Path(__file__).resolve().parents[1] / "工具/运行垃圾/test-fixtures/guagu"
        fixture_root.mkdir(parents=True, exist_ok=True)
        for model_id, resolution in [("seedance28", "720p"), ("seedance54", "4k"), ("seedance52", "1080p"), ("seedance50", "480p")]:
            with self.subTest(model=model_id), tempfile.TemporaryDirectory(dir=fixture_root) as temporary:
                root = Path(temporary).resolve()
                self.assertEqual(root.parent, fixture_root)
                args = self.args(root, model_id, resolution)
                model = {"model": model_id, "resolution": resolution, "type": "image_to_video", "credit_cost": 0.21}
                session = Mock()
                response = Mock(status_code=202)
                response.json.return_value = {"code": 0, "data": {"id": "offline-only", "status": "queued"}}
                session.post.return_value = response
                with contextlib.ExitStack() as stack:
                    stack.enter_context(patch.object(guagu.requests.Session, "request", side_effect=AssertionError("network forbidden")))
                    stack.enter_context(patch.object(guagu, "load_video_provider_config", return_value=("offline", "https://example.invalid")))
                    stack.enter_context(patch.object(guagu, "session_for", return_value=session))
                    stack.enter_context(patch.object(guagu, "get_with_transient_retry", return_value=[model]))
                    prepare = stack.enter_context(patch.object(guagu, "prepare_units", return_value={1: {"prompt": "逐字台词", "paths": [root / "image.png"], "duration": 15}}))
                    stack.enter_context(patch.object(guagu, "upload_reference", return_value={"url": "asset://offline"}))
                    stack.enter_context(contextlib.redirect_stdout(io.StringIO()))
                    self.assertEqual(guagu.submit(args), 0)
                prepare.assert_called_once()
                self.assertEqual(prepare.call_args.kwargs["resolution"], resolution)
                self.assertEqual(session.post.call_count, 1)
                payload = session.post.call_args.kwargs["json"]
                self.assertEqual(payload["resolution"], resolution)
                self.assertEqual(payload["model"], model_id)
                self.assertEqual(payload["duration"], 15)
                self.assertEqual(payload["prompt"], "逐字台词")
                entry = json.loads(Path(args.ledger).read_text(encoding="utf-8"))
                self.assertEqual(entry["resolution"], resolution)
                self.assertIn(f"-{resolution}-test", entry["request_id"])

    def test_resolution_change_is_rejected_before_submission(self):
        args = argparse.Namespace(model="seedance54", resolution="720p")
        rows = [{"model": "seedance54", "resolution": "4k", "type": "image_to_video"}]
        with patch.object(guagu, "get_with_transient_retry", return_value=rows):
            with self.assertRaisesRegex(RuntimeError, "无法唯一匹配"):
                guagu.require_current_model(args, Mock(), "https://example.invalid")
            args.resolution = None
            with contextlib.redirect_stdout(io.StringIO()):
                self.assertEqual(guagu.require_current_model(args, Mock(), "https://example.invalid")["resolution"], "4k")


if __name__ == "__main__":
    unittest.main()
