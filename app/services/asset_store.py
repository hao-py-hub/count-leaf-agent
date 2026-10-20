"""Local development asset registry for Agent-submitted image files."""

from dataclasses import dataclass
from pathlib import Path
from threading import RLock
from uuid import UUID, uuid4

from .ports import AssetRepository


@dataclass(frozen=True)
class RegisteredAsset:
    asset_id: UUID
    file_path: Path
    source_label: str
    source_kind: str = "agent_upload"
    project_id: str = "default"


class AssetStore:
    """Keeps Agent asset identifiers separate from filesystem paths."""

    def __init__(self, repository: AssetRepository | None = None):
        self._assets: dict[UUID, RegisteredAsset] = {}
        self._lock = RLock()
        self._repository = repository

    def register(self, file_path: Path, source_label: str, project_id: str = "default") -> RegisteredAsset:
        if not file_path.is_file():
            raise FileNotFoundError("上传图片不存在")
        project_id = project_id.strip()
        if not project_id or len(project_id) > 128:
            raise ValueError("project_id 无效")
        asset = RegisteredAsset(uuid4(), file_path, source_label, project_id=project_id)
        if self._repository:
            self._repository.create(
                asset_id=str(asset.asset_id),
                file_path=asset.file_path,
                content_type="image/upload",
                filename=asset.source_label,
                source_kind=asset.source_kind,
                project_id=asset.project_id,
            )
        with self._lock:
            self._assets[asset.asset_id] = asset
        return asset

    def get(self, asset_id: UUID) -> RegisteredAsset:
        with self._lock:
            asset = self._assets.get(asset_id)
        if asset is None and self._repository:
            record = self._repository.get(str(asset_id))
            if record:
                asset = RegisteredAsset(
                    asset_id=UUID(str(record["asset_id"])),
                    file_path=Path(record["file_path"]),
                    source_label=str(record["filename"]),
                    source_kind=str(record.get("source_kind", "agent_upload")),
                    project_id=str(record.get("project_id", "default")),
                )
        if asset is None:
            raise KeyError("图片资源不存在")
        return asset
