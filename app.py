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


DEFAULT_OPENAI_API_BASE_URL = os.environ.get("OPENAI_API_BASE_URL", "https://api.openai.com/v1/chat/completions")
DEFAULT_OPENAI_API_KEY = os.environ.get("OPENAI_API_KEY", "").strip()
DEFAULT_OPENAI_MODEL = os.environ.get("OPENAI_MODEL", "gpt-4o-mini")
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


class WeeklyPlanResponse(BaseModel):
    plan: str


class ImportBackupRequest(BaseModel):
    tasks: list[dict[str, Any]]


class ImportBackupResponse(BaseModel):
    imported: int
    skipped: int


class LoginRequest(BaseModel):
    access_token: str = Field(min_length=1)


class ApiConfigInput(BaseModel):
    api_base_url: str = Field(min_length=1, max_length=500)
    api_key: str = Field(default="", max_length=500)
    model: str = Field(min_length=1, max_length=120)


class ApiConfigResponse(BaseModel):
    api_base_url: str
    api_key_set: bool
    model: str


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
        conn.execute(
            """
            create table if not exists settings (
                key text primary key,
                value text not null
            )
            """
        )


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


def month_end(year: int, month: int) -> date:
    if month == 12:
        return date(year, 12, 31)
    return date(year, month + 1, 1) - timedelta(days=1)


def infer_plan_date_from_prompt(prompt: str, today: date | None = None) -> str | None:
    today = today or date.today()
    text = prompt.strip()

    # 中文数字到阿拉伯数字的映射
    CN_NUM_MAP = {
        "一": 1, "二": 2, "两": 2, "三": 3, "四": 4, "五": 5,
        "六": 6, "七": 7, "八": 8, "九": 9, "十": 10,
    }

    def _parse_cn_num(s: str) -> int | None:
        """解析中文数字或阿拉伯数字，返回 int 或 None"""
        if s.isdigit():
            return int(s)
        return CN_NUM_MAP.get(s)

    # "下周前N天" / "下周前N" → 下周一+(N-1)天（下周一为第1天）
    match = re.search(r"下周\s*前\s*(\d+|[一二三四五六七八九十两]+)", text)
    if match:
        days = _parse_cn_num(match.group(1))
        if days is not None:
            next_week_start = today + timedelta(days=(7 - today.weekday()))
            return (next_week_start + timedelta(days=days - 1)).isoformat()

    # "下周X" → 下周特定星期几（需排除"下周前"已经被上面匹配的情况）
    WEEKDAY_MAP = {
        "一": 0, "二": 1, "三": 2, "四": 3, "五": 4, "六": 5, "日": 6, "天": 6,
    }
    match = re.search(r"下周(?!\s*前)\s*([一二三四五六日天])", text)
    if match:
        target_weekday = WEEKDAY_MAP.get(match.group(1))
        if target_weekday is not None:
            next_week_start = today + timedelta(days=(7 - today.weekday()))
            return (next_week_start + timedelta(days=target_weekday)).isoformat()

    # "本周前N天" / "本周前N"
    match = re.search(r"本周\s*前\s*(\d+|[一二三四五六七八九十两]+)", text)
    if match:
        days = _parse_cn_num(match.group(1))
        if days is not None:
            this_week_start = today - timedelta(days=today.weekday())
            return (this_week_start + timedelta(days=days - 1)).isoformat()

    # "今天" / "明天" / "后天"
    match = re.search(r"(今天|明天|后天)", text)
    if match:
        offset = {"今天": 0, "明天": 1, "后天": 2}
        return (today + timedelta(days=offset[match.group(1)])).isoformat()

    # "X月上旬/中旬/下旬"、"X月初/中/底/末"
    match = re.search(r"(\d{1,2})\s*月\s*(上旬|中旬|下旬|月初|初|月中|中|月底|月末|底|末)", text)
    if match:
        month = int(match.group(1))
        if not 1 <= month <= 12:
            return None
        year = today.year if month >= today.month else today.year + 1
        period = match.group(2)
        if period in {"上旬", "月初", "初"}:
            target = date(year, month, 5)
        elif period in {"中旬", "月中", "中"}:
            target = date(year, month, 15)
        else:
            target = month_end(year, month)
        if re.search(r"提前\s*(?:一|1)\s*周完成", text):
            target -= timedelta(days=7)
        return target.isoformat()

    # "X月底"
    match = re.search(r"(\d{1,2})\s*月底", text)
    if not match:
        return None
    month = int(match.group(1))
    if not 1 <= month <= 12:
        return None
    year = today.year if month >= today.month else today.year + 1
    target = month_end(year, month)
    if re.search(r"提前\s*(?:一|1)\s*周完成", text):
        target -= timedelta(days=7)
    return target.isoformat()


