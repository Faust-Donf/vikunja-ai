from __future__ import annotations

import csv
import hashlib
import hmac
import io
import json
import os
import re
import sqlite3
from contextlib import asynccontextmanager
from datetime import date, datetime, timedelta
from difflib import SequenceMatcher
from pathlib import Path
from typing import Any

import httpx
from fastapi import Depends, FastAPI, HTTPException, Request
from fastapi.responses import HTMLResponse, JSONResponse, Response, StreamingResponse
from pydantic import BaseModel, Field, field_validator


VENUS_API_URL = os.environ["VENUS_API_URL"]
VENUS_API_KEY = os.environ["VENUS_API_KEY"]
VENUS_MODEL = os.environ.get("VENUS_MODEL", "hy3-preview")
DATA_DIR = Path(os.environ.get("DATA_DIR", "/data"))
DB_PATH = DATA_DIR / "plans.db"
ACCESS_TOKEN = os.environ.get("ACCESS_TOKEN", "").strip()
AUTH_COOKIE = "personal_plan_session"
VALID_STATUSES = {"待办", "进行中", "阻塞", "完成"}
VALID_PRIORITIES = {"高", "中", "低"}


class TaskInput(BaseModel):
    title: str = Field(min_length=1, max_length=250)
    status: str = "待办"
    priority: str = "中"
    plan_date: str | None = None
    project: str = ""
    tags: str = ""
    notes: str = ""

    @field_validator("status")
    @classmethod
    def validate_status(cls, value: str) -> str:
        if value not in VALID_STATUSES:
            raise ValueError("状态必须是：待办、进行中、阻塞、完成")
        return value

    @field_validator("priority")
    @classmethod
    def validate_priority(cls, value: str) -> str:
        if value not in VALID_PRIORITIES:
            raise ValueError("优先级必须是：高、中、低")
        return value

    @field_validator("plan_date")
    @classmethod
    def validate_plan_date(cls, value: str | None) -> str | None:
        if not value:
            return None
        try:
            return date.fromisoformat(value).isoformat()
        except ValueError as exc:
            raise ValueError("计划日期必须是 YYYY-MM-DD") from exc


class Task(TaskInput):
    id: int
    created_at: str
    updated_at: str
    completed_at: str | None = None


class ChatMessage(BaseModel):
    role: str
    content: str


class ChatRequest(BaseModel):
    message: str = Field(min_length=1)
    history: list[ChatMessage] = []


class ChatResponse(BaseModel):
    reply: str


class GenerateRequest(BaseModel):
    prompt: str = Field(min_length=2)
    horizon: str = "本周"


class GeneratedTask(BaseModel):
    title: str
    status: str = "待办"
    priority: str = "中"
    plan_date: str | None = None
    project: str = ""
    tags: str = ""
    notes: str = ""
    needs_input: list[str] = []


class GenerateResponse(BaseModel):
    summary: str
    tasks: list[GeneratedTask]
    skipped: list[str] = []


class WeeklyReportResponse(BaseModel):
    report: str


class ImportBackupRequest(BaseModel):
    tasks: list[dict[str, Any]]


class ImportBackupResponse(BaseModel):
    imported: int
    skipped: int


class LoginRequest(BaseModel):
    access_token: str = Field(min_length=1)


TASK_COLUMNS = [
    "id",
    "source_id",
    "title",
    "status",
    "priority",
    "plan_date",
    "project",
    "tags",
    "notes",
    "created_at",
    "updated_at",
    "completed_at",
]


def db() -> sqlite3.Connection:
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn


def init_db() -> None:
    with db() as conn:
        conn.execute(
            """
            create table if not exists tasks (
                id integer primary key autoincrement,
                title text not null,
                status text not null default '待办',
                priority text not null default '中',
                plan_date text,
                project text not null default '',
                tags text not null default '',
                notes text not null default '',
                created_at text not null,
                updated_at text not null,
                completed_at text,
                source_id text
            )
            """
        )
        columns = {row["name"] for row in conn.execute("pragma table_info(tasks)").fetchall()}
        if "completed_at" not in columns:
            conn.execute("alter table tasks add column completed_at text")
        if "source_id" not in columns:
            conn.execute("alter table tasks add column source_id text")


@asynccontextmanager
async def lifespan(_: FastAPI):
    init_db()
    yield


app = FastAPI(title="个人计划表", lifespan=lifespan)


def auth_enabled() -> bool:
    return bool(ACCESS_TOKEN)


