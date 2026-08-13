"""Olist 智能问数 Agent — Demo 原型界面。

⚠ 注意：这是 Demo 原型界面，非最终 UI，从简实现，仅用于开发测试与演示。
正式界面在后续独立设计（见主方案 4.10）。

运行: uv run streamlit run ui/app.py
"""
from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import pandas as pd
import streamlit as st
from dotenv import load_dotenv

# 加载项目根 .env（DEEPSEEK_API_KEY / DB 等）
load_dotenv(ROOT / ".env")

from agent_core.attribution import run_attribution
from agent_core.data_provider import SampleProvider
from agent_core.intent import Intent
from agent_core.semantic import SemanticLayer

# =====================================================================
# 纯函数（供展示与测试复用，不依赖 streamlit）
# =====================================================================

ATTRIBUTION_HINTS = ("归因", "为什么", "原因", "优先治理", "改善建议", "哪个因素")


def is_attribution_question(q: str) -> bool:
    return any(h in q for h in ATTRIBUTION_HINTS)


def build_priority_df(res: dict) -> pd.DataFrame:
    """归因优先级 → DataFrame（P0/P1/P2 表格）。"""
    rows = []
    for g in res.get("priorities", []):
        rows.append({
            "优先级": g.get("priority"),
            "维度": g.get("dimension"),
            "对象": g.get("value"),
            "样本量": g.get("sample"),
            "低评分率": f"{g.get('low_score_rate', 0):.1%}",
            "Lift": round(g.get("lift") or 0, 2),
            "超额低评分": g.get("excess_low_score"),
        })
    return pd.DataFrame(rows)


def build_route_summary(res: dict) -> list[str]:
    """route 深挖摘要（文本行）。"""
    rt = res.get("routes", {})
    lines = []
    for g in rt.get("top_routes", [])[:5]:
        lines.append(f"{g.get('priority')} 线路 {g.get('value')}："
                     f"率{g.get('low_score_rate', 0):.1%} Lift{g.get('lift') or 0:.2f} "
                     f"超额{g.get('excess_low_score', 0):.0f}")
    conc = rt.get("concentration", {})
    if conc.get("top5_share") is not None:
        lines.append(f"Top5 线路集中度：{conc['top5_share']:.1%} "
                     f"({conc['top5_low_score_count']}/{conc['total_low_score_count']})")
    return lines


def build_verification_summary(res: dict) -> list[str]:
    """统计验证摘要。"""
    v = res.get("verification", {})
    lines = []
    ev = v.get("evidence", {})
    if ev:
        lines.append(f"关键因素 {ev.get('factor')}：{ev.get('grade')} "
                     f"(OR={ev.get('or')}, p={ev.get('p', 1):.2e})")
    lo = v.get("logistic", {}).get("order", {})
    late = next((t for t in lo.get("terms", []) if t.get("term") == "is_late_delivery"), None)
    if late:
        lines.append(f"Logistic 调整 OR={late['or']} 95%CI {late['ci95']} (HC3)")
    return lines


def build_recommendation_lines(res: dict) -> list[str]:
    """改善建议 → 文本行。"""
    recs = res.get("recommendations", {}).get("recommendations", [])
    return [f"[{r.get('priority')}] {r.get('factor')} → 责任方:{r.get('responsibility')} "
            f"| 动作:{'、'.join(r.get('actions', []))} "
            f"| 监控:{'、'.join(r.get('monitor_metrics', []))} "
            f"| 验证:{r.get('verify')}" for r in recs]


# =====================================================================
# Streamlit 界面（Demo）
# =====================================================================

st.set_page_config(page_title="Olist 智能问数 · Demo", page_icon="⚠️", layout="wide")
st.markdown("## ⚠️ Demo 原型界面 — **非最终 UI**")
st.caption("从简实现，用于开发测试与演示；核心逻辑见 `agent_core/`，正式界面后续独立设计。")


@st.cache_resource
def _provider():
    return SampleProvider()


@st.cache_resource
def _semantic():
    return SemanticLayer()


