from __future__ import annotations

import asyncio
import json
import os
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from fastapi import FastAPI, Header, HTTPException


OUTPUT_PATH = Path(os.getenv("MC_OUTPUT", "outputs/mediacrawler/latest-hotspots.json"))
RUN_TRIGGER_TOKEN = os.getenv("RUN_TRIGGER_TOKEN", "")

app = FastAPI(title="Xiaod MediaCrawler Executor")
run_lock = asyncio.Lock()
last_run: dict[str, Any] = {
    "status": "never_run",
    "started_at": None,
    "finished_at": None,
    "returncode": None,
    "stdout_tail": "",
    "stderr_tail": "",
}


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


def authorize(x_run_token: str | None) -> None:
    if RUN_TRIGGER_TOKEN and x_run_token != RUN_TRIGGER_TOKEN:
        raise HTTPException(status_code=401, detail="invalid trigger token")


async def run_exporter() -> None:
    global last_run
    async with run_lock:
        started_at = now_iso()
        timeout_seconds = int(os.getenv("RUN_TIMEOUT_SECONDS", "360"))
        last_run = {
            "status": "running",
            "started_at": started_at,
            "finished_at": None,
            "returncode": None,
            "stdout_tail": "",
            "stderr_tail": "",
        }
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
                "started_at": started_at,
                "finished_at": now_iso(),
                "returncode": None,
                "stdout_tail": tail(stdout_text),
                "stderr_tail": tail(stderr_text) or f"run exceeded {timeout_seconds}s",
            }
            return
        stdout_text = stdout.decode("utf-8", errors="replace")
        stderr_text = stderr.decode("utf-8", errors="replace")
        last_run = {
            "status": "success" if process.returncode == 0 else "failed",
            "started_at": started_at,
            "finished_at": now_iso(),
            "returncode": process.returncode,
            "stdout_tail": tail(stdout_text),
            "stderr_tail": tail(stderr_text),
        }


@app.get("/healthz")
async def healthz() -> dict[str, str]:
    return {"status": "ok"}


@app.get("/status")
async def status() -> dict[str, Any]:
    return {"executor": last_run, "latest": read_latest_payload()}


@app.post("/run")
async def run_now(x_run_token: str | None = Header(default=None)) -> dict[str, Any]:
    authorize(x_run_token)
    if run_lock.locked():
        return {"status": "already_running", "executor": last_run}
    asyncio.create_task(run_exporter())
    return {"status": "queued", "started_at": now_iso()}
