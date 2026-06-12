import importlib
import os
import sys
from pathlib import Path

from fastapi.testclient import TestClient

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))


def load_app(tmp_path, monkeypatch):
    monkeypatch.setenv("OPENAI_API_BASE_URL", "https://api.example.com/v1/chat/completions")
    monkeypatch.setenv("OPENAI_API_KEY", "test-key")
    monkeypatch.setenv("OPENAI_MODEL", "test-model")
    monkeypatch.setenv("DATA_DIR", str(tmp_path))
    monkeypatch.delenv("ACCESS_TOKEN", raising=False)

    import app

    app = importlib.reload(app)
    app.init_db()
    return app, TestClient(app.app)


def load_app_with_access_token(tmp_path, monkeypatch, passcode="test-passcode"):
    monkeypatch.setenv("OPENAI_API_BASE_URL", "https://api.example.com/v1/chat/completions")
    monkeypatch.setenv("OPENAI_API_KEY", "test-key")
    monkeypatch.setenv("OPENAI_MODEL", "test-model")
    monkeypatch.setenv("DATA_DIR", str(tmp_path))
    monkeypatch.setenv("ACCESS_TOKEN", passcode)

    import app

    app = importlib.reload(app)
    app.init_db()
    return app, TestClient(app.app)


def test_openai_config_can_be_updated_without_returning_secret(tmp_path, monkeypatch):
    _, client = load_app(tmp_path, monkeypatch)

    initial = client.get("/api/config/openai").json()
    updated = client.put(
        "/api/config/openai",
        json={
            "api_base_url": "https://example.com/v1/chat/completions",
            "api_key": "new-key",
            "model": "custom-model",
        },
    ).json()
    persisted = client.get("/api/config/openai").json()

    assert initial == {"api_base_url": "https://api.example.com/v1/chat/completions", "api_key_set": True, "model": "test-model"}
    assert updated == {"api_base_url": "https://example.com/v1/chat/completions", "api_key_set": True, "model": "custom-model"}
    assert persisted == updated
    assert "new-key" not in str(updated)


def test_access_token_protects_task_api_when_enabled(tmp_path, monkeypatch):
    _, client = load_app_with_access_token(tmp_path, monkeypatch)

    status = client.get("/api/auth/status")
    blocked = client.get("/api/tasks")
    wrong_login = client.post("/api/auth/login", json={"access_token": "wrong"})
    login = client.post("/api/auth/login", json={"access_token": "test-passcode"})
    allowed = client.get("/api/tasks")

    assert status.json() == {"enabled": True, "authenticated": False}
    assert blocked.status_code == 401
    assert wrong_login.status_code == 401
    assert login.status_code == 200
    assert login.cookies.get("personal_plan_session")
    assert allowed.status_code == 200


def test_logout_clears_access_cookie(tmp_path, monkeypatch):
    _, client = load_app_with_access_token(tmp_path, monkeypatch)

    login = client.post("/api/auth/login", json={"access_token": "test-passcode"})
    allowed = client.get("/api/tasks")
    logout = client.post("/api/auth/logout")
    status = client.get("/api/auth/status")
    blocked = client.get("/api/tasks")

    assert login.status_code == 200
    assert allowed.status_code == 200
    assert logout.status_code == 200
    assert status.json() == {"enabled": True, "authenticated": False}
    assert blocked.status_code == 401


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


def test_task_input_rejects_invalid_status_priority_and_plan_date(tmp_path, monkeypatch):
    _, client = load_app(tmp_path, monkeypatch)
    payload = {
        "title": "非法字段测试",
        "status": "随便写",
        "priority": "高",
        "plan_date": "2026-06-07",
        "project": "测试",
        "tags": "",
        "notes": "",
    }

    bad_status = client.post("/api/tasks", json=payload)
    bad_priority = client.post("/api/tasks", json={**payload, "status": "待办", "priority": "最高"})
    bad_plan_date = client.post("/api/tasks", json={**payload, "status": "待办", "plan_date": "下周三"})

    assert bad_status.status_code == 422
    assert bad_priority.status_code == 422
    assert bad_plan_date.status_code == 422


