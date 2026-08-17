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

