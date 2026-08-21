"""真实 DeepSeek 端到端重复评测。

默认每题运行一次；评估稳定性时使用 --repeat 3。输出完成率、工具选择正确率、
重复一致率与 P50/P95 延迟，不能只看一个“通过率”。
"""
from __future__ import annotations

import argparse
import json
import statistics
import sys
import time
from collections import defaultdict
from datetime import datetime
from pathlib import Path

import yaml
from dotenv import load_dotenv

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
load_dotenv(ROOT / ".env")

from agent_core.attribution import run_attribution  # noqa: E402
from agent_core.data_provider import (  # noqa: E402
    DATABASE_SOURCE_LABEL, SAMPLE_SOURCE_LABEL, DataProvider,
    MySQLProvider, ProjectCsvProvider,
)
from agent_core.deep_validation import analyze_deep_validation  # noqa: E402
from agent_core.intent import Intent  # noqa: E402
from agent_core.llm import DeepSeekLLM  # noqa: E402
from agent_core.loop import ReActLoop  # noqa: E402
from agent_core.semantic import SemanticLayer  # noqa: E402
from agent_core.statistical_analysis import analyze_statistical_question  # noqa: E402


def _failed_record(case: dict, semantic: SemanticLayer, exc: Exception,
                   elapsed: float) -> dict:
    """把单题未预期异常记为失败，而不是终止整批评测。"""
    intent = Intent(semantic).classify(case["question"])
    return {
        "id": case["id"], "question": case["question"],
        "intent": intent, "intent_ok": intent == case["expected_intent"],
        "completed": False, "correct_path": False,
        "used_llm": intent not in {"deep_validation", "statistical", "attribution"},
        "latency_seconds": round(elapsed, 3),
        "signature": ("exception", type(exc).__name__),
        "answer_preview": f"{type(exc).__name__}: {exc}",
        "trace": [{"event": "unhandled_exception",
                   "error_type": type(exc).__name__, "error": str(exc)}],
    }


def _failure_reason(record: dict) -> str:
    if not record.get("intent_ok"):
        return f"意图不符({record.get('intent')})"
    if not record.get("completed"):
        return "未完成有效回答"
    failed_tools = [event for event in record.get("trace", [])
                    if event.get("event") == "tool" and not event.get("ok")]
    if failed_tools:
        event = failed_tools[-1]
        return f"工具失败({event.get('tool')}): {event.get('error', '未知错误')}"
    if not record.get("correct_path"):
        return "工具路径与预期指标/维度不一致"
    return "未知失败"


def _write_checkpoint(output: Path, records: list[dict], total_runs: int) -> None:
    """每题落盘，断网、异常或人工中断后仍可查看已完成结果。"""
    payload = {
        "generated_at": datetime.now().isoformat(timespec="seconds"),
        "status": "running",
        "completed_runs": len(records),
        "planned_runs": total_runs,
        "records": records,
    }
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")


def _signature(trace: list[dict]) -> tuple:
    items = []
    for event in trace:
        if event.get("event") != "tool":
            continue
        args = event.get("args", {})
        if event.get("tool") == "top_n":
            metrics = [args.get("metric")] if args.get("metric") else []
            dimensions = [args.get("dimension")] if args.get("dimension") else []
        else:
            metrics = args.get("metrics", [])
            dimensions = args.get("dimensions", [])
        items.append((
            event.get("tool"), args.get("table"),
            tuple(sorted(metrics)), tuple(sorted(dimensions)),
        ))
    return tuple(items)


def _tool_match(case: dict, trace: list[dict]) -> bool:
    expected_metrics = set(case.get("metrics", []))
    expected_dimensions = set(case.get("dimensions", []))
    for event in trace:
        if event.get("event") != "tool" or not event.get("ok"):
            continue
        args = event.get("args", {})
        if case.get("table") and args.get("table") != case["table"]:
            continue
        if event.get("tool") == "top_n":
            actual_metrics = {args.get("metric")} if args.get("metric") else set()
            actual_dimensions = (
                {args.get("dimension")} if args.get("dimension") else set()
            )
        else:
            actual_metrics = set(args.get("metrics", []))
            actual_dimensions = set(args.get("dimensions", []))
        # seller_state + customer_state 与预生成 route 在卖家表中是等价线路表达。
        if (
            "route" in expected_dimensions
            and args.get("table") == "mart_order_seller_delivery"
            and {"seller_state", "customer_state"} <= actual_dimensions
        ):
            actual_dimensions.add("route")
        if expected_metrics <= actual_metrics and expected_dimensions <= actual_dimensions:
            return True
    return not (case.get("table") or expected_metrics or expected_dimensions)