def infer_project_from_prompt(prompt: str) -> str:
    lines = [line.strip() for line in prompt.splitlines() if line.strip()]
    if not lines:
        return ""
    for line in lines:
        match = re.search(r"(?:项目|任务|计划)[:：]\s*([^，,。；;\n]+)", line)
        if match:
            return match.group(1).strip()[:30]
    first = re.split(r"[，,。；;：:]", lines[0], maxsplit=1)[0].strip()
    if (
        2 <= len(first) <= 30
        and not first.startswith(("http://", "https://"))
        and re.search(r"(项目|系统|预测|平台|产品|版本|专项|计划)$", first)
    ):
        return first
    return ""


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


def prompt_has_multiple_task_signals(prompt: str) -> bool:
    text = prompt.strip()
    if re.search(r"(^|\n)\s*(?:[-*]|\d+[.、])\s*\S+", text):
        return True
    lines = [line.strip() for line in text.splitlines() if line.strip()]
    if len(lines) >= 3:
        return True
    if len(lines) == 2:
        first, second = title_tokens(lines[0]), title_tokens(lines[1])
        if first and second and (first in second or second in first):
            return False
        if SequenceMatcher(None, first, second).ratio() < 0.45:
            return True
    return bool(re.search(r"[；;]|(?:、[^，。；;]{2,}(?:、|和|及))", text))


def relevance_score(prompt: str, task: GeneratedTask) -> float:
    source = title_tokens(prompt)
    target = title_tokens(f"{task.title}{task.notes}{task.tags}")
    if not source or not target:
        return 0
    shared = sum(1 for char in set(source) if char in target)
    coverage = shared / max(len(set(source)), 1)
    return coverage + SequenceMatcher(None, source, target).ratio()


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


def row_lines(rows: list[sqlite3.Row], fallback: str, include_ids: bool = True) -> str:
    if not rows:
        return fallback
    lines = []
    for row in rows:
        prefix = f"- #{row['id']} [{row['status']}]" if include_ids else f"- [{row['status']}]"
        lines.append(
            f"{prefix} {row['title']} "
            f"优先级:{row['priority']} 计划日期:{row['plan_date'] or '-'} "
            f"完成时间:{row['completed_at'] or '-'} 项目:{row['project'] or '-'} "
            f"标签:{row['tags'] or '-'} 备注:{row['notes'] or '-'}"
        )
    return "\n".join(lines)


def weekly_project_summary(completed: list[sqlite3.Row], planned: list[sqlite3.Row]) -> str:
    projects: dict[str, dict[str, int]] = {}
    for row in completed:
        project = row["project"] or "未归类"
        projects.setdefault(project, {"completed": 0, "planned": 0})
        projects[project]["completed"] += 1
    for row in planned:
        project = row["project"] or "未归类"
        projects.setdefault(project, {"completed": 0, "planned": 0})
        projects[project]["planned"] += 1
    if not projects:
        return "无项目数据。"
    lines = []
    for project, stats in sorted(projects.items()):
        lines.append(f"- {project}: 本周完成 {stats['completed']} 项；下周候选 {stats['planned']} 项")
    return "\n".join(lines)


def get_setting(key: str, default: str = "") -> str:
    with db() as conn:
        row = conn.execute("select value from settings where key = ?", (key,)).fetchone()
    return row["value"] if row else default


def set_setting(key: str, value: str) -> None:
    with db() as conn:
        conn.execute(
            "insert into settings (key, value) values (?, ?) on conflict(key) do update set value = excluded.value",
            (key, value),
        )


def get_openai_config() -> dict[str, str]:
    return {
        "api_base_url": get_setting("openai_api_base_url", DEFAULT_OPENAI_API_BASE_URL),
        "api_key": get_setting("openai_api_key", DEFAULT_OPENAI_API_KEY),
        "model": get_setting("openai_model", DEFAULT_OPENAI_MODEL),
    }


async def call_openai(messages: list[dict[str, str]], temperature: float = 0.3) -> str:
    config = get_openai_config()
    api_key = config["api_key"]
    if not api_key:
        raise HTTPException(400, "请先配置 OpenAI-compatible API Key")
    headers = {
        "Authorization": f"Bearer {api_key}",
        "Content-Type": "application/json",
    }
    payload = {
        "model": config["model"],
        "messages": messages,
        "temperature": temperature,
    }
    async with httpx.AsyncClient(timeout=60) as client:
        resp = await client.post(config["api_base_url"], headers=headers, json=payload)
        if resp.status_code >= 400:
            raise HTTPException(resp.status_code, f"OpenAI-compatible API 调用失败：{resp.text[:300]}")
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


@app.get("/api/config/openai", response_model=ApiConfigResponse, dependencies=[Depends(require_access)])
async def read_openai_config() -> ApiConfigResponse:
    config = get_openai_config()
    return ApiConfigResponse(
        api_base_url=config["api_base_url"],
        api_key_set=bool(config["api_key"]),
        model=config["model"],
    )