def test_delete_task_returns_404_when_task_is_missing(tmp_path, monkeypatch):
    _, client = load_app(tmp_path, monkeypatch)
    created = client.post(
        "/api/tasks",
        json={
            "title": "删除语义测试",
            "status": "待办",
            "priority": "中",
            "plan_date": "2026-06-07",
            "project": "测试",
            "tags": "",
            "notes": "",
        },
    ).json()

    deleted = client.delete(f"/api/tasks/{created['id']}")
    missing = client.delete(f"/api/tasks/{created['id']}")

    assert deleted.status_code == 200
    assert deleted.json() == {"deleted": 1}
    assert missing.status_code == 404


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

    async def fake_call_openai(messages, temperature=0.3):
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

    monkeypatch.setattr(app_module, "call_openai", fake_call_openai)
    response = client.post("/api/generate", json={"prompt": "做任务", "horizon": "本周"})

    assert response.status_code == 200
    body = response.json()
    assert body["skipped"] == ["验证表格任务保存与编辑"]
    assert [task["title"] for task in body["tasks"]] == ["新复盘任务"]
    assert body["tasks"][0]["needs_input"] == ["plan_date", "project"]


def test_generate_keeps_single_sentence_request_to_one_task(tmp_path, monkeypatch):
    app_module, client = load_app(tmp_path, monkeypatch)

    async def fake_call_openai(messages, temperature=0.3):
        assert "默认只生成 1 个任务" in messages[-1]["content"]
        return """
        {
          "summary": "本周计划重点为今日内完成与敬炎对接CRP系统接口开发时间的相关工作",
          "skipped": [],
          "tasks": [
            {
              "title": "找敬炎对齐CRP系统接口的开发时间",
              "status": "待办",
              "priority": "高",
              "plan_date": "2026-06-08",
              "project": "",
              "tags": "对接,CRP系统,接口开发",
              "notes": "今日内完成与敬炎的沟通，明确CRP系统接口的开发时间安排",
              "needs_input": ["project"]
            },
            {
              "title": "整理CRP系统接口对接需求文档",
              "status": "待办",
              "priority": "中",
              "plan_date": null,
              "project": "",
              "tags": "文档,CRP系统,接口开发",
              "notes": "梳理CRP系统接口对接的相关需求",
              "needs_input": ["plan_date", "project"]
            },
            {
              "title": "确认CRP系统接口开发所需资源",
              "status": "待办",
              "priority": "中",
              "plan_date": null,
              "project": "",
              "tags": "资源协调,CRP系统,接口开发",
              "notes": "明确开发需要的人力、环境等资源是否到位",
              "needs_input": ["plan_date", "project"]
            }
          ]
        }
        """

    monkeypatch.setattr(app_module, "call_openai", fake_call_openai)
    response = client.post(
        "/api/generate",
        json={
            "prompt": "本周计划重点为今日内完成与敬炎对接CRP系统接口开发时间的相关工作\n找敬炎对齐CRP系统接口的开发时间",
            "horizon": "本周",
        },
    )

    assert response.status_code == 200
    body = response.json()
    assert [task["title"] for task in body["tasks"]] == ["找敬炎对齐CRP系统接口的开发时间"]
    assert body["tasks"][0]["plan_date"] == "2026-06-08"
    assert body["tasks"][0]["needs_input"] == ["project"]


def test_infer_plan_date_supports_month_periods(tmp_path, monkeypatch):
    app_module, _ = load_app(tmp_path, monkeypatch)
    today = app_module.date(2026, 6, 11)

    assert app_module.infer_plan_date_from_prompt("订到7月中", today=today) == "2026-07-15"
    assert app_module.infer_plan_date_from_prompt("7月中旬完成", today=today) == "2026-07-15"
    assert app_module.infer_plan_date_from_prompt("6月下旬到6月底左右提前一周完成", today=today) == "2026-06-23"


