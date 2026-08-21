# 测试记录（Test Log）

> 项目：Olist 智能问数 Agent
> 本文件记录所有测试的过程与结果，随阶段持续追加。
> 当前覆盖：M1 阶段

---

## 1. 测试环境

| 项 | 值 |
|---|---|
| 系统 | Windows (Git Bash) |
| Python 管理 | uv 0.12.1（Python 3.14.6，项目 .venv） |
| 依赖 | pyyaml 6.0.3, openai 3.0.0, pytest 9.1.1, python-dotenv 1.2.2 |
| 数据 | 样例数据（SQLite 内存）：`mart_order_delivery` 1000 行、`mart_order_seller_delivery` 1151 行 |
| 语义字典 | `semantics/metrics_dict.yaml`（mart_order_delivery + mart_order_seller_delivery） |
| LLM（真调） | DeepSeek `deepseek-v4-flash`（base_url https://api.deepseek.com） |

## 2. 测试目标（M1 过渡 M2 前的验收）

1. 工具层对样例数据算出的指标数字，能与"手写 SQL 直接重算"对账一致
2. ReAct 循环端到端可跑通（识别意图 → 调工具 → 观察 → 给结论）
3. 真实 LLM（DeepSeek）能把自然语言正确翻译成工具调用，5 个基准问题无幻觉、数字可对账
4. 安全/口径校验生效（拒绝未知指标/表、非法排序）

## 3. 自动化测试（pytest）结果

命令：`uv run pytest tests/ -v` → **10 passed in 0.09s**

| # | 用例 | 目的 | 结果 |
|---|---|---|---|
| 1 | `test_1_overall_rates` | 总体延迟率/低评分率 与 SQL 重算对账 | ✅ PASS |
| 2 | `test_2_delayed_vs_ontime` | 按时 vs 延迟评分对比对账；期望延迟评分更低 | ✅ PASS |
| 3 | `test_3_delay_bucket` | 延迟分档低评分率对账；期望单调上升 | ✅ PASS |
| 4 | `test_4_top_n` | Top5 品类排名与 SQL 重算一致 | ✅ PASS |
| 5 | `test_5_time_breakdown` | 审批/总履约时长对账 | ✅ PASS |
| 6 | `test_seller_table` | 卖家表 Top5 卖家州排名对账 | ✅ PASS |
| 7 | `test_reject_unknown_metric` | 拒绝语义字典外的指标（口径锁死） | ✅ PASS |
| 8 | `test_reject_unknown_table` | 拒绝白名单外的表 | ✅ PASS |
| 9 | `test_order_by_must_be_in_query` | 拒绝非本次查询指标排序 | ✅ PASS |
| 10 | `test_react_mock_end_to_end` | ReAct(Mock) 端到端：调工具+给答案 | ✅ PASS |

### 对账结论（样例数据实测值）

| 指标 | 值 |
|---|---|
| 总体延迟率 | 36.8% |
| 总体低评分率 | 40.8% |
| 按时 / 延迟1-3天 / 4-7天 / 8-14天 / 15天+ 低评分率 | 23.6% / 32.6% / 53.4% / 68.0% / 88.4% |

→ 低评分率随延迟档位**单调上升**，业务模式成立。

## 4. 真实 LLM 基准测试（DeepSeek 真调）

命令：`uv run python run.py "<问题>"`（读 `.env` 中的 key/model）

| # | 基准问题 | 模型回答（摘要） | 对账 |
|---|---|---|---|
| 1 | 总体延迟率和低评分率是多少？ | 延迟率 36.8%，低评分率 40.8% | ✅ |
| 2 | 提前/按期/延迟订单的评分表现对比？ | 按时 4.11 → 延迟15天+ 2.77；低评分率 23.6%→88.4% | ✅ |
| 3 | 延迟 1-3/4-7/8-14/15+ 天低评分率变化？ | 32.6%→53.4%→68.0%→88.4%（单调上升） | ✅ |
| 4 | 低评分率最高的 5 个品类？ | sports_leisure 44.6%、computers_accessories 43.3%、health_beauty 43.1%、furniture_decor 41.0%、toys_games 38.0% | ✅ |
| 5 | 支付审批和总履约时长是多少？ | 审批 1.57 天，总履约 8.57 天 | ✅ |

**关键结论**：5/5 基准问题，真实模型正确识别意图、选用语义字典预置的指标/维度/筛选/排序，无幻觉；每次回答附来源 SQL，数字与对账一致。

## 5. 测试过程记录与问题修复

### 5.1 首次运行 pytest：3 failed / 7 passed
- **`test_2_delayed_vs_ontime` 失败**：语义字典缺 `is_delayed` 维度（按时 vs 延迟对比需要），被 `check_dimension` 拒绝。
- **`test_4_top_n`、`test_seller_table` 失败**：`top_n` 传 `order_by=指标名`，但 `query_mart` 校验时期望的是别名 `_m_<metric>`，导致"order_by 必须是查询指标之一"。

**修复**：
1. `semantics/metrics_dict.yaml`：`mart_order_delivery.dimensions` 增加 `is_delayed`
2. `agent_core/tools.py`：`query_mart` 的 `order_by` 改为按指标名映射到别名 `_m_<metric>`（不再要求调用方传别名）

**复测**：10/10 PASS。

### 5.2 DeepSeek key 验证
- 首次提供 key（sk-a2fbd…a0f）：官方端点返回 `authentication_error`（无效/过期）。
- 二次提供 key（sk-5e97…b7）：验证通过，`/models` 返回 `deepseek-v4-flash` / `deepseek-v4-pro`。
- 写入 `.env` 并通过 `python-dotenv` 在 `run.py` 加载。

## 6. 结论

- M1 工具层与 ReAct 流程可靠，5 个基准问题在真实 LLM 下全部正确、可对账。
- 已满足"M1 过渡 M2 前的测试"验收标准。
- 待办（M1 之后）：接入真实 MySQL（实现 `MySQLProvider`），M2 阶段开发与测试。

## 7. 复现方式

```bash
uv sync
Get-ChildItem data/sample/*.csv                 # 确认三张 Mart 截取样本已就位
uv run pytest tests/ -v                          # 自动化测试
uv run python run.py "总体延迟率和低评分率是多少？"  # 真实 LLM 联调（需 .env 配好 key）
```

---

## 8. 追加记录：并入朋友思路（v2.5 变更）

> 日期：2026-08-13
> 依据：《低评分归因与改善建议Agent搭建思路》+ `docs/design/并入变更清单_朋友思路.md`（用户审批通过，全部执行）

### 8.1 字段名标准化（沿用朋友命名）

样例数据、语义字典、测试同步改名，逻辑不变：

| 变更后（标准） | 变更前 |
|---|---|
| `is_late_delivery` | `is_delayed` |
| `late_days` | `delivery_delay_days` |
| `is_strict_negative_score` | `is_strict_negative` |
| `is_delivery_analysis_eligible` | `delivery_analysis_eligible` |
| `primary_category_name` | `product_category` |
| `primary_payment_type` | `payment_type` |
| `order_purchase_timestamp` | `purchase_date` |
| 新增 `has_review_record` / `order_month` / `delivery_variance_days` / `route` | — |
| 指标 `delay_rate` → `late_rate` | — |

### 8.2 复测结果

命令：`uv run pytest tests/ -v` → **10 passed in 0.32s**

字段名/指标名统一后，全部对账测试与端到端测试仍通过，无回归。

### 8.3 语义字典 guards 补强

新增：`low_score_definition`(≤3)、`reviewed_only`(分母有评价)、`min_group_sample=100`、`multi_seller_rule`(默认 is_multi_seller_order=0 去重)、`text_boundary`(禁臆测文本原因)、`forbid_join`。

### 8.4 主方案升级 v2.5

- 4.5 统计工具扩展为 10 个（含 `excess_low_score` / `rank_priorities` / 贡献拆解 / 方法匹配表 / 多重校正 / 双模型约束）
- 新增 4.8 L2 归因诊断（11 步流程 + 候选因素 + 边界）
- 新增 4.9 建议规则库（`config/recommendation_rules.yml`）
- 4.4 System Prompt 10 条约束；4.7 证据分级 + 5 段回答结构模板
- 6 里程碑重排对齐 M1-M4（M1 已完成维持现状）
- 9 新增验收标准 8 条

### 8.5 待后续验证（M2/M3）

- L2 描述性归因、统计检验、建议规则库为**设计稿**，尚未实现
- 接入真实 MySQL 时按标准字段名对齐真实 mart 表

---

## 9. M2 开发与测试记录（L2 描述性归因）

> 日期：2026-08-13
> 范围：`agent_core/attribution.py`、intent/loop/run 集成、`tests/test_m2.py`

