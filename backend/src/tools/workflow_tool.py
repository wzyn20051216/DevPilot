"""! @brief 记录并校验修改前的候选修复方案。"""

from __future__ import annotations

from typing import Any


def record_candidates(
    candidates: list[dict[str, Any]],
    selected_index: int,
    selection_reason: str,
) -> dict[str, Any]:
    """! @brief 校验候选修复位置并记录选择结论。

    @param candidates 候选列表；每项包含 location、rationale 与 risk。
    @param selected_index 选中的零基下标。
    @param selection_reason 基于现有代码/测试证据的选择理由。
    @return 供 Agent 内核记录的结构化候选信息。
    @raise ValueError 候选无效、重复或选择越界时抛出。
    """

    if not 1 <= len(candidates) <= 4:
        raise ValueError("candidates 数量必须在 1 到 4 之间")
    if not 0 <= selected_index < len(candidates):
        raise ValueError("selected_index 超出 candidates 范围")
    normalized: list[dict[str, str]] = []
    locations: set[str] = set()
    for index, candidate in enumerate(candidates):
        location = str(candidate.get("location", "")).strip()
        rationale = str(candidate.get("rationale", "")).strip()
        risk = str(candidate.get("risk", "")).strip()
        if not location or not rationale or not risk:
            raise ValueError(
                f"candidate[{index}] 必须完整填写 location/rationale/risk"
            )
        folded_location = location.casefold()
        if folded_location in locations:
            raise ValueError("候选 location 必须互不相同")
        locations.add(folded_location)
        normalized.append(
            {"location": location, "rationale": rationale, "risk": risk}
        )
    if not selection_reason.strip():
        raise ValueError("selection_reason 不能为空")
    return {
        "recorded": True,
        "candidate_count": len(normalized),
        "selected_index": selected_index,
        "selected": normalized[selected_index],
        "selection_reason": selection_reason.strip(),
        "candidates": normalized,
    }
