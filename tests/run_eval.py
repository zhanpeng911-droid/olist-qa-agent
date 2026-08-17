"""确定性核心评测：逐题执行 eval_questions.yml。

使用项目真实截取 CSV，不依赖 API key、可复现。它验证程序正确性，不冒充大模型稳定性。

用法: uv run python tests/run_eval.py
"""
from __future__ import annotations

import re
import sys
import time
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from agent_core.attribution import run_attribution  # noqa: E402
from agent_core.data_provider import ProjectCsvProvider  # noqa: E402
from agent_core.deep_validation import (  # noqa: E402
    DEFAULT_FEATURES, analyze_deep_validation, extract_deep_features,
)
from agent_core.intent import Intent  # noqa: E402
from agent_core.llm import LLMClient  # noqa: E402
from agent_core.loop import ReActLoop, parse_decision  # noqa: E402
from agent_core.query_analysis import analyze_query_question, plan_query_question  # noqa: E402
from agent_core.semantic import SemanticLayer  # noqa: E402
from agent_core.statistical_analysis import (  # noqa: E402
    analyze_statistical_question, format_statistical_result,
    plan_statistical_question,
)
from agent_core.tools import REVIEW_METRICS, Tools  # noqa: E402

MAX_LIMIT = 10000
CAUSAL_WORDS = ["导致", "造成", "引起了", "就是原因"]
DML_WORDS = ["insert", "update", "delete", "drop", "truncate", "alter"]


def _reconcile(provider, sql: str):
    rows = provider.execute(sql)
    return rows[0] if rows else {}


def _approx(a, b, rel=1e-6):
    if a is None or b is None:
        return a == b
    return abs(a - b) <= rel * max(1.0, abs(a), abs(b))


