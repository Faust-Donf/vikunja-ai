import json
import re
import shutil
import subprocess
from pathlib import Path

import pytest


HTML = Path(__file__).resolve().parents[1] / "static" / "index.html"


def test_frontend_contains_ai_assistant_and_markdown_renderer():
    html = HTML.read_text(encoding="utf-8")

    assert "AI助手" in html
    assert "表格管理 + AI助手" in html
    assert "表格管理 + ChatAI" not in html
    assert "function renderMarkdown" in html
    assert "node.innerHTML = renderMarkdown(content)" in html
    assert "生成本周计划" in html
    assert "生成周报" in html
    assert "/api/weekly-plan" in html
    assert "function generateWeeklyPlan" in html
    assert 'weeklyPlanBtn").addEventListener("click", generateWeeklyPlan)' in html


def test_chat_input_enter_sends_and_shift_enter_keeps_newline():
    html = HTML.read_text(encoding="utf-8")

    assert 'event.key === "Enter" && !event.shiftKey' in html
    assert "event.preventDefault()" in html
    assert 'sendChat($("chatInput").value)' in html
    assert "event.metaKey || event.ctrlKey" not in html


def test_markdown_renderer_compiles_ai_reply_sample():
    if shutil.which("node") is None:
        pytest.skip("node is required to execute the frontend markdown renderer")

    html = HTML.read_text(encoding="utf-8")
    script = re.search(r"function escapeHtml[\s\S]+?async function jsonFetch", html)
    assert script is not None
    renderer = script.group(0).removesuffix("async function jsonFetch")
    sample = "结合任务优先级：\n1. **优先完成#1 验证表格任务保存与编辑**：今天内闭环。\n2. **推进#10 适配发布流水线剩余工作**：避免后续时间紧张。"
    js = f"{renderer}\nconsole.log(renderMarkdown({json.dumps(sample)}));"

    result = subprocess.run(["node", "-e", js], check=True, capture_output=True, text=True)

    assert "<p>结合任务优先级：</p>" in result.stdout
    assert "<ol>" in result.stdout
    assert "<li><strong>优先完成#1 验证表格任务保存与编辑</strong>：今天内闭环。</li>" in result.stdout
    assert "**" not in result.stdout


def test_markdown_renderer_supports_tables_and_blockquotes():
    if shutil.which("node") is None:
        pytest.skip("node is required to execute the frontend markdown renderer")

    html = HTML.read_text(encoding="utf-8")
    script = re.search(r"function escapeHtml[\s\S]+?async function jsonFetch", html)
    assert script is not None
    renderer = script.group(0).removesuffix("async function jsonFetch")
    sample = "| 项目 | 状态 |\n| --- | --- |\n| **架构师评价算法** | 高优 |\n\n> 风险需关注"
    js = f"{renderer}\nconsole.log(renderMarkdown({json.dumps(sample)}));"

    result = subprocess.run(["node", "-e", js], check=True, capture_output=True, text=True)

    assert "<table>" in result.stdout
    assert "<th>项目</th>" in result.stdout
    assert "<td><strong>架构师评价算法</strong></td>" in result.stdout
    assert "<blockquote>风险需关注</blockquote>" in result.stdout


def test_frontend_contains_generation_review_flow():
    html = HTML.read_text(encoding="utf-8")

    assert "已跳过重复任务" in html
    assert "data-gen-field=\"project\"" in html
    assert "data-gen-field=\"plan_date\"" in html
    assert "data-delete-generated" in html
    assert "generatedTasks.splice(index, 1)" in html
    assert "还有任务缺少 DDL 或项目归属" in html
    assert "needs_input" in html


def test_frontend_generation_project_supports_existing_or_new():
    html = HTML.read_text(encoding="utf-8")

    assert 'list="generatedProjectList"' in html
    assert 'id="generatedProjectList"' in html
    assert "tasks.map((task) => (task.project || \"\").trim()).filter(Boolean)" in html


def test_frontend_contains_quadrant_layout_rules():
    html = HTML.read_text(encoding="utf-8")

    assert "quadrant-rule" in html
    assert "task.plan_date <= addDays(todayString(), 3)" in html
    assert "高优先级 + 3天内" in html
    assert "非高优先级 + 3天内" in html
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


def test_frontend_contains_openai_api_config_flow():
    html = HTML.read_text(encoding="utf-8")

    assert "OpenAI-compatible API 配置" in html
    assert "apiConfigBtn" in html
    assert "apiBaseUrlInput" in html
    assert "apiKeyInput" in html
    assert "apiModelInput" in html
    assert "/api/config/openai" in html
    assert "已配置，留空则保持不变" in html


def test_frontend_contains_access_token_login_flow():
    html = HTML.read_text(encoding="utf-8")

    assert "authScreen" in html
    assert "accessTokenInput" in html
    assert "/api/auth/status" in html
    assert "/api/auth/login" in html
    assert "/api/auth/logout" in html
    assert "logoutBtn" in html
    assert "退出" in html
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


def test_frontend_contains_wrapping_full_width_table_layout():
    html = HTML.read_text(encoding="utf-8")

    assert "table-layout: fixed" in html
    assert "overflow-x: hidden" in html
    assert "min-width: 0" in html
    assert ".project-cell" in html
    assert ".tags-cell" in html
    assert ".actions-cell" in html
    assert "font-size: 13px" in html
    assert "resize: none" in html
    assert "overflow-wrap: anywhere" in html
    assert '<td class="title-cell"><textarea data-field="title">' in html
    assert '<td class="project-cell"><textarea data-field="project">' in html
    assert '<td class="tags-cell"><textarea data-field="tags">' in html


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
    assert "function startOfWeek" in html
    assert "function endOfWeek" in html
    assert 'filter === "overdue"' in html
    assert "planDate >= startOfWeek(today)" in html


def test_frontend_contains_project_filter():
    html = HTML.read_text(encoding="utf-8")

    assert 'id="projectFilter"' in html
    assert "全部项目" in html
    assert "function renderProjectFilterOptions" in html
    assert 'const project = $("projectFilter").value' in html
    assert "(!project || task.project === project)" in html
    assert '$("projectFilter").value = ""' in html
    assert 'if (field === "status" || field === "priority" || field === "plan_date") renderTasks();' in html


def test_frontend_contains_reset_view_action():
    html = HTML.read_text(encoding="utf-8")

    assert "resetViewBtn" in html
    assert "重置视图" in html
    assert "function resetView" in html
    assert '$("archiveFilter").value = "active"' in html
    assert 'sortState = { field: "", direction: "asc" }' in html
