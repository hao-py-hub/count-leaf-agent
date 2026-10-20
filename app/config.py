import os
from pathlib import Path
from dotenv import load_dotenv

load_dotenv()

# Paths
BASE_DIR = Path(__file__).resolve().parent.parent
UPLOAD_DIR = BASE_DIR / "uploads"
RESULT_DIR = BASE_DIR / "results"
ONLINE_UPLOAD_DIR = BASE_DIR / "online_uploads"
CACHE_DIR = BASE_DIR / "benchmark_cache"
AGENT_ASSET_DIR = UPLOAD_DIR / "agent_assets"
DATA_DIR = Path(os.getenv("DATA_DIR", str(BASE_DIR / "data")))
UPLOAD_DIR.mkdir(exist_ok=True)
RESULT_DIR.mkdir(exist_ok=True)
ONLINE_UPLOAD_DIR.mkdir(exist_ok=True)
CACHE_DIR.mkdir(exist_ok=True)
AGENT_ASSET_DIR.mkdir(parents=True, exist_ok=True)
DATA_DIR.mkdir(parents=True, exist_ok=True)
HISTORY_DB_PATH = DATA_DIR / "history.sqlite3"
KNOWLEDGE_DB_PATH = DATA_DIR / "knowledge.sqlite3"
AGENT_AUDIT_DB_PATH = DATA_DIR / "agent_audit.sqlite3"
AUDIT_LEDGER_DB_PATH = DATA_DIR / "audit_ledger.sqlite3"
AGENT_CONVERSATION_DB_PATH = DATA_DIR / "agent_conversations.sqlite3"
REVIEW_DB_PATH = DATA_DIR / "reviews.sqlite3"

# Doubao VLM
ARK_API_KEY = os.getenv("ARK_API_KEY", "")
ARK_API_BASE = os.getenv("ARK_API_BASE", "https://ark.cn-beijing.volces.com/api/v3")
VLM_MODEL = os.getenv("VLM_MODEL", "ep-20260420011113-6jvmx")
ANALYSIS_ALGORITHM_VERSION = os.getenv("ANALYSIS_ALGORITHM_VERSION", "stalk-v0.1")
ANALYSIS_PROMPT_VERSION = os.getenv("ANALYSIS_PROMPT_VERSION", "phenotype-prompt-v1")
VLM_INPUT_COST_PER_MILLION = float(os.getenv("VLM_INPUT_COST_PER_MILLION", "0"))
VLM_OUTPUT_COST_PER_MILLION = float(os.getenv("VLM_OUTPUT_COST_PER_MILLION", "0"))
if VLM_INPUT_COST_PER_MILLION < 0 or VLM_OUTPUT_COST_PER_MILLION < 0:
    raise ValueError("VLM token 费率不能为负数")
ENABLE_VLM_ADHESION_DETECTION = os.getenv("ENABLE_VLM_ADHESION_DETECTION", "false").strip().lower() == "true"

# VLM Pod Verify — 纠错置信度阈值
# P 点剔除、F 点补回都必须达到该阈值；越高越保守，纠错越少。
VLM_FP_CONFIDENCE_THRESHOLD = float(os.getenv("VLM_FP_CONFIDENCE_THRESHOLD", "0.8"))

# VLM 请求选项。关闭思考可避免单次调用出现数百秒的不可控等待；如需保留
# 模型默认行为，可将 VLM_THINKING 设置为 default。
VLM_THINKING = os.getenv("VLM_THINKING", "disabled").strip().lower()
# 显式设置请求超时与重试次数，避免沿用 SDK 过长的默认等待时间。
VLM_TIMEOUT_S = float(os.getenv("VLM_TIMEOUT_S", "120"))
VLM_MAX_RETRIES = int(os.getenv("VLM_MAX_RETRIES", "1"))


def bounded_unit_ratio(value: str | float, name: str) -> float:
    """Parse a positive ratio that cannot exceed its median reference value."""
    try:
        ratio = float(value)
    except (TypeError, ValueError) as exc:
        raise ValueError(f"{name} 必须是数值") from exc
    if not 0 < ratio <= 1:
        raise ValueError(f"{name} 必须在 (0, 1] 区间")
    return ratio


STALK_TWIG_WIDTH_RATIO = bounded_unit_ratio(os.getenv("STALK_TWIG_WIDTH_RATIO", "0.70"), "STALK_TWIG_WIDTH_RATIO")
STALK_TWIG_AREA_RATIO = bounded_unit_ratio(os.getenv("STALK_TWIG_AREA_RATIO", "0.25"), "STALK_TWIG_AREA_RATIO")

# Enterprise infrastructure (inactive unless the enterprise deployment profile is used).
DATABASE_URL = os.getenv("DATABASE_URL", "sqlite:///./data/agent.sqlite3")
REDIS_URL = os.getenv("REDIS_URL", "redis://localhost:6379/0")
CELERY_TASK_BROKER_URL = os.getenv("CELERY_TASK_BROKER_URL", REDIS_URL)
CELERY_RESULT_BACKEND = os.getenv("CELERY_RESULT_BACKEND", REDIS_URL)
JOB_EXECUTOR_MODE = os.getenv("JOB_EXECUTOR_MODE", "in_process")
DAILY_ANALYSIS_JOB_LIMIT = int(os.getenv("DAILY_ANALYSIS_JOB_LIMIT", "100"))
ANALYSIS_JOB_TIMEOUT_SECONDS = int(os.getenv("ANALYSIS_JOB_TIMEOUT_SECONDS", "300"))
if ANALYSIS_JOB_TIMEOUT_SECONDS < 1:
    raise ValueError("ANALYSIS_JOB_TIMEOUT_SECONDS 必须至少为 1")


def cors_allowed_origins(value: str | None = None) -> list[str]:
    """Return a bounded origin allowlist; never combine credentials with '*'."""
    configured = value if value is not None else os.getenv("CORS_ALLOWED_ORIGINS", "")
    origins = [origin.strip().rstrip("/") for origin in configured.split(",") if origin.strip()]
    if "*" in origins:
        raise ValueError("CORS_ALLOWED_ORIGINS 不能包含 '*'，请配置明确的来源地址")
    return origins or ["http://localhost:8501", "http://127.0.0.1:8501"]


CORS_ALLOWED_ORIGINS = cors_allowed_origins()
MAX_IMAGE_UPLOAD_BYTES = int(os.getenv("MAX_IMAGE_UPLOAD_BYTES", str(20 * 1024 * 1024)))
MAX_IMAGE_PIXELS = int(os.getenv("MAX_IMAGE_PIXELS", "40000000"))
MAX_KNOWLEDGE_UPLOAD_BYTES = int(os.getenv("MAX_KNOWLEDGE_UPLOAD_BYTES", str(512 * 1024)))
RAG_RETRIEVAL_MODE = os.getenv("RAG_RETRIEVAL_MODE", "keyword").strip().lower()
if RAG_RETRIEVAL_MODE not in {"keyword", "hybrid"}:
    raise ValueError("RAG_RETRIEVAL_MODE 只能是 keyword 或 hybrid")

# Server
HOST = os.getenv("HOST", "0.0.0.0")
PORT = int(os.getenv("PORT", "8501"))
