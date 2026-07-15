from __future__ import annotations

import asyncio
import hashlib
import json
import os
import secrets
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from uuid import uuid4

from fastapi import FastAPI, Header, HTTPException


OUTPUT_PATH = Path(os.getenv("MC_OUTPUT", "outputs/mediacrawler/latest-hotspots.json"))
STATE_PATH = Path(os.getenv("MC_EXECUTOR_STATE", "data/executor-state.json"))
RUN_TRIGGER_TOKEN = os.getenv("RUN_TRIGGER_TOKEN", "")
GITHUB_TOKEN = os.getenv("GITHUB_TOKEN", "")
TRIGGER_TOKEN_CONTEXT = b"xiaod-mediacrawler-run:v1\0"

app = FastAPI(title="Xiaod MediaCrawler Executor")
run_lock = asyncio.Lock()


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def tail(value: str, limit: int = 4000) -> str:
    return value[-limit:] if len(value) > limit else value


def read_latest_payload() -> dict[str, Any] | None:
    if not OUTPUT_PATH.exists():
        return None
    try:
        return json.loads(OUTPUT_PATH.read_text(encoding="utf-8"))
    except Exception as exc:  # pragma: no cover - status endpoint should stay alive
        return {"status": "unreadable_output", "error": str(exc)}


def read_state() -> dict[str, Any] | None:
    if not STATE_PATH.exists():
        return None
    try:
        value = json.loads(STATE_PATH.read_text(encoding="utf-8"))
        return value if isinstance(value, dict) else None
    except Exception:
        return None


def write_state(value: dict[str, Any]) -> None:
    STATE_PATH.parent.mkdir(parents=True, exist_ok=True)
    tmp = STATE_PATH.with_suffix(f"{STATE_PATH.suffix}.tmp")
    tmp.write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    tmp.replace(STATE_PATH)


def initial_state() -> dict[str, Any]:
    persisted = read_state()
    if persisted:
        return persisted
    latest = read_latest_payload()
    if latest and latest.get("finished_at"):
        return {
            "status": "restored_from_output",
            "run_id": latest.get("run_id"),
            "started_at": latest.get("started_at"),
            "finished_at": latest.get("finished_at"),
            "returncode": None,
            "stdout_tail": "",
            "stderr_tail": "",
            "output_status": latest.get("status"),
            "item_count": len(latest.get("items") or []),
            "error_count": len(latest.get("errors") or []),
        }
    return {
        "status": "never_run",
        "run_id": None,
        "started_at": None,
        "finished_at": None,
        "returncode": None,
        "stdout_tail": "",
        "stderr_tail": "",
        "output_status": None,
        "item_count": 0,
        "error_count": 0,
    }


last_run: dict[str, Any] = initial_state()


def effective_run_token() -> str:
    if RUN_TRIGGER_TOKEN:
        return RUN_TRIGGER_TOKEN
    if GITHUB_TOKEN:
        return hashlib.sha256(TRIGGER_TOKEN_CONTEXT + GITHUB_TOKEN.encode("utf-8")).hexdigest()
    return ""


def trigger_auth_mode() -> str:
    if RUN_TRIGGER_TOKEN:
        return "explicit"
    if GITHUB_TOKEN:
        return "derived"
    return "missing"


def authorize(x_run_token: str | None) -> None:
    expected = effective_run_token()
    if not expected:
        raise HTTPException(status_code=503, detail="run trigger token is not configured")
    if not x_run_token or not secrets.compare_digest(x_run_token, expected):
        raise HTTPException(status_code=401, detail="invalid trigger token")


async def run_exporter() -> None:
    global last_run
    async with run_lock:
        started_at = now_iso()
        run_id = str(uuid4())
        timeout_seconds = int(os.getenv("RUN_TIMEOUT_SECONDS", "360"))
        last_run = {
            "status": "running",
            "run_id": run_id,
            "started_at": started_at,
            "finished_at": None,
            "returncode": None,
            "stdout_tail": "",
            "stderr_tail": "",
        }
        write_state(last_run)
        process = await asyncio.create_subprocess_exec(
            sys.executable,
            "run_once_exporter.py",
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        try:
            stdout, stderr = await asyncio.wait_for(process.communicate(), timeout=timeout_seconds)
        except asyncio.TimeoutError:
            process.kill()
            stdout, stderr = await process.communicate()
            stdout_text = stdout.decode("utf-8", errors="replace")
            stderr_text = stderr.decode("utf-8", errors="replace")
            last_run = {
                "status": "failed_timeout",
                "run_id": run_id,
                "started_at": started_at,
                "finished_at": now_iso(),
                "returncode": None,
                "stdout_tail": tail(stdout_text),
                "stderr_tail": tail(stderr_text) or f"run exceeded {timeout_seconds}s",
            }
            write_state(last_run)
            return
        stdout_text = stdout.decode("utf-8", errors="replace")
        stderr_text = stderr.decode("utf-8", errors="replace")
        latest = read_latest_payload() or {}
        last_run = {
            "status": "success" if process.returncode == 0 else "failed",
            "run_id": latest.get("run_id") or run_id,
            "started_at": started_at,
            "finished_at": now_iso(),
            "returncode": process.returncode,
            "stdout_tail": tail(stdout_text),
            "stderr_tail": tail(stderr_text),
            "output_status": latest.get("status"),
            "item_count": len(latest.get("items") or []),
            "error_count": len(latest.get("errors") or []),
        }
        write_state(last_run)


@app.get("/healthz")
async def healthz() -> dict[str, str]:
    return {
        "status": "ok",
        "trigger_auth": "configured" if effective_run_token() else "missing",
        "trigger_auth_mode": trigger_auth_mode(),
    }


@app.get("/status")
async def status() -> dict[str, Any]:
    return {
        "executor": last_run,
        "latest": read_latest_payload(),
        "trigger_auth": "configured" if effective_run_token() else "missing",
        "trigger_auth_mode": trigger_auth_mode(),
    }


@app.post("/run")
async def run_now(x_run_token: str | None = Header(default=None)) -> dict[str, Any]:
    authorize(x_run_token)
    if run_lock.locked():
        return {"status": "already_running", "executor": last_run}
    asyncio.create_task(run_exporter())
    return {"status": "queued", "started_at": now_iso()}