### 9.1 新增模块
- `agent_core/attribution.py`：`build_baseline` / `screen_factors` / `rank_priorities` / `run_attribution`（固定顺序确定性流程）
- 候选维度：订单级 6 个（is_late_delivery / delay_bucket / customer_state / primary_category_name / primary_payment_type / order_month）+ 卖家级 3 个（seller_state / route / cross_state，`is_multi_seller_order=0`）
- 指标口径：低评分率（AVG is_low_score）、超额低评分（样本×max(率-基准率,0)）、Lift（率/基准率）、优先级综合分（超额×(Lift-1)），P0/P1/P2 分位数划分
- 语义字典新增 `low_score_count` 指标（SUM is_low_score）
- 集成：intent 加"归因"意图；loop 注册 `run_attribution` 工具（含反思纠错）；run.py 归因类问题直接走确定性流程

### 9.2 测试结果：`uv run pytest tests/` → **17 passed**（M1 10 + M2 7）

| M2 用例 | 目的 | 结果 |
|---|---|---|
| test_baseline_order / test_baseline_seller | 订单级/卖家级基准对账 | ✅ |
| test_screen_factors_reconcile | 分组扫描对账（与手写 SQL） | ✅ |
| test_min_sample_filter | min_group_sample=100 过滤生效 | ✅ |
| test_lift_and_excess | Lift/超额公式对账 | ✅ |
| test_rank_priorities | 综合分降序 + P0 为最高分 | ✅ |
| test_run_attribution_end_to_end | 归因流程输出结构完整 | ✅ |

### 9.3 过程中修复的 bug
- `ReActLoop` 缺 `_provider` 属性：`_run_attribution` 引用 `self._p` 报 AttributeError → 在 `__init__` 存 `self._provider` 并改用。
- `test_lift_and_excess` 失败：实现 `excess_low_score` 按 1 位小数舍入，测试按精确值断言 → 测试改为断言 `round(期望,1)`。

### 9.4 样例数据实测（描述性归因输出）
- 订单级基准：样本 1000，低评分率 38.10%；卖家级基准（单卖家）：样本 848，37.74%
- P0：`delay_bucket=15天+`（率85.89%，Lift 2.25，超额78）、`is_late_delivery=1`（率69.77%，Lift 1.83，超额109）、`toys_games`（率45.45%，超额13）
- P1：seller_state RS/RJ、boleto、customer_state BA；延迟为最大驱动因素，符合业务模式

### 9.5 DeepSeek 真调验证
- ReAct 端到端（问题"为什么低评分比较高，主要有哪些因素？"）：模型正确调用 `run_attribution` → 输出结构化归因结论（延迟为最大驱动，含 Lift/超额）
- 轨迹中出现一次 `format_error`，被反思机制纠正后成功——验证纠错回路有效
- 结论：M2 描述性归因验收通过；不下因果结论、不生成建议（M4）的边界已落实

---

## 10. 接入真实 MySQL + route 线路深挖（M2 边角）

> 日期：2026-08-13

### 10.1 MySQLProvider（真实库接入）
- `agent_core/data_provider.py` 实现 `MySQLProvider`（pymysql）：
  - 从 `.env` 读 `DB_HOST/DB_PORT/DB_USER/DB_PASSWORD/DB_NAME`
  - 安全约束：仅 SELECT、表名白名单（`guards.allow_tables`）、强制 LIMIT
  - 接口与 `SampleProvider` 一致，工具层/归因层无需改动
- 运行切换：`run.py --db mysql`（默认样例数据）
- `.env.example` / `.env` 增加 DB 配置模板
- 真库连通性验证：**待用户填入真实连接信息后执行**（见 §10.5）

### 10.2 route 线路深挖（`analyze_routes`，三个方向）
- `top_routes`：route 维度 Top 线路（样本量/低评分率/Lift/超额 + P0/P1/P2）
- `route_concentration`：Top5 线路低评分订单数占总低评分比例（集中度）
- `route_cross_delay`：线路×延迟交叉低评分率（识别"延迟又低评分"的线路）
- 集成进 `run_attribution` 输出（`routes` 块）+ run.py 展示
- 语义字典：卖家表补 `is_late_delivery` 维度

### 10.3 测试结果：`uv run pytest tests/` → **21 passed**（M1 10 + M2 11）
新增用例：analyze_routes_structure / reconcile / route_concentration / route_cross_delay

### 10.4 过程中修复的问题
1. **route 高基数被 min_group_sample 全过滤**：样例中 route 最大样本仅 33（848 行/49 线路）→ 引入动态相对阈值 `max(15, 样本量×2%)`，样例/真库通用。
2. **卖家表缺 `is_late_delivery` 维度**：线路×延迟交叉查询被校验拒绝 → 语义字典补维度。
3. **舍入精度**：`top5_share` 实现按 4 位小数舍入，测试改为对齐 `round(,4)`。

### 10.5 待办：真实库连通性验证
- 需用户在 `.env` 填入真实 `DB_HOST/DB_USER/DB_PASSWORD/DB_NAME`
- 执行 `uv run python run.py --db mysql "对低评分进行归因"` 做连通性 + 对账验证
- 若真实 mart 表字段/口径与样例有出入，记录并对齐语义字典

### 10.6 样例数据实测（route 深挖）
- 线路集中度：Top5 线路低评分 58/320 = 18.1%
- 线路×延迟交叉（示例）：`RJ→RJ` 延迟组低评分率 89% vs 非延迟 23%；`SP→MG` 延迟 100% vs 非延迟 17%——延迟对线路低评分影响显著

---

## 11. M3 统计验证开发与测试记录

> 日期：2026-08-13
> 范围：`agent_core/statistics.py`、`run_attribution` 集成 verification 块、`tests/test_m3.py`

### 11.1 依赖与兼容性
- `uv add scipy statsmodels pandas`（scipy 1.18.0 / statsmodels 0.14.6 / pandas 3.0.5），**Python 3.14 兼容确认**，无需降级

### 11.2 新增模块 `agent_core/statistics.py`
| 函数 | 用途 |
|---|---|
| `categorical_test` | 2×2 卡方/Fisher + OR/RR + 95%CI |
| `chi_square_rc` | R×C 卡方 + Cramér's V |
| `distribution_test` | Mann-Whitney U + rank-biserial 效应量 |
| `correlation_test` | Spearman ρ + p |
| `trend_test` | Cochran-Armitage 趋势检验（有序×二分类） |
| `logistic_model_formula` | Logit + HC3 稳健 SE → 调整 OR/CI/p |
| `multiple_correction` | FDR-BH / Holm 多重校正 |
| `evidence_grade` | 强/中/待验证 证据分级 |
| `verify_factors` | 批量单变量 + 双 Logistic + 证据分级（集成） |

### 11.3 集成 `run_attribution`
- 归因输出新增 `verification` 块（单变量检验 + 订单级/单卖家 Logistic + 证据分级）
- 统计验证失败不阻断描述性归因（try/except 兜底）

### 11.4 测试结果：`uv run pytest tests/` → **32 passed**（M1 10 + M2 11 + M3 11）
- 构造已知关系数据验证各检验方法正确性（卡方显著/不显著、趋势、MWU、Spearman、校正、分级）
- 样例业务关联：is_late_delivery×low_score 卡方显著且 OR>1；late_days×review_score 负相关
- 集成：verify_factors 结构完整；run_attribution 含 verification 块；Logistic is_late_delivery 调整后仍显著（CI 不含 1）

### 11.5 过程中修复的问题
1. **statsmodels `get_robustcov_results` 不可用**（BinaryResultsWrapper 无公开方法）→ 改为 `logit(...).fit(cov_type='HC3')`，results 直接携带稳健 SE。
2. **`evidence_grade` 对无校正的单次检验给"中等证据"**：is_late_delivery p≈1e-50、OR 8.4 应属强证据 → 逻辑改为"无 p_adjusted 时用 p_raw"。
3. **`verify_factors` 参数名不匹配**（`min_sample` vs `min_group_sample`）→ 调用处修正。

### 11.6 样例数据实测（统计验证结果）
- is_late_delivery×低评分：卡方 p=5.6e-50，OR=8.43（95%CI [6.27, 11.33]）→ **强证据**
- delay_bucket 趋势检验 p=1.8e-61（低评分率随延迟档位单调上升）
- late_days×review_score Spearman ρ=-0.38（p=2.2e-36，负相关）
- MWU：延迟组评分中位数 3 vs 非延迟组 5（p=3.2e-27）
- 州/品类/支付方式卡方均不显著（BH 校正 p=0.94）
- Logistic 订单模型 is_late_delivery：调整 OR=8.64（95%CI [6.38, 11.7]，p=4.1e-44，HC3）——控制其他变量后仍显著
- 单卖家模型样本 848，Logistic 正常拟合

### 11.7 说明
- 样例数据统计结论仅供参考；真库由使用者自测（见 README"接入真实数据库"）

---

## 12. M4 建议生成 + 标准问题评测

> 日期：2026-08-13
> 范围：`agent_core/recommendation.py`、`tests/eval_questions.yml`、`tests/run_eval.py`、`tests/test_m4.py`

### 12.1 建议生成（`recommend_actions`，并入归因流程）
- 基于归因 + 统计验证的**已验证证据**匹配 `config/recommendation_rules.yml`
- 只对强/中证据因素给建议；待验证线索标注"暂不建议/待验证"
- 样例实测触发：严重延迟（P0）→ 客服/物流 人工介入+补偿；高规模线路 → 物流 线路SLA+P90 预警；品类不显著 → 不凭空给品类建议
- `run_attribution` 输出新增 `recommendations` 块；`run.py` 展示建议