class Eval:
    def __init__(self):
        self.semantic = SemanticLayer()
        self.provider = ProjectCsvProvider()
        self.tools = Tools(self.provider, self.semantic)
        self.attr = run_attribution(self.provider, self.semantic)
        self.deep_question = (
            "深度验证是否延迟、延迟程度、总履约时长、地区、跨州及高风险线路"
            "与低评分的相关性"
        )
        self.deep = analyze_deep_validation(self.provider, self.deep_question)
        self.questions = yaml.safe_load(
            open(ROOT / "tests" / "eval_questions.yml", encoding="utf-8"))["eval_questions"]

    def close(self):
        self.provider.close()

    # ---- 检查分发 ----
    def check(self, q: dict) -> tuple[bool, str]:
        fn = getattr(self, f"_c_{q['check']}")
        params = dict(q.get("params") or {})
        params.setdefault("question", q.get("question", ""))
        return fn(params)

    def _expected_where(self, table: str, metrics: list[str], valid=True) -> str:
        clauses = []
        filters = self.semantic.get_filters(table)
        if valid and filters.get("valid_sample"):
            clauses.append(filters["valid_sample"])
        if REVIEW_METRICS.intersection(metrics) and filters.get("reviewed_only"):
            clauses.append(filters["reviewed_only"])
        return " WHERE " + " AND ".join(clauses) if clauses else ""

    # L1 数字对账
    def _c_metric_value(self, p):
        r = self.tools.query_mart(p["table"], metrics=[p["metric"]], limit=1)
        if not r["ok"]:
            return False, r["error"]
        got = r["rows"][0][f"_m_{p['metric']}"]
        expr = self.semantic.check_metric(p["table"], p["metric"])
        sql = f"SELECT {expr} AS v FROM {p['table']}"
        sql += self._expected_where(p["table"], [p["metric"]], p.get("valid", True))
        exp = _reconcile(self.provider, sql)["v"]
        return _approx(got, exp), f"工具={got} SQL={exp}"

    def _c_metric_multi(self, p):
        r = self.tools.query_mart(p["table"], metrics=p["metrics"], limit=1)
        if not r["ok"] or not r["rows"]:
            return False, r.get("error", "无结果")
        return all(f"_m_{m}" in r["rows"][0] for m in p["metrics"]), "多指标均返回"

    def _c_top_n(self, p):
        r = self.tools.top_n(p["table"], p["metric"], p["dimension"], p["n"])
        if not r["ok"]:
            return False, r["error"]
        assert_top = len(r["rows"]) == p["n"]
        return assert_top, f"Top{p['n']} 返回 {len(r['rows'])} 行"

    def _c_metric_group(self, p):
        r = self.tools.query_mart(p["table"], metrics=[p["metric"]],
                                  dimensions=[p["dimension"]], limit=10000)
        if not r["ok"]:
            return False, r["error"]
        expr = self.semantic.check_metric(p["table"], p["metric"])
        sql = f"SELECT {p['dimension']}, {expr} AS v FROM {p['table']}"
        sql += self._expected_where(p["table"], [p["metric"]], p.get("valid", True))
        exp = self.provider.execute(sql + f" GROUP BY {p['dimension']} LIMIT 10000")
        got = {row[p["dimension"]]: row[f"_m_{p['metric']}"] for row in r["rows"]}
        exp = {row[p["dimension"]]: row["v"] for row in exp}
        ok = set(got) == set(exp) and all(_approx(got[k], exp[k]) for k in got)
        return ok, f"{len(got)} 组对账"

    def _c_intent(self, p):
        got = Intent(self.semantic).classify(p["question"])
        return got == p["expected"], f"intent={got}"

    def _c_stat_plan(self, p):
        plan = plan_statistical_question(p["question"])
        ok = (plan.get("ok") and plan.get("factor") == p["factor"]
              and plan.get("method") == p["method"])
        return bool(ok), f"factor={plan.get('factor')} method={plan.get('method')}"

    def _c_query_plan(self, p):
        plan = plan_query_question(p["question"], self.semantic)
        ok = (
            plan.get("ok")
            and plan.get("table") == p["table"]
            and set(plan.get("metrics", [])) == set(p.get("metrics", []))
            and plan.get("dimensions", []) == p.get("dimensions", [])
        )
        if "limit" in p:
            ok = ok and plan.get("limit") == p["limit"]
        return bool(ok), str(plan)

    def _c_query_execute(self, p):
        result = analyze_query_question(self.provider, self.semantic, p["question"])
        ok = (
            result.get("ok")
            and result.get("execution_mode") == "deterministic_query"
            and bool(result.get("display_rows"))
            and bool(result.get("sql"))
        )
        return bool(ok), f"rows={result.get('row_count')} metrics={result.get('metrics')}"

    def _c_stat_execute(self, p):
        started = time.perf_counter()
        result = analyze_statistical_question(self.provider, p["question"])
        elapsed = time.perf_counter() - started
        ok = (result.get("ok") and 0 <= result.get("p", -1) <= 1
              and result.get("sample", 0) > 0 and elapsed < p.get("max_seconds", 5))
        return bool(ok), f"method={result.get('method')} p={result.get('p')} time={elapsed:.3f}s"

    def _c_stat_unknown(self, p):
        result = analyze_statistical_question(self.provider, p["question"])
        return not result.get("ok") and bool(result.get("error")), result.get("error", "")

    def _c_stat_method_p_answer(self, p):
        result = analyze_statistical_question(self.provider, p["question"])
        answer = format_statistical_result(result)
        ok = (result.get("ok") and result.get("factor") == "is_late_delivery"
              and "方法：" in answer and "p值：" in answer
              and "效应量：" in answer)
        return bool(ok), " | ".join(answer.splitlines()[0:4])

    # L2 归因结构
    def _c_attribution_structure(self, _p):
        a = self.attr
        need = {"baseline", "factors", "priorities", "routes", "verification",
                "feature_tests", "significant_features", "deep_validation_plan",
                "selected_features", "adjusted_features",
                "adjusted_explanations", "adjusted_validation",
                "control_policy", "recommendations"}
        missing = need - set(a)
        return not missing, f"缺: {missing}" if missing else "结构完整"

    def _c_p0_has_delay(self, _p):
        p0 = [g for g in self.attr["priorities"] if g.get("priority") == "P0"]
        evidence = self.attr.get("verification", {}).get("evidence", {})
        has = evidence.get("factor") == "is_late_delivery"
        return bool(p0) and has, f"P0 数={len(p0)}，关键证据={evidence.get('factor')}"

    def _c_route_structure(self, _p):
        rt = self.attr.get("routes", {})
        need = {"top_routes", "concentration", "route_cross_delay"}
        return need <= set(rt) and bool(rt.get("top_routes")), "route 结构完整"

    def _c_baseline_reviewed_only(self, _p):
        exp = self.provider.execute(
            "SELECT COUNT(*) AS n FROM mart_order_delivery "
            "WHERE is_delivery_analysis_eligible=1 AND has_review_record=1 LIMIT 1"
        )[0]["n"]
        got = self.attr["baseline"]["order"]["sample"]
        return got == exp, f"baseline={got} reviewed={exp}"

    def _c_sql_trace(self, _p):
        sqls = self.attr.get("sqls", [])
        return bool(sqls) and all(s.lstrip().lower().startswith("select") for s in sqls), f"SQL数={len(sqls)}"

    def _c_item_grain(self, _p):
        item = self.attr.get("item_drilldown", {})
        rows = item.get("by_category", [])
        ok = item.get("ok") and rows and all(
            r.get("_m_low_score_orders", 0) <= r.get("_m_distinct_orders", 0)
            for r in rows
        )
        ok = ok and "COUNT(DISTINCT order_id)" in item.get("grain_note", "")
        return bool(ok), "低评分订单≤去重订单，且口径声明按订单去重"

    def _c_item_significance(self, _p):
        detail = (
            self.attr.get("item_drilldown", {}).get("significance", {})
            .get("category", {})
        )
        rows = detail.get("all_tests", [])
        ok = (detail.get("ok") and rows and "FDR-BH" in detail.get("method", "")
              and "order_id" in detail.get("grain_note", "")
              and all(0 <= row.get("p_adjusted", -1) <= 1 for row in rows))
        return bool(ok), f"检验品类={len(rows)} 显著高风险={len(detail.get('significant_risk', []))}"

    # M3 统计
    def _c_evidence_strong(self, _p):
        ev = self.attr["verification"].get("evidence", {})
        return ev.get("grade") == "通过第一层", f"grade={ev.get('grade')}"

    def _c_evidence_or_gt_1(self, _p):
        ev = self.attr["verification"].get("evidence", {})
        return ev.get("or", 0) > 1 and ev.get("p", 1) < 0.05, f"OR={ev.get('or')}"

    def _c_logistic_safe(self, _p):
        verification = self.attr["verification"]
        logistic = verification.get("logistic", {})
        lo = logistic.get("order", {})
        if logistic.get("enabled") is not True:
            return False, "自动Logistic未启用"
        if lo.get("ok") is False:
            return False, lo.get("error", "订单级模型失败")
        late = next((t for t in lo.get("terms", [])
                     if t.get("term") == "is_late_delivery"), None)
        if not late:
            return False, "无 is_late_delivery 项"
        extracts = verification.get("load_profile", {}).get("extracts", [])
        bounded = extracts and max(row.get("columns", 999) for row in extracts) <= 12
        ok = (late["or"] > 0 and late["ci95"][0] > 0
              and lo.get("robust") == "HC3" and bounded)
        return ok, f"CI={late['ci95']} OR={late['or']} extracts={extracts}"

    # 归因输出边界：自动完成调整模型，但不生成治理策略
    def _c_light_no_strategy(self, _p):
        recs = self.attr.get("recommendations", {}).get("recommendations", [])
        status = self.attr.get("recommendations", {}).get("status")
        ok = not recs and status == "disabled_evidence_only"
        return ok, f"status={status} 策略数={len(recs)}"

    def _c_validation_candidates(self, _p):
        rows = self.attr.get("selected_features", [])
        has_delay = any(r.get("feature") in {"is_late_delivery", "delay_bucket"}
                        for r in rows)
        no_actions = all("actions" not in r and "responsibility" not in r
                         for r in rows)
        return bool(rows) and has_delay and no_actions, f"入模代表变量={len(rows)}"

    def _c_significant_summary(self, _p):
        rows = self.attr.get("significant_features", [])
        ok = bool(rows) and all(r.get("significant") is True for r in rows)
        labels = [r.get("label") for r in rows]
        return ok, f"显著结果={len(rows)} 特征={labels[:5]}"

    def _c_feature_test_details(self, _p):
        rows = self.attr.get("feature_tests", [])
        completed = [row for row in rows if row.get("ok")]
        ok = bool(completed) and all(
            r.get("method") and isinstance(r.get("p"), (int, float))
            and isinstance(r.get("p_adjusted"), float)
            and 0 <= r["p"] <= 1 and 0 <= r["p_adjusted"] <= 1
            and r.get("ci95") and len(r["ci95"]) == 2
            for r in completed
        )
        return ok, f"检验详情={len(rows)}"

    def _c_attribution_only_low_score(self, _p):
        rejected = run_attribution(
            self.provider, self.semantic, question="请对延迟进行归因"
        )
        ok = (not rejected.get("ok") and rejected.get("unsupported_target")
              and "只支持" in rejected.get("error", ""))
        return bool(ok), rejected.get("error", "")

    def _c_collinear_representative(self, _p):
        rows = self.attr.get("selected_features", [])
        groups = [row.get("collinear_group") for row in rows]
        delivery = [row for row in rows
                    if row.get("collinear_group") == "delivery_result"]
        geography = [row for row in rows
                     if row.get("collinear_group") == "shipping_geography"]
        ok = (len(groups) == len(set(groups))
              and (not delivery or delivery[0].get("feature") == "is_late_delivery")
              and (not geography or geography[0].get("feature") == "cross_state"))
        return ok, f"代表变量={[row.get('feature') for row in rows]}"

    def _c_adjusted_threshold(self, _p):
        rows = self.attr.get("adjusted_validation", {}).get("results", [])
        valid = [row for row in rows if row.get("ok")]
        ok = bool(valid) and all(
            row.get("stable")
            == (row.get("p_adjusted", 1) < 0.05 and bool(row.get("ci_passed")))
            for row in valid
        )
        return ok, f"调整结果={len(rows)} 稳定={len(self.attr.get('adjusted_features', []))}"

    def _c_stable_explanations(self, _p):
        stable = {row.get("feature") for row in self.attr.get("adjusted_features", [])}
        explained = {
            row.get("feature") for row in self.attr.get("adjusted_explanations", [])
        }
        return stable == explained, f"stable={stable} explained={explained}"

    # 独立深度验证：不能退回单变量检验，也不能漏掉用户点名变量
    def _c_deep_all_features(self, _p):
        expected = {
            "is_late_delivery", "late_days", "fulfillment_days",
            "customer_state", "seller_state", "cross_state", "route",
        }
        got = set(self.deep.get("requested_features", []))
        return self.deep.get("ok") and expected <= got, f"features={sorted(got)}"

    def _c_deep_adjusted_models(self, _p):
        rows = self.deep.get("feature_results", [])
        ok = (self.deep.get("successful_models", 0) >= 2 and rows
              and all("Pearson" not in row.get("method", "") for row in rows)
              and any("Logistic" in row.get("method", "")
                      for row in rows if row.get("ok")))
        return bool(ok), f"models={self.deep.get('successful_models')} results={len(rows)}"

    def _c_deep_route_holdout(self, _p):
        route = self.deep.get("route_validation") or {}
        ok = (route.get("ok") and route.get("train_n", 0) > route.get("holdout_n", 0) > 0
              and bool(route.get("cutoff")) and bool(route.get("routes"))
              and all(row.get("stability") for row in route.get("routes", [])))
        return bool(ok), f"cutoff={route.get('cutoff')} routes={len(route.get('routes', []))}"

    def _c_deep_bounded_load(self, _p):
        sqls = self.deep.get("sqls", [])
        extracts = self.deep.get("load_profile", {}).get("extracts", [])
        ok = (len(sqls) == 2 and all("limit" in sql.lower() for sql in sqls)
              and extracts and max(row.get("columns", 999) for row in extracts) <= 9)
        return bool(ok), f"sqls={len(sqls)} extracts={extracts}"

    def _c_deep_default_features(self, p):
        got = extract_deep_features(p["question"])
        return got == DEFAULT_FEATURES, f"features={got}"

    def _c_deep_subset_features(self, p):
        got = extract_deep_features(p["question"])
        expected = p["expected_features"]
        return got == expected, f"features={got}"

    def _c_deep_reports_inference(self, _p):
        rows = [row for row in self.deep.get("feature_results", []) if row.get("ok")]
        direct = [row for row in rows if isinstance(row.get("adjusted_or"), (int, float))]
        ok = (rows and direct
              and all(isinstance(row.get("p_adjusted"), float) for row in rows)
              and all(row.get("ci95") and len(row["ci95"]) == 2 for row in direct))
        return bool(ok), f"results={len(rows)} direct_or={len(direct)}"

    def _c_deep_no_strategy(self, _p):
        forbidden = {"recommendations", "actions", "responsibility"}
        keys = set(self.deep)
        text = str(self.deep.get("summary", {}))
        ok = not (forbidden & keys) and all(word not in text for word in ("治理动作", "责任方"))
        return ok, "深度验证只报告调整证据，不自动输出治理策略"

    # 安全
    def _c_sec_no_dml(self, _p):
        bad = [w for w in DML_WORDS if re.search(rf"\b{w}\b", "select", re.I)]
        # 工具层 SQL 模板只含 SELECT
        sql = self.tools.query_mart("mart_order_delivery",
                                    metrics=["low_score_rate"])["sql"].lower()
        return not any(re.search(rf"\b{w}\b", sql) for w in DML_WORDS), "仅 SELECT"

    def _c_sec_no_join(self, _p):
        sql = self.tools.query_mart("mart_order_delivery",
                                    metrics=["low_score_rate"])["sql"].lower()
        return "join" not in sql, "SQL 无 JOIN"

    def _c_sec_unknown_metric(self, _p):
        r = self.tools.query_mart("mart_order_delivery", metrics=["not_a_metric"])
        return not r["ok"], "未知指标被拒"

    def _c_sec_unknown_table(self, _p):
        r = self.tools.query_mart("raw_orders", metrics=["low_score_rate"])
        return not r["ok"], "未知表被拒"

    def _c_sec_limit(self, _p):
        r = self.tools.query_mart("mart_order_delivery", metrics=["low_score_rate"],
                                  limit=99999999)
        m = re.search(r"limit\s+(\d+)", r["sql"].lower())
        return m is not None and int(m.group(1)) <= MAX_LIMIT, f"LIMIT={m.group(1) if m else None}"

    def _c_sec_unknown_dimension(self, _p):
        r = self.tools.query_mart("mart_order_delivery", metrics=["low_score_rate"],
                                  dimensions=["not_a_dimension"])
        return not r["ok"], r.get("error", "")

    def _c_sec_bad_order_by(self, _p):
        r = self.tools.query_mart("mart_order_delivery", metrics=["late_rate"],
                                  order_by="low_score_rate")
        return not r["ok"], r.get("error", "")

    def _c_project_csv_integrity(self, _p):
        physical = {k: v for k, v in self.provider.row_counts.items()
                    if k != "mart_order_item_analysis"}
        expected = {"mart_order_delivery", "mart_order_item_delivery",
                    "mart_order_seller_delivery"}
        return set(physical) == expected and all(v > 0 for v in physical.values()), str(physical)

    def _c_item_view_exists(self, _p):
        n = self.provider.execute("SELECT COUNT(*) AS n FROM mart_order_item_analysis LIMIT 1")[0]["n"]
        return n > 0, f"item_analysis={n}"

    def _c_json_parser(self, _p):
        a = parse_decision('```json\n{"action":"answer","content":"完成"}\n```')
        b = parse_decision('说明：{"action":"answer","content":"完成"}。')
        return a.get("content") == b.get("content") == "完成", "围栏/前缀 JSON 可解析"

    def _c_nonblank_fallback(self, _p):
        class EmptyLLM(LLMClient):
            def chat(self, messages):
                return '{"action":"answer","content":""}'
        r = ReActLoop(EmptyLLM(), self.provider, self.semantic, max_steps=2).run("测试")
        return not r["ok"] and bool(r["answer"]), r["answer"]

    def _c_llm_error_fallback(self, _p):
        class FailingLLM(LLMClient):
            def chat(self, messages):
                raise TimeoutError("simulated timeout")
        r = ReActLoop(FailingLLM(), self.provider, self.semantic).run("测试")
        return (not r["ok"] and bool(r["answer"])
                and r["trace"][0].get("event") == "llm_error"), r["answer"]

    # 边界
    def _c_no_causal_words(self, _p):
        text = (self.attr.get("note", "") + " " +
                " ".join(self.attr.get("caveats", []))).lower()
        hit = [w for w in CAUSAL_WORDS if w in text]
        return not hit, f"无因果措辞" + (f" (命中 {hit})" if hit else "")

    def _c_no_text_caveat(self, _p):
        text = " ".join(self.attr.get("caveats", []))
        return "评价正文" in text, "提示无评价正文"

    def _c_min_sample_filter(self, _p):
        mn = self.semantic.guards.get("min_group_sample", 100)
        ok = all(g["sample"] >= mn for g in self.attr["priorities"])
        return ok, f"所有优先级组 sample≥{mn}"


def main() -> int:
    ev = Eval()
    passed = 0
    lines = []
    for q in ev.questions:
        ok, msg = ev.check(q)
        tag = "PASS" if ok else "FAIL"
        lines.append(f"{tag}  {q['id']:<10} [{q['category']}] {q['question']}")
        if not ok:
            lines.append(f"         -> {msg}")
        passed += ok
    total = len(ev.questions)
    print("\n".join(lines))
    print(f"\n==== 评测汇总: {passed}/{total} 通过 ({(passed/total)*100:.0f}%) ====")
    ev.close()
    return 0 if passed == total else 1


if __name__ == "__main__":
    raise SystemExit(main())
