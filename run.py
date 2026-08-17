"""命令行交互入口。

用法:
  uv run python run.py "总体延迟率和低评分率是多少？"

配置 DEEPSEEK_API_KEY 后调用 DeepSeek；未配置时使用内置示例响应检查交互流程。
"""
from __future__ import annotations

import sys
from pathlib import Path

from dotenv import load_dotenv

# 加载项目根 .env（DEEPSEEK_API_KEY 等）
load_dotenv(Path(__file__).resolve().parent / ".env")

from agent_core.attribution import run_attribution
from agent_core.data_provider import (
    DATABASE_SOURCE_LABEL,
    SAMPLE_SOURCE_LABEL,
    MySQLProvider,
    ProjectCsvProvider,
)
from agent_core.intent import Intent
from agent_core.llm import MockLLM, create_llm
from agent_core.loop import ReActLoop
from agent_core.semantic import SemanticLayer
from agent_core.statistical_analysis import (
    analyze_statistical_question,
    format_statistical_result,
)


def _print_attribution(res: dict) -> None:
    if not res.get("ok"):
        print("【关联因素分析未执行】")
        print(res.get("error", "低评分关联因素分析未完成"))
        return
    base = res["baseline"]
    print("【低评分关联因素分析】")
    print(f"订单级基准: 样本 {base['order']['sample']}，低评分率 "
          f"{base['order']['low_score_rate']:.2%}")
    print(f"卖家级基准(单卖家): 样本 {base['seller']['sample']}，低评分率 "
          f"{base['seller']['low_score_rate']:.2%}")

    print("\n【单变量筛选：FDR校正 + 95%置信区间】")
    for row in res.get("feature_tests", []):
        if not row.get("ok"):
            print(f"  {row['label']}: 未执行（{row.get('error', '未知原因')}）")
            continue
        ci = row.get("ci95") or [None, None]
        print(
            f"  {row['label']}: {row['method']}，FDR校正后p={row['p_adjusted']:.4g}，"
            f"{row.get('effect_name')}={row.get('effect_value'):.4g}，"
            f"95%CI={ci}，{'保留' if row['significant'] else '不保留'}"
        )

    print("\n【共线性处理后纳入模型的变量】")
    selected = res.get("selected_features", [])
    print("  " + ("、".join(row["label"] for row in selected) if selected else "无"))

    print("\n【多变量Logistic调整（HC3稳健标准误）】")
    for row in res.get("adjusted_validation", {}).get("results", []):
        if not row.get("ok"):
            print(f"  {row['label']}: 未能估计（{row.get('error', '未知原因')}）")
            continue
        effect = (
            f"调整后优势比（OR）={row['adjusted_or']:.3f}, 95%CI={row['ci95']}"
            if isinstance(row.get("adjusted_or"), (int, float))
            else "分类变量整体联合检验，对象级OR见明细"
        )
        print(f"  {row['label']}: {effect}, FDR校正后p={row['p_adjusted']:.4g}，"
              f"{'控制其他因素后仍显著' if row['stable'] else '调整后未达到显著性标准'}")

    print("\n【调整后仍显著变量的分布】")
    explanations = res.get("adjusted_explanations", [])
    if not explanations:
        print("  当前没有控制其他因素后仍显著的变量。")
    for explanation in explanations:
        details = explanation.get("details")
        print(f"  {explanation['label']}：")
        if explanation.get("kind") == "numeric":
            for row in (details or {}).get("by_target", []):
                print(f"    {row['group']} n={row['sample']}，中位数={row['median']:.3f}")
        else:
            for row in (details or [])[:5]:
                print(f"    {row['value']} n={row['sample']}，"
                      f"低评分率={row['low_score_rate']:.2%}，"
                      f"相对总体倍数={row.get('lift', 0):.2f}")

    print("\n结果边界：仅报告统计关联，不作因果判断，也不自动生成治理方案。")

    print("\n边界提示:")
    for c in res["caveats"]:
        print(f"  - {c}")


def _parse_args(argv: list[str]) -> tuple[str, str]:
    """解析命令行：--source project/mysql 分别对应演示样本和完整数据库。"""
    source = "project"
    cleaned: list[str] = []
    i = 0
    while i < len(argv):
        if argv[i] in ("--source", "--db") and i + 1 < len(argv):
            source = argv[i + 1]
            i += 2
            continue
        cleaned.append(argv[i])
        i += 1
    if source not in ("project", "mysql"):
        raise SystemExit("--source 仅支持 project / mysql")
    return (" ".join(cleaned) if cleaned else ""), source


def main() -> None:
    question, source = _parse_args(sys.argv[1:])
    if not question:
        question = input("请输入你的问题: ").strip()

    semantic = SemanticLayer()
    if source == "mysql":
        provider = MySQLProvider(allow_tables=semantic.allowed_tables())
        print(f"[数据源] {DATABASE_SOURCE_LABEL}\n")
    else:
        provider = ProjectCsvProvider()
        physical_counts = {
            k: v for k, v in provider.row_counts.items()
            if k != "mart_order_item_analysis"
        }
        table_names = {
            "mart_order_delivery": "订单级分析宽表",
            "mart_order_seller_delivery": "订单-卖家级分析宽表",
        }
        rows_text = ", ".join(
            f"{table_names.get(k, k)}={v}" for k, v in physical_counts.items()
        )
        print(f"[数据源] {SAMPLE_SOURCE_LABEL}（{rows_text}）")
        if physical_counts and max(physical_counts.values()) <= 1000:
            print("[提示] 当前样本仅用于检查功能与分析流程；业务结论请使用完整业务数据库。")
        print()

    intent = Intent(semantic).classify(question)
    # 显著性问题：按变量类型选择固定检验，数据库只返回聚合结果。
    if intent == "statistical":
        print(f"问：{question}\n")
        res = analyze_statistical_question(provider, question)
        print(format_statistical_result(res))
        provider.close()
        return

    # 归因类问题：直接走确定性归因流程（无需 LLM）
    if intent == "attribution":
        print(f"问：{question}\n")
        res = run_attribution(provider, semantic, question=question)
        _print_attribution(res)
        provider.close()
        return

    try:
        llm = create_llm()
        print("[大模型] DeepSeek\n")
    except RuntimeError as e:
        print(f"[提示] {e}")
        print("[大模型] 使用内置示例响应检查交互流程；该响应不作为数据结论。\n")
        llm = MockLLM(
            tool_call={
                "tool": "query_mart",
                "args": {"table": "mart_order_delivery",
                         "metrics": ["late_rate", "low_score_rate"]},
            },
            answer="（内置示例响应）已按预设口径查询订单级分析宽表。",
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
