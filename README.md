# Olist 智能问数 Agent

Olist 电商履约与评分分析的自然语言问数 Agent（M1 阶段骨架）。

基于已治理好的 mart 宽表（语义字典锁死口径），用自建 ReAct 循环把自然语言问题翻译成结构化查询，保证结果**可对账**。

## 环境要求
- [uv](https://docs.astral.sh/uv/)（Python 项目管理）
- Python（由 uv 自动管理）

## 快速开始

```bash
# 1. 初始化依赖（自动建 .venv）
uv sync

# 2. 生成样例数据（如 sample_data 已存在可跳过）
uv run python scripts/generate_sample_data.py

# 3. 运行测试（工具对账 + ReAct 端到端）
uv run pytest tests/ -v

# 3.5 运行 M4 标准问题评测（26 题，确定性，样例数据）
uv run python tests/run_eval.py

# 4. 交互/演示（未配 key 时用 MockLLM 演示流程）
uv run python run.py "总体延迟率和低评分率是多少？"
```

## 接入 DeepSeek 真调（联调）

```bash
# 方式一：直接导出环境变量
export DEEPSEEK_API_KEY=sk-xxxx
uv run python run.py "总体延迟率和低评分率是多少？"

# 方式二：复制 .env.example 为 .env 并填入 key（代码需自行用 load_dotenv 或由 shell 导出）
```

配置后 `create_llm()` 返回 `DeepSeekLLM`，用 OpenAI 兼容接口调用 `deepseek-chat`；未配置 key 时回退到 `MockLLM` 验证流程。

## 项目结构

```text
olist-qa-agent/
├─ pyproject.toml            # uv 配置
├─ run.py                    # 演示/交互入口
├─ semantics/
│  └─ metrics_dict.yaml      # 语义字典（唯一真相源，锁死口径）
├─ config/
│  └─ recommendation_rules.yml # 建议规则库（责任方/动作/指标/验证）
├─ sample_data/              # 样例数据（由脚本生成）
│  ├─ mart_order_delivery.csv
│  └─ mart_order_seller_delivery.csv
├─ scripts/
│  └─ generate_sample_data.py# 生成确定性样例数据
├─ agent_core/
│  ├─ semantic.py            # 加载语义字典
│  ├─ data_provider.py       # 数据访问抽象（Sample/MySQL）
│  ├─ tools.py               # 工具层：query_mart / top_n 等
│  ├─ intent.py              # 意图识别骨架
│  ├─ llm.py                 # LLM 抽象（DeepSeek / Mock）
│  └─ loop.py                # 自建 ReAct 循环
├─ docs/                     # 方案设计 + 变更清单
└─ tests/
   ├─ benchmark_questions.md # 5 个基准问题清单
   ├─ TEST_LOG.md            # 测试记录台账
   └─ test_m1.py             # 对账 + 端到端测试
```

## 设计要点

- **语义字典锁口径**：指标/维度必须来自 `metrics_dict.yaml`，模型只能"选"不能"编"
- **结构化 SQL**：`query_mart` 用模板生成 SQL，杜绝自由 join 与语法错误
- **可对账**：每次查询附来源 SQL，测试对样例数据做"工具结果 vs 直接 SQL 重算"一致校验
- **数据访问抽象**：M1 用 SQLite 加载样例 CSV；接真 MySQL 时实现 `MySQLProvider`（只读账号 + 白名单），工具层无需改动

## 接入真实数据库（朋友自测）

项目支持切换真实 MySQL（`MySQLProvider`，pymysql 只读 + 白名单 + 限行）。当前用样例数据跑通逻辑；**真实库由接收方自测**：

```bash
# 1. 在 .env 填入真实连接信息
DB_HOST=你的主机
DB_PORT=3306
DB_USER=你的账号
DB_PASSWORD=你的密码
DB_NAME=你的库名

# 2. 用真实库跑归因/问数（--db mysql）
uv run python run.py --db mysql "对低评分进行归因"
```

注意：真实 mart 表字段名需与 `semantics/metrics_dict.yaml` 一致（`is_late_delivery` / `late_days` / `is_low_score` 等标准命名）；若有出入，在语义字典对齐并记录到 `tests/TEST_LOG.md`。

## 路线图

- M1（L1 问数）：语义字典 + 工具层 + ReAct 循环 + 对账测试 ✅
- M2（L2 描述性归因）：候选因素扫描 + Lift/超额低评分 + P0/P1/P2 + route 线路深挖 ✅
- M3（统计验证）：卡方/趋势/MWU/Spearman + 双 Logistic(HC3) + 证据分级 ✅
- M4（建议与评测）：建议规则库（并入归因）+ 26 题标准问题评测（26/26）✅
- 接真实 MySQL：`MySQLProvider` 已实现，等待填 .env 自测（朋友自测）