@st.cache_data(show_spinner=False)
def _attribution():
    return run_attribution(_provider(), _semantic())


def render_attribution(q: str, res: dict) -> None:
    st.markdown(f"**归因结果**（问题：{q}）")
    base = res["baseline"]
    c1, c2, c3, c4 = st.columns(4)
    c1.metric("订单级低评分率", f"{base['order']['low_score_rate']:.1%}",
              help=f"样本 {base['order']['sample']}")
    c2.metric("卖家级低评分率(单卖家)", f"{base['seller']['low_score_rate']:.1%}",
              help=f"样本 {base['seller']['sample']}")
    c3.metric("延迟订单 OR", f"{res['verification']['evidence'].get('or', '—'):.2f}",
              help="延迟 vs 非延迟 低评分 odds 比")
    c4.metric("证据分级", res["verification"]["evidence"].get("grade", "—"))

    df = build_priority_df(res)
    if not df.empty:
        st.subheader("P0/P1/P2 优先级问题对象")
        st.dataframe(df, use_container_width=True, hide_index=True)

    rt = build_route_summary(res)
    if rt:
        st.subheader("route 线路深挖")
        st.write("\n".join(f"- {x}" for x in rt))

    vs = build_verification_summary(res)
    if vs:
        st.subheader("统计验证摘要")
        st.write("\n".join(f"- {x}" for x in vs))

    rl = build_recommendation_lines(res)
    if rl:
        st.subheader("改善建议（基于已验证证据）")
        st.write("\n".join(f"- {x}" for x in rl))

    st.caption("边界：" + "；".join(res.get("caveats", [])))


def render_answer(q: str, answer: str) -> None:
    st.markdown(f"**问**：{q}")
    st.write(answer if answer else "（未得到答案）")


# ---- 聊天区 ----
st.subheader("对话")
if "history" not in st.session_state:
    st.session_state.history = []

for role, text in st.session_state.history:
    with st.chat_message(role):
        st.write(text)

q = st.chat_input("例如：对低评分进行归因 / 总体延迟率是多少？")
if q:
    st.session_state.history.append(("user", q))
    with st.chat_message("user"):
        st.write(q)
    with st.chat_message("assistant"):
        if is_attribution_question(q):
            res = _attribution()
            render_attribution(q, res)
            st.session_state.history.append(
                ("assistant", "已输出归因结果（Demo 界面见上方）"))
        else:
            from agent_core.llm import DeepSeekLLM, MockLLM
            from agent_core.loop import ReActLoop
            try:
                llm = DeepSeekLLM()
            except (RuntimeError, ValueError) as e:
                st.warning(f"{e}；未配置 key，使用 Mock 演示。")
                llm = MockLLM(
                    tool_call={"tool": "query_mart",
                               "args": {"table": "mart_order_delivery",
                                        "metrics": ["low_score_rate"]}},
                    answer="（Mock 演示）已调用 query_mart 查询低评分率。")
            loop = ReActLoop(llm, _provider(), _semantic())
            res = loop.run(q)
            render_answer(q, res.get("answer", ""))
            st.session_state.history.append(
                ("assistant", res.get("answer", "") or "（未得到答案）"))

# ---- 侧边栏 ----
with st.sidebar:
    st.markdown("#### Olist 智能问数 · Demo")
    st.caption("⚠ 非最终 UI")
    st.markdown("- 归因类问题走确定性流程（无需 LLM）\n"
                "- 问数类问题走 ReAct（DeepSeek / Mock）\n"
                "- 数据源：样例数据（`--db mysql` 未接入 UI）")
    if st.button("运行 26 题标准评测"):
        import subprocess
        r = subprocess.run([sys.executable, str(ROOT / "tests" / "run_eval.py")],
                           capture_output=True, text=True)
        st.text(r.stdout[-500:] if r.stdout else r.stderr[-500:])
    st.markdown("---")
    st.caption("Demo 原型；正式界面见主方案 4.10（后续独立设计）")
