"""Shared, explainable automatic-review reason selection."""


def review_reason_details(quality: dict, result: dict) -> tuple[list[str], list[str]]:
    """Return human-readable reasons and stable reason codes for a result."""
    reasons: list[str] = []
    codes: list[str] = []
    if quality.get("recommended_to_retake"):
        reasons.append("图片质量检查提示：" + "；".join(quality.get("issues") or []))
        codes.append("image_quality")
    plants = result.get("plants") or []
    declared_total = result.get("total_pods")
    calculated_total = sum(int(item.get("pod_count", 0)) for item in plants if isinstance(item, dict))
    if isinstance(declared_total, (int, float)) and declared_total != calculated_total:
        reasons.append(f"统计异常：结果总数 {declared_total} 与部件合计 {calculated_total} 不一致")
        codes.append("count_mismatch")
    if any(isinstance(item, dict) and item.get("pod_count_before_vlm") != item.get("pod_count") for item in plants):
        reasons.append("模型复核冲突：VLM 复核改变了候选计数")
        codes.append("model_conflict")
    warnings = (result.get("label_validation") or {}).get("warnings")
    if isinstance(warnings, list) and warnings:
        reasons.append("模型标注异常：检测到多主干或疑似重复的部件框")
        codes.append("model_conflict")
    return reasons, list(dict.fromkeys(codes))
