# 当前 Agent 的 M/U 测试

2026-10-04最新实际结果：U142/142、真实DeepSeek M82/82（三轮取数75、前置条件1、工程3、恢复保护3），三轮完整CSV均核对99,441行全部字段；源修复与完整恢复隔离验收4/4。正式库仅执行了可追溯的11个城市值修复且保留前版，M期间正式校验和不变。新证据与网页交互核验状态见 [业务闭环补齐与验收](业务闭环补齐与验收_20261004.md)；历史全量LLM时间扩展见 [上一轮完整验收](真实模型完整验收_20261004.md)。再次外部模型测试仍需明确授权。

## 三类证据分开统计

| 测试 | 验证内容 | 真实LLM | 数据库范围 |
|---|---|---|---|
| U回归 | CSV、合并政策、质量门、SQL权限、HTTP接口、异常和状态保护 | 否 | 写入只在隔离测试库 |
| M-F固定任务验收 | 原始九表经实际HTTP接口完成建库、时间扩展、报表、导出、错误拒绝及恢复 | 否 | 仅向隔离验收库发布 |
| M-LLM实际模型验收 | 配置的DeepSeek理解问题、生成并执行SQL、澄清、拒绝越权及候选建模 | 是 | 正式库取数只读；工程在隔离测试库 |

`verify_agent_tools.py`的脚本模型工具链验收不能代替M-LLM。`run_llm_eval.py --validate-oracles`只是检查本地SQL参照规则和查询工具，也不是实际模型测试。旧归因Agent的M用例不适用于当前数据工程版。

## 通过标准

不能根据“模型说已完成”判定通过。取数将实际执行结果与独立SQL参照结果核对，同时检查粒度、评分/延迟分母、NULL、截断和CSV行数；参照SQL不提供给模型。工程要求实际提交SQL、7张建模表兼容、36项质量门无FAIL、发布前不改变基线、确认后行数和金额一致。最后复核正式16张表及版本标记校验和。

拒绝或需要澄清是部分用例的正确结果。通过只说明覆盖场景可用，不证明任意业务问题、规模、网络、源结构变更或并发条件均稳定。

## M-LLM用例

| 用例 | 覆盖重点 |
|---|---|
| M-01 | 已送达订单、独立客户、商品GMV、客单价 |
| M-02 | 低评分和延迟的不同分母，未知值处理 |
| M-03 | 月份、州、状态的组合条件 |
| M-04 / M-05 | 按商品项归属品类/卖家金额，防重复计算 |
| M-06 / M-07 | 回探Raw每笔支付、多条评价及Staging最终评价 |
| M-08 | EXISTS筛选多支付订单，金额不能倍增 |
| M-09 | 独立客户标识与购买次数分布 |
| M-10 | CTE、窗口函数LAG与首月NULL |
| M-11 | 取消/不可用订单，保留没有商品项的订单 |
| M-12 | 延迟分层下的评分与样本数 |
| M-13 | 没有数据的时间段，不编造结果 |
| M-14 | 轻量明细截断提示；使用business-gaps时确认完整导出，逐行逐字段对账全部99,441单 |
| M-15 / M-16 | 数值条件组合、GROUP BY与HAVING |
| M-17 / M-18 | 利润/成本及联系人字段缺失的识别 |
| M-19 | 澄清高价值定义后同一任务继续 |
| M-20 / M-21 | 指令注入、凭据读取及删除订单拒绝 |
| M-22 | 不发送结果预览时不得猜数 |
| M-23 | 订单—卖家Mart的单卖家线路、延迟与交接超期分母 |
| M-24 | 回探产品Raw的特定字段缺失，保持商品项粒度 |
| M-25 | 含引号/SQL片段的业务值必须按字面量筛选 |
| M-PRECONDITIONS | 没有数据发送许可或源批次，在模型调用前拒绝 |

工程另有FIRST / EXTENSION / OVERLAP：60笔真实Olist已送达订单拆成30笔旧记录、30笔新增记录及60笔重叠快照，在`olist_llm_*`测试库构建及确认发布。FULL使用原始全量九表核验99,441笔订单、16张表行数与金额，以及三张Mart全部现有基线字段；也可在同轮M-F恢复的`olist_accept_*`隔离历史库验证时间扩展。

## 运行

MySQL启动后在v2目录执行。U与构建/维护验收必须串行：清理、构建、发布共用数据库锁，并行会触发正常拒绝。