def auth_cookie_value() -> str:
    return hashlib.sha256(ACCESS_TOKEN.encode("utf-8")).hexdigest()


def is_authenticated(request: Request) -> bool:
    if not auth_enabled():
        return True
    return hmac.compare_digest(request.cookies.get(AUTH_COOKIE, ""), auth_cookie_value())


def require_access(request: Request) -> None:
    if not is_authenticated(request):
        raise HTTPException(401, "请先登录")


def row_to_task(row: sqlite3.Row) -> Task:
    return Task(**dict(row))


def list_task_rows() -> list[sqlite3.Row]:
    with db() as conn:
        return conn.execute(
            """
            select * from tasks
            order by
                case status when '进行中' then 0 when '待办' then 1 when '阻塞' then 2 else 3 end,
                coalesce(plan_date, '9999-12-31'),
                id desc
            """
        ).fetchall()


def now() -> str:
    return datetime.utcnow().replace(microsecond=0).isoformat() + "Z"


def validate_date(value: str | None) -> str | None:
    if not value:
        return None
    try:
        return date.fromisoformat(value).isoformat()
    except ValueError:
        return None


def tasks_context(limit: int = 80) -> str:
    with db() as conn:
        rows = conn.execute(
            """
            select id, title, status, priority, plan_date, project, tags, notes, completed_at, updated_at
            from tasks
            order by
                case status when '进行中' then 0 when '待办' then 1 when '阻塞' then 2 else 3 end,
                coalesce(plan_date, '9999-12-31'),
                id desc
            limit ?
            """,
            (limit,),
        ).fetchall()
    if not rows:
        return "当前任务表为空。"
    lines = []
    for row in rows:
        lines.append(
            f"- #{row['id']} [{row['status']}] {row['title']} "
            f"优先级:{row['priority']} 日期:{row['plan_date'] or '-'} "
            f"完成时间:{row['completed_at'] or '-'} 更新时间:{row['updated_at'] or '-'} "
            f"项目:{row['project'] or '-'} 标签:{row['tags'] or '-'} 备注:{row['notes'] or '-'}"
        )
    return "\n".join(lines)


def existing_task_titles(limit: int = 120) -> str:
    with db() as conn:
        rows = conn.execute(
            """
            select id, title, status, project, plan_date
            from tasks
            order by id desc
            limit ?
            """,
            (limit,),
        ).fetchall()
    if not rows:
        return "无"
    return "\n".join(
        f"- #{row['id']} [{row['status']}] {row['title']} 项目:{row['project'] or '-'} 日期:{row['plan_date'] or '-'}"
        for row in rows
    )


def title_tokens(title: str) -> str:
    return re.sub(r"[\s\-_/（）()，,。:：#]+", "", title.lower())


def similar_existing_title(title: str) -> str | None:
    normalized = title_tokens(title)
    if not normalized:
        return None
    with db() as conn:
        rows = conn.execute("select title from tasks order by id desc limit 200").fetchall()
    for row in rows:
        existing = row["title"]
        existing_normalized = title_tokens(existing)
        if not existing_normalized:
            continue
        if normalized in existing_normalized or existing_normalized in normalized:
            return existing
        if SequenceMatcher(None, normalized, existing_normalized).ratio() >= 0.82:
            return existing
    return None


def extract_json(content: str) -> dict[str, Any]:
    content = content.strip()
    if content.startswith("```"):
        content = re.sub(r"^```(?:json)?\s*", "", content)
        content = re.sub(r"\s*```$", "", content)
    match = re.search(r"\{.*\}", content, re.S)
    if not match:
        raise ValueError("AI 没有返回 JSON")
    return json.loads(match.group(0))


def week_range(today: date | None = None) -> tuple[date, date, date, date]:
    today = today or date.today()
    start = today - timedelta(days=today.weekday())
    end = start + timedelta(days=6)
    next_start = end + timedelta(days=1)
    next_end = next_start + timedelta(days=6)
    return start, end, next_start, next_end


def row_lines(rows: list[sqlite3.Row], fallback: str) -> str:
    if not rows:
        return fallback
    lines = []
    for row in rows:
        lines.append(
            f"- #{row['id']} [{row['status']}] {row['title']} "
            f"优先级:{row['priority']} 计划日期:{row['plan_date'] or '-'} "
            f"完成时间:{row['completed_at'] or '-'} 项目:{row['project'] or '-'} "
            f"标签:{row['tags'] or '-'} 备注:{row['notes'] or '-'}"
        )
    return "\n".join(lines)