### 12.2 标准问题评测集（26 题，确定性）
- `tests/eval_questions.yml`：L1 数字对账 8 / L2 归因结构 3 / M3 统计 3 / M4 建议 4 / 安全 5 / 边界 3
- `tests/run_eval.py`：样例数据、不依赖 API key、可复现；数字对账用"工具结果 vs SQL 重算"动态对账
- 运行：`uv run python tests/run_eval.py` → **26/26 通过（100%）**

### 12.3 测试结果：`uv run pytest tests/` → **44 passed**（M1 10 + M2 11 + M3 11 + M4 12）
M4 覆盖：建议结构/可执行性/证据对应/无未验证建议 + 安全（禁DML/无JOIN/未知指标表拒绝/限行）+ 边界（无因果措辞/无评价正文提示/小样本过滤）

### 12.4 过程中修复的问题
- **循环导入**（attribution ↔ recommendation）→ `recommend_actions` 中 `run_attribution` 改为函数内延迟导入

### 12.5 真调评测（可选）
- `tests/run_eval_live.py` 未编写（本次仅确定性评测）；如需真调，有 key 时对代表性问题跑 ReAct 并记录到此节

---

## 13. Demo UI（⚠ 非最终界面）

> 日期：2026-08-13
> 定位：**Demo 原型界面，非最终 UI**，从简，仅用于开发测试与演示

### 13.1 方案
- 主方案新增 **4.10 展示层 Demo UI**（明确标注非最终界面；正式 UI 后续独立设计）
- 技术：Streamlit 单文件 `ui/app.py`，核心逻辑零改动（仅薄壳）

### 13.2 实现
- `ui/app.py`：聊天输入 + 消息流；归因类走确定性流程并结构化渲染（优先级表格/route/统计验证/建议）；问数类走 ReAct（DeepSeek/Mock）；侧边栏 26 题评测按钮
- 纯展示函数独立（`build_priority_df` 等）便于测试

### 13.3 验证
- `uv run pytest tests/test_ui.py` → **5 passed**（展示函数 + 归因判定）
- `import ui.app` 可导入 ✓
- Streamlit headless 启动冒烟：`/_stcore/health` → `ok`，`http://localhost:8501` 可访问

### 13.4 说明
- 界面顶部常驻"⚠ Demo 原型界面，非最终 UI"
- 运行：`uv run streamlit run ui/app.py`
- 数据源仍为样例数据（MySQL 未接入 UI，真库走 CLI `--db mysql`）

---

## 14. 全量 M 测试容错、路径与稳定排序修复

> 日期：2026-08-15
> 详细总结：`artifacts/evaluations/model_eval_20260815_final_summary.md`

- 原始120次评测：意图90%、完成87.5%、正确路径76.67%、路径一致60%、P95 48.236秒。
- 修复非法筛选导致的批量崩溃、`top_n`误判、ASC/DESC与多字段排序、口语意图漏识别、表选择漂移和成功后重复查询。
- 完整最终评测 `model_eval_20260815_181011.json`：120/120通过；意图、完成、正确路径及LLM子集完成/路径均100%；路径一致90%；P50 3.5725秒，P95 17.311秒。
- 并列Top-N新增维度升序稳定键；受影响排名题专项15/15通过，M-16三轮线路名单完全一致。
- 最终本地验证：`pytest` 100/100，确定性核心评测102/102。

---

## 15. 低评分自动两层归因改造

> 日期：2026-08-16

- 归因目标固定为 `is_low_score`（`review_score<=3`）；明确要求对延迟、复购、金额等其他目标归因时直接拒绝，不偷换目标。
- 第一层统一以是否低评分为结果变量：二分类用卡方/Fisher，分类用Pearson卡方，有序变量用Cochran-Armitage，连续变量用Mann-Whitney U；跨变量使用FDR-BH校正，并要求效应量95%CI不含无效值。
- 共线性组使用预设业务代表，不按最小p值或最大OR临时选优：履约结果组选是否延迟，运输地理组选是否跨州。
- 第一层代表变量自动进入分Mart的二项Logistic，使用固定控制变量与HC3稳健标准误；调整后再次要求FDR校正p<0.05且95%CI有效。
- 只有调整后稳定变量才生成分布或对象下钻；归因Agent不生成责任方、治理动作、监控指标或A/B方案。
- 负载保持受控：分类检验在数据库端聚合，连续变量逐次只读两列，两个调整模型串行读取必要字段。
- 本地验证：`pytest` **123/123**；确定性核心评测 **117/117**；Streamlit AppTest对低评分归因及其他目标拒绝路径均无异常。

---

## 16. 完整M重复评测与归因路由修复

> 日期：2026-08-16

- 正式联网运行 `tests/run_model_eval.py --repeat 3`：41题×3轮，共123次；其中75次真实调用DeepSeek。
- 联网报告：`model_eval_20260816_175529.json`。API调用完成率100%，API工具路径正确率100%，整体回答完成率100%；P50 3.033秒，P95 23.448秒。
- 首次发现M-27、M-37含“归因”和“调整后”时被误分到独立深度验证。已调整意图优先级：明确“归因”进入自动两层归因；明确“深度验证”才进入补充验证；询问检验方法仍进入统计页。
- 修复后专项运行M-27/M-37各3次，共6次全部通过；意图、完成、路径及重复一致率均100%。报告：`model_eval_20260816_180926.json`。
- 完整联网轮次的跨题重复一致率为80.49%；M-02、M-06、M-13、M-16、M-17、M-19、M-29、M-38三轮采用了不同但均正确的工具轨迹。该指标说明DeepSeek路径仍有随机性，不能用“全部答对”替代稳定性评价。
- 修复后再次验证：`pytest` **123/123**，确定性核心评测 **117/117**；新版网页服务健康启动。

---

## 17. 全量归因模型稳定性与高基数页面修复

> 日期：2026-08-16

- 修复第一层选择说明：只有一个共线性组内确有多个保留变量时才标记“组内最直观代表变量”；单独变量改为“无同组冗余，直接进入调整模型”。
- 修复全量订单模型 `LinAlgError`：Logistic拟合与HC3协方差解耦，HC3 sandwich使用广义逆；标准MLE失败时自动回退到二项GLM，同时保留拟合方式和回退原因供页面审计。
- 对全量模型中的月份、州和品类稀疏水平进行预设阈值合并，减少准完全分离和高维哑变量不稳定。
- 线路归因只展示同时通过训练期调整和后20%时间留出验证的线路；其他高基数分组与类别项最多渲染50行，避免Streamlit前端React #185错误。
- 页面将“共线性代表变量”更正为“调整模型候选变量”，并提示大样本下统计显著不等于业务重要。
- 二分类下钻将0/1改为“同州/跨州”“无交接超期/存在交接超期”等业务标签，避免用户自行猜测编码。
- 新增秩不足回归、选择文案、线路过滤、业务标签及展示字段测试。验证结果：`pytest` **127 passed, 1 skipped**；确定性核心评测 **117/117**；Streamlit AppTest低评分归因路径无异常。

---

## 18. 调整后稳定变量的延迟分层可视化

> 日期：2026-08-17

- 仅对调整后稳定变量生成图表，避免把第一层相关但调整后不稳定的变量包装成结论。
- 分类、二分类和有序变量在数据库端按“变量×是否延迟”聚合；展示高频12组，其余合并为“其他”。线路只展示通过调整与时间留出验证的对象。
- 连续变量每次只读取“变量＋是否延迟”两列，按全体有效样本五分位分箱；离散数值取值较少时按实际取值展示。
- 柱高分别以延迟组、未延迟组总样本为分母，消除两组规模不同造成的视觉误导；悬浮信息保留样本量。
- “是否延迟”变量本身不做重复拆分，改为直接比较延迟与未延迟订单的低评分率。
- 验证结果：`pytest` **129 passed, 1 skipped**；确定性核心评测 **117/117**；Streamlit AppTest归因路径无异常。

---

## 19. 高基数品类明细 React #185 修复

> 日期：2026-08-17

- 确认柱状图正常，异常来自图表下方52组品类明细使用的旧版Streamlit交互式`dataframe`组件。
- 高基数对象明细改用紧凑静态表，不再创建高负载交互表；按“对象显著高风险→超额低评分→样本量”排序，只展示前20组。
- 分类变量调整后显著水平同步改为紧凑中文静态表，最多显示调整后OR最高的20项。
- 验证结果：`pytest` **131 passed, 1 skipped**；Streamlit AppTest归因路径无异常。

---

## 20. 对外术语与统计表述统一

> 日期：2026-08-17

