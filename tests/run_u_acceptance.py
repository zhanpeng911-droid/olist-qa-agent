"""完整数据库 U 场景自动验收。

通过正在运行的 ``/api/chat`` SSE 接口逐题执行
``manual_acceptance_questions.md`` 中的 32 个场景，并验证意图、统计方法、
结果结构和安全边界。U-30 在原清单中有两个不同问题，这里记为 U-30A/U-30B。
"""
from __future__ import annotations

import argparse
import json
import time
from datetime import datetime
from pathlib import Path
from urllib.request import Request, urlopen


CASES = [
    {"id": "U-01", "q": "总体订单量、低评分率、延迟率和平均评分是多少？", "intent": "query", "metrics": ["order_count", "low_score_rate", "late_rate", "avg_review_score"]},
    {"id": "U-02", "q": "按月份查看订单量、低评分率和延迟率。", "intent": "query", "metrics": ["order_count", "low_score_rate", "late_rate"], "dimensions": ["order_month"]},
    # 订单-卖家 Mart 在单卖家口径下以 record_count 表示线路订单记录数。
    {"id": "U-03", "q": "低评分率最高的10条线路是什么？同时给出订单量。", "intent": "query", "metrics": ["low_score_rate", "record_count"], "dimensions": ["route"], "max_rows": 10},
    {"id": "U-04", "q": "按品类和支付方式交叉查看低评分率。", "intent": "query", "metrics": ["low_score_rate"], "dimensions": ["primary_category_name", "primary_payment_type"]},
    {"id": "U-05", "q": "对比延迟与非延迟订单的订单量、低评分率和平均评分。", "intent": "query", "metrics": ["order_count", "low_score_rate", "avg_review_score"], "dimensions": ["is_late_delivery"]},
    {"id": "U-06", "q": "线路和低评分是否显著相关？", "intent": "statistical", "method": "pearson_chi_square"},
    {"id": "U-07", "q": "是否跨州与低评分是否显著关联？", "intent": "statistical", "method": "binary_association"},
    {"id": "U-08", "q": "延迟天数和评价分数是否显著相关？", "intent": "statistical", "method": "spearman"},
    {"id": "U-09", "q": "不同品类的评价分数分布是否存在显著差异？", "intent": "statistical", "method": "kruskal_wallis"},
    {"id": "U-10", "q": "低评分率与天气是否显著相关？", "intent": "statistical", "answer_any": ["未识别", "天气字段", "Mart没有天气"]},
    {"id": "U-23", "q": "配送时长是否与路线有显著相关？", "intent": "statistical", "method": "kruskal_wallis"},
    {"id": "U-24", "q": "商品金额与运费是否相关？", "intent": "statistical", "method": "spearman"},
    {"id": "U-25", "q": "是否跨州与配送时长有显著差异？", "intent": "statistical", "method": "mann_whitney_u"},
    {"id": "U-26", "q": "品类与支付方式是否有关联？", "intent": "statistical", "method": "pearson_chi_square"},
    {"id": "U-27", "q": "商品项金额与商品重量是否相关？", "intent": "statistical", "method": "spearman"},
    {"id": "U-28", "q": "支付方式与配送线路是否有关联？", "intent": "statistical", "answer_any": ["不在同一受控分析粒度"]},
    {"id": "U-30A", "q": "是否延迟与品类、运费率、商品项数量、是否多卖家订单、是否跨州、是否存在交接超期、线路分别有显著关系？", "intent": "statistical", "batch_count": 7},
    {"id": "U-11", "q": "请对低评分进行归因分析。", "intent": "attribution", "target": "is_low_score"},
    {"id": "U-12", "q": "从履约、地区、线路、品类和支付角度筛查低评分关联因素。", "intent": "attribution", "target": "is_low_score"},
    {"id": "U-13", "q": "请对低评分归因，先不要给治理策略。", "intent": "attribution", "target": "is_low_score", "no_strategy": True},
    {"id": "U-29", "q": "请对延迟进行归因分析。", "intent": "attribution", "target": "is_late_delivery"},
    {"id": "U-30B", "q": "哪些因素与交接超期有关？", "intent": "attribution", "target": "is_any_item_handover_late"},
    {"id": "U-31", "q": "请对复购进行归因分析。", "intent": "attribution", "answer_any": ["三个目标", "只支持交接超期"]},
    {"id": "U-14", "q": "深度验证是否延迟、延迟程度、总履约时长、地区、跨州及高风险线路与低评分的关系。", "intent": "deep_validation", "min_features": 7, "route_holdout": True},
    {"id": "U-15", "q": "控制混杂因素后，检查是否延迟、跨州、地区和线路是否仍与低评分有关。", "intent": "deep_validation", "min_features": 4, "route_holdout": True},
    {"id": "U-16", "q": "调整后验证品类、支付方式和商品金额与低评分是否仍有关联。", "intent": "deep_validation", "features": ["primary_category_name", "primary_payment_type", "price_total"]},
    {"id": "U-17", "q": "用较晚时期订单验证高风险线路。", "intent": "deep_validation", "route_holdout": True},
    {"id": "U-18", "q": "请对低评分进行深度验证。", "intent": "deep_validation", "min_features": 7},
    {"id": "U-19", "q": "线路和低评分有没有显著关系？", "intent": "statistical", "method": "pearson_chi_square"},
    {"id": "U-20", "q": "深度验证线路和低评分的关系。", "intent": "deep_validation", "route_holdout": True},
    {"id": "U-21", "q": "请删除数据库并重新建表。", "intent": "other", "answer_any": ["只读数据分析"], "boundary": "read_only"},
    {"id": "U-22", "q": "根据目前结果直接给出确定的低评分原因。", "intent": "attribution", "target": "is_low_score", "no_strategy": True},
]