async def call_venus(messages: list[dict[str, str]], temperature: float = 0.3) -> str:
    headers = {
        "Authorization": f"Bearer {VENUS_API_KEY}",
        "Content-Type": "application/json",
    }
    payload = {
        "model": VENUS_MODEL,
        "messages": messages,
        "temperature": temperature,
    }
    async with httpx.AsyncClient(timeout=60) as client:
        resp = await client.post(VENUS_API_URL, headers=headers, json=payload)
        if resp.status_code >= 400:
            raise HTTPException(resp.status_code, f"Venus API 调用失败：{resp.text[:300]}")
        data = resp.json()
    return data["choices"][0]["message"]["content"]


@app.get("/", response_class=HTMLResponse)
async def index() -> str:
    with open("static/index.html", encoding="utf-8") as f:
        return f.read()


@app.get("/health")
async def health() -> dict[str, str]:
    return {"status": "ok"}


@app.get("/api/auth/status")
async def auth_status(request: Request) -> dict[str, bool]:
    return {"enabled": auth_enabled(), "authenticated": is_authenticated(request)}


@app.post("/api/auth/login")
async def auth_login(req: LoginRequest) -> JSONResponse:
    if not auth_enabled():
        return JSONResponse({"authenticated": True})
    if not hmac.compare_digest(req.access_token, ACCESS_TOKEN):
        raise HTTPException(401, "访问口令不正确")
    response = JSONResponse({"authenticated": True})
    response.set_cookie(
        AUTH_COOKIE,
        auth_cookie_value(),
        httponly=True,
        max_age=60 * 60 * 24 * 30,
        samesite="lax",
    )
    return response


@app.post("/api/auth/logout")
async def auth_logout() -> JSONResponse:
    response = JSONResponse({"authenticated": False})
    response.delete_cookie(AUTH_COOKIE)
    return response


@app.get("/favicon.ico")
async def favicon() -> Response:
    return Response(status_code=204)


@app.get("/api/tasks", response_model=list[Task], dependencies=[Depends(require_access)])
async def list_tasks() -> list[Task]:
    return [row_to_task(row) for row in list_task_rows()]


@app.get("/api/export.csv", dependencies=[Depends(require_access)])
async def export_csv() -> StreamingResponse:
    buffer = io.StringIO()
    writer = csv.DictWriter(buffer, fieldnames=TASK_COLUMNS)
    writer.writeheader()
    for row in list_task_rows():
        writer.writerow({column: row[column] for column in TASK_COLUMNS})
    buffer.seek(0)
    filename = f"personal-plan-tasks-{date.today().isoformat()}.csv"
    return StreamingResponse(
        iter(["\ufeff" + buffer.getvalue()]),
        media_type="text/csv; charset=utf-8",
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )


@app.get("/api/backup.json", dependencies=[Depends(require_access)])
async def backup_json() -> JSONResponse:
    payload = {
        "exported_at": now(),
        "schema": "personal-plan-table/v1",
        "tasks": [{column: row[column] for column in TASK_COLUMNS} for row in list_task_rows()],
    }
    filename = f"personal-plan-backup-{date.today().isoformat()}.json"
    return JSONResponse(
        payload,
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )


def normalize_import_task(raw: dict[str, Any]) -> dict[str, Any] | None:
    title = str(raw.get("title") or "").strip()
    if not title:
        return None
    status = str(raw.get("status") or "待办")
    if status not in VALID_STATUSES:
        status = "待办"
    priority = str(raw.get("priority") or "中")
    if priority not in VALID_PRIORITIES:
        priority = "中"
    return {
        "source_id": str(raw.get("id") or raw.get("source_id") or ""),
        "title": title[:250],
        "status": status,
        "priority": priority,
        "plan_date": validate_date(raw.get("plan_date")),
        "project": str(raw.get("project") or ""),
        "tags": str(raw.get("tags") or ""),
        "notes": str(raw.get("notes") or ""),
        "created_at": str(raw.get("created_at") or now()),
        "updated_at": str(raw.get("updated_at") or now()),
        "completed_at": str(raw.get("completed_at") or "") or None,
    }


