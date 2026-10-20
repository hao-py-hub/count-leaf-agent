"""Safe user-facing messages for failures originating in analysis internals."""

ANALYSIS_FAILURE_MESSAGE = "分析任务失败，请检查图片质量和模型服务配置后重试。"
ANALYSIS_TIMEOUT_MESSAGE = "分析任务超时，请检查图片质量和模型服务后重试。"


def failure_diagnostic(exc: BaseException) -> str:
    """Return a non-sensitive diagnostic category for internal logs."""
    return type(exc).__name__[:80] or "UnknownError"
