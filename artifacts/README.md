# 生成产物目录

此目录只保存程序运行生成的内容，不放业务代码：

- `evaluations/`：DeepSeek 重复评测的 JSON 轨迹和人工总结；
- `runtime_logs/`：Streamlit 后台进程的标准输出与错误日志。

原始 JSON 与运行日志默认不提交到 Git；需要长期保留的评测结论可整理为 Markdown 后提交。