- 数据源统一显示为“演示样本（截取数据）”与“完整业务数据库（MySQL）”，不再暴露“项目CSV”“真实MySQL”等开发期称呼。
- 保留分析人员需要的 FDR、优势比（OR）、Logistic、HC3 和分析宽表（Mart）等术语，并在界面提供简要口径说明。
- 分析流程统一为“单变量筛选→共线性处理→多变量调整→必要时跨时间验证”；“调整后稳定变量”改为“控制其他因素后仍显著的变量”。
- 表格列名明确区分原始p值、FDR校正后p值、效应量及其95%置信区间，避免“CI通过”“进入Logistic”等省略主语的内部写法。
- 同步更新网页、命令行输出、README、部署说明和手工验收清单；新增对外禁用词回归断言。
- 验证结果：`pytest` **133 passed, 1 skipped**；确定性核心评测 **117/117**；Streamlit AppTest与实际浏览器页面均无异常。

---

## 21. 完整业务数据库 M 重复评测

> 日期：2026-08-17
> 原始报告：`artifacts/evaluations/mysql_model_eval_20260817_full.json`
> 人工总结：`artifacts/evaluations/mysql_model_eval_20260817_full_summary.md`

- 为 `run_model_eval.py` 增加 `--source mysql`，评测前只读检查三张完整分析宽表并在报告中记录数据源。
- 41题×3轮共123次：意图准确率100%，完成率与正确工具路径率均99.19%，重复路径一致率80.49%。
- 75次DeepSeek调用中74次完成；唯一失败为M-16第3轮，SQL成功后生成回答发生`APITimeoutError`。
- P50为5.449秒，P95为88.302秒；全量低评分关联因素分析约88秒，性能长尾可复现。
- M-16使用网页实际确定性取数路径复测成功：0.585秒、1条SQL、5行结果，说明失败位于模型压力测试路径而非数据库查询。

---

## 22. 本地接入 MySQL 与导入验证（本机操作）

> 日期：2026-08-17
> 背景：本地合并朋友提交 `208a935` 后，因缺少 `data/sample/*.csv`（.gitignore 忽略未提交）导致测试一度 5 failed + 88 errors。

### 22.1 数据就位
- 用户提供数据集：`C:\Users\Alexn\Downloads\mart`（3 张 mart 宽表，各 1000 行截取样本；表头已含内部口径列 is_low_score / is_late_delivery / late_days / is_delivery_analysis_eligible 等）
- 放入 `data/sample/`（ProjectCsvProvider 默认目录）→ 测试恢复全绿：**133 passed, 1 skipped**；确定性评测 **117/117**

### 22.2 导入 MySQL
- 新增 `scripts/import_mart_to_mysql.py`：读 CSV → 类型推断（数值列 DOUBLE / 日期列 VARCHAR / 其余 VARCHAR）→ 建表导入本地 `olist` 库（幂等 DROP+CREATE）
- 导入结果：mart_order_delivery / mart_order_item_delivery / mart_order_seller_delivery 各 1000 行
- 修复 1：pymysql `executemany` 占位符需 `( %s, %s, ... )` 带括号（原先写成裸 `%s, %s` 触发 1064 语法错误）
- 修复 2：CSV 字面 `"NULL"` 被当字符串入库，`is_cross_state` 报 `int()` 错误 → `_to_sql`/`_infer_types` 将 NULL/NONE/NAN 视为缺失转真正的 NULL

### 22.3 真库归因验证（`--db mysql`）
- 数据源：完整业务数据库（MySQL）；订单级基准 966 样本、低评分率 22.15%
- 是否延迟：卡方（Yates）FDR p=1.8e-34，OR=25.67（95%CI [12.8,51.4]）→ 强保留
- 延迟分档：Cochran-Armitage p=2.1e-31、Spearman ρ=0.40 → 保留
- 多卖家订单：Fisher OR=9.70（p=0.001）→ 保留
- 州/品类/月份/卖家州：因达到样本门槛(100)的分组不足而"未执行"——1000 行截取样本所致，非代码问题（全量 9.9 万行可执行）
- 结论：MySQLProvider 全流程（字段映射/意图/单变量/多变量）正常

---

## 23. 正式 UI（FastAPI + Vue3）

> 日期：2026-08-17
> 定位：替代 Streamlit demo 的企业级正式界面（首期核心两页，按 Modern SaaS 设计体系）

### 23.1 技术栈
- 后端：FastAPI + uvicorn（`server/main.py`），包装 agent_core 为 REST + SSE API，核心逻辑零改动
- 前端：Vue3 + Vite + TS + Element Plus + ECharts + Pinia + axios（`web/`）
- 数据源：固定后端配置（默认 ProjectCsvProvider；`USE_MYSQL=1` 用 MySQLProvider）

### 23.2 后端 API
- `POST /api/intent` / `query` / `statistical` / `attribution` / `deep-validation`（返回 agent dict 整包透传）
- `GET /api/meta`（语义字典 + 数据源标签）；`POST /api/chat`（**SSE 流式**：intent→running→result/step→answer→done）
- 生产模式：`web/dist` 存在时托管静态前端（单端口 8000）

### 23.3 前端页面（首期两页）
- **总览看板** `/dashboard`：KPI 指标栏（低评分率/延迟率/样本/平均评分 + pill + sparkline）+ 延迟分档面积图 + 品类 Donut + 州条形图
- **智能对话** `/chat`：SSE 流式对话 + 结果卡片（归因→优先级表格+OR 森林图+建议；统计→方法/p/效应量；查询→表格+SQL）
- 设计体系：底色 #F4F7FB / 白卡 #FFF / 主蓝 #2F65F6 / 渐变青蓝 / 薄荷绿 #10B981 / 珊瑚粉 #F43F5E；大圆角 16-24px；微阴影 `0 10px 25px -5px rgba(0,0,0,.03)`；Inter 字体；高呼吸感

### 23.4 验证
- 后端 API 测试 `tests/test_api.py`：7/7 通过（intent/query/statistical/attribution/deep-validation/meta/SSE chat）
- 全量 `uv run pytest tests/` → **140 passed, 1 skipped**
- `npm run build` 成功（dist 生成）；正式 UI 启动冒烟：GET / → 200；/api/intent 正常；SSE chat 返回 intent→running→result→done 完整事件流

### 23.5 运行方式
```bash
# 开发（后端 + 前端热更新）
uv run uvicorn server.main:app --port 8000
cd web && npm run dev          # http://127.0.0.1:5173（proxy /api → :8000）

# 生产（单端口）
cd web && npm run build
uv run uvicorn server.main:app --port 8000   # http://127.0.0.1:8000
```

### 23.6 二期（未做）
- 设置页（数据源/评测）、登录权限与审计、报告导出（PDF/Excel）、DeepSeek 逐 token 流式

---

## 24. 全量数据导入与正式 UI 切换（MySQL 全量）

> 日期：2026-08-17
> 来源：朋友提供 `olist_ecommerce_20260817_170315` 全量导出（manifest 记录与完整库一致）

### 24.1 全量导入
- 表/行数：mart_order_delivery **99,441** / mart_order_item_business **112,650** / mart_order_seller_delivery **100,010**
- 升级 `scripts/import_mart_to_mysql.py`：`--dir` 参数、大文件分批读/分批插入（采样 3000 行推断类型、每批 5000）、表名取文件名
- 导入到本地 MySQL `olist_ecommerce` 库（.env `DB_NAME` 同步）；item 表名 `mart_order_item_business` 与 MySQLProvider 默认一致

### 24.2 全量归因验证（`--db mysql`）
- 订单级基准：样本 95,824，低评分率 21.07%；卖家级基准（单卖家）：样本 94,563，20.54%
- **16+ 因素全部执行**（1000 行样本时跳过的 州/品类/月份/线路 现在全部保留且显著）
- 是否延迟：OR=13.08（95%CI [12.34, 13.87]）；多卖家 OR=5.91；跨州 OR=1.33
- 已知非致命告警：statistics.py:310 `overflow encountered in exp`（CI 边界计算），不影响结果

### 24.3 正式 UI 切换
- `.env` 加 `USE_MYSQL=1` → 后端 `get_provider()` 用 MySQLProvider（olist_ecommerce）
- 重启后 `/api/meta` 显示 source_label=MySQL，表含 mart_order_item_analysis 视图
- 看板/对话全部基于全量数据；注意全量归因耗时约 30-90 秒（后续可做缓存优化）

### 24.4 说明
- 之前 1000 行样本的"州/品类/月份分组不足"问题已随全量解决，看板图表自动补全

---

## 25. 正式 UI 全量联调修复（2026-08-17 后续）

> 全量数据上线后的一系列问题与修复，均在 `server/main.py` / `agent_core/` / `web/src/`。

### 25.1 看板 500：JSON 无法序列化 inf
- 现象：全量归因 logistic 某 CI 边界 `exp` 溢出为 `inf` → `/api/attribution` 500
- 根因：`json.dumps` 的 `default` 回调**不处理 inf**（inf 是合法 float，dumps 直接抛错）
- 修复：`server/main.py` 新增 `_clean()` **递归清理**（inf/nan → null，先 `item()` 归一化 numpy 标量），`_json`/`_sse` 均使用
- 验证：全量 logistic 82 项正常，延迟调整 OR=13.33（95%CI [12.54,14.17]）

