import json
import os
from datetime import datetime, timedelta, timezone

import psycopg
from fastapi import Depends, FastAPI, HTTPException
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from jose import JWTError, jwt
from passlib.context import CryptContext
from pydantic import BaseModel, Field
from psycopg.rows import dict_row

from rules import energy_cost, judge

SECRET = os.environ.get("JWT_SECRET", "herb-process-dev-secret")
DSN = os.environ.get("DATABASE_URL", "postgresql://app:app@localhost:54393/herb")
# 额度日界按北京时间零点计算
CN_TZ = timezone(timedelta(hours=8))
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
    daily_limit: int = Field(ge=0, le=1_000_000)


DEFAULT_QUOTA = 0


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


def roll_quota_day(conn) -> dict:
    """取本日额度行；跨天则把上日已用清零并写一条 reset 流水。须在事务内调用。"""
    today = datetime.now(CN_TZ).date()
    row = conn.execute("SELECT * FROM energy_quota WHERE id = 1").fetchone()
    if row is None:
        row = conn.execute(
            "INSERT INTO energy_quota (id, daily_limit, used, quota_date) VALUES (1, %s, 0, %s) RETURNING *",
            (DEFAULT_QUOTA, today),
        ).fetchone()
        conn.execute(
            """INSERT INTO quota_ledger (change_type, old_limit, new_limit, used_before, used_after,
                                        quota_date, operator)
               VALUES ('init', NULL, %s, 0, 0, %s, 'system')""",
            (DEFAULT_QUOTA, today),
        )
        return row
    if row["quota_date"] != today:
        conn.execute(
            """INSERT INTO quota_ledger (change_type, old_limit, new_limit, used_before, used_after,
                                        quota_date, operator)
               VALUES ('reset', %s, 0, %s, 0, %s, 'system')""",
            (row["daily_limit"], row["used"], today),
        )
        row = conn.execute(
            "UPDATE energy_quota SET daily_limit = 0, used = 0, quota_date = %s WHERE id = 1 RETURNING *",
            (today,),
        ).fetchone()
    return row


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
                id integer PRIMARY KEY CHECK (id = 1),
                daily_limit integer NOT NULL,
                used integer NOT NULL,
                quota_date date NOT NULL
            )"""
        )
        conn.execute(
            """CREATE TABLE IF NOT EXISTS quota_ledger (
                id serial PRIMARY KEY,
                change_type text NOT NULL,
                old_limit integer,
                new_limit integer,
                used_before integer NOT NULL,
                used_after integer NOT NULL,
                quota_date date NOT NULL,
                operator text NOT NULL,
                created_at timestamptz NOT NULL DEFAULT now()
            )"""
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
    with connect() as conn:
        quota = roll_quota_day(conn)
        if quota["used"] + cost > quota["daily_limit"]:
            raise HTTPException(
                status_code=409,
                detail=(
                    f"能耗额度不足：本笔需 {cost} 点，"
                    f"本日已用 {quota['used']} 点，本日额度 {quota['daily_limit']} 点"
                ),
            )
        row = conn.execute(
            """INSERT INTO batches (herb, doc, verdict, reason, created_by, created_at)
               VALUES (%s, %s::jsonb, %s, %s, %s, %s)
               RETURNING id, herb, doc, verdict, reason, created_by""",
            (body.herb.strip(), json.dumps(doc, ensure_ascii=False), verdict, reason, user["username"], datetime.now(timezone.utc)),
        ).fetchone()
        conn.execute("UPDATE energy_quota SET used = used + %s WHERE id = 1", (cost,))
        conn.execute(
            """INSERT INTO quota_ledger (change_type, old_limit, new_limit, used_before, used_after,
                                        quota_date, operator)
               VALUES ('consume', NULL, NULL, %s, %s, %s, %s)""",
            (quota["used"], quota["used"] + cost, quota["quota_date"], user["username"]),
        )
        conn.commit()
    return row


@app.get("/api/quota")
def get_quota(_user: dict = Depends(current_user)):
    with connect() as conn:
        quota = roll_quota_day(conn)
        conn.commit()
    return {
        "quota_date": quota["quota_date"],
        "daily_limit": quota["daily_limit"],
        "used": quota["used"],
        "remaining": quota["daily_limit"] - quota["used"],
    }


@app.put("/api/quota")
def set_quota(body: QuotaIn, user: dict = Depends(require_writer)):
    with connect() as conn:
        quota = roll_quota_day(conn)
        conn.execute("UPDATE energy_quota SET daily_limit = %s WHERE id = 1", (body.daily_limit,))
        conn.execute(
            """INSERT INTO quota_ledger (change_type, old_limit, new_limit, used_before, used_after,
                                        quota_date, operator)
               VALUES ('set_limit', %s, %s, %s, %s, %s, %s)""",
            (quota["daily_limit"], body.daily_limit, quota["used"], quota["used"],
             quota["quota_date"], user["username"]),
        )
        conn.commit()
    return {
        "quota_date": quota["quota_date"],
        "daily_limit": body.daily_limit,
        "used": quota["used"],
        "remaining": body.daily_limit - quota["used"],
    }


@app.get("/api/quota/ledger")
def get_quota_ledger(_user: dict = Depends(current_user)):
    with connect() as conn:
        roll_quota_day(conn)
        conn.commit()
        rows = conn.execute(
            """SELECT id, change_type, old_limit, new_limit, used_before, used_after,
                      quota_date, operator, created_at
               FROM quota_ledger ORDER BY id DESC LIMIT 200"""
        ).fetchall()
    return rows
