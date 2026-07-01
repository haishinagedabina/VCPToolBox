import os
import sys
import tempfile
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

import bootstrap  # noqa: E402


class MaterializeImageTests(unittest.TestCase):
    def test_relative_image_path_resolves_from_project_base(self):
        with tempfile.TemporaryDirectory() as tmp:
            project_base = Path(tmp)
            image_path = project_base / "image" / "comfyuigen" / "cover.png"
            image_path.parent.mkdir(parents=True)
            image_path.write_bytes(b"png-bytes")

            old_project_base = os.environ.get("PROJECT_BASE_PATH")
            os.environ["PROJECT_BASE_PATH"] = str(project_base)
            try:
                resolved = bootstrap.materialize_image(
                    "image/comfyuigen/cover.png",
                    param_name="cover_image_url",
                )
            finally:
                if old_project_base is None:
                    os.environ.pop("PROJECT_BASE_PATH", None)
                else:
                    os.environ["PROJECT_BASE_PATH"] = old_project_base

            self.assertEqual(resolved, image_path)

    def test_local_image_server_url_resolves_from_project_base(self):
        with tempfile.TemporaryDirectory() as tmp:
            project_base = Path(tmp)
            image_path = project_base / "image" / "comfyuigen" / "cover.png"
            image_path.parent.mkdir(parents=True)
            image_path.write_bytes(b"png-bytes")

            old_project_base = os.environ.get("PROJECT_BASE_PATH")
            os.environ["PROJECT_BASE_PATH"] = str(project_base)
            try:
                resolved = bootstrap.materialize_image(
                    "http://localhost:6005/pw=test-key/images/comfyuigen/cover.png",
                    param_name="cover_image_url",
                )
            finally:
                if old_project_base is None:
                    os.environ.pop("PROJECT_BASE_PATH", None)
                else:
                    os.environ["PROJECT_BASE_PATH"] = old_project_base

            self.assertEqual(resolved, image_path)


if __name__ == "__main__":
    unittest.main()