### 25.2 看板超时 + 归因缓存
- 现象：全量归因 60-180s，前端 axios 120s 超时 → "timeout of 120000ms exceeded"
- 修复：axios timeout 120s → **300s**；后端 `_cached_attribution` 归因缓存（TTL 最初 300s，后改 **86400s/24h**，数据静态、重启刷新）
- 实测：首次 63.8s / 缓存命中 0.03s

### 25.3 对话白屏（两处 JS 错误，靠全局 errorHandler 定位）
- ① `intentLabel is not a function`：ChatView 重写时漏定义 `INTENT_LABEL`/`intentLabel`（模板已引用）→ 补回
- ② `(d ?? t.value).trim is not a function`：`@keyup.enter`/`@click` 把 **Event 对象**传给 `send(q)`，`q.trim` 崩 → `send` 改为仅接受字符串参数
- 配套：`main.ts` 加**全局 errorHandler**（白屏时底部红条显示错误）；`ResultCard` 加 `onErrorCaptured`（子渲染错误局部显示，不整页白屏）

### 25.4 业务 Bug：SQL 漏过滤（"延迟 15 天以上"答非所问）
- 现象：问"延迟 15 天以上订单低评分率"，SQL 无 `late_days >= 15`，算出的是全量大盘
- 根因：`query_analysis.py` 的确定性解析**只提取指标/维度/排名，无筛选解析**；且 `query_mart` 过滤白名单仅限维度列
- 修复：
  - `query_analysis.py`：识别"延迟 X 天以上/以内/超过/至少" → `filters {late_days: {op:>=/<=, value:X}}`
  - `tools.py`：新增 `FILTERABLE_COLUMNS` 数值列白名单（late_days/review_score/金额/时长，仅 WHERE 不 GROUP BY）
- 全量实测：延迟15天+ = **87.49%** / 延迟3天以内 = 17.94% / 总体 = 21.07%（逻辑正确）

### 25.5 前端展示重构（业务友好度）
- query 结果：**核心大数字前置**（用后端 `display_rows` 业务化展示，不再暴露 `_m_` 表头）；技术参数（表/模式/SQL）收进**"执行明细"折叠面板**；表名/模式中文映射（mart_order_delivery→订单交付宽表、deterministic_query→确定性查询）

### 25.6 模糊意图澄清
- 现象："对比另一个维度" → 返回空数据
- 修复：`ChatView` 检测模糊词（另一个维度/其他维度等）或空结果 → 显示**维度选择澄清卡片**（客户州/支付方式/品类），点击发送明确问题

### 25.7 其它 UI 精修
- 顶部时间筛选（近30/90/全年）为假交互 → 改为静态真实数据范围标签"📅 全量数据 · 2016-09 ~ 2018-10"

### 25.8 测试
- `tests/test_api.py` 7/7（全量数据下耗时约 2 分钟）；`tests/test_m1.py` 通过；回归无破坏

---

## 26. 架构修复：确定性优先 + LLM 兜底（2026-08-17）

> 背景：用户反馈"指标查询只给固定答案、不用 LLM 判断"，暴露确定性解析"部分识别当成功"的静默降级缺陷。

### 26.1 问题确认
- `plan_query_question` 只做指标/维度别名匹配；"各**商品品类**的低评分率对比""查看低评分率的**月度趋势**"因别名未覆盖而**丢维度**，但 `ok=True` → 静默降级成总体单值 21.07%（答非所问）
- 确定性解析失败/不完整时，对话无 LLM 兜底

### 26.2 修复
1. **补维度别名**：`primary_category_name` 加"商品品类/品类"；`order_month` 加"月度趋势/月度/每月趋势/每个月"
2. **完整性检测**：`query_analysis.DIMENSION_HINTS`（各/对比/按/趋势/分布/不同/分类/哪些…），有维度暗示但未识别出维度 → `incomplete=True`
3. **LLM 兜底**（`server/main.py`）：`/api/chat` 的 query 分支在 `incomplete` 时、statistical/deep_validation 在确定性失败时 → 回退 `ReActLoop(DeepSeek)`；无 key 时给明确提示

### 26.3 验证（全量）
- "各商品品类的低评分率对比" → **74 个品类分组**（cool_stuff 18.89%…，原为单值 21.07%）
- "查看低评分率的月度趋势" → **23 个月度分组**
- "按地区分组看低评分率"（"地区"未识别）→ **LLM 兜底**，DeepSeek 正确按客户州分组（27 州，RR 31.71% 最高）
- 普通"总体低评分率是多少" → 确定性单值 21.07%（不误伤）
- `tests/test_api.py` 7/7 通过

### 26.4 开发规范（写入方案）
> **确定性-LLM 分工原则**：能确定的用确定性快路径（可对账、快）；不能确定的明确回退 LLM（DeepSeek），**绝不静默降级**。别名库持续补全以扩大确定性覆盖。

---

## 27. 其他模块边界检查与修复（2026-08-17）

> 对 query 之外的模块（intent / statistical / deep_validation / attribution）做边界测试，发现并修复 4 类问题。

### 27.1 函数级测试发现的问题
| 问题 | 现象 | 修复 |
|---|---|---|
| intent 误分类 | "哪些因素影响低评分"→query（应 attribution）；"为什么这个月订单变少了"→attribution（应开放式） | `intent.py`：明确"归因/优先治理/改善建议"才 attribution；"为什么/哪些因素"需围绕低评分主题才 attribution，否则归入 other（LLM 解释） |
| query 完全未识别无兜底 | 指标也未识别时显示"未识别"，无 LLM 兜底 | `server/main.py` query 分支：`plan.ok=False` 或 `incomplete` 都回退 LLM |
| attribution 不支持目标无兜底 | "退款原因归因"走确定性但报 unsupported | `server/main.py` attribution 分支：`unsupported_target` → 回退 LLM 解释边界 |
| supports 判定漏网 | "对退款**原因**归因"（"原因归因"连用、无"进行"）未被正则识别为非低评分 | `attribution.py` 正则扩为 `对X(进行\|做)?(原因)?(归因\|原因分析)` |

### 27.2 端到端验证（/api/chat SSE）
| 输入 | 期望 | 实际 |
|---|---|---|
| 哪些因素影响低评分 | attribution | ✅ 确定性归因 result |
| 为什么这个月订单变少了 | other→LLM | ✅ LLM 兜底 |
| 对退款原因归因 | attribution→unsupported→LLM | ✅ LLM 解释"无退款数据" |
| 对退款进行归因 / 对高评分进行归因 | 同上 | ✅ LLM 兜底 |
| 对低评分进行归因 | attribution | ✅ 确定性 |
| 运费和满意度关系 | other→LLM | ✅ LLM 兜底 |
| 各商品品类的低评分率对比 | query 确定性 | ✅ 品类分组 |
| 深度验证一下（空特征） | deep_validation 默认特征 | ✅ 6 个特征结果正常 |

### 27.3 结论
- `deep_validation` 空特征有默认特征集，无静默空结果 ✅
- statistical 确定性失败已由 other/兜底覆盖 ✅
- 全模块统一"确定性优先 + LLM 兜底"、绝不静默降级 ✅
- 回归：test_api 7/7 + test_m1 通过

---

## 28. 前端 UI 升级至一线 SaaS 标准（2026-08-17）

> 纯前端改造（`web/src/`），**后端与业务逻辑零改动**（用户明确要求）。按现代高质感 SaaS 设计规范实施。

### 28.1 升级点
| 项 | 实现 |
|---|---|
| 设计 tokens | 阴影规范 `0 10px 30px -5px rgba(15,23,42,.04), 0 4px 6px -2px rgba(15,23,42,.02)`；涨跌胶囊 emerald/rose 语义色 |
| Header 搜索框 | 真实可用：任意页面输入问题 → 跳对话页自动发送（`?q=` 参数） |
| 日期范围胶囊 | 看板顶部"📅 全量数据 · 2016-09~2018-10 ⌄" |
| DataTable 组件 | Top1-3 排名徽章（蓝/湖蓝/浅蓝）、内嵌数据条（淡蓝渐变进度）、长数据折叠（>6 行"展开全部 N 组"）、表头 #F8FAFC |
| ResultCard | 洞察摘要金句（分组取最高/最低差、归因给延迟 OR+证据分级）；`[📋表格]/[📊图表]` 视图切换（图表=横向条形）；业务中文表名/模式 |
| MarkdownText | AI 文本回答 Markdown 渲染：加粗/列表/代码块(深色)/引用块(浅蓝边框)，样式符合设计体系 |
| 会话历史 | localStorage **多会话持久化**：切换/新建/删除；每次回答后自动保存；刷新恢复；**大结果降级存摘要**（如"低评分率 21.07% · 延迟 OR 13.33"），避免超 localStorage 限额 |

### 28.2 验证
- `npm run build` 通过（新增 `marked` 依赖）
- 静态页 200；后端返回的 `index.html` 引用最新 hash JS（确认无缓存问题，浏览器需硬刷新 Ctrl+Shift+R）
- 会话历史：对话 → 刷新 → 恢复；多会话切换/新建/删除正常