def test_generate_infers_project_and_fuzzy_deadline_from_prompt(tmp_path, monkeypatch):
    app_module, client = load_app(tmp_path, monkeypatch)

    async def fake_call_openai(messages, temperature=0.3):
        return """
        {
          "summary": "中长期年度预测计划",
          "skipped": [],
          "tasks": [
            {
              "title": "中长期年度预测",
              "status": "待办",
              "priority": "高",
              "plan_date": null,
              "project": "",
              "tags": "年度预测,年底预测系统",
              "notes": "准备年底预测系统、一部年度收入和飞哥聊天记录等数据",
              "needs_input": ["plan_date", "project"]
            }
          ]
        }
        """

    monkeypatch.setattr(app_module, "call_openai", fake_call_openai)
    response = client.post(
        "/api/generate",
        json={
            "prompt": "中长期年度预测，预估6月下旬到6月底左右开始，\n以下是该任务所需的数据、系统\n年底预测系统：https://cvmforecast.no1.woa.com/\n一部年度收入，飞哥聊天记录\n中长期年度预测，ddl：6月下旬到6月底左右提前一周完成",
            "horizon": "本周",
        },
    )

    assert response.status_code == 200
    task = response.json()["tasks"][0]
    assert task["project"] == "中长期年度预测"
    assert task["plan_date"] == "2026-06-23"
    assert task["needs_input"] == []


def test_weekly_report_uses_completed_and_planned_tasks(tmp_path, monkeypatch):
    app_module, client = load_app(tmp_path, monkeypatch)
    captured = {}

    async def fake_call_openai(messages, temperature=0.3):
        captured["prompt"] = messages[-1]["content"]
        return "# 本周周报\n\n- 已完成任务\n- 下周 Todo"

    monkeypatch.setattr(app_module, "call_openai", fake_call_openai)

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
    assert "本周已完成任务数：1" in captured["prompt"]
    assert "周报总结分析专家" in captured["prompt"]
    assert "一、本周总体概览" in captured["prompt"]
    assert "趋势洞察" in captured["prompt"]
    assert "二、按项目进展与关键里程碑" in captured["prompt"]
    assert "关键 milestone" in captured["prompt"]
    assert "本周预计投入时间" in captured["prompt"]
    assert "工作量判断" in captured["prompt"]
    assert "四、下周 Roadmap" in captured["prompt"]
    assert "不要输出 #16、#23 这类任务编号" in captured["prompt"]
    assert "不要逐条罗列" in captured["prompt"]
    assert "下周进展" not in captured["prompt"]
    assert "三、风险与关注点" in captured["prompt"]
    assert "五、需要协同/确认事项" in captured["prompt"]
    assert "输出要有洞察" in captured["prompt"]
    assert "测试: 本周完成 1 项；下周候选 1 项" in captured["prompt"]
    assert "不要使用“高优=4h、中优=2h、低优=1h”等固定公式" in captured["prompt"]
    assert "预估投入 4h" not in captured["prompt"]
    assert "规则：" in captured["prompt"]


def test_weekly_plan_uses_stable_project_grouped_prompt(tmp_path, monkeypatch):
    app_module, client = load_app(tmp_path, monkeypatch)
    captured = {}

    async def fake_call_openai(messages, temperature=0.3):
        captured["prompt"] = messages[-1]["content"]
        captured["temperature"] = temperature
        return "本周计划\n\n【测试项目】\n- 本周项目任务：明确完成标准"

    monkeypatch.setattr(app_module, "call_openai", fake_call_openai)

    client.post(
        "/api/tasks",
        json={
            "title": "本周项目任务",
            "status": "待办",
            "priority": "高",
            "plan_date": "2026-06-08",
            "project": "测试项目",
            "tags": "",
            "notes": "明确完成标准",
        },
    )

    response = client.post("/api/weekly-plan", json={})

    assert response.status_code == 200
    assert "本周计划" in response.json()["plan"]
    assert "输出格式必须每次保持一致" in captured["prompt"]
    assert "按项目安排：" in captured["prompt"]
    assert "【<项目名或未归类>】" in captured["prompt"]
    assert "必须按 project 归类" in captured["prompt"]
    assert "不要使用 Markdown 表格" in captured["prompt"]
    assert "像发群里的文字汇总" in captured["prompt"]
    assert "| 优先级 |" not in captured["prompt"]
    assert "本周项目任务" in captured["prompt"]
    assert captured["temperature"] == 0.2


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
    assert csv_response.content.startswith(b"\xef\xbb\xbf")
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