@app.put("/api/config/openai", response_model=ApiConfigResponse, dependencies=[Depends(require_access)])
async def update_openai_config(config: ApiConfigInput) -> ApiConfigResponse:
    set_setting("openai_api_base_url", config.api_base_url.strip())
    if config.api_key.strip():
        set_setting("openai_api_key", config.api_key.strip())
    set_setting("openai_model", config.model.strip())
    updated = get_openai_config()
    return ApiConfigResponse(
        api_base_url=updated["api_base_url"],
        api_key_set=bool(updated["api_key"]),
        model=updated["model"],
    )


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
    return ChatResponse(reply=await call_openai(messages, temperature=0.4))


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
你是一位专业的周报总结分析专家，擅长从工作记录中提炼重点，生成逻辑清晰、简洁明了的 Markdown 格式周报。
你的目标是帮助用户将日常工作内容转化为有洞察力、适合阅读/汇报/归档的周报，让管理者一目了然。
请根据下面的个人任务表数据生成中文周报。周报面向领导/项目干系人，必须聚焦项目进展、关键 milestone、roadmap 和风险，不要写成个人 Todo 流水账。

本周范围：{start_s} 至 {end_s}
下周范围：{next_start_s} 至 {next_end_s}

本周已完成任务数：{len(completed)}
本周项目统计（仅统计任务数量，不提供固定耗时；工作量请你根据任务标题、备注、任务数量、任务复杂度自行判断）：
{weekly_project_summary(completed, planned)}

本周已完成任务明细（仅供归纳，不要逐条罗列，不要输出任务编号）：
{row_lines(completed, "本周没有记录到已归档完成任务。", include_ids=False)}

下周待办候选（仅供提炼 roadmap，不要逐条罗列，不要输出任务编号）：
{row_lines(planned, "暂时没有下周待办候选。", include_ids=False)}

输出要求：语言精准、客观、有条理；注重逻辑结构和关键指标；能从记录中提炼趋势、成果与风险；保持专业、简洁、无冗余。
输出格式必须稳定，严格按下面 Markdown 结构输出，不要使用 Markdown 表格：

本周周报（{start_s} - {end_s}）

一、本周总体概览
- 完成任务数：必须写 {len(completed)} 项，不要自行重新计数或估算。
- 核心成果：用 2-4 句话概括本周主要推进方向、关键产出和管理者需要关注的成果。
- 投入分布：按项目概括主要精力投入，请基于任务标题、备注、任务数量和复杂度自行判断工作量，不要机械按优先级折算。
- 趋势洞察：从完成项中提炼本周工作趋势，例如需求交付、算法优化、数据质量、流程协同、风险收敛等。

二、按项目进展与关键里程碑
【<项目名或未归类>】
- 项目进展：用 2-4 句话概括本周该项目推进到什么阶段、解决了什么关键问题、形成了什么阶段性产出；不要逐条罗列具体 Todo。
- 关键 milestone：提炼 1-3 个本周达成的里程碑或阶段成果；如果没有明确里程碑，写“本周主要处于推进/优化阶段”。
- 本周预计投入时间：根据该项目的完成项数量、任务复杂度、备注信息和交付难度，自行估算本周投入时间（例如“约 0.5 天 / 1-2 天 / 3 天以上”或“约 X 小时”）；不要按优先级固定换算小时。
- 工作量判断：补充说明为什么这样估算，判断本周投入是高/中/低或给出合理的文字说明。

三、风险与关注点
- 只写任务表中状态为阻塞、备注里明确有风险，或下周高优先级任务暴露出的风险。
- 如果没有明确阻塞，也要从任务结构中提炼可能需要关注的点，例如跨团队确认、数据口径、上线节奏、需求收敛等；没有就写“暂无明确风险/阻塞”。

四、下周 Roadmap
- 按项目输出下周 roadmap，不要逐条罗列 Todo。
- 每个项目提炼 1-3 个推进方向或关键里程碑，说明目标、优先级、工作量判断和建议完成时间。
- 高优先级、进行中、阻塞、下周有计划日期的任务优先进入。

五、需要协同/确认事项
- 从任务标题和备注中提取需要他人确认、跨团队协同、需求澄清的事项。
- 没有就写“暂无明确需要协同事项”。

六、总结
- 用 2-3 句话总结本周成果、节奏判断、下周最重要抓手。

