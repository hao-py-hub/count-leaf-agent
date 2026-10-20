"""图片资产项目归属的回归测试。"""

from pathlib import Path
from tempfile import TemporaryDirectory
import unittest

from app.services.asset_store import AssetStore


class AssetProjectScopeTests(unittest.TestCase):
    def test_registered_asset_retains_its_project_scope(self) -> None:
        with TemporaryDirectory() as directory:
            image_path = Path(directory) / "plant.jpg"
            image_path.write_bytes(b"image")

            asset = AssetStore().register(image_path, "plant.jpg", project_id="trial-a")

            self.assertEqual(asset.project_id, "trial-a")


if __name__ == "__main__":
    unittest.main()