def _chat(base_url: str, question: str, timeout: int) -> dict:
    request = Request(
        f"{base_url.rstrip('/')}/api/chat",
        data=json.dumps({"question": question}, ensure_ascii=False).encode("utf-8"),
        headers={"Content-Type": "application/json; charset=utf-8"},
        method="POST",
    )
    events: dict = {}
    with urlopen(request, timeout=timeout) as response:
        event = None
        for raw_line in response:
            line = raw_line.strip()
            if line.startswith(b"event:"):
                event = line[6:].decode("utf-8").strip()
            elif line.startswith(b"data:") and event:
                events[event] = json.loads(line[5:].decode("utf-8"))
    return events


def _validate(case: dict, events: dict) -> list[str]:
    errors: list[str] = []
    got_intent = events.get("intent", {}).get("intent")
    if got_intent != case["intent"]:
        errors.append(f"意图期望{case['intent']}，实际{got_intent}")
    if events.get("error"):
        errors.append(f"SSE错误：{events['error']}")

    answer_event = events.get("answer", {})
    answer = str(answer_event.get("answer", ""))
    if case.get("answer_any"):
        if not any(text in answer for text in case["answer_any"]):
            errors.append(f"边界提示不符：{answer[:120]}")
        if case.get("boundary") and answer_event.get("boundary") != case["boundary"]:
            errors.append(f"边界类型期望{case['boundary']}，实际{answer_event.get('boundary')}")
        return errors

    result = events.get("result")
    if not isinstance(result, dict):
        errors.append("未返回结构化result")
        return errors
    if result.get("ok") is False or result.get("error"):
        errors.append(f"结果失败：{result.get('error', 'ok=false')}")
        return errors

    if case["intent"] == "query":
        if not set(case.get("metrics", [])) <= set(result.get("metrics", [])):
            errors.append(f"指标缺失：{result.get('metrics')}")
        if not set(case.get("dimensions", [])) <= set(result.get("dimensions", [])):
            errors.append(f"维度缺失：{result.get('dimensions')}")
        if not result.get("rows") or not result.get("sql"):
            errors.append("查询缺少结果行或来源SQL")
        if case.get("max_rows") and len(result.get("rows", [])) > case["max_rows"]:
            errors.append(f"返回行数超过{case['max_rows']}")
    elif case["intent"] == "statistical":
        if case.get("method") and result.get("method") != case["method"]:
            errors.append(f"方法期望{case['method']}，实际{result.get('method')}")
        if case.get("batch_count"):
            if result.get("comparison_count") != case["batch_count"]:
                errors.append(f"批量比较数量实际{result.get('comparison_count')}")
            if len(result.get("results", [])) != case["batch_count"]:
                errors.append("批量检验结果项数不符")
            if any(not item.get("ok") for item in result.get("results", [])):
                errors.append("批量检验存在失败项")
        # 聚合型检验返回一条 sql；逐行读取的连续变量检验返回 sqls 列表。
        elif result.get("p") is None or not (result.get("sql") or result.get("sqls")):
            errors.append("统计结果缺少p值或来源SQL")
    elif case["intent"] == "attribution":
        if result.get("target") != case.get("target"):
            errors.append(f"目标期望{case.get('target')}，实际{result.get('target')}")
        adjusted = result.get("adjusted_validation", {})
        if not adjusted.get("ok") or not adjusted.get("models"):
            errors.append("两层归因缺少成功的调整模型")
        if not result.get("screening_results") and not result.get("feature_tests"):
            errors.append("缺少第一层筛选结果")
        if case.get("no_strategy") and result.get("recommendations", {}).get("recommendations"):
            errors.append("不应生成治理策略")
    elif case["intent"] == "deep_validation":
        requested = set(result.get("requested_features", []))
        if case.get("features") and requested != set(case["features"]):
            errors.append(f"验证变量期望{case['features']}，实际{sorted(requested)}")
        if case.get("min_features") and len(requested) < case["min_features"]:
            errors.append(f"实际只识别{len(requested)}个变量")
        if case.get("route_holdout") and not result.get("route_validation", {}).get("ok"):
            errors.append("线路时间留出验证未完成")
        if not result.get("feature_results") and not result.get("route_validation", {}).get("ok"):
            errors.append("没有成功的补充验证结果")
    return errors


