# 数据目录

`sample/` 存放从三张 Mart 分析宽表截取的本地演示数据，用于功能检查与自动回归，不用于形成全量业务结论：

- `mart_order_delivery.csv`：订单级分析宽表；
- `mart_order_seller_delivery.csv`：订单—卖家级分析宽表；
- `mart_order_item_delivery.csv`：商品项级分析宽表。

这些 CSV 默认不提交到 Git。程序默认读取 `data/sample/`；需要使用其他 CSV 目录时，在 `.env` 中设置 `PROJECT_DATA_DIR`。完整业务分析应使用只读 MySQL 数据源。
