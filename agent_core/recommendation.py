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
    "delay_overall": "is_late_delivery",
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

    # 策略门槛：轻量单因素显著只能生成“待深度验证”任务，不能直接转成治理动作。
    logistic = verification.get("logistic", {})
    order_model = logistic.get("order", {})
    if (res.get("analysis_mode") != "deep"
            or logistic.get("enabled") is not True
            or order_model.get("ok") is not True):
        return {
            "ok": True,
            "status": "withheld_pending_deep_validation",
            "recommendations": [],
            "pending_verification": res.get("deep_validation_plan", []),
            "note": "尚未通过深度多变量验证，暂不生成治理策略。",
        }

    rules = _load_rules()
    priorities = res.get("priorities", [])
    grade = _evidence_grade(verification)
    late_ev = verification.get("evidence", {})
    order_terms = order_model.get("terms", [])

    def _term_significant(term: dict) -> bool:
        ci = term.get("ci95") or []
        return bool(
            term.get("p", 1) < 0.05
            and len(ci) == 2
            and not (ci[0] <= 1 <= ci[1])
        )

    late_adjusted = next(
        (term for term in order_terms
         if term.get("term") == "is_late_delivery" and _term_significant(term)),
        None,
    )

    recommendations: list[dict] = []
    matched_ids: set[str] = set()

    # 1. 延迟相关（is_late_delivery 有证据时）
    if (grade in ("强证据", "中等证据")
            and late_ev.get("or", 1) > 1
            and late_adjusted
            and late_adjusted.get("or", 1) > 1):
        if _has_factor(priorities, "delay_bucket", "15天+"):
            matched_ids.add("delay_4d_plus")
        if _has_factor(priorities, "delay_bucket", "1-3天"):
            matched_ids.add("delay_1_3d")
        if not matched_ids and _has_factor(priorities, "delay_bucket", "4-7天"):
            matched_ids.add("delay_4d_plus")
        if not ({"delay_1_3d", "delay_4d_plus"} & matched_ids):
            matched_ids.add("delay_overall")

    # 2. 线路风险仍只完成描述性定位；当前深度模型没有 route 项，禁止提前给线路策略。
    top_routes = res.get("routes", {}).get("top_routes", [])
    route_risk = [g for g in top_routes if g.get("priority") in ("P0", "P1")]

    # 3. 品类必须同时通过轻量联合检验和深度模型中的至少一个品类项。
    rc_cat = next((t for t in verification.get("single_tests", [])
                   if t.get("factor") == "primary_category_name"), None)
    category_adjusted = any(
        str(term.get("term", "")).startswith("C(primary_category_name)")
        and _term_significant(term)
        for term in order_terms
    )
    if (rc_cat and rc_cat.get("p_adjusted", 1) < 0.05
            and category_adjusted):
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
        "status": "generated_after_deep_validation",
        "recommendations": recommendations,
        "pending_verification": pending,
        "basis": {
            "evidence_grade": grade,
            "unadjusted_or": late_ev.get("or"),
            "adjusted_late": late_adjusted,
        },
        "note": (
            "建议只对应已通过多变量模型的特征；线路等仍未深度验证的因素不生成策略；"
            "观察性、禁因果。"
        ),
    }


def _priority_of(rid: str, route_risk: list[dict]) -> str:
    if rid == "route_high_risk" and route_risk:
        return min(g["priority"] for g in route_risk)  # P0 > P1
    if rid in ("delay_4d_plus", "delay_1_3d", "delay_overall"):
        return "P0" if rid == "delay_4d_plus" else "P1"
    if rid == "category_high_lowscore":
        return "P1"
    return "P2"
