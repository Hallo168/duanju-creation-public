import tempfile
import unittest
from pathlib import Path

from dolasd_video_batch import multipart_body, read_references


class DolasdReferenceTests(unittest.TestCase):
    def test_reference_images_are_required_and_keep_mount_order(self):
        with self.assertRaisesRegex(RuntimeError, "requires 1-9"):
            read_references([])
        with tempfile.TemporaryDirectory() as directory:
            first = Path(directory) / "first.png"
            second = Path(directory) / "second.jpg"
            first.write_bytes(b"\x89PNG\r\n\x1a\nfirst-image")
            second.write_bytes(b"\xff\xd8second-image")
            references = read_references([str(first), str(second)])
            body, content_type = multipart_body(
                {"model": "seedance-2.5", "prompt": "reference images", "duration": 30, "size": "720x1280"},
                references,
            )
            self.assertTrue(content_type.startswith("multipart/form-data; boundary="))
            self.assertLess(body.index(b"first-image"), body.index(b"second-image"))
            self.assertIn(b'name="file"; filename="reference-01.png"', body)
            self.assertIn(b'name="file"; filename="reference-02.jpg"', body)


if __name__ == "__main__":
    unittest.main()
