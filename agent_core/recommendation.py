"""M4 建议生成：把归因 + 统计验证的"已验证证据"映射为可执行建议。

建议不由 LLM 自由发挥，而是从 config/recommendation_rules.yml 匹配规则，
输出责任方/动作/监控指标/验证方式。只对已验证因素建议，不凭空给。
"""
from __future__ import annotations

from pathlib import Path

import yaml

from .data_provider import DataProvider
from .semantic import SemanticLayer

_RULES_PATH = Path(__file__).resolve().parent.parent / "config" / "recommendation_rules.yml"

# 规则 id -> 触发依据（基于归因/验证输出的判定）
_RULE_MATCH = {
    "delay_1_3d": "delay_bucket=1-3天",      # 延迟 1-3 天风险上升
    "delay_4d_plus": "delay_bucket=4-7天",   # 延迟 4 天以上进入高风险
    "route_high_risk": "route",              # 高规模高风险线路
    "seller_late_handoff": "seller_late",    # 卖家晚交运
    "multi_item_order": "multi_seller",      # 多商品/多卖家订单
    "category_high_lowscore": "category",    # 品类低评分偏高
}


def _load_rules() -> list[dict]:
    with open(_RULES_PATH, encoding="utf-8") as f:
        data = yaml.safe_load(f)
    return data["rules"]


def _evidence_grade(verification: dict) -> str | None:
    return verification.get("evidence", {}).get("grade")


def _has_factor(priorities: list[dict], dimension: str, value_prefix: str) -> bool:
    return any(
        g.get("dimension") == dimension and str(g.get("value", "")).startswith(value_prefix)
        for g in priorities
    )


def recommend_actions(provider: DataProvider, semantic: SemanticLayer,
                      attribution_res: dict | None = None) -> dict:
    """基于归因 + 统计验证的已验证证据生成建议列表。

    只对"强证据/中等证据"的因素给建议；待验证线索标注"暂不建议/待验证"。
    """
    res = attribution_res if attribution_res is not None else None
    if res is None:
        from .attribution import run_attribution  # 延迟导入避免循环
        res = run_attribution(provider, semantic)
    verification = res.get("verification", {})
    if not verification or verification.get("ok") is False:
        return {"ok": False, "error": "缺少统计验证结果，无法生成建议",
                "recommendations": []}

    rules = _load_rules()
    priorities = res.get("priorities", [])
    grade = _evidence_grade(verification)
    late_ev = verification.get("evidence", {})

    recommendations: list[dict] = []
    matched_ids: set[str] = set()

    # 1. 延迟相关（is_late_delivery 有证据时）
    if grade in ("强证据", "中等证据") and late_ev.get("or", 1) > 1:
        if _has_factor(priorities, "delay_bucket", "15天+"):
            matched_ids.add("delay_4d_plus")
        if _has_factor(priorities, "delay_bucket", "1-3天"):
            matched_ids.add("delay_1_3d")
        if not matched_ids and _has_factor(priorities, "delay_bucket", "4-7天"):
            matched_ids.add("delay_4d_plus")

    # 2. 线路风险（route P0/P1）
    top_routes = res.get("routes", {}).get("top_routes", [])
    route_risk = [g for g in top_routes if g.get("priority") in ("P0", "P1")]
    if route_risk:
        matched_ids.add("route_high_risk")

    # 3. 品类（仅当统计验证显著才建议）
    rc_cat = next((t for t in verification.get("single_tests", [])
                   if t.get("factor") == "primary_category_name"), None)
    if rc_cat and rc_cat.get("p_adjusted", 1) < 0.05:
        matched_ids.add("category_high_lowscore")

    # 4. 卖家晚交运 / 多卖家（样例无对应字段，真库触发时补充）
    #    样例数据不触发；预留规则，真库验证后由字段存在性决定

    # 5. 组装建议
    by_id = {r["id"]: r for r in rules}
    for rid in sorted(matched_ids):
        r = by_id[rid]
        recommendations.append({
            "priority": _priority_of(rid, route_risk),
            "factor": _RULE_MATCH.get(rid, rid),
            "responsibility": r["responsibility"],
            "actions": r["actions"],
            "monitor_metrics": r["monitor_metrics"],
            "verify": r["verify"],
            "evidence_grade": grade,
        })

    pending = []
    if grade == "待验证线索":
        pending.append({"factor": "is_late_delivery", "note": "证据不足，暂不建议，待补充数据验证"})

    return {
        "ok": True,
        "recommendations": recommendations,
        "pending_verification": pending,
        "basis": {"evidence_grade": grade, "or": late_ev.get("or")},
        "note": "建议对应已验证证据；未验证/不显著因素不凭空建议；观察性、禁因果。",
    }


def _priority_of(rid: str, route_risk: list[dict]) -> str:
    if rid == "route_high_risk" and route_risk:
        return min(g["priority"] for g in route_risk)  # P0 > P1
    if rid in ("delay_4d_plus", "delay_1_3d"):
        return "P0" if rid == "delay_4d_plus" else "P1"
    if rid == "category_high_lowscore":
        return "P1"
    return "P2"
