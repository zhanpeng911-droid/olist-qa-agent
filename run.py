"""M1 交互/演示入口。

用法:
  uv run python run.py "总体延迟率和低评分率是多少？"

配置了 DEEPSEEK_API_KEY 时使用 DeepSeek 真调；未配置时用 MockLLM 演示流程。
"""
from __future__ import annotations

import sys
from pathlib import Path

from dotenv import load_dotenv

# 加载项目根 .env（DEEPSEEK_API_KEY 等）
load_dotenv(Path(__file__).resolve().parent / ".env")

from agent_core.attribution import run_attribution
from agent_core.data_provider import MySQLProvider, SampleProvider
from agent_core.intent import Intent
from agent_core.llm import MockLLM, create_llm
from agent_core.loop import ReActLoop
from agent_core.semantic import SemanticLayer


def _print_attribution(res: dict) -> None:
    base = res["baseline"]
    print("【低评分描述性归因结果】")
    print(f"订单级基准: 样本 {base['order']['sample']}，低评分率 "
          f"{base['order']['low_score_rate']:.2%}")
    print(f"卖家级基准(单卖家): 样本 {base['seller']['sample']}，低评分率 "
          f"{base['seller']['low_score_rate']:.2%}")
    print("\n优先级问题对象 (P0/P1/P2):")
    for g in res["priorities"]:
        print(f"  {g['priority']}  [{g['dimension']}={g['value']}]  "
              f"样本{g['sample']} 低评分率{g['low_score_rate']:.2%} "
              f"基准{g['base_rate']:.2%} Lift{g['lift']:.2f} "
              f"超额{g['excess_low_score']:.0f}")

    routes = res.get("routes", {})
    if routes:
        print("\n【route 线路深挖】")
        print("Top 线路 (按规模×风险):")
        for g in routes.get("top_routes", [])[:5]:
            print(f"  {g['priority']}  [{g['value']}]  "
                  f"样本{g['sample']} 低评分率{g['low_score_rate']:.2%} "
                  f"Lift{g['lift']:.2f} 超额{g['excess_low_score']:.0f}")
        conc = routes.get("concentration", {})
        if conc.get("top5_share") is not None:
            print(f"线路集中度: Top5 线路低评分 {conc['top5_low_score_count']} / "
                  f"总低评分 {conc['total_low_score_count']} = "
                  f"{conc['top5_share']:.1%}")
        print("线路×延迟交叉 (延迟/非延迟低评分率):")
        for c in routes.get("route_cross_delay", []):
            late = c.get("late"); not_late = c.get("not_late")
            late_s = f"{late['low_score_rate']:.0%}(n={late['sample']})" if late else "-"
            nlate_s = f"{not_late['low_score_rate']:.0%}(n={not_late['sample']})" if not_late else "-"
            print(f"  {c['route']}: 延迟 {late_s} | 非延迟 {nlate_s}")

    ver = res.get("verification", {})
    if ver and ver.get("ok") is not False:
        print("\n【统计验证】")
        print("单变量检验:")
        for t in ver.get("single_tests", []):
            p = t.get("p_adjusted", t.get("p"))
            extra = ""
            if "or" in t:
                extra = f" OR={t['or']}"
            if "rho" in t:
                extra = f" ρ={t['rho']:.3f}"
            print(f"  {t['factor']}: p={p:.4g}{extra}")
        ev = ver.get("evidence", {})
        if ev:
            print(f"关键因素 {ev['factor']} 证据分级: {ev['grade']} "
                  f"(OR={ev['or']}, 95%CI {ev['or_ci']}, p={ev['p']:.2e})")
        lo = ver.get("logistic", {}).get("order", {})
        late = next((t for t in lo.get("terms", []) if t["term"] == "is_late_delivery"), None)
        if late:
            print(f"Logistic 订单模型 is_late_delivery: 调整OR={late['or']}, "
                  f"95%CI {late['ci95']}, p={late['p']:.2e} (HC3)")

    rec = res.get("recommendations", {})
    if rec and rec.get("ok") is not False and rec.get("recommendations"):
        print("\n【改善建议（基于已验证证据）】")
        for r in rec["recommendations"]:
            print(f"  [{r['priority']}] {r['factor']} → 责任方:{r['responsibility']}")
            print(f"       动作: {', '.join(r['actions'])}")
            print(f"       监控: {', '.join(r['monitor_metrics'])} | 验证: {r['verify']}")
    if rec and rec.get("pending_verification"):
        for p in rec["pending_verification"]:
            print(f"  ⚠ {p['factor']}: {p['note']}")

    print("\n边界提示:")
    for c in res["caveats"]:
        print(f"  - {c}")


def _parse_args(argv: list[str]) -> tuple[str, bool]:
    """解析命令行：--db mysql 切换真实库，其余拼为问题。"""
    use_mysql = "--db" in argv and "mysql" in argv
    argv = [a for a in argv if a not in ("--db", "mysql")]
    return (" ".join(argv) if argv else ""), use_mysql


def main() -> None:
    question, use_mysql = _parse_args(sys.argv[1:])
    if not question:
        question = input("请输入你的问题: ").strip()

    semantic = SemanticLayer()
    if use_mysql:
        provider = MySQLProvider(allow_tables=semantic.allowed_tables())
        print("[DB] 使用真实 MySQL\n")
    else:
        provider = SampleProvider()
        print("[DB] 使用样例数据（--db mysql 切真实库）\n")

    # 归因类问题：直接走确定性归因流程（无需 LLM）
    if Intent(semantic).classify(question) == "attribution":
        print(f"问：{question}\n")
        res = run_attribution(provider, semantic)
        _print_attribution(res)
        provider.close()
        return

    try:
        llm = create_llm()
        print("[LLM] 使用 DeepSeek\n")
    except RuntimeError as e:
        print(f"[提示] {e}")
        print("[LLM] 使用 MockLLM 演示流程（仅验证，非真实回答）。配置 DEEPSEEK_API_KEY 后可真调。\n")
        llm = MockLLM(
            tool_call={
                "tool": "query_mart",
                "args": {"table": "mart_order_delivery",
                         "metrics": ["late_rate", "low_score_rate"]},
            },
            answer="（Mock 演示）已按需调用 query_mart 工具查询 mart_order_delivery 获得结果。",
        )

    loop = ReActLoop(llm, provider, semantic)
    print(f"问：{question}\n")
    res = loop.run(question)

    print("=" * 50)
    if res.get("ok"):
        print("答：", res["answer"])
    else:
        print("（未得到答案）", res.get("error", ""))
    print("\n--- 执行轨迹 ---")
    for t in res.get("trace", []):
        print(t)
    provider.close()


if __name__ == "__main__":
    main()
