import os
import sys
import tempfile
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

import WeWritePublish  # noqa: E402


class ArticleImageResolutionTests(unittest.TestCase):
    def test_local_image_server_url_resolves_for_upload(self):
        with tempfile.TemporaryDirectory() as tmp:
            project_base = Path(tmp)
            image_path = project_base / "image" / "comfyuigen" / "inline.png"
            image_path.parent.mkdir(parents=True)
            image_path.write_bytes(b"png-bytes")

            old_project_base = os.environ.get("PROJECT_BASE_PATH")
            os.environ["PROJECT_BASE_PATH"] = str(project_base)
            try:
                resolved = WeWritePublish._resolve_article_image(
                    "http://localhost:6005/pw=test-key/images/comfyuigen/inline.png",
                    md_dir=project_base / "Plugin" / "WeWriteCore" / "wewrite" / "output",
                )
            finally:
                if old_project_base is None:
                    os.environ.pop("PROJECT_BASE_PATH", None)
                else:
                    os.environ["PROJECT_BASE_PATH"] = old_project_base

            self.assertEqual(resolved, image_path)

    def test_external_http_image_is_left_unchanged(self):
        resolved = WeWritePublish._resolve_article_image(
            "https://example.com/inline.png",
            md_dir=Path.cwd(),
        )

        self.assertIsNone(resolved)


if __name__ == "__main__":
    unittest.main()
