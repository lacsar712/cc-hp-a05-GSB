import json
import math
import os
from datetime import date, datetime, timedelta, timezone

import psycopg
from fastapi import Depends, FastAPI, HTTPException
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from jose import JWTError, jwt
from passlib.context import CryptContext
from pydantic import BaseModel, Field
from psycopg.rows import dict_row

from rules import judge

SECRET = os.environ.get("JWT_SECRET", "herb-process-dev-secret")
DSN = os.environ.get("DATABASE_URL", "postgresql://app:app@localhost:54393/herb")
pwd = CryptContext(schemes=["bcrypt"], deprecated="auto")
security = HTTPBearer(auto_error=False)
USERS = {
    "processor": {"role": "writer", "password_hash": pwd.hash("herb123456")},
    "checker": {"role": "reader", "password_hash": pwd.hash("check123456")},
}


def connect():
    return psycopg.connect(DSN, row_factory=dict_row)


class LoginIn(BaseModel):
    username: str
    password: str


class StepIn(BaseModel):
    name: str
    temp_c: float
    minutes: float


class BatchIn(BaseModel):
    herb: str = Field(min_length=1, max_length=80)
    steps: list[StepIn]


class QuotaIn(BaseModel):
    points: int = Field(ge=0, le=1_000_000)


def local_today() -> date:
    return datetime.now().astimezone().date()


def energy_cost(doc: dict) -> int:
    """每笔成功写入的能耗：各工序 温度×时长÷10 取整 之和。"""
    total = 0
    for step in doc.get("steps") or []:
        temp = float(step.get("temp_c") or 0)
        minutes = float(step.get("minutes") or 0)
        total += max(math.floor(temp * minutes / 10), 0)
    return total


def ensure_energy_today(conn) -> None:
    """每日零点额度清零：日期翻篇时把额度与已用清零，清零动作写入额度流水。"""
    today = local_today()
    row = conn.execute("SELECT day, points FROM energy_quota WHERE id = 1 FOR UPDATE").fetchone()
    if row["day"] >= today:
        return
    prev = conn.execute("SELECT used FROM energy_usage WHERE day = %s", (row["day"],)).fetchone()
    prev_used = prev["used"] if prev else 0
    now = datetime.now(timezone.utc)
    conn.execute(
        """INSERT INTO energy_ledger (day, kind, points, used_after, quota_after, actor, note, created_at)
           VALUES (%s, 'reset', 0, 0, 0, 'system', %s, %s)""",
        (today, f"每日零点额度清零（{row['day']} 已用 {prev_used} 点，额度 {row['points']} 点）", now),
    )
    conn.execute(
        "UPDATE energy_quota SET day = %s, points = 0, updated_by = 'system', updated_at = %s WHERE id = 1",
        (today, now),
    )


def energy_state(conn) -> dict:
    ensure_energy_today(conn)
    today = local_today()
    quota = conn.execute("SELECT points FROM energy_quota WHERE id = 1").fetchone()["points"]
    row = conn.execute("SELECT used FROM energy_usage WHERE day = %s", (today,)).fetchone()
    used = row["used"] if row else 0
    return {"day": today.isoformat(), "quota": quota, "used": used, "remaining": max(quota - used, 0)}


def current_user(credentials: HTTPAuthorizationCredentials | None = Depends(security)) -> dict:
    if credentials is None:
        raise HTTPException(status_code=401, detail="未登录")
    try:
        payload = jwt.decode(credentials.credentials, SECRET, algorithms=["HS256"])
    except JWTError as exc:
        raise HTTPException(status_code=401, detail="无效令牌") from exc
    if payload.get("sub") not in USERS:
        raise HTTPException(status_code=401, detail="无效令牌")
    return {"username": payload["sub"], "role": payload.get("role")}


def require_writer(user: dict = Depends(current_user)) -> dict:
    if user["role"] != "writer":
        raise HTTPException(status_code=403, detail="仅炮制员可写入记录")
    return user


app = FastAPI(title="饮片炮制记录台")