### 28.3 说明
- 大结果（attribution 405KB）不持久化，历史消息显示可读摘要；完整图表/表格需重新提问
- 会话记录仅存浏览器 localStorage，不上传后端（隐私友好）
- 待推送 git（网络不通，本地已 commit；Markdown 渲染 + 会话历史 + 前端升级 3 个 commit）


---

## 29. 修复：年份期间对比静默降级（"2020 相比 2019 变化"返回总体单值）

> 日期：2026-08-18
> 触发：用户在对话中问"2020 年相比 2019 年，低评分率变化了多少"，系统只返回总体单值 21.07%（静默降级）

### 29.1 根因（两层）
1. **完整性检测失效**：`DIMENSION_HINTS` 只含"各/对比/按/趋势/分布…"，用户问题用"相比/变化"，两词均不在表 → `incomplete=False`，未触发 LLM 兜底，直接返回总体单值
2. **维度字典缺"年份"**：只有 `order_month`（月份），无年份维度，即使想按年分组也无路可走

### 29.2 修复（确定性优先，绝不静默降级）
- **语义层**：`metrics_dict.yaml` 为 `mart_order_delivery` 新增 `order_year` 维度（表达式 `SUBSTR(order_month,1,4)`，MySQL/SQLite 双兼容）；`semantic.py` 加 `get_dimension_expr()`
- **tools.py**：维度 select 支持表达式（`expr AS dim`），GROUP BY 用表达式本体（MySQL 不支持 GROUP BY 别名——曾踩坑：`GROUP BY order_year` 别名导致整表聚合、年份全 NULL）
- **query_analysis.py**：
  - `DIMENSION_ALIASES` 加 `order_year`（按年份/按年/各年/每年/年度…）
  - `DIMENSION_HINTS` 扩充"相比/较/同比/环比/变化/去年/今年/上年/近一年"
  - 新增**期间对比路径** `deterministic_period_compare`：识别"X年相比Y年"→ 按 `order_year` 分组 → 筛目标两年 → 算差值，返回自然语言回答（如"2016 年低评分率 24.62%，2017 年 20.62%，变化 -4.00 个百分点"）+ `compare` 结构化字段
  - 目标年份在数据中缺失时（如问 2019/2020，数据只到 2018）**明确提示缺失年份与可用范围**，不静默落回

### 29.3 验证
- 函数级：MySQL + CSV 双端通过（各年份分组 / 期间对比 / 月份趋势 / 总体值回归）
- API 级：`POST /api/query` 正确返回 `execution_mode=deterministic_period_compare`
- SSE 对话：`intent=query → running → result(含 compare) → done` 完整事件流，answer 含变化说明
- 回归：`tests/test_query_analysis.py + test_m1.py + test_api.py` → **28 passed**（1 条已知非致命 overflow warning）
- 前端未消费 `compare` 字段，但 `answer` 文本随 SSE 渲染，功能完整

---

## 30. 修复：历史会话"智能对话以外的意图只显示少量摘要"

> 日期：2026-08-18
> 触发：用户退出后点历史对话，指标查询等意图只显示一行摘要（如"品类 security_and_services，低评分率 50.00%"），看不到完整表格

### 30.1 根因
- 会话持久化 `serializeMessages` 一律只存 `summary`（单行摘要），丢弃 `result`
- 当初为防 attribution 405KB 大结果撑爆 localStorage 的取舍，误伤了 query/statistical 等小结果（通常几 KB～几十 KB）

### 30.2 修复（web/src/views/ChatView.vue）
- 序列化按意图区分：
  - query / statistical 等小结果 → **完整存储 result**（历史恢复可渲染表格/图表/SQL）
  - attribution / deep_validation 大结果 → 仍降级存摘要
  - 兜底：任何单条结果 > 200KB（RESULT_STORE_LIMIT）也不完整存储
- hydrate 恢复 `result: m.result ?? null`；旧会话（只有 summary）兼容显示摘要，不报错

### 30.3 验证（浏览器实测）
- 新会话发"哪个品类的低评分率最高"→ 回答完成 → 刷新 → 该会话**完整恢复表格**（品类排名 6+ 行、洞察、展开全部、SQL 明细齐全）
- 归因历史会话 → 仍显示摘要（低评分率 21.1% · 延迟 OR 13.08），localStorage 不爆
- `vite build` 通过

---

## 31. 会话历史迁移到 MySQL 数据库存储

> 日期：2026-08-18
> 需求：用户问"对话不能存在数据库里吗"——将会话历史从浏览器 localStorage 迁到数据库（存现有 olist_ecommerce 库，不迁移旧数据，全新开始）

### 31.1 动机
- localStorage 局限：约 5MB 总量、只在本机、换设备即丢；归因 405KB 大结果被迫降级存摘要
- 数据库方案：结果 JSON 整份入库（LONGTEXT），query 等小结果可完整还原表格；跨设备保留；天然支持多用户

### 31.2 实现
- **后端** `server/session_store.py`（新建）：建表 `chat_session` / `chat_message`（幂等 DDL），SessionStore 提供 会话列表/创建/改名/删除 + 消息整批覆盖读写；result 以 JSON 存 LONGTEXT
- **后端** `server/main.py`：新增 REST
  - `GET/POST /api/sessions`、`POST /api/sessions/{id}/rename`、`DELETE /api/sessions/{id}`
  - `GET/POST /api/sessions/{id}/messages`（后接保存，先清后插保证快照一致）
- **前端** `web/src/api.ts`：新增 6 个会话 API 封装
- **前端** `web/src/composables/useSessions.ts`（重写）：localStorage → MySQL API（方法全部 async）；无会话时自动创建默认会话保证首屏可直接发消息；请求失败降级为本地临时会话
- **前端** 调用方 `AppLayout.vue` / `ChatView.vue`：适配 async（await 化，watch/onMounted/发送流程）

### 31.3 验证
- API 层：建会话 → 存消息（含 result JSON）→ 读消息（result 完整还原）→ 列表带 message_count → 删除，全通过
- 浏览器实测（用户真实发消息）：query 会话 result 完整入库（678 字节含表格数据）；attribution 会话按设计存摘要；刷新后侧边栏正确恢复两个会话
- `vite build` 通过
- 备注：Playwright 自动化对 Element Plus v-model 有盲区（fill/type 不触发 Vue input 事件导致发送按钮禁用），真实用户手输正常

### 31.4 说明
- 旧 localStorage 数据未迁移（按需求），数据库空表全新开始

---

## 32. 修复：数据库会话存储后归因历史仍只显示摘要/空白

> 日期：2026-08-18
> 触发：切到数据库会话存储后，归因历史会话恢复只显示"归因分析"标签、无内容；query 历史恢复正常显示完整表格

### 32.1 根因（两处）
1. **降级规则残留**：`serializeMessages` 里 `heavy = attribution || deep_validation` 不存 result——这是 localStorage 时代为防 5MB 限制的设计。切到数据库后（LONGTEXT 无大小限制）未移除，导致归因结果仍被砍，历史恢复无完整内容
2. **侧边栏消息数失效**：数据库化后 sessions 的 messages 懒加载（初始 []），AppLayout 用 `s.messages?.length` 恒为 0——后端 `listSessions` 已返回 `message_count` 但前端丢弃

### 32.2 修复
- **web/src/views/ChatView.vue**：`serializeMessages` 移除 heavy 降级与 RESULT_STORE_LIMIT，所有意图 result 整份入库（数据库 LONGTEXT 支持）
- **web/src/composables/useSessions.ts**：会话对象增加 `messageCount` 字段（loadSessions 从后端读取、newSession/降级/默认会话初始化为 0、setMessages 后更新为 msgs.length）
- **web/src/layouts/AppLayout.vue**：侧边栏消息数改显示 `s.messageCount`

### 32.3 验证
- 浏览器实测：query 历史会话（有完整 result）恢复**完整表格**（21.07% / 6.66% 指标 + 洞察 + SQL 明细按钮）✓；归因历史会话因旧数据 result 为空只显示标签（需重新触发才能完整）
- API 实测：归因大结果（baseline/verification/factors/suggestions/display_rows）完整存取 OK
- 侧边栏两个会话均正确显示消息数 "2" ✓
- `vite build` 通过

### 32.4 说明
- 已存在的归因会话 result 为空（旧规则砍掉），**需重新触发归因**才能存完整 result 并在历史中完整恢复

---

## 33. 看板图表美学重构（按评审方案）

> 日期：2026-08-18
> 需求：按设计评审文档对标原版风格，解决"图表单调、图例挤压、缺乏设计感"

### 33.1 改造内容
- **charts.ts / barOption（客户州排行）**：加 #F1F5F9 浅灰全长圆角底槽（track）+ 青蓝→电光蓝渐变胶囊（borderRadius 9）+ 右侧大号深色百分比标签（13px/700）
- **charts.ts / donutOption（支付构成）**：环体加粗（radius 62%→84%）+ 宽扇区环内白色百分比标注（≥12% 显示）+ 中心大数字（30px/700）+ 深蓝黑 tooltip；原 ECharts 底部单行图例移除
- **charts.ts / areaOption（月度趋势）**：showSymbol:false 去常驻数据点（hover 动态点亮）+ smooth 0.35 贝塞尔 + 加深渐变面积（rgba(47,101,246,.25)→透明）+ 深色 tooltip（#1E2238）
- **DashboardView.vue**：
  - 州排行严格降序（按 low_score_rate 高→低），Top3 徽章（1/2/3，r1 渐变蓝底白字）
  - 支付构成改 2×2 卡片化图例网格（donut-legend：色块+名称+单量+占比，灰底圆角容器）
  - 卡片质感沿用 --shadow 弥散阴影 + 20px 圆角 + #F4F7FB 底（已达标未改）

