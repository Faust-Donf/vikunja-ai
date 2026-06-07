# 个人计划表

一个轻量的个人计划管理表，形态接近飞书表格，并带 AI助手。

功能：

- 表格化维护任务：状态、优先级、计划日期、项目、标签、备注。
- AI助手会读取当前任务表，帮你做计划、复盘、优先级判断。
- AI助手回复支持 Markdown 渲染。
- AI 根据自然语言目标生成任务行，会对照现有任务去重。
- AI 不确定 DDL 或项目归属时，会在追加前要求补充。
- 一键根据已完成归档任务生成周报和下周 Todo。
- 支持重要/紧急四象限视图、优先级高亮、完成归档。
- SQLite 持久化。

服务端需要环境变量：

- `VENUS_API_URL`
- `VENUS_API_KEY`
- `VENUS_MODEL`
- `DATA_DIR`

## 本地测试

```bash
python3 -m venv .venv
. .venv/bin/activate
pip install -r requirements.txt
pytest -q
```