```powershell
$env:PYTEST_DISABLE_PLUGIN_AUTOLOAD='1'
$env:PYTHONIOENCODING='utf-8'
$env:OLIST_TEST_MYSQL='1'
.venv\Scripts\python.exe -m pytest -q -p no:cacheprovider --basetemp artifacts/mu_u_tmp --junitxml artifacts/mu_u.xml

# M-F：零外部模型调用
.venv\Scripts\python.exe scripts\run_task_acceptance.py

# 本地SQL参照与工具：零外部模型调用
.venv\Scripts\python.exe scripts\run_llm_eval.py --validate-oracles

# M-LLM：取得明确的数据发送授权后执行，消耗真实API额度
.venv\Scripts\python.exe scripts\run_llm_eval.py --allow-model-data --repeat 1 --engineering --full-engineering

# 三轮取数；顺序执行整套25题，共75次场景
.venv\Scripts\python.exe scripts\run_llm_eval.py --allow-model-data --repeat 3
# 本轮新增业务闭环：同一套三轮取数，另验证完整导出与AI版本恢复
.venv\Scripts\python.exe scripts\run_llm_eval.py --allow-model-data --repeat 3 --engineering --business-gaps
# 仅工程，不再重复取数；默认为新建隔离库
.venv\Scripts\python.exe scripts\run_llm_eval.py --allow-model-data --engineering-only --engineering --full-engineering

# 对已有两份真实模型构建结果做独立只读内容对账，零API调用
.venv\Scripts\python.exe scripts\verify_model_marts.py --targets olist_llm_full_4767f55f olist_accept_88e89f01
```

带`--allow-model-data`的命令发送问题、表结构、skill、SQL、校验摘要及每次最多50行结果，不发送密码或API key。不能因存在.env而视为已授权。未经授权不添加`--allow-model-data`。已有许可时可用`--cases M-03,M-08`复测个别用例；`--repeat 3`表示M-01到M-25完整顺序跑三轮，不是先把单个用例跑三次。最末只读对账命令只适用于本机已有两份验收库，其他机器应替换为它自己的隔离验收库名。

`--baseline-target olist_accept_<8位标识>`仅用于同轮固定验收已经结束、已恢复至历史版本的隔离库，不能传正式库。默认FULL创建新`olist_llm_full_*`空库。轮次、token、SQL时限及API失败均记录FAIL，不绕过限制发布。

## 证据与限制

- U：JUnit XML；启用MySQL后要求0失败/错误/跳过。
- M-F：`artifacts/engineering/task_acceptance_*/acceptance.json`和service.log。
- M-LLM：`artifacts/engineering/llm_eval_*/model_eval.json`，tasks保存工具对话、轨迹、SQL及本地结果，不应公开上传查询审计。
- 本地参照：`artifacts/engineering/llm_oracles_*/oracles.json`，明确标记external_llm_calls=0。
- 全量字段：`artifacts/engineering/model_mart_comparison_*.json`。源CSV校准仅接受可追溯的旧卖家城市反斜杠+r转义差异；独立校验目标Raw卖家四字段与源一致，其他键/字段/值变化仍失败，不泛化文本清洗或删除列。若源与基准出现其他差异，应先调查，不修改验收容差掩盖问题。

实际M-LLM须逐项PASS、整体PASS且formal_checksum_unchanged=true；区分API故障、权限、语义SQL错误、质量门及测试断言错误，保留失败记录。不能只挑一次成功来宣称稳定。

`--business-gaps`使用M-14实际模型生成的SQL，再通过用户确认的完整导出HTTP接口逐项核对全部结果；该额外步骤不调用模型、不将全量行发送给模型。配合`--engineering`实际验证AI发布60笔重叠版本恢复至60笔增量版，再恢复至30笔首次版；对非当前版本、缺少确认、错误计划、重复回滚及首次空库无完整前版的情况必须拒绝。核对完整三层和版本标记的校验和，不只核对订单数。

`scripts/verify_source_repair.py`用于本机已确认的历史城市转义缺口，零外部模型调用：全量克隆正式16表到隔离库，确认修复1个Raw城市及两张Mart各5个对应城市，其他字段逐项核对；再恢复原版并验证全部校验和。如果旧差异已修复，原本要求该差异存在的复现场景不适用，不应改动正确数据制造错误；应查阅已保存的隔离验收证据。正式修复脚本`repair_legacy_source.py`必须显式传`--confirm-production`，不能当作普通无副作用测试运行。

测试库、源快照和证据不自动删除，重复运行占用磁盘。AI回滚现已有页面入口与单独HTTP验收；固定流程回滚与AI回滚仍分开统计。CSV缺少可靠更新时间、删除事件及部分事件唯一键的限制仍然存在。
