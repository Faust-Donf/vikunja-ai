import importlib
import os
import sys
from pathlib import Path

from fastapi.testclient import TestClient

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))


def load_app(tmp_path, monkeypatch):
    monkeypatch.setenv("VENUS_API_URL", "http://venus.invalid/chat")
    monkeypatch.setenv("VENUS_API_KEY", "test-key")
    monkeypatch.setenv("VENUS_MODEL", "test-model")
    monkeypatch.setenv("DATA_DIR", str(tmp_path))

    import app

    app = importlib.reload(app)
    app.init_db()
    return app, TestClient(app.app)


def test_task_completion_sets_and_clears_completed_at(tmp_path, monkeypatch):
    _, client = load_app(tmp_path, monkeypatch)

    created = client.post(
        "/api/tasks",
        json={
            "title": "完成时间测试",
            "status": "待办",
            "priority": "高",
            "plan_date": "2026-06-07",
            "project": "测试",
            "tags": "",
            "notes": "",
        },
    ).json()

    completed = client.put(
        f"/api/tasks/{created['id']}",
        json={**created, "status": "完成"},
    ).json()
    assert completed["status"] == "完成"
    assert completed["completed_at"]

    reopened = client.put(
        f"/api/tasks/{created['id']}",
        json={**completed, "status": "待办"},
    ).json()
    assert reopened["status"] == "待办"
    assert reopened["completed_at"] is None


def test_generate_filters_existing_tasks_and_preserves_input_requests(tmp_path, monkeypatch):
    app_module, client = load_app(tmp_path, monkeypatch)

    client.post(
        "/api/tasks",
        json={
            "title": "验证表格任务保存与编辑",
            "status": "待办",
            "priority": "高",
            "plan_date": "2026-06-07",
            "project": "系统验证",
            "tags": "",
            "notes": "",
        },
    )

    async def fake_call_venus(messages, temperature=0.3):
        return """
        {
          "summary": "生成计划",
          "skipped": [],
          "tasks": [
            {
              "title": "验证表格任务保存与编辑",
              "status": "待办",
              "priority": "高",
              "plan_date": "2026-06-07",
              "project": "系统验证",
              "tags": "",
              "notes": "重复项",
              "needs_input": []
            },
            {
              "title": "新复盘任务",
              "status": "待办",
              "priority": "中",
              "plan_date": null,
              "project": "",
              "tags": "",
              "notes": "需要补充",
              "needs_input": ["plan_date", "project"]
            }
          ]
        }
        """

    monkeypatch.setattr(app_module, "call_venus", fake_call_venus)
    response = client.post("/api/generate", json={"prompt": "做任务", "horizon": "本周"})

    assert response.status_code == 200
    body = response.json()
    assert body["skipped"] == ["验证表格任务保存与编辑"]
    assert [task["title"] for task in body["tasks"]] == ["新复盘任务"]
    assert body["tasks"][0]["needs_input"] == ["plan_date", "project"]


def test_weekly_report_uses_completed_and_planned_tasks(tmp_path, monkeypatch):
    app_module, client = load_app(tmp_path, monkeypatch)
    captured = {}

    async def fake_call_venus(messages, temperature=0.3):
        captured["prompt"] = messages[-1]["content"]
        return "# 本周周报\n\n- 已完成任务\n- 下周 Todo"

    monkeypatch.setattr(app_module, "call_venus", fake_call_venus)

    task = client.post(
        "/api/tasks",
        json={
            "title": "本周完成项",
            "status": "待办",
            "priority": "高",
            "plan_date": "2026-06-07",
            "project": "测试",
            "tags": "",
            "notes": "",
        },
    ).json()
    client.put(f"/api/tasks/{task['id']}", json={**task, "status": "完成"})
    client.post(
        "/api/tasks",
        json={
            "title": "下周高优先级待办",
            "status": "待办",
            "priority": "高",
            "plan_date": "2026-06-14",
            "project": "测试",
            "tags": "",
            "notes": "",
        },
    )

    response = client.post("/api/weekly-report", json={})

    assert response.status_code == 200
    assert "本周周报" in response.json()["report"]
    assert "本周完成项" in captured["prompt"]
    assert "下周高优先级待办" in captured["prompt"]


def test_export_csv_and_backup_json_include_tasks(tmp_path, monkeypatch):
    _, client = load_app(tmp_path, monkeypatch)
    client.post(
        "/api/tasks",
        json={
            "title": "导出测试任务",
            "status": "待办",
            "priority": "中",
            "plan_date": "2026-06-07",
            "project": "测试",
            "tags": "导出",
            "notes": "确认 CSV 和 JSON 备份",
        },
    )

    csv_response = client.get("/api/export.csv")
    assert csv_response.status_code == 200
    assert csv_response.headers["content-type"].startswith("text/csv")
    assert "导出测试任务" in csv_response.text
    assert "completed_at" in csv_response.text.splitlines()[0]

    backup_response = client.get("/api/backup.json")
    assert backup_response.status_code == 200
    payload = backup_response.json()
    assert payload["schema"] == "personal-plan-table/v1"
    assert payload["tasks"][0]["title"] == "导出测试任务"
    assert "exported_at" in payload


def test_import_json_merges_backup_and_skips_duplicates(tmp_path, monkeypatch):
    _, client = load_app(tmp_path, monkeypatch)
    backup = {
        "tasks": [
            {
                "id": 42,
                "title": "导入任务",
                "status": "待办",
                "priority": "高",
                "plan_date": "2026-06-08",
                "project": "导入测试",
                "tags": "备份",
                "notes": "从 JSON 备份导入",
                "created_at": "2026-06-07T00:00:00Z",
                "updated_at": "2026-06-07T00:00:00Z",
                "completed_at": None,
            }
        ]
    }

    first = client.post("/api/import.json", json=backup)
    second = client.post("/api/import.json", json=backup)

    assert first.status_code == 200
    assert first.json() == {"imported": 1, "skipped": 0}
    assert second.json() == {"imported": 0, "skipped": 1}

    tasks = client.get("/api/tasks").json()
    assert len(tasks) == 1
    assert tasks[0]["title"] == "导入任务"
    assert tasks[0]["project"] == "导入测试"
