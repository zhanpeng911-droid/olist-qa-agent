# M1 基准测试问题清单

覆盖三大问题域，均可用样例数据（sample_data）对账验证。

## 1. 总体延迟率与低评分率
> 问题：总体延迟率和低评分率是多少？
- 表：`mart_order_delivery`
- 指标：`late_rate`, `low_score_rate`（有效样本口径）
- 对账：与 `SELECT AVG(is_late_delivery), AVG(is_low_score) FROM mart_order_delivery WHERE is_delivery_analysis_eligible=1` 一致

## 2. 按时 vs 延迟订单的评分对比
> 问题：提前 / 按期 / 延迟订单的评分表现对比？
- 表：`mart_order_delivery`
- 维度：`is_late_delivery`
- 指标：`avg_review_score`（或 `low_score_rate`）
- 对账：与按 `is_late_delivery` 分组的 `AVG(review_score)` 一致

## 3. 延迟分档低评分率
> 问题：延迟 1-3 / 4-7 / 8-14 / 15+ 天的低评分率变化？
- 表：`mart_order_delivery`
- 维度：`delay_bucket`
- 指标：`low_score_rate`
- 对账：与按 `delay_bucket` 分组的 `AVG(is_low_score)` 一致；期望低评分率随延迟档位单调上升

## 4. Top-N 州 / 品类低评分率
> 问题：低评分率最高的 5 个品类 / 客户州？
- 表：`mart_order_delivery`
- 工具：`top_n(metric=low_score_rate, dimension=primary_category_name|customer_state, n=5)`
- 对账：与 `GROUP BY dimension ORDER BY AVG(is_low_score) DESC LIMIT 5` 一致

## 5. 履约时长拆解
> 问题：支付审批 / 总履约时长拆解？
- 表：`mart_order_delivery`
- 指标：`avg_approval_days`, `avg_fulfillment_days`
- 对账：与 `SELECT AVG(approval_days), AVG(fulfillment_days)` 一致

## 附加（卖家表）
> 问题：哪个卖家州的低评分率最高？
- 表：`mart_order_seller_delivery`
- 工具：`top_n(metric=low_score_rate, dimension=seller_state, n=5)`

---

# M2 基准问题（L2 描述性归因）

## 6. 低评分描述性归因
> 问题：请对低评分进行归因 / 为什么低评分高 / 有哪些主要因素？
- 流程：`run_attribution`（固定顺序确定性流程，无需 LLM 逐步决策）
- 覆盖：订单级（is_late_delivery / delay_bucket / customer_state / primary_category_name / primary_payment_type / order_month）+ 卖家级（seller_state / route / cross_state，is_multi_seller_order=0）
- 输出：基准率、各组 {样本量/低评分率/率差/Lift/超额低评分}、P0/P1/P2 优先级、边界提示、可对账 SQL
- 对账：各组指标与手写 SQL 一致；`超额低评分 = 样本量 × max(组率-基准率,0)`；`Lift = 组率/基准率`
- 边界：只做描述性归因、禁因果；不生成改善建议（M4）

### 样例数据实测（M2）
- 订单级基准：样本 1000，低评分率 38.10%
- 卖家级基准（单卖家）：样本 848，低评分率 37.74%
- P0 主要对象：`delay_bucket=15天+`（率85.89%，Lift 2.25，超额78）、`is_late_delivery=1`（率69.77%，Lift 1.83，超额109）、`primary_category_name=toys_games`（率45.45%，超额13）

## 7. route 线路深挖（M2 边角）
> 问题：哪些线路是低评分重点？哪些线路延迟又低评分？线路集中吗？
- 工具：`analyze_routes`（集成进 `run_attribution` 的 `routes` 块）
- 输出：
  - `top_routes`：Top 线路（样本量/低评分率/Lift/超额 + P0/P1/P2），动态阈值 `max(15, 卖家样本×2%)`
  - `route_concentration`：Top5 线路低评分占比（集中度）
  - `route_cross_delay`：线路×延迟交叉（延迟/非延迟低评分率）
- 边界：卖家级，`is_multi_seller_order=0`；描述性，禁因果

### 样例数据实测
- 线路集中度：Top5 线路低评分 58/320 = 18.1%
- 交叉示例：`RJ→RJ` 延迟 89% vs 非延迟 23%；`SP→MG` 延迟 100% vs 非延迟 17%

## 8. 统计验证（M3，自动并入归因）
> 归因输出自动含 `verification` 块：单变量检验 + 双 Logistic + 证据分级
- 单变量：卡方/Fisher（is_late_delivery×low_score）、Cochran-Armitage 趋势（delay_bucket）、R×C 卡方+BH 校正（州/品类/支付）、Spearman（时长×评分）、Mann-Whitney U（延迟组 vs 非延迟组评分）
- 多变量：订单级 + 单卖家级 Logit（HC3 稳健 SE）→ 调整 OR/CI/p
- 证据分级：强/中/待验证（综合显著性、效应、样本）
- 边界：观察性、禁因果

### 样例数据实测（M3）
- is_late_delivery：卡方 p=5.6e-50，OR=8.43（CI [6.27,11.33]）→ **强证据**
- 趋势检验 p=1.8e-61；Spearman late_days×评分 ρ=-0.38
- Logistic 订单模型调整 OR=8.64（CI [6.38,11.7]，p=4.1e-44，HC3）——控制变量后仍显著
- 州/品类/支付方式不显著（BH 校正 p=0.94）

## 9. 改善建议（M4，并入归因）
> 归因输出自动含 `recommendations` 块：基于已验证证据匹配规则库 → 责任方/动作/监控指标/验证方式
- 只对强/中证据因素建议；待验证线索标注"暂不建议"
- 样例实测：严重延迟（P0）→ 客服/物流 人工介入+补偿；高规模线路 → 物流 线路SLA+P90；品类不显著 → 无品类建议

## 10. 标准问题评测（M4，确定性）
> 评测集：`tests/eval_questions.yml`（26 题：L1 数字 8 / L2 结构 3 / M3 统计 3 / M4 建议 4 / 安全 5 / 边界 3）
> 运行：`uv run python tests/run_eval.py` → **26/26 通过（100%）**