def run_case(case: dict, semantic: SemanticLayer, provider: DataProvider,
             llm: DeepSeekLLM) -> dict:
    started = time.perf_counter()
    intent = Intent(semantic).classify(case["question"])
    trace: list[dict] = []
    used_llm = False
    if intent == "deep_validation":
        result = analyze_deep_validation(provider, case["question"])
        completed = bool(
            result.get("ok")
            and (
                result.get("feature_results")
                or (result.get("route_validation") or {}).get("ok")
            )
        )
        expected_features = set(case.get("expected_features", []))
        got_features = set(result.get("requested_features", []))
        route_ok = (
            not case.get("expect_route_holdout")
            or bool((result.get("route_validation") or {}).get("ok"))
        )
        correct = completed and expected_features <= got_features and route_ok
        answer = "深度验证完成" if completed else str(result.get("error", ""))
        signature = (
            "deep_validation", tuple(result.get("requested_features", [])),
            result.get("successful_models", 0), route_ok,
        )
    elif intent == "statistical":
        result = analyze_statistical_question(provider, case["question"])
        completed = bool(result.get("ok"))
        method_ok = result.get("method") == case.get("method")
        correct = completed and method_ok
        answer = result.get("conclusion", result.get("error", ""))
        signature = ("statistical", result.get("method"))
    elif intent == "attribution":
        result = run_attribution(provider, semantic, question=case["question"])
        expected_supported = case.get("expect_supported", True)
        if expected_supported:
            completed = bool(
                result.get("ok")
                and result.get("adjusted_validation", {}).get("ok")
            )
            no_strategy = not result.get("recommendations", {}).get(
                "recommendations", []
            )
            adjusted_ok = (
                not case.get("expect_adjusted")
                or bool(result.get("adjusted_validation", {}).get("models"))
            )
            correct = completed and no_strategy and adjusted_ok
            answer = (
                f"{result.get('target_short_label', '目标')}自动两层归因完成"
                if completed else str(result.get("error", ""))
            )
        else:
            completed = bool(result.get("unsupported_target") and result.get("error"))
            correct = completed and not result.get("ok")
            answer = str(result.get("error", ""))
        signature = (
            "attribution", bool(result.get("ok")),
            tuple(row.get("feature") for row in result.get("selected_features", [])),
            tuple(row.get("feature") for row in result.get("adjusted_features", [])),
        )
    else:
        used_llm = True
        result = ReActLoop(llm, provider, semantic).run(case["question"])
        trace = result.get("trace", [])
        answer = result.get("answer", "")
        completed = bool(result.get("ok") and answer.strip())
        correct = completed and _tool_match(case, trace)
        signature = _signature(trace)
    elapsed = time.perf_counter() - started
    return {
        "id": case["id"], "question": case["question"],
        "intent": intent, "intent_ok": intent == case["expected_intent"],
        "completed": completed, "correct_path": correct,
        "used_llm": used_llm,
        "latency_seconds": round(elapsed, 3), "signature": signature,
        "answer_preview": answer[:300], "trace": trace,
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--repeat", type=int, default=1,
                        help="每题重复次数；稳定性评测建议 3")
    parser.add_argument("--max-cases", type=int, default=0,
                        help="只跑前 N 题；0 表示全部")
    parser.add_argument(
        "--case-ids", type=str, default="",
        help="只运行指定题号，逗号分隔，例如 M-21,M-31,M-34",
    )
    parser.add_argument("--output", type=Path)
    parser.add_argument(
        "--source", choices=("sample", "mysql"), default="sample",
        help="评测数据源：sample=演示样本，mysql=完整业务数据库",
    )
    args = parser.parse_args()
    if args.repeat < 1:
        raise SystemExit("--repeat 必须大于等于 1")

    cases = yaml.safe_load(
        (ROOT / "tests" / "model_eval_questions.yml").read_text(encoding="utf-8")
    )["model_eval_questions"]
    if args.case_ids:
        requested = {case_id.strip() for case_id in args.case_ids.split(",") if case_id.strip()}
        known = {case["id"] for case in cases}
        missing = sorted(requested - known)
        if missing:
            raise SystemExit("未知题号：" + ", ".join(missing))
        cases = [case for case in cases if case["id"] in requested]
    if args.max_cases:
        cases = cases[:args.max_cases]

    output = args.output or ROOT / "artifacts" / "evaluations" / (
        datetime.now().strftime("model_eval_%Y%m%d_%H%M%S.json")
    )

    semantic = SemanticLayer()
    if args.source == "mysql":
        provider = MySQLProvider(allow_tables=semantic.allowed_tables())
        inspection = provider.inspect_marts()
        print(
            f"数据源：{DATABASE_SOURCE_LABEL}；"
            + "；".join(
                f"{table}={info['row_count']}行"
                for table, info in inspection["tables"].items()
            ),
            flush=True,
        )
    else:
        provider = ProjectCsvProvider()
        print(f"数据源：{SAMPLE_SOURCE_LABEL}", flush=True)
    try:
        llm = DeepSeekLLM()
    except (ValueError, RuntimeError) as exc:
        provider.close()
        raise SystemExit(f"无法启动模型评测：{exc}") from exc

    records = []
    try:
        for repeat in range(1, args.repeat + 1):
            for case in cases:
                case_started = time.perf_counter()
                try:
                    record = run_case(case, semantic, provider, llm)
                except Exception as exc:
                    record = _failed_record(
                        case, semantic, exc, time.perf_counter() - case_started
                    )
                record["repeat"] = repeat
                records.append(record)
                tag = "PASS" if (record["intent_ok"] and record["correct_path"]) else "FAIL"
                suffix = "" if tag == "PASS" else f" | {_failure_reason(record)}"
                print(
                    f"{tag} {case['id']} run={repeat} "
                    f"{record['latency_seconds']:.2f}s{suffix}",
                    flush=True,
                )
                _write_checkpoint(output, records, len(cases) * args.repeat)
    finally:
        provider.close()

    by_case: dict[str, list[dict]] = defaultdict(list)
    for record in records:
        by_case[record["id"]].append(record)
    stable = {
        case_id: len({json.dumps(r["signature"], ensure_ascii=False)
                      for r in runs}) == 1
        for case_id, runs in by_case.items()
    }
    latencies = [r["latency_seconds"] for r in records]
    sorted_latencies = sorted(latencies)
    p95_index = max(0, int(len(sorted_latencies) * 0.95 + 0.9999) - 1)
    summary = {
        "data_source": args.source,
        "cases": len(cases), "repeat": args.repeat, "runs": len(records),
        "intent_accuracy": sum(r["intent_ok"] for r in records) / len(records),
        "completion_rate": sum(r["completed"] for r in records) / len(records),
        "correct_path_rate": sum(r["correct_path"] for r in records) / len(records),
        "repeat_consistency": sum(stable.values()) / len(stable),
        "latency_p50_seconds": statistics.median(latencies),
        "latency_p95_seconds": sorted_latencies[p95_index],
    }
    llm_records = [record for record in records if record["used_llm"]]
    summary["llm_runs"] = len(llm_records)
    summary["llm_completion_rate"] = (
        sum(record["completed"] for record in llm_records) / len(llm_records)
        if llm_records else None
    )
    summary["llm_correct_path_rate"] = (
        sum(record["correct_path"] for record in llm_records) / len(llm_records)
        if llm_records else None
    )
    report = {"generated_at": datetime.now().isoformat(timespec="seconds"),
              "summary": summary, "stable_by_case": stable, "records": records}
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(summary, ensure_ascii=False, indent=2))
    print(f"报告：{output}")
    return 0 if summary["correct_path_rate"] == 1 else 1


if __name__ == "__main__":
    raise SystemExit(main())