规则：
1. 本周完成数量必须使用 {len(completed)}，不能写其他数字。
2. 必须按 project 归类；project 为空时归到“未归类”。
3. 不要输出 #16、#23 这类任务编号，也不要逐条罗列每个项目里的具体工作 Todo。
4. 不要编造任务表里没有的项目、日期、负责人、业务背景或量化结果。
5. 工作量判断由你根据任务内容自行判断，不要使用“高优=4h、中优=2h、低优=1h”等固定公式。
6. 可以对同项目任务做归纳总结，但必须能从任务明细中追溯。
7. 语气要专业、简洁、适合领导汇报，可直接复制发送。
8. 输出要有洞察，不只是改写任务；同类事项要合并，避免冗余。
"""
    messages = [
        {
            "role": "system",
            "content": "你是专业的周报总结分析专家，擅长从工作记录中提炼重点、趋势、成果与风险，输出适合领导阅读和归档的 Markdown 周报。", 
        },
        {"role": "user", "content": prompt},
    ]
    return WeeklyReportResponse(report=await call_openai(messages, temperature=0.35))


@app.post("/api/weekly-plan", response_model=WeeklyPlanResponse, dependencies=[Depends(require_access)])
async def weekly_plan() -> WeeklyPlanResponse:
    start, end, _, _ = week_range()
    start_s, end_s = start.isoformat(), end.isoformat()
    with db() as conn:
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
              coalesce(nullif(project, ''), '未归类'),
              case priority when '高' then 0 when '中' then 1 else 2 end,
              coalesce(plan_date, '9999-12-31'),
              id
            limit 80
            """,
            (start_s, end_s),
        ).fetchall()
    prompt = f"""
请根据下面的个人任务表数据，生成一份中文本周计划。

本周范围：{start_s} 至 {end_s}

本周计划候选任务：
{row_lines(planned, "暂时没有本周计划候选任务。")}

输出格式必须每次保持一致，严格使用下面结构：

本周计划（{start_s} - {end_s}）

本周目标：
- <用 1-3 句话概括本周整体目标>

按项目安排：

【<项目名或未归类>】
- <任务标题>：<用自然语言说明本周要完成什么、计划日期、优先级、完成标准/备注>
- <任务标题>：<同上>

风险与阻塞：
- <只列任务表中状态为阻塞或备注里明确有风险的信息；没有就写“暂无明确风险”。>

本周执行顺序：
1. <按重要性和紧急性排序，给出 3-7 条>

规则：
1. 必须按 project 归类；project 为空时归到“未归类”。
2. 只能使用任务表已有任务，不要编造新任务、日期、负责人或背景。
3. 高优先级、进行中、阻塞、本周有计划日期的任务优先进入计划。
4. 不要使用 Markdown 表格，不要输出表格分隔线。
5. 内容要像发群里的文字汇总，简洁、自然、可直接复制发送。
"""
    messages = [
        {
            "role": "system",
            "content": "你是中文个人计划管理助理，输出稳定、结构化、按项目归类的周计划。",
        },
        {"role": "user", "content": prompt},
    ]
    return WeeklyPlanResponse(plan=await call_openai(messages, temperature=0.2))


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

任务数量规则：
- 默认只生成 1 个任务。
- 只有当用户明确列出多个事项、多个目标或多个步骤时，才生成多条任务。
- 不要把一句话目标扩写成文档整理、资源确认、信息同步、进度梳理等用户没有明确说出的后续任务。
- 任务标题必须贴近用户原话中的动作和对象。
不要生成与现有任务标题相同或高度相似的任务，重复项放进 skipped。
如果无法判断 ddl 或项目归属，不要编造；对应字段留空或 null，并在 needs_input 中写 plan_date 或 project。
"""
    content = await call_openai(
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
    inferred_project = infer_project_from_prompt(req.prompt)
    inferred_plan_date = infer_plan_date_from_prompt(req.prompt)
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
        plan_date = validate_date(item.get("plan_date")) or inferred_plan_date
        project = str(item.get("project") or "").strip() or inferred_project
        needs_input = [
            str(field)
            for field in item.get("needs_input", [])
            if str(field) in {"plan_date", "project", "priority", "notes"}
        ]
        if plan_date:
            needs_input = [field for field in needs_input if field != "plan_date"]
        if project:
            needs_input = [field for field in needs_input if field != "project"]
        tasks.append(
            GeneratedTask(
                title=title[:250],
                status=status,
                priority=priority,
                plan_date=plan_date,
                project=project,
                tags=str(item.get("tags") or ""),
                notes=str(item.get("notes") or ""),
                needs_input=needs_input,
            )
        )
    if not tasks:
        raise HTTPException(502, "AI 没有生成可用任务")
    if len(tasks) > 1 and not prompt_has_multiple_task_signals(req.prompt):
        tasks = sorted(tasks, key=lambda task: relevance_score(req.prompt, task), reverse=True)[:1]
    skipped = list(dict.fromkeys(skipped))
    return GenerateResponse(summary=str(raw.get("summary") or ""), tasks=tasks[:10], skipped=skipped[:20])