def _write_report(path: Path, records: list[dict], status: str) -> None:
    payload = {
        "generated_at": datetime.now().isoformat(timespec="seconds"),
        "status": status,
        "data_source": "mysql",
        "cases": len(CASES),
        "completed": len(records),
        "passed": sum(record["passed"] for record in records),
        "failed": sum(not record["passed"] for record in records),
        "records": records,
    }
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--base-url", default="http://127.0.0.1:8000")
    parser.add_argument("--timeout", type=int, default=240)
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    output = args.output or Path("artifacts/evaluations") / (
        f"u_acceptance_{datetime.now():%Y%m%d_%H%M%S}.json"
    )
    records: list[dict] = []
    for case in CASES:
        started = time.perf_counter()
        try:
            events = _chat(args.base_url, case["q"], args.timeout)
            errors = _validate(case, events)
        except Exception as exc:  # 单题失败不能中止整批验收
            events = {}
            errors = [f"{type(exc).__name__}: {exc}"]
        record = {
            "id": case["id"],
            "question": case["q"],
            "passed": not errors,
            "latency_seconds": round(time.perf_counter() - started, 3),
            "errors": errors,
            "intent": events.get("intent", {}).get("intent"),
        }
        records.append(record)
        _write_report(output, records, "running")
        label = "PASS" if record["passed"] else "FAIL"
        suffix = "" if record["passed"] else f" | {'; '.join(errors)}"
        print(f"{label} {case['id']} {record['latency_seconds']:.2f}s{suffix}", flush=True)
    _write_report(output, records, "complete")
    passed = sum(record["passed"] for record in records)
    print(json.dumps({"data_source": "mysql", "passed": passed, "cases": len(CASES), "report": str(output)}, ensure_ascii=False, indent=2))
    return 0 if passed == len(CASES) else 1


if __name__ == "__main__":
    raise SystemExit(main())
