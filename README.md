# 个人计划表

一个轻量的个人计划管理表，形态接近飞书表格，并带 AI助手。

功能：

- 表格化维护任务：状态、优先级、计划日期、项目、标签、备注。
- 支持搜索、状态/优先级/日期/归档筛选、表头排序和重置视图。
- 支持重要/紧急四象限视图、优先级高亮、完成归档和恢复。
- 支持批量选择、批量完成归档和批量删除。
- AI助手会读取当前任务表，帮你做计划、复盘、优先级判断。
- 网页端可配置 OpenAI-compatible API Base URL、API Key 和模型名。
- AI助手回复支持 Markdown 渲染。
- AI 根据自然语言目标生成任务行，会对照现有任务去重。
- AI 不确定 DDL 或项目归属时，会在追加前要求补充。
- 一键根据已完成归档任务生成周报和下周 Todo。
- 支持 CSV 导出、JSON 备份下载和 JSON 备份合并导入。
- 可选访问口令保护，支持登录和退出。
- 后端会校验任务状态、优先级和计划日期，避免直接 API 写入脏数据。
- SQLite 持久化。

服务端环境变量：

- `OPENAI_API_BASE_URL`：可选，默认 `https://api.openai.com/v1/chat/completions`。也可在网页端覆盖配置。
- `OPENAI_API_KEY`：可选，OpenAI-compatible API Key。也可在网页端配置。
- `OPENAI_MODEL`：可选，默认 `gpt-4o-mini`。也可在网页端覆盖配置。
- `DATA_DIR`：可选，默认 `/data`。
- `ACCESS_TOKEN`：可选。默认不设置，直接访问；设置后访问任务表和 AI 接口需要先输入口令。

## 本地测试

```bash
python3 -m venv .venv
. .venv/bin/activate
pip install -r requirements.txt
pytest -q
```