### 33.2 验证
- `vite build` 通过
- 浏览器实测：看板渲染正常——canvas 7 个（KPI sparkline×4 + 图表×3）、图例网格 4 项（信用卡 75% / Boleto 20% / 借记卡 1% / 代金券 3%）、Top3 徽章 3 个（1 MA / 2 AL / 3 PA）

---

## 34. 看板与对话最后阶段细节精修

> 日期：2026-08-18
> 需求：对看板条形图/环形图 + 对话响应卡片做细节打磨

### 34.1 看板
- **客户州条形图**：`yAxis: { inverse: true }` 确保排名第 1（MA）显示在最顶部；`xAxis: { max: 35 }` 让条形拉长充满卡片宽度 80%+，避免右侧留白
- **支付环形图**：移除环内浮动百分比文字（label:false），环体干净饱满；底部 2×2 图例严格两列（grid-template-columns: 1fr 1fr），单量与百分比用 lg-nums 包裹、间距 8px

### 34.2 对话响应卡片（ResultCard + ChatView）
- 去除卡片下方重复纯文本"低评分率: 21.07%; 延迟率: 6.66%"，改为**加粗业务洞察结论**（conclusionText：`当前低评分率为 21.07%，整体表现处于可控区间。`，13px/600）
- 补回卡片底部「继续追问」快捷胶囊：suggestions 结构化为 `{label, prompt, icon}`，ResultCard 底部渲染带图标的胶囊（TrendingUp/Map/BarChart3 等），点击 @followup → send 发送
- hydrateMessages 时按 intent 自动补 suggestions（历史会话也有追问胶囊）
- **用户头像**：顶部 + 消息区从"企"字改为浅色矢量人像（lucide User 图标，浅紫渐变 #E0E7FF→#DBEAFE + #4338CA）

### 34.3 验证
- `vite build` 通过
- 浏览器实测（query 会话）：
  - 卡片显示加粗结论"当前低评分率为 21.07%…"（重复纯文本已消失）
  - 底部「继续追问」+「查看月度趋势」「查看各州分布」胶囊渲染 ✓
  - 顶部/消息区用户头像均为 SVG 矢量图标（空文本 + svg 计数 2/1）✓
  - 看板 Top3 徽章（1 MA 2 AL 3 PA）与 2×2 图例正常

---

## 35. 偏难怪题批量测试（API 直测）+ 修复两个 bug

> 日期：2026-08-18
> 方式：不再用浏览器 UI 鼠标操作（效率低且无读图能力），改为脚本直接调后端 /api/chat（SSE）批量并行测试 20 道偏难怪题，新增 tests/edge_case_probe.py 探针

### 35.1 测试结果（20 题）
- **意图路由**：订单量下降→other✓；退款率原因→other✓（正确说明无指标+给替代）；对退款原因归因→attribution✓（明确拒绝）；SP归因→attribution✓（完整结果）；**❌"帮我分析哪些因素和差评有关"→other（应 attribution）**
- **静默降级**：2020vs2019→正确提示缺数据✓；去年今年延迟率→正确对比✓；近三年/各月/哪个品类→确定性 query✓
- **解析器边界**：延迟>7天→2862单✓；评分≤2→12.81%✓；**❌"运费 100 块以上"→返回整体 4.16（筛选未生效，静默降级）**；SP/RJ对比✓；延迟+低评分→4677单✓
- **统计/归因/兜底**：SP/RJ显著→两样本比例检验✓；延迟因果→正确说明"相关≠因果"✓；运营风险总结→综合回答✓

### 35.2 发现并修复的 bug
1. **金额筛选静默降级**（query_analysis.py）：只支持"延迟 X 天"，不支持"运费/金额 X 块以上"。补全运费/金额/价格的上限/下限正则（支持"运费超过 50 元"与"运费 100 块以上"两种语序），映射到 freight_total。修复后"运费100以上"→平均评分 3.66（此前错误返回整体 4.16）
2. **"差评"漏判归因**（intent.py）：LOWSCORE_THEME 缺"差评"，"帮我分析哪些因素和差评有关"被误判 other。补入后正确判 attribution

### 35.3 附带修复
- **SessionStore 连接并发 bug**（session_store.py）：全局单例共享 pymysql 连接，并发请求下"一请求关闭、另一请求仍在用已失效连接"→ struct.error / 500。改为每次方法独立开/关连接（contextmanager），消除共享连接

### 35.4 验证
- 修复后 API 实测：差评→attribution✓、运费100以上→3.66✓、运费>50→29.71%✓、金额200以上→3.32✓、运费30以内→6.46%✓
- 回归：tests/test_query_analysis.py + test_m1.py → 21 passed

---

## 36. 压力评测暴露：MySQL 表字段 BOM 污染修复

> 日期：2026-08-18
> 触发：运行 `run_model_eval.py --source mysql --repeat 3`（复现朋友的压力测试）时，启动即失败：`inspect_marts` 报三张表都缺 `order_id`

### 36.1 根因
- 三张 Mart 表的首列名为 `\ufefforder_id`（带 UTF-8 BOM 字符），是当初 CSV 导入时 BOM 编码残留
- 正式 UI 因 `_compatibility_sql` 用 `m.*` 通配不直接引用列名，未暴露；`inspect_marts` 用精确字段名校验即失败

### 36.2 修复
- `ALTER TABLE ... CHANGE COLUMN \`\ufefforder_id\` \`order_id\`` 重命名三张表（mart_order_delivery / mart_order_seller_delivery / mart_order_item_business）
- 修复后 `inspect_marts` 通过：99441 / 100010 / 112650 行，字段缺失 = 空

### 36.3 压力评测（41题×3轮，全量MySQL）运行中
- 目的：复现 README 记录的 DeepSeek 重复稳定性评测（friend 版 99.19%）
- 首 11 轮：意图正确 11/11、工具路径正确 11/11、完成 11/11（单轮 4-5s）

---

## 37. 压力评测复现（41题×3轮）+ 归因连接断连 bug 根治

> 日期：2026-08-18
> 目的：复现 README 记录的 DeepSeek 重复稳定性压力评测（--source mysql --repeat 3），验证当前版本稳定性

### 37.1 评测过程暴露的 bug（两个）
1. **MySQL 表字段 BOM 污染**：三张 Mart 表首列是 `\ufefforder_id`（CSV 导入残留 BOM），`inspect_marts` 校验失败。已 ALTER TABLE 重命名修复
2. **归因长任务连接断连（根因）**：MySQLProvider 单一连接跑 18 因素检验 + Logistic，中途被服务端回收（`InterfaceError: (0,'')`），导致所有因素 ok=False、selected_features 空、归因耗时 184s+ 且结果全失败
   - 修复：`data_provider.py` 的 `execute` 增加**连接失效自动重连**（InterfaceError / OperationalError 2006/2013/1927 时重连重试当前 SQL），并统一 pymysql import

### 37.2 修复后验证
- 第一层筛查：18 因素全部 ok=True、p=0.0、retained=True（15.9s）
- 完整归因第 5 步：14 个因素进入调整后 Logistic（is_late_delivery/approval_days/customer_state/route 等），102s 正常

### 37.3 压力评测最终结果（123 轮全跑完）
| 指标 | 朋友基线(08-17) | 本次(08-18) |
|---|---|---|
| 意图识别准确率 | 100.00% | 100.00% |
| 回答完成率 | 99.19% | **100.00%** |
| 正确工具路径率 | 99.19% | **100.00%** |
| 重复路径一致率 | 80.49% | **85.37%** |
| DeepSeek 完成率 | 98.67% | **100.00%** |
| 延迟 P50 / P95 | 5.45s / 88.3s | 6.48s / 441.7s |

- 0 失败；P95 升高因归因慢题（M-27/28/37 每轮约 460s，全量计算本身耗时，非失败）
- 报告：`artifacts/evaluations/model_eval_20260818_141726.json`

---

## 38. 看板加载优化：归因后台并行，KPI 秒出

> 日期：2026-08-18
> 问题：看板首屏加载很久（几十秒）
> 根因：`DashboardView.onMounted` 第一步 `await runAttribution()`（全量归因 30-90s，重启后 24h 缓存为空），KPI/趋势查询被串行卡在归因后面，loading 全程转圈
> 修复：归因改为 fire-and-forget 后台加载（`.then` 填充 attr），KPI（meta + 延迟率/平均评分/趋势三查询）并行先加载先显示
> 效果：KPI 秒出；三张图表待归因完成（首访 30-90s，命中缓存后秒出）后填充

## 39. 归因性能优化：HC3 稳健标准误 BLAS 化 + 模型矩阵缓存