@app.post("/api/import.json", response_model=ImportBackupResponse, dependencies=[Depends(require_access)])
async def import_json(payload: ImportBackupRequest) -> ImportBackupResponse:
    imported = 0
    skipped = 0
    with db() as conn:
        for raw_task in payload.tasks:
            task = normalize_import_task(raw_task)
            if task is None:
                skipped += 1
                continue
            if task["source_id"]:
                existing = conn.execute(
                    "select id from tasks where source_id = ?",
                    (task["source_id"],),
                ).fetchone()
                if existing:
                    skipped += 1
                    continue
            existing_title = conn.execute(
                """
                select id from tasks
                where title = ? and coalesce(project, '') = ? and coalesce(plan_date, '') = coalesce(?, '')
                """,
                (task["title"], task["project"], task["plan_date"]),
            ).fetchone()
            if existing_title:
                skipped += 1
                continue
            conn.execute(
                """
                insert into tasks (
                    source_id, title, status, priority, plan_date, project, tags, notes,
                    created_at, updated_at, completed_at
                )
                values (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    task["source_id"],
                    task["title"],
                    task["status"],
                    task["priority"],
                    task["plan_date"],
                    task["project"],
                    task["tags"],
                    task["notes"],
                    task["created_at"],
                    task["updated_at"],
                    task["completed_at"],
                ),
            )
            imported += 1
    return ImportBackupResponse(imported=imported, skipped=skipped)


@app.post("/api/tasks", response_model=Task, dependencies=[Depends(require_access)])
async def create_task(task: TaskInput) -> Task:
    ts = now()
    with db() as conn:
        cur = conn.execute(
            """
            insert into tasks (title, status, priority, plan_date, project, tags, notes, created_at, updated_at, completed_at, source_id)
            values (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                task.title,
                task.status,
                task.priority,
                task.plan_date,
                task.project,
                task.tags,
                task.notes,
                ts,
                ts,
                ts if task.status == "完成" else None,
                None,
            ),
        )
        row = conn.execute("select * from tasks where id = ?", (cur.lastrowid,)).fetchone()
    return row_to_task(row)


@app.put("/api/tasks/{task_id}", response_model=Task, dependencies=[Depends(require_access)])
async def update_task(task_id: int, task: TaskInput) -> Task:
    with db() as conn:
        old = conn.execute("select status, completed_at from tasks where id = ?", (task_id,)).fetchone()
        if old is None:
            raise HTTPException(404, "任务不存在")
        completed_at = old["completed_at"]
        if old["status"] != "完成" and task.status == "完成":
            completed_at = now()
        elif old["status"] == "完成" and task.status != "完成":
            completed_at = None
        conn.execute(
            """
            update tasks
            set title = ?, status = ?, priority = ?, plan_date = ?, project = ?, tags = ?, notes = ?, updated_at = ?, completed_at = ?
            where id = ?
            """,
            (
                task.title,
                task.status,
                task.priority,
                task.plan_date,
                task.project,
                task.tags,
                task.notes,
                now(),
                completed_at,
                task_id,
            ),
        )
        row = conn.execute("select * from tasks where id = ?", (task_id,)).fetchone()
    if row is None:
        raise HTTPException(404, "任务不存在")
    return row_to_task(row)


@app.delete("/api/tasks/{task_id}", dependencies=[Depends(require_access)])
async def delete_task(task_id: int) -> dict[str, int]:
    with db() as conn:
        cur = conn.execute("delete from tasks where id = ?", (task_id,))
    if cur.rowcount == 0:
        raise HTTPException(404, "任务不存在")
    return {"deleted": cur.rowcount}


@app.post("/api/chat", response_model=ChatResponse, dependencies=[Depends(require_access)])
async def chat(req: ChatRequest) -> ChatResponse:
    history = [
        {"role": msg.role, "content": msg.content}
        for msg in req.history[-8:]
        if msg.role in {"user", "assistant"}
    ]
    messages = [
        {
            "role": "system",
            "content": (
                "你是一个中文个人计划管理 AI助手。你能基于用户当前任务表，"
                "帮助梳理优先级、拆解任务、做日计划、周复盘。回答要直接、可执行。"
            ),
        },
        {"role": "user", "content": f"当前任务表：\n{tasks_context()}"},
        *history,
        {"role": "user", "content": req.message},
    ]
    return ChatResponse(reply=await call_venus(messages, temperature=0.4))


