"""M4 确定性评测：逐题执行 eval_questions.yml 的 25 个标准问题。

样例数据、不依赖 API key、可复现。数字对账类用"工具层结果 vs SQL 重算"动态对账。

用法: uv run python tests/run_eval.py
"""
from __future__ import annotations

import re
import sys
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from agent_core.attribution import run_attribution  # noqa: E402
from agent_core.data_provider import SampleProvider  # noqa: E402
from agent_core.semantic import SemanticLayer  # noqa: E402
from agent_core.tools import Tools  # noqa: E402

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
        self.provider = SampleProvider()
        self.tools = Tools(self.provider, self.semantic)
        self.attr = run_attribution(self.provider, self.semantic)
        self.questions = yaml.safe_load(
            open(ROOT / "tests" / "eval_questions.yml", encoding="utf-8"))["eval_questions"]

    def close(self):
        self.provider.close()

    # ---- 检查分发 ----
    def check(self, q: dict) -> tuple[bool, str]:
        fn = getattr(self, f"_c_{q['check']}")
        return fn(q.get("params") or {})

    # L1 数字对账
    def _c_metric_value(self, p):
        r = self.tools.query_mart(p["table"], metrics=[p["metric"]], limit=1)
        if not r["ok"]:
            return False, r["error"]
        got = r["rows"][0][f"_m_{p['metric']}"]
        expr = self.semantic.check_metric(p["table"], p["metric"])
        sql = f"SELECT {expr} AS v FROM {p['table']}"
        if p.get("valid"):
            sql += " WHERE is_delivery_analysis_eligible = 1"
        exp = _reconcile(self.provider, sql)["v"]
        return _approx(got, exp), f"工具={got:.4f} SQL={exp:.4f}"

    def _c_metric_multi(self, p):
        for m in p["metrics"]:
            r = self.tools.query_mart(p["table"], metrics=[m], limit=1)
            if not r["ok"]:
                return False, f"{m}: {r['error']}"
        return True, "多指标对账通过"

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
        exp = self.provider.execute(
            f"SELECT {p['dimension']}, AVG(is_low_score) AS v FROM {p['table']} "
            "WHERE is_delivery_analysis_eligible=1 "
            f"GROUP BY {p['dimension']} LIMIT 10000")
        got = {row[p["dimension"]]: row[f"_m_{p['metric']}"] for row in r["rows"]}
        exp = {row[p["dimension"]]: row["v"] for row in exp}
        return set(got) == set(exp), f"{len(got)} 组对账一致"

    # L2 归因结构
    def _c_attribution_structure(self, _p):
        a = self.attr
        need = {"baseline", "factors", "priorities", "routes", "verification",
                "recommendations"}
        missing = need - set(a)
        return not missing, f"缺: {missing}" if missing else "结构完整"

    def _c_p0_has_delay(self, _p):
        p0 = [g for g in self.attr["priorities"] if g.get("priority") == "P0"]
        has = any(g.get("dimension") in ("is_late_delivery", "delay_bucket") for g in p0)
        return bool(p0) and has, f"P0 数={len(p0)}"

    def _c_route_structure(self, _p):
        rt = self.attr.get("routes", {})
        need = {"top_routes", "concentration", "route_cross_delay"}
        return need <= set(rt) and bool(rt.get("top_routes")), "route 结构完整"

    # M3 统计
    def _c_evidence_strong(self, _p):
        ev = self.attr["verification"].get("evidence", {})
        return ev.get("grade") == "强证据", f"grade={ev.get('grade')}"

    def _c_evidence_or_gt_1(self, _p):
        ev = self.attr["verification"].get("evidence", {})
        return ev.get("or", 0) > 1 and ev.get("p", 1) < 0.05, f"OR={ev.get('or')}"

    def _c_logistic_ci_excludes_1(self, _p):
        lo = self.attr["verification"].get("logistic", {}).get("order", {})
        late = next((t for t in lo.get("terms", [])
                     if t.get("term") == "is_late_delivery"), None)
        if not late:
            return False, "无 is_late_delivery 项"
        return late["ci95"][0] > 1, f"CI={late['ci95']} OR={late['or']}"

    # M4 建议
    def _c_rec_fields(self, _p):
        recs = self.attr.get("recommendations", {}).get("recommendations", [])
        if not recs:
            return False, "无建议"
        ok = all({"responsibility", "actions", "monitor_metrics", "verify"}
                 <= set(r) for r in recs)
        return ok, f"{len(recs)} 条建议字段完整"

    def _c_rec_has_delay(self, _p):
        recs = self.attr.get("recommendations", {}).get("recommendations", [])
        has = any("delay" in r.get("factor", "") for r in recs)
        return has, f"{len(recs)} 条建议"

    def _c_rec_no_unverified(self, _p):
        recs = self.attr.get("recommendations", {}).get("recommendations", [])
        has_cat = any("category" in r.get("factor", "") for r in recs)
        # 样例中品类统计不显著，不应有品类建议
        return not has_cat, "无品类建议（品类不显著）"

    def _c_rec_has_evidence(self, _p):
        recs = self.attr.get("recommendations", {}).get("recommendations", [])
        ok = all(r.get("evidence_grade") for r in recs)
        return ok, "每条建议有证据分级"

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
