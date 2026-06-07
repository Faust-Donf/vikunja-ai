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


def test_frontend_contains_access_token_login_flow():
    html = HTML.read_text(encoding="utf-8")

    assert "authScreen" in html
    assert "accessTokenInput" in html
    assert "/api/auth/status" in html
    assert "/api/auth/login" in html
    assert "ensureAuthenticated" in html
    assert "[hidden]" in html
    assert "display: none !important" in html


def test_frontend_contains_table_sorting_flow():
    html = HTML.read_text(encoding="utf-8")

    assert "sortState" in html
    assert "function sortedTasks" in html
    assert "function renderSortHeaders" in html
    assert 'data-sort-field="priority"' in html
    assert 'data-sort-field="plan_date"' in html
    assert "sort-button active" not in html


def test_frontend_contains_bulk_task_actions():
    html = HTML.read_text(encoding="utf-8")

    assert "bulkBar" in html
    assert "selectedIds" in html
    assert "function selectedTasks" in html
    assert "selectVisible" in html
    assert "bulkCompleteBtn" in html
    assert "bulkDeleteBtn" in html
    assert "批量完成归档" in html
    assert "确定删除选中的" in html


def test_frontend_contains_plan_date_quick_filters():
    html = HTML.read_text(encoding="utf-8")

    assert 'id="dateFilter"' in html
    assert "全部日期" in html
    assert "逾期" in html
    assert "未来 7 天" in html
    assert "function dateMatchesFilter" in html
    assert 'filter === "overdue"' in html
