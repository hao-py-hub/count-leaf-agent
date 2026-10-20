"""图片上传接口的项目范围测试。"""

from io import BytesIO
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest

from fastapi.testclient import TestClient
from PIL import Image

import app.api.v2.assets as assets_module
from app.main import app
from app.services.asset_store import AssetStore


class AssetUploadRouteTests(unittest.TestCase):
    def setUp(self) -> None:
        self.directory = TemporaryDirectory()
        self.original_directory = assets_module.AGENT_ASSET_DIR
        self.original_store = app.state.agent_asset_store
        assets_module.AGENT_ASSET_DIR = Path(self.directory.name)
        app.state.agent_asset_store = AssetStore()
        image = BytesIO()
        Image.new("RGB", (2, 2), "black").save(image, format="JPEG")
        self.image = image.getvalue()

    def tearDown(self) -> None:
        assets_module.AGENT_ASSET_DIR = self.original_directory
        app.state.agent_asset_store = self.original_store
        self.directory.cleanup()

    def test_upload_returns_registered_project_scope(self) -> None:
        response = TestClient(app).post(
            "/api/v2/assets/upload?project_id=trial-a",
            files={"file": ("plant.jpg", self.image, "image/jpeg")},
        )
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["project_id"], "trial-a")

    def test_upload_rejects_empty_project_before_file_write(self) -> None:
        response = TestClient(app).post(
            "/api/v2/assets/upload?project_id=",
            files={"file": ("plant.jpg", self.image, "image/jpeg")},
        )
        self.assertEqual(response.status_code, 422)
        self.assertEqual(list(Path(self.directory.name).iterdir()), [])

    def test_upload_rejects_whitespace_project_before_file_write(self) -> None:
        response = TestClient(app).post(
            "/api/v2/assets/upload?project_id=%20%20",
            files={"file": ("plant.jpg", self.image, "image/jpeg")},
        )
        self.assertEqual(response.status_code, 422)
        self.assertEqual(list(Path(self.directory.name).iterdir()), [])

    def test_upload_cleans_file_when_asset_registration_fails(self) -> None:
        """资源登记失败不能遗留未受控的上传文件。"""
        class BrokenStore:
            def register(self, *args, **kwargs):
                raise RuntimeError("数据库不可用")

        app.state.agent_asset_store = BrokenStore()
        with TestClient(app, raise_server_exceptions=False) as client:
            response = client.post(
                "/api/v2/assets/upload?project_id=trial-a",
                files={"file": ("plant.jpg", self.image, "image/jpeg")},
            )
        self.assertEqual(response.status_code, 500)
        self.assertEqual(list(Path(self.directory.name).iterdir()), [])


if __name__ == "__main__":
    unittest.main()
