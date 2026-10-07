# Olist 数据工程与经营报表 Agent

新版在原 `v2` 目录重建。现在同时提供确定性数据工程、LLM 自主数据工程、自然语言动态取数和 A / B 经营报表。固定工作流无需模型；AI 数据助手调用本地 `.env` 中配置的 DeepSeek，按注册 skill 检查输入/表结构、生成 SQL、调用工具并检查结果。

**最新验收（2026-10-04）：** 三项业务闭环缺口已补齐：完整查询CSV、AI发布版确认恢复、源证据限定的旧城市转义修复。U142/142；真实DeepSeek M82/82（75项取数、前置条件、3项工程、3项恢复保护），三次完整99,441行导出逐字段对账通过；源修复与完整恢复隔离验收4/4。正式库仅按已验证路径修正11个城市值，完整前版保留；M期间正式库校验和不变。详细证据、网页交互核验状态和保护预算见 [业务闭环补齐与验收](docs/业务闭环补齐与验收_20261004.md)。此前历史45,430→99,441单的真实LLM全量工程和内容对账另见 [上一轮完整验收](docs/真实模型完整验收_20261004.md)，不能将旧状态当作当前限制。

## 数据来源与复现

原始数据来自 **Olist 在 Kaggle 发布的 [Brazilian E-Commerce Public Dataset by Olist](https://www.kaggle.com/datasets/olistbr/brazilian-ecommerce)**。当前版本以该数据集中的九张原始 CSV 为数据工程输入，不再依赖旧版预先导出的三张 Mart CSV。数据库中的 Raw、Staging 和 Mart 均由导入与建模流程生成；取数默认优先使用 Mart，必要时可回查其他授权层。

下载并解压后，保留以下标准文件名：

| 原始文件 | 业务内容 |
|---|---|
| `olist_orders_dataset.csv` | 订单、状态及生命周期时间 |
| `olist_order_items_dataset.csv` | 订单商品项、卖家、商品金额及运费 |
| `olist_order_payments_dataset.csv` | 支付方式、分期及支付金额 |
| `olist_order_reviews_dataset.csv` | 订单评分及评价文本 |
| `olist_customers_dataset.csv` | 客户标识及地区 |
| `olist_products_dataset.csv` | 商品品类及物理属性 |
| `olist_sellers_dataset.csv` | 卖家及地区 |
| `olist_geolocation_dataset.csv` | 邮编地理位置 |
| `product_category_name_translation.csv` | 商品品类的葡萄牙语—英语对照 |

本仓库提供代码、SQL、运行时 skill 和复现文档；原始九表请从上述来源下载，不要求将本地数据、数据库备份或任务导出一起上传。将九个文件放在同一个本地目录，在 `.env` 中设置 `OLIST_SOURCE_DIR`，或者在数据工程页一次上传九个文件，随后按“导入一个新时间段”中的流程构建数据库。首次使用可以导入完整数据集；复现增量场景时，需要按订单关系准备分期批次，并保留其引用的维度记录。

数据集页面标注的许可为 [CC BY-NC-SA 4.0](https://creativecommons.org/licenses/by-nc-sa/4.0/)；数据的使用或再分发须遵守署名、非商业性使用和相同方式共享条件。公开可下载不等于可无条件用于商业业务。该许可属于上游数据，不代表本仓库代码已经授予相同许可；代码许可应由协作维护者另行明确。

## 界面

- **数据工程**：目录接入／浏览器上传、仅追加／带版本快照更新、字段与业务键校验、批次执行记录、合并计数、质量门、版本恢复、备份清理预览、三张 Mart CSV 导出。
- **AI 数据助手**：自然语言动态 SELECT / CTE 取数、轻量结果及完整 CSV 导出；选择已校验九表批次，由 LLM 自主建立候选库、提交 Staging / Mart SQL、检查质量门，等待用户确认发布；当前 AI 发布版支持预览并确认恢复上一版。页面显示加载的 skill、工具轨迹、实际 SQL 和结果，不靠预设取数条件匹配问题。
- **A · 经营总览**：已送达商品金额、订单数、客户数、商品客单价；商品金额、订单／客户及延迟／低评分月度趋势；分母样本数明细。
- **B · 增长来源**：同月份窗口的两个年份对照及增幅、客单价与人均订单趋势、客户州／品类／卖家金额 Top 10，以及金额贡献、Top 1／Top 10 集中度和 HHI。

A / B 页参照 `Olist_经营总览.twb` 的计算字段及筛选关系。商品金额仅计已送达订单且不含运费；客户数使用 `customer_unique_id`。品类和卖家贡献来自商品项 Mart，避免将订单全部金额归给主要品类。月份与州筛选在两页共用；B 页同期窗口独立于趋势月份筛选。

## 本机启动

1. 启动 MySQL80 服务。
2. 保留本目录 `.env` 的数据库连接配置。新环境复制 `.env.example` 为 `.env` 并填写 `DB_*`。
3. 双击 `启动Agent.bat`，打开 `http://127.0.0.1:8000`。

AI 功能复用现有 `.env` 的 `DEEPSEEK_API_KEY`、`DEEPSEEK_BASE_URL`、`DEEPSEEK_MODEL`，不要用 `.env.example` 覆盖已有凭据。缺少配置时填写对应字段并重启服务；确定性流程和 A / B 报表不受影响。查询可选设置 `QUERY_DB_USER` / `QUERY_DB_PASSWORD` 为只具 SELECT 权限的专用账户；未设置时沿用现有连接，但仍有 AST 限制和 READ ONLY 事务。

## AI 数据助手怎么使用

1. **取数**：进入 `/agent`，选择“自然语言取数”，输入时间、对象、粒度与指标要求。例：“按月统计2018年上半年RJ州已送达订单的商品金额、订单数、低评分率，并按支付方式拆分”。条件不需预先登记；指标定义、表范围和权限有明确约束。
2. **数据工程**：在数据工程页先上传或校验九张原始 CSV，不点击固定流程的发布按钮；到 AI 页选择该“待构建”批次，要求“合并本批次、按Olist skill生成并执行四张Staging和三张Mart的SQL，完成质量门，等待确认”。Raw CSV解析与增量合并继续由确定性工具完成；模型真正提交SQL构建后续层，而非只触发原来的固定SQL执行器。
3. 模型任务启动前确认数据发送范围。默认只发送本次指令、字段结构、skill、校验摘要与工具状态，查询数据行留在本地；另勾选结果预览许可才发送每次最多50行。密码与API Key不交给模型。任务会消耗 API 额度，未提供自动成本上限，只有限定轮次/token预算。
4. 轻量查询保存最多5,000行、页面预览50行，超限明确显示“截断”。需要全部明细时，在该结果点击“完整导出原始 SQL 结果”并确认：按当前发布版重新执行原始 SQL，保留用户明确的 LIMIT、OFFSET 与筛选，不使用轻量工具自动附加的 LIMIT。完整结果流式保存到本地，不发送给模型；最多200万行、1GiB、5分钟，超限/中断不提供部分文件。查看下载行数与版本标记，避免把旧预览当成同一版数据。CSV文本的潜在公式前缀会加单引号保护。
5. 工程在 `_olist_work_<任务ID>` 候选库执行。除36项质量门外还检查表字段、主键粒度与InnoDB兼容性。正式库在确认发布前不变化；确认时复查基线校验和，其他更新会阻止旧候选覆盖。首次候选合并仍按现有增量政策，不由LLM推断缺失的更新时间或删除事件。
6. 恢复 AI 发布版：选择状态“已发布”的工程任务，点击“预览恢复上一版”，核验质量门与影响后确认。仅当前版本可恢复；当前数据、备份或计划变化会拒绝旧确认。完整 Raw/Staging/Mart 与版本元数据一并恢复，当前版保留在恢复库。首次空库发布没有完整前版，不可恢复。

技能说明在 `skills/data-retrieval/SKILL.md` 与 `skills/olist-engineering/SKILL.md`；由加载器读取全文并保存SHA-256，进入模型任务上下文，模型也可按需加载注册skill。工具由Python函数实现。允许模型自主规划不等于允许任意shell、root SQL或正式库写入。详见 [AI Agent新增架构与操作](docs/AI_Agent新增架构与操作.md)。

首次安装：

```powershell
python -m venv .venv
.venv\Scripts\python.exe -m pip install -r requirements.txt
cd web
pnpm install
pnpm build
```

服务监听本机回环地址。数据库账户需要 SELECT、INSERT、UPDATE、CREATE、ALTER、DROP 和 RENAME TABLE 涉及的权限，以创建候选版本与恢复库。默认沿用本地 `olist_ecommerce`，也可在 `.env` 中指定新的 `DB_NAME`。

## 导入一个新时间段

1. 在一个目录中放入九张标准命名的 CSV，或者在数据工程页一次选择九张文件上传。
   默认选择“仅追加”，已有业务键发生有效内容变化会被拒绝；确需更新旧订单或维度时，选择“快照更新”并填写可信、带时区的源快照时间。它不是订单购买时间；必须晚于已发布快照水位。旧库首次快照更新建立水位基线，不能自动鉴别用户声明是否真实。
2. 输入批次名称并点击“校验目录”／“上传并校验”。程序保存本批次源文件快照，以标准 CSV 规则处理引号、逗号、嵌入换行和反斜杠，并检查字段、类型、必填项、业务键冲突和取值范围。
3. 检查逻辑记录数、时间范围与错误列表。校验通过后点击“合并、构建并发布”。
4. 程序复制已有 Raw，合并新批次，在独立候选库运行三层 SQL，并验证关联、粒度、金额、最终评价和标记口径。
5. FAIL 阻止发布；质量门通过后，单条 MySQL 8 原子 `RENAME TABLE` 同时切换九张 Raw、四张 Staging 和三张 Mart。刷新 A / B 报表即可看到扩展后的结果。

新批次可以只有新增记录，也可以包含重叠历史快照。九个文件均需提供；没有本批次新增／更新记录的表可以只保留表头。客户、商品、卖家、翻译及地理表没有可靠时间字段，必须提供新增订单引用的维度数据，不能机械地按年份裁切每张表。

### 合并语义

| 表 | 业务键／合并规则 |
|---|---|
| orders | order_id；显式快照模式可更新状态和时间，保护 customer_id 与购买时间；阻止全部生命周期回退；delivered / canceled / unavailable 终结状态只能保持原状态；processing 与 invoiced 视为同级；空增量时间不清除已有时间 |
| customers | customer_id |
| order_items | order_id + order_item_id |
| order_payments | order_id + payment_sequential |
| products / sellers / category_translation | product_id / seller_id / product_category_name |
| reviews / geolocation | 完整记录内容匹配；每种相同记录取历史与本批次重复次数的较大值，既防重叠导入膨胀，也保留原始合法重复次数 |

同一有键记录在一个文件中出现不同内容会失败；跨批次覆盖必须显式选择快照更新并声明更晚的上游快照时间。源数据没有 `updated_at`，程序不能自动证明该声明，默认仅追加避免误覆盖。评价内容更新作为另一条完整记录保留，最终评价按回答时间等规则选择。CSV 不携带删除事件，缺失记录不意味着删除；当前不支持删除事件导入。

地理位置和评价没有稳定业务唯一键，无法区分“新增且与历史完全相同的事件”和“重复快照”；这里采用保守的完整记录匹配规则。若后续接入真实业务系统，应由上游提供稳定记录 ID、更新时间和删除标记。

## 数据建模

`sql/` 保存原项目的四份构建 SQL，执行器忽略其中硬编码的 `USE olist_ecommerce`，所有构建语句在候选库运行。导入以参数化批量 INSERT 执行，规避原先 `LOAD DATA` 文本转义问题。

- `01_create_database_and_tables.sql`：九张 Raw 的字段与粒度。
- `04_create_staging_tables.sql`：邮编、订单支付、最终评价和订单商品聚合。
- `06_create_mart_tables.sql`：订单级与订单—卖家级 Mart。
- `11_create_operating_item_mart.sql`：商品项级 `mart_order_item_business`；这是现行 SQL 和 TWB 使用的第三张 Mart 名称。

低评分为 ≤3 分，延迟按实际／预计送达的自然日判断。零金额分母的运费率为 NULL。完整金额对账在数据库 DECIMAL 上执行，展示时才转换为数值。

新增数据在 Raw 层按键合并；相同文件指纹且构建签名一致时复用 Raw 快照；未受变更影响的 Staging 按依赖复用。合并没有新增／更新且规则未变时标记“未变化”，对账后沿用当前版本，不重建 Mart、不发布、不新增备份。业务数据有变化时 Mart 仍全量重建，以更新受维度变更影响的历史订单；不是 CDC 或变化订单级流式刷新。

## 质量门与恢复

36 项检查包含 Raw 引用完整性、已送达订单商品项完整性、取值范围、Staging 唯一粒度、Mart 行数保全、商品／运费／支付对账、最终评价选择、来源与延迟／低评分标记。缺少支付、时间异常、品类翻译和地理信息以 WARNING 保留。

每个批次保存源文件 SHA-256、不可变输入快照、磁盘上的规范化记录、执行阶段、合并计数、质量报告。文件解析分块，键索引和规范化记录存 SQLite；没有全量 pandas 载入，也没有并行统计建模。一次只执行一个构建，数据库锁防止多个服务同时发布。

发布前失败不影响正式表；发布后保存原表于 `_olist_backup_<批次ID>`。仅当前版本可恢复，旧备份必须包含完整三层表；恢复后的当前表保留在 `_olist_retired_<批次ID>`。首次新库没有完整前版，未变化批次也不产生可回滚的新版本。

数据工程页提供备份清理预览，默认保留最近创建的三个已发布／已回滚批次的备份，并额外保护当前版本的上一版；只处理本批次审计库明确归属的精确备份库。用户确认后永久删除列出的目标，相应历史版本将不可恢复。未自动删除正式库、失败候选、恢复库、输入快照或旧代码备份，这些仍需单独保留政策。连接恢复后的成功轮询／刷新会清除旧连接提示，不清除任务失败记录。

不支持同步其他工具在导入期间直接修改 Raw。此工作流假定本 Agent 是构建期间唯一 Raw 写入者；报表读取仍可正常运行。旧项目的非本工作流表与视图不在发布列表中，也不会自动刷新，分析应使用当前三张 Mart。

### 旧版文本转义修复

数据工程页新增“旧版城市文本修复”：以九表源目录中的卖家 CSV 为证据，仅接受旧 `LOAD DATA` 将字面量反斜杠+r误解为回车的城市差异。先显示源文件 SHA-256、旧/新值、各表影响行数；用户确认后克隆完整三层，只修正 Raw 卖家及对应卖家/商品项 Mart 的城市字段，通过36项质量门与基线复查才原子发布。键、邮编、州及任何其他内容差异均拒绝；不是通用覆盖接口，不修改上游快照水位。完整前版可从批次记录恢复。

## 测试

```powershell
.venv\Scripts\python.exe -m pip install pytest httpx
.venv\Scripts\python.exe -m pytest -q -p no:cacheprovider --basetemp artifacts/test_tmp
$env:OLIST_TEST_MYSQL='1'
.venv\Scripts\python.exe -m pytest -q -p no:cacheprovider --basetemp artifacts/test_tmp
.venv\Scripts\python.exe scripts\run_task_acceptance.py
.venv\Scripts\python.exe scripts\verify_incremental.py

# 经允许发送测试数据后，真实DeepSeek取数三轮（产生API费用）
.venv\Scripts\python.exe scripts\run_llm_eval.py --allow-model-data --repeat 3
# 真实模型隔离库工程验收；不与U/M-F构建同时运行
.venv\Scripts\python.exe scripts\run_llm_eval.py --allow-model-data --engineering-only --engineering --full-engineering
# 真实模型三轮取数＋完整明细导出＋首次/增量/重叠及AI恢复
.venv\Scripts\python.exe scripts\run_llm_eval.py --allow-model-data --repeat 3 --engineering --business-gaps
# 历史转义缺口的全量克隆、修复、逐字段对账与恢复（零模型调用）
.venv\Scripts\python.exe scripts\verify_source_repair.py
```

默认测试不要求数据库；设置 `OLIST_TEST_MYSQL=1` 后增加隔离库中的状态更新、空时间保护、文本回写和重复合并测试。全量验收用原始九表生成 2018 年前／2018 年及以后批次，在 `olist_verify_*` 独立数据库执行：时间扩展、完整快照重复导入、三层行数与金额对账、报表贡献总额对账、失败批次隔离和原子回滚。输出在 `artifacts/engineering/verification_*/acceptance.json`，测试库和候选／恢复库保留，不修改 `DB_NAME` 指向的正式库。

`run_task_acceptance.py` 通过独立服务的实际 HTTP 接口验收目录接入、九文件上传、后台构建、重复导入、错误拦截、版本恢复、报表与三张 Mart 导出；使用 `olist_accept_*` 隔离库，不修改正式库或 `.env`。结果输出到 `artifacts/engineering/task_acceptance_*/acceptance.json`。不要与正式页面的数据构建同时运行。详见 [任务验收与复测指南](docs/任务验收与复测指南.md)。

`artifacts/test_tmp` 是测试专用临时目录，pytest 会在新一轮测试开始时清理它，不要在其中存放业务文件。已完成的测试记录见 [验收结果](docs/验收结果.md)。

## 目录

```text
engineering/      CSV 合约、流式校验、增量合并、质量门与版本化构建
server/           FastAPI、上传与批次 API、报表与 Mart 导出
agent_core/       LLM 工具调用循环、skill加载、SQL AST校验与候选库工具
skills/           动态取数及Olist数据工程的运行时技能指引
sql/              原项目构建 SQL
web/src/          数据工程、AI 数据助手与 A / B 固定报表
tests/            解析、指标与增量行为验证
scripts/          启动与全量数据验收
docs/             原项目数据工程指引及新版设计说明
artifacts/        批次记录、运行日志与旧版备份
```

旧版归因、统计问答与旧测试入口仍不在运行路径中。重建前的代码、文档和配置保存于 `artifacts/legacy_backup/`，本地凭据、数据集和 v1 保留。此次新增LLM工程/取数不是恢复旧归因模块；旧 M 模型测试不直接适用。

详细架构、Agent／skill／tool／工作流概念、技术栈和功能实现见 [Agent详细说明](docs/Agent详细说明.md)。测试区分U代码回归、M-F固定工程任务验收和M-LLM实际模型验收。后者使用`scripts/run_llm_eval.py`真实调用.env配置的DeepSeek，以独立SQL参照核对动态取数，并在隔离库验证自主建模；需先取得模型数据发送许可。脚本模型及本地SQL参照不能代替真实模型测试。详见 [MU测试指南](docs/MU测试指南.md)，不沿用旧归因Agent的模型通过率。
