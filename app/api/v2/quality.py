"""Operator-only quality endpoints for the bounded Agent workflow."""

from fastapi import APIRouter

from scripts.evaluate_agent_quality import evaluate


router = APIRouter(prefix="/api/v2/quality", tags=["operator-quality"])


@router.get("/agent-evaluation")
def get_agent_evaluation() -> dict:
    """Run offline golden cases; this is not an Agent business tool."""
    return evaluate()