@app.on_event("startup")
def startup():
    with connect() as conn:
        conn.execute(
            """CREATE TABLE IF NOT EXISTS batches (
                id serial PRIMARY KEY,
                herb text NOT NULL,
                doc jsonb NOT NULL,
                verdict text NOT NULL,
                reason text NOT NULL,
                created_by text NOT NULL,
                created_at timestamptz NOT NULL
            )"""
        )
        conn.execute(
            """CREATE TABLE IF NOT EXISTS energy_quota (
                id int PRIMARY KEY,
                day date NOT NULL,
                points int NOT NULL,
                updated_by text NOT NULL,
                updated_at timestamptz NOT NULL
            )"""
        )
        conn.execute(
            """CREATE TABLE IF NOT EXISTS energy_usage (
                day date PRIMARY KEY,
                used int NOT NULL
            )"""
        )
        conn.execute(
            """CREATE TABLE IF NOT EXISTS energy_ledger (
                id serial PRIMARY KEY,
                day date NOT NULL,
                kind text NOT NULL,
                points int NOT NULL,
                used_after int NOT NULL,
                quota_after int NOT NULL,
                actor text NOT NULL,
                note text NOT NULL,
                created_at timestamptz NOT NULL
            )"""
        )
        conn.execute(
            """INSERT INTO energy_quota (id, day, points, updated_by, updated_at)
               VALUES (1, %s, 0, 'system', %s)
               ON CONFLICT (id) DO NOTHING""",
            (local_today(), datetime.now(timezone.utc)),
        )
        count = conn.execute("SELECT COUNT(*) AS n FROM batches").fetchone()["n"]
        if count == 0:
            now = datetime.now(timezone.utc)
            samples = [
                ("甘草", {"steps": [{"name": "清炒", "temp_c": 120, "minutes": 12}]}),
                ("黄芩", {"steps": [{"name": "清炒", "temp_c": 40, "minutes": 12}]}),
            ]
            for herb, doc in samples:
                verdict, reason = judge(doc)
                conn.execute(
                    """INSERT INTO batches (herb, doc, verdict, reason, created_by, created_at)
                       VALUES (%s, %s::jsonb, %s, %s, %s, %s)""",
                    (herb, json.dumps(doc, ensure_ascii=False), verdict, reason, "processor", now),
                )
        conn.commit()


@app.get("/api/health")
def health():
    return {"status": "ok", "service": "herb-process-record"}


@app.post("/api/auth/login")
def login(body: LoginIn):
    user = USERS.get(body.username.strip())
    if not user or not pwd.verify(body.password, user["password_hash"]):
        raise HTTPException(status_code=401, detail="用户名或密码错误")
    exp = datetime.now(timezone.utc) + timedelta(hours=8)
    token = jwt.encode({"sub": body.username.strip(), "role": user["role"], "exp": exp}, SECRET, algorithm="HS256")
    return {"access_token": token, "username": body.username.strip(), "role": user["role"]}


@app.get("/api/batches")
def list_batches(_user: dict = Depends(current_user)):
    with connect() as conn:
        rows = conn.execute("SELECT id, herb, doc, verdict, reason, created_by FROM batches ORDER BY id DESC").fetchall()
    return rows


@app.post("/api/batches", status_code=201)
def create_batch(body: BatchIn, user: dict = Depends(require_writer)):
    doc = {"steps": [s.model_dump() for s in body.steps]}
    verdict, reason = judge(doc)
    cost = energy_cost(doc)
    now = datetime.now(timezone.utc)
    with connect() as conn:
        state = energy_state(conn)
        if cost > state["remaining"]:
            raise HTTPException(
                status_code=409,
                detail=f"能耗超额，拒绝写入：本日已用 {state['used']} 点，本日额度 {state['quota']} 点，本次需 {cost} 点",
            )
        row = conn.execute(
            """INSERT INTO batches (herb, doc, verdict, reason, created_by, created_at)
               VALUES (%s, %s::jsonb, %s, %s, %s, %s)
               RETURNING id, herb, doc, verdict, reason, created_by""",
            (body.herb.strip(), json.dumps(doc, ensure_ascii=False), verdict, reason, user["username"], now),
        ).fetchone()
        today = local_today()
        conn.execute(
            """INSERT INTO energy_usage (day, used) VALUES (%s, %s)
               ON CONFLICT (day) DO UPDATE SET used = energy_usage.used + EXCLUDED.used""",
            (today, cost),
        )
        conn.execute(
            """INSERT INTO energy_ledger (day, kind, points, used_after, quota_after, actor, note, created_at)
               VALUES (%s, 'consume', %s, %s, %s, %s, %s, %s)""",
            (today, cost, state["used"] + cost, state["quota"], user["username"],
             f"开炒《{body.herb.strip()}》消耗 {cost} 点", now),
        )
        conn.commit()
    return row


@app.get("/api/energy/quota")
def get_quota(_user: dict = Depends(current_user)):
    with connect() as conn:
        state = energy_state(conn)
        conn.commit()
    return state


@app.put("/api/energy/quota")
def set_quota(body: QuotaIn, user: dict = Depends(require_writer)):
    now = datetime.now(timezone.utc)
    with connect() as conn:
        state = energy_state(conn)
        today = local_today()
        conn.execute(
            "UPDATE energy_quota SET points = %s, updated_by = %s, updated_at = %s WHERE id = 1",
            (body.points, user["username"], now),
        )
        conn.execute(
            """INSERT INTO energy_ledger (day, kind, points, used_after, quota_after, actor, note, created_at)
               VALUES (%s, 'set', %s, %s, %s, %s, %s, %s)""",
            (today, body.points, state["used"], body.points, user["username"],
             f"设定本日能耗额度为 {body.points} 点", now),
        )
        conn.commit()
    return {"day": today.isoformat(), "quota": body.points, "used": state["used"],
            "remaining": max(body.points - state["used"], 0)}


@app.get("/api/energy/ledger")
def get_ledger(_user: dict = Depends(current_user)):
    with connect() as conn:
        energy_state(conn)
        rows = conn.execute(
            """SELECT id, day, kind, points, used_after, quota_after, actor, note, created_at
               FROM energy_ledger ORDER BY id DESC LIMIT 100"""
        ).fetchall()
        conn.commit()
    return rows
