"""Confirmed, privacy-preserving exports of local analysis history."""

import csv
import io
from datetime import datetime, timezone

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import Response
from pydantic import BaseModel, Field
from uuid import UUID, uuid4
from openpyxl import Workbook
from app.services.policy_engine import PolicyEngine


router = APIRouter(prefix="/api/v2/exports", tags=["exports"])
_POLICY_ENGINE = PolicyEngine()


class HistoryExportRequest(BaseModel):
    query: str = Field(default="", max_length=120)
    format: str = Field(default="csv", pattern="^(csv|xlsx)$")
    confirm: bool = False
    trace_id: UUID = Field(default_factory=uuid4)
    project_id: str = Field(default="default", min_length=1, max_length=128)


@router.post("/history", response_model=None)
def export_history(payload: HistoryExportRequest, request: Request) -> dict | Response:
    """Export filtered, API-safe summaries only after explicit confirmation."""
    try:
        payload.project_id = _POLICY_ENGINE.require_project_id(payload.project_id)
    except ValueError as error:
        raise HTTPException(422, str(error)) from error
    records = request.app.state.history_store.list(limit=500, query=payload.query, project_id=payload.project_id)
    if not payload.confirm:
        return {
            "status": "confirmation_required",
            "scope_query": payload.query.strip(),
            "record_count": records["count"],
            "message": "确认后才会导出当前筛选范围的历史数据。",
        }

    if payload.format == "xlsx":
        workbook = Workbook()
        sheet = workbook.active
        sheet.title = "history"
        sheet.append(["generated_at", datetime.now(timezone.utc).isoformat(timespec="seconds")])
        sheet.append(["scope_query", payload.query.strip()])
        sheet.append(["record_count", records["count"]])
        sheet.append([])
        sheet.append(["run_id", "source_label", "source_kind", "status", "created_at", "completed_at", "total_pods", "branch_count", "plant_count", "revision", "is_modified"])
        for item in records["items"]:
            sheet.append([item[key] for key in ("run_id", "source_label", "source_kind", "status", "created_at", "completed_at", "total_pods", "branch_count", "plant_count", "revision", "is_modified")])
        output = io.BytesIO()
        workbook.save(output)
        request.app.state.audit_ledger.event(event_type="history_exported", source="rest", trace_id=str(payload.trace_id), status="completed", metadata={"format": "xlsx", "record_count": records["count"]}, project_id=payload.project_id)
        return Response(content=output.getvalue(), media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet", headers={"Content-Disposition": "attachment; filename=phenotype-history.xlsx"})

    output = io.StringIO(newline="")
    writer = csv.writer(output)
    writer.writerow(["generated_at", datetime.now(timezone.utc).isoformat(timespec="seconds")])
    writer.writerow(["scope_query", payload.query.strip()])
    writer.writerow(["record_count", records["count"]])
    writer.writerow([])
    writer.writerow((
        "run_id", "source_label", "source_kind", "status", "created_at", "completed_at",
        "total_pods", "branch_count", "plant_count", "revision", "is_modified",
    ))
    for item in records["items"]:
        writer.writerow((
            item["run_id"], item["source_label"], item["source_kind"], item["status"],
            item["created_at"], item["completed_at"], item["total_pods"],
            item["branch_count"], item["plant_count"], item["revision"], item["is_modified"],
        ))
    request.app.state.audit_ledger.event(event_type="history_exported", source="rest", trace_id=str(payload.trace_id), status="completed", metadata={"format": "csv", "record_count": records["count"]}, project_id=payload.project_id)
    return Response(
        content="\ufeff" + output.getvalue(),
        media_type="text/csv; charset=utf-8",
        headers={"Content-Disposition": "attachment; filename=phenotype-history.csv"},
    )
