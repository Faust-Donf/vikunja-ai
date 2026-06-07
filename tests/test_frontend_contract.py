from pathlib import Path


HTML = Path(__file__).resolve().parents[1] / "static" / "index.html"


def test_frontend_contains_ai_assistant_and_markdown_renderer():
    html = HTML.read_text(encoding="utf-8")

    assert "AI助手" in html
    assert "表格管理 + AI助手" in html
    assert "表格管理 + ChatAI" not in html
    assert "function renderMarkdown" in html
    assert "node.innerHTML = renderMarkdown(content)" in html
    assert "生成周报" in html


def test_frontend_contains_generation_review_flow():
    html = HTML.read_text(encoding="utf-8")

    assert "已跳过重复任务" in html
    assert "data-gen-field=\"project\"" in html
    assert "data-gen-field=\"plan_date\"" in html
    assert "还有任务缺少 DDL 或项目归属" in html
    assert "needs_input" in html


def test_frontend_contains_quadrant_layout_rules():
    html = HTML.read_text(encoding="utf-8")

    assert "quadrant-rule" in html
    assert "高优先级 + 今天/逾期" in html
    assert "非高优先级 + 非紧急" in html
    assert "toolbar-main" in html


def test_frontend_contains_export_and_backup_actions():
    html = HTML.read_text(encoding="utf-8")

    assert "导出 CSV" in html
    assert "下载备份" in html
    assert "导入备份" in html
    assert "/api/export.csv" in html
    assert "/api/backup.json" in html
    assert "/api/import.json" in html
    assert "importFileInput" in html