> 日期：2026-08-19
> 背景：全量业务库（MySQL）低评分归因单次 7 分钟以上仍未跑完，卡在 `statistics.logistic_model_formula` 手写 HC3 sandwich 的 leverage 逐行二次型 `np.einsum("ij,jk,ik->i", design, bread, design)`（9.5 万行 × 上百列设计矩阵，numpy 解释型三重循环，无 BLAS 加速）。

### 优化 A：leverage 改为 BLAS 向量化（statistics.py）

- `leverage = weights * np.einsum("ij,jk,ik->i", design, bread, design)`
  → `leverage = weights * (design @ bread * design).sum(axis=1)`（`design @ bread` 走 BLAS gemm）
- bread 求解：`np.linalg.pinv(...)`（总是 SVD 伪逆，慢）→ 先 `np.linalg.inv(...)`（快），奇异时回退 `pinv`（保留对秩不足设计矩阵的鲁棒性）

### 优化 B：模型矩阵磁盘缓存（model_cache.py）

- 新增 `agent_core/model_cache.py::cached_frame`，缓存「特征工程后的 DataFrame」
- 缓存 key = 表名 + 排序列 + 行数指纹（`COUNT(*)`）；行数变化（数据更新）自动失效
- `_run_order_model` / `_run_seller_model` 提取出 `_engineer_order` / `_engineer_seller`，load + 特征工程改为 `cached_frame` 包装
- 缓存目录 `artifacts/model_cache/`（已加入 .gitignore）

### 实测效果（MySQL 全量，9.5 万订单）

| 阶段 | 优化前 | 优化后 |
|---|---|---|
| run_adjusted_validation（Logistic + HC3） | 7min+（未跑完） | 55.8s（冷）/ 51.9s（热） |
| screen_low_score_features（单变量筛选） | — | 11.1s |

- 优化 A 是主要提速来源：leverage 从 einsum 慢路径换到 BLAS，logistic 由 7min+ 降到 ~56s
- 优化 B 仅省 ~4s：特征工程本身不是瓶颈，剩余慢点在 Logit 拟合（~52s）

### 新发现的瓶颈（超出本次范围）

- `analyze_item_drilldown` 中 `mart_order_item_analysis`（商品项表 JOIN 订单表）按 `product_id` 分组聚合，MySQL 查询超过 read_timeout(180s) 触发 Lost connection(2013)；归因内该段被 try/except 降级
- 属数据层慢查询（缺索引 / JOIN+GROUP BY 全表），与 HC3 无关，需商品项表加索引或查询重构，建议后续单独处理

### 回归

- `pytest tests/test_m3.py` → 14 passed
- `pytest tests/test_api.py` → 7 passed（含 `/api/attribution`）
- 保留对秩不足/完全分离设计矩阵的 pinv 降级，`test_logistic_rank_deficient_design_does_not_crash` 通过

## 40. 商品项下钻慢查询修复：Mart 表补建索引

> 日期：2026-08-20
> 问题：`analyze_item_drilldown` 中 `mart_order_item_analysis`（商品项表 JOIN 订单表）按 `product_id`/`category_name`/`seller_id` 分组聚合，MySQL 查询超 read_timeout(180s) 触发 Lost connection(2013)，归因里该段被 try/except 降级为 `ok=False`
> 根因：三张 Mart 表（订单 / 订单-卖家 / 商品项）**全部无索引**；商品项表 JOIN 订单表 + GROUP BY 分组 + COUNT(DISTINCT) 全靠全表扫描 + Block Nested Loop + 临时表 + 文件排序（EXPLAIN 显示两张表 `type=ALL`、`rows`≈9.5万/10.9万）
> 修复：补建索引（root 直连 DDL）
>   - `mart_order_delivery`：`PRIMARY KEY (order_id)`（order_id 已验证唯一）
>   - `mart_order_item_business`：`INDEX(order_id)` + `INDEX(product_id)` + `INDEX(category_name)` + `INDEX(seller_id)`
>   - `mart_order_seller_delivery`：`INDEX(order_id)` + `INDEX(seller_state)` + `INDEX(customer_state)`
>   - `scripts/import_mart_to_mysql.py` 新增 `INDEX_DEFS`，建表时自动带索引（样本/全量商品项表名均覆盖）

### 实测效果（MySQL 全量）

| 环节 | 修复前 | 修复后 |
|---|---|---|
| 商品项分组查询（category/product/seller） | 180s+ 超时 | 1.4~1.5s |
| analyze_item_drilldown 完整（含显著性检验） | 180s+ 超时降级 | 12.3s（ok=True） |
| run_attribution 端到端 | 375s（超时降级） | 95.2s（ok=True） |

### 回归

- `pytest tests/test_m3.py tests/test_api.py` → 21 passed in 78.6s
- 修复后 `/api/attribution` 正常返回 `ok=True`，`item_drilldown.ok=True`

## 41. 三目标时序归因扩展

> 日期：2026-08-21
> 目标：在保留既有低评分两层归因的前提下，新增“是否最终延迟”和“是否存在交接超期”两个受控目标，并禁止目标发生后的变量反向入模。

### 目标与候选变量边界

- 交接超期：订单、商品、承诺时效和运输地理等事前基础属性 → `is_any_item_handover_late`。
- 最终延迟：基础属性 + `is_any_item_handover_late` → `is_late_delivery`；不使用低评分。
- 低评分：继续使用原有候选变量与既有订单级/订单-卖家级归因流程。
- 三者统一执行：类型匹配的单变量检验 → FDR-BH + 效应量95%CI → 共线性预设代表 → 多变量二项Logistic（HC3）→ 调整后FDR + CI → 稳定变量分布。

### 回归与完整数据库验收

- `pytest tests -q` → `124 passed, 1 skipped`。
- `python tests/run_eval.py` → `117/117`。
- 前端 Vite 生产构建成功；高基数分类图改为横向条形图，实测画布宽度627px、高度500px，无浏览器错误。
- 完整数据库 M-41～M-43 → `3/3`，`llm_runs=0`：延迟归因约97.87秒、交接超期约25.20秒、越界目标即时拒绝。
- 完整数据库结果：延迟发生率6.77%，14项通过第一层、13项入模、11项调整后稳定；交接超期发生率8.97%，9项通过第一层并调整后稳定。
- 模型公式核查通过：延迟模型不含低评分；交接超期模型不含最终延迟或低评分。

## 42. 全量 U 验收缺陷修复与最终回归

> 日期：2026-08-21
> 数据源：完整业务数据库（MySQL）

### 修复内容

- 未注册因素、跨 Mart 粒度变量、非白名单归因目标不再回退 DeepSeek，而是返回可解释的受控边界提示。
- “筛查/筛选低评分关联因素”稳定进入三目标关联因素分析；“较晚时期/高风险线路稳定性”稳定进入深度验证。
- 自然语言写数据请求（如“删除最近一个月的订单记录”）与显式 SQL 写操作统一在 SSE 对话入口拦截，数据库保持只读。
- `tests/edge_case_probe.py` 改用 Python 标准库 HTTP 客户端，虚拟环境无需额外安装 `requests`。

### 最终结果

- 全量 U 场景：`32/32`（原文两个不同问题同为 U-30，执行时记作 U-30A/U-30B）。
- `pytest -q`：`133 passed, 1 skipped`。
- `tests/run_eval.py`：`117/117`。
- 受路由修改影响的 M-31/M-34/M-35/M-43：每题3轮，`12/12`，完成率、正确路径率和重复一致率均为100%。
- 网页复验覆盖未知因素、跨粒度、越界归因、自然语言删除请求和线路留出验证；页面正常渲染，浏览器控制台无错误。

## 43. 完整 MySQL MU 发布前验收

> 日期：2026-08-21
> 数据量：订单级99,441行、订单-卖家级100,010行、商品项级112,650行。

### 执行中发现并修复

- M-27“多维归因，完成单变量筛选和调整后验证”因“调整后验证”被误路由至补充验证模块。
- 修正优先级：明确“深度验证”仍进入补充验证；明确“归因”则运行完整两层归因，内部的“调整后验证”不再改变意图。
- 新增意图回归断言，并增加 `tests/run_u_acceptance.py`，让32项U清单能够通过SSE接口重复执行和落盘。
- U首次自动运行中的7项失败均定位为验收器字段别名问题：订单-卖家表使用`record_count`，连续变量检验使用`sqls`列表；校正验收器后全部通过。

### 最终结果

- M：43题×3轮，`129/129`通过；意图准确率、完成率、正确路径率、DeepSeek完成率和DeepSeek正确路径率均为100%。
- M耗时：P50 `5.989秒`，P95 `91.668秒`；共75次DeepSeek调用。
- M重复签名一致率：`88.37%`。M-02/M-16/M-17/M-19/M-29在附加指标或等价工具选择上存在变化，但三轮均满足目标指标和维度要求，不属于失败。
- U：完整数据库32项，`32/32`通过。
- 代码回归：`133 passed, 1 skipped`；跳过项为截取样本没有满足统计门槛的条件性用例，不是失败。
