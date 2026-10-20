"""Authorized local asset upload endpoints for the v0.1 Agent workflow."""

import uuid
from pathlib import Path

from fastapi import APIRouter, File, HTTPException, Query, Request, UploadFile

from app.agent.models import AgentAssetResponse
from app.config import AGENT_ASSET_DIR, MAX_IMAGE_PIXELS, MAX_IMAGE_UPLOAD_BYTES
from app.services.policy_engine import PolicyEngine
from app.services.upload_limits import copy_upload_limited, verify_image_upload

router = APIRouter(prefix="/assets", tags=["agent-assets"])
_IMAGE_EXTENSIONS = {".jpg", ".jpeg", ".png", ".bmp", ".tiff"}
_POLICY_ENGINE = PolicyEngine()


@router.post("/upload", response_model=AgentAssetResponse)
async def upload_agent_asset(
    request: Request,
    file: UploadFile = File(...),
    project_id: str = Query("default", min_length=1, max_length=128),
) -> AgentAssetResponse:
    """登记上传图片，并返回 Agent 工具使用的不透明资源标识。"""
    try:
        project_id = _POLICY_ENGINE.require_project_id(project_id)
    except ValueError as error:
        raise HTTPException(422, str(error)) from error
    extension = Path(file.filename or "unknown.jpg").suffix.lower()
    if extension not in _IMAGE_EXTENSIONS:
        raise HTTPException(400, f"Unsupported file type: {extension}")

    destination = AGENT_ASSET_DIR / f"{uuid.uuid4().hex}{extension}"
    try:
        copy_upload_limited(file.file, destination, MAX_IMAGE_UPLOAD_BYTES)
        verify_image_upload(destination, MAX_IMAGE_PIXELS)
    except ValueError as error:
        status_code = 413 if "大小上限" in str(error) else 400
        raise HTTPException(status_code, str(error)) from error

    try:
        asset = request.app.state.agent_asset_store.register(
            destination,
            file.filename or destination.name,
            project_id=project_id,
        )
    except Exception:
        destination.unlink(missing_ok=True)
        raise
    return AgentAssetResponse(
        asset_id=asset.asset_id,
        source_label=asset.source_label,
        project_id=asset.project_id,
    )
