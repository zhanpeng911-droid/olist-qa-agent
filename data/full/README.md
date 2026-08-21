# 完整 Mart 数据

本目录保存由Olist公共电商数据治理得到的三张完整分析宽表。它们与Agent的语义层和MySQL读取层保持同一字段口径，可用于复现完整数据库分析。

| 文件 | 粒度 | 数据行数 | SHA-256 |
|---|---|---:|---|
| `mart_order_delivery.csv` | 每个订单一行 | 99,441 | `037C0BDC8D5BDDB68B9723E668FA8037507994A837600A568C998B79DA729783` |
| `mart_order_seller_delivery.csv` | 每个订单-卖家组合一行 | 100,010 | `BF0589453DBD338E4D52157D97E9C16270BB529D6B7042A8399DA2A214D2978D` |
| `mart_order_item_business.csv` | 每个订单商品项一行 | 112,650 | `1E6786FEF7D5B1AE930838622C962352073BB8B28AB2ED5867BA79D2BD154A5A` |

注意事项：

- 三张表的粒度不同，不应直接横向拼接后计算订单指标。
- 默认的完整业务分析仍推荐导入MySQL，并通过`.env`配置只读连接。
- `scripts/import_mart_to_mysql.py`会从CSV重建表；只有明确需要覆盖数据库现有表时才传入`--replace`。
- 文件包含Olist数据中的匿名标识字段，不包含数据库凭据、API密钥或本机会话记录。