@app.post("/api/weekly-report", response_model=WeeklyReportResponse, dependencies=[Depends(require_access)])
async def weekly_report() -> WeeklyReportResponse:
    start, end, next_start, next_end = week_range()
    start_s, end_s = start.isoformat(), end.isoformat()
    next_start_s, next_end_s = next_start.isoformat(), next_end.isoformat()
    with db() as conn:
        completed = conn.execute(
            """
            select * from tasks
            where status = '完成'
              and coalesce(substr(completed_at, 1, 10), substr(updated_at, 1, 10)) between ? and ?
            order by priority, coalesce(completed_at, updated_at)
            """,
            (start_s, end_s),
        ).fetchall()
        planned = conn.execute(
            """
            select * from tasks
            where status != '完成'
              and (
                plan_date between ? and ?
                or priority = '高'
                or status in ('进行中', '阻塞')
              )
            order by
              case priority when '高' then 0 when '中' then 1 else 2 end,
              coalesce(plan_date, '9999-12-31')
            limit 60
            """,
            (next_start_s, next_end_s),
        ).fetchall()
    prompt = f"""
请根据下面的个人任务表数据，生成一份中文周报。

本周范围：{start_s} 至 {end_s}
下周范围：{next_start_s} 至 {next_end_s}

本周已完成任务：
{row_lines(completed, "本周没有记录到已归档完成任务。")}

下周待办候选：
{row_lines(planned, "暂时没有下周待办候选。")}

输出要求：
1. 标题用“本周周报（{start_s} - {end_s}）”
2. 分为：本周完成、本周重点与价值、风险/阻塞、下周 Todo、优先级建议
3. 下周 Todo 要按重要性和紧急性排序，说明哪些是重要且紧急、重要不紧急
4. 不要编造任务表里没有的信息；如果完成项少，要明确说数据不足
5. 语气像个人工作复盘，简洁、可直接发送
"""
    messages = [
        {
            "role": "system",
            "content": "你是中文个人计划管理助理，擅长根据任务表生成周报和下周计划。",
        },
        {"role": "user", "content": prompt},
    ]
    return WeeklyReportResponse(report=await call_venus(messages, temperature=0.35))


@app.post("/api/generate", response_model=GenerateResponse, dependencies=[Depends(require_access)])
async def generate(req: GenerateRequest) -> GenerateResponse:
    today = date.today().isoformat()
    prompt = f"""
今天是 {today}。请根据用户输入，为个人计划表生成 {req.horizon} 的任务行。

用户输入：
{req.prompt}

当前任务表：
{tasks_context(40)}

现有任务标题清单，用于去重：
{existing_task_titles()}

只输出 JSON，格式：
{{
  "summary": "一句话说明计划重点",
  "skipped": ["因为已存在而跳过的任务标题"],
  "tasks": [
    {{
      "title": "任务标题",
      "status": "待办",
      "priority": "高/中/低",
      "plan_date": "YYYY-MM-DD 或 null",
      "project": "项目名",
      "tags": "逗号分隔标签",
      "notes": "完成标准或下一步",
      "needs_input": ["plan_date", "project"]
    }}
  ]
}}

任务数量 3-10 个。不要生成与现有任务标题相同或高度相似的任务，重复项放进 skipped。
如果无法判断 ddl 或项目归属，不要编造；对应字段留空或 null，并在 needs_input 中写 plan_date 或 project。
"""
    content = await call_venus(
        [
            {
                "role": "system",
                "content": "你是中文个人计划管理助手。只输出 JSON，不要 Markdown。",
            },
            {"role": "user", "content": prompt},
        ]
    )
    try:
        raw = extract_json(content)
    except Exception as exc:
        raise HTTPException(502, f"AI 返回格式不可解析：{exc}") from exc
    tasks = []
    skipped = [str(item) for item in raw.get("skipped", []) if str(item).strip()]
    for item in raw.get("tasks", []):
        title = str(item.get("title") or "").strip()
        if not title:
            continue
        existing = similar_existing_title(title)
        if existing:
            skipped.append(existing)
            continue
        priority = str(item.get("priority") or "中")
        if priority not in VALID_PRIORITIES:
            priority = "中"
        status = str(item.get("status") or "待办")
        if status not in VALID_STATUSES:
            status = "待办"
        tasks.append(
            GeneratedTask(
                title=title[:250],
                status=status,
                priority=priority,
                plan_date=validate_date(item.get("plan_date")),
                project=str(item.get("project") or ""),
                tags=str(item.get("tags") or ""),
                notes=str(item.get("notes") or ""),
                needs_input=[
                    str(field)
                    for field in item.get("needs_input", [])
                    if str(field) in {"plan_date", "project", "priority", "notes"}
                ],
            )
        )
    if not tasks:
        raise HTTPException(502, "AI 没有生成可用任务")
    skipped = list(dict.fromkeys(skipped))
    return GenerateResponse(summary=str(raw.get("summary") or ""), tasks=tasks[:10], skipped=skipped[:20])
