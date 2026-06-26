from __future__ import annotations

import asyncio
import base64
import json
import os
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import config
import main as crawler_main


DEFAULT_PLATFORMS = ("bili", "tieba", "zhihu")
DEFAULT_KEYWORDS = "电视剧,综艺,短剧,热播,定档,开播"
OUTPUT_PATH = Path(os.getenv("MC_OUTPUT", "outputs/mediacrawler/latest-hotspots.json"))
SAVE_DATA_PATH = Path(os.getenv("MC_SAVE_DATA_PATH", "data/railway-run"))
OUTPUT_REPO = os.getenv("OUTPUT_REPO", "")
OUTPUT_BRANCH = os.getenv("OUTPUT_BRANCH", "main")
GITHUB_TOKEN = os.getenv("GITHUB_TOKEN", "")
GITHUB_OUTPUT_PATH = os.getenv("OUTPUT_PATH", "outputs/mediacrawler/latest-hotspots.json")


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def env_bool(name: str, default: bool = False) -> bool:
    value = os.getenv(name)
    if value is None:
        return default
    return value.strip().lower() in {"1", "true", "yes", "y"}


def platforms() -> list[str]:
    raw = os.getenv("MC_PLATFORMS", ",".join(DEFAULT_PLATFORMS))
    allowed = {"bili", "tieba", "zhihu"}
    return [item.strip() for item in raw.split(",") if item.strip() in allowed]


def patch_runtime_config() -> None:
    config.ENABLE_CDP_MODE = False
    config.CDP_HEADLESS = True
    config.HEADLESS = True
    config.SAVE_LOGIN_STATE = False
    config.SAVE_DATA_OPTION = "jsonl"
    config.SAVE_DATA_PATH = str(SAVE_DATA_PATH)
    config.ENABLE_GET_COMMENTS = False
    config.ENABLE_GET_SUB_COMMENTS = False
    config.ENABLE_GET_MEIDAS = False
    config.ENABLE_GET_WORDCLOUD = False
    config.MAX_CONCURRENCY_NUM = int(os.getenv("MC_MAX_CONCURRENCY", "1"))
    config.CRAWLER_MAX_NOTES_COUNT = int(os.getenv("MC_MAX_NOTES", "20"))
    os.environ.setdefault("MEDIACRAWLER_USE_BUNDLED_CHROMIUM", "1")


async def run_platform(platform: str) -> dict[str, Any]:
    patch_runtime_config()
    timeout_seconds = int(os.getenv("MC_PLATFORM_TIMEOUT_SECONDS", "90"))
    args = [
        "main.py",
        "--platform",
        platform,
        "--lt",
        "cookie",
        "--type",
        "search",
        "--keywords",
        os.getenv("MC_KEYWORDS", DEFAULT_KEYWORDS),
        "--get_comment",
        "false",
        "--get_sub_comment",
        "false",
        "--headless",
        "true",
        "--save_data_option",
        "jsonl",
        "--save_data_path",
        str(SAVE_DATA_PATH),
        "--crawler_max_notes_count",
        os.getenv("MC_MAX_NOTES", "20"),
        "--max_concurrency_num",
        os.getenv("MC_MAX_CONCURRENCY", "1"),
    ]
    cookie = os.getenv(f"{platform.upper()}_COOKIES", "")
    if cookie:
        args.extend(["--cookies", cookie])
    previous_argv = sys.argv[:]
    started = time.monotonic()
    try:
        sys.argv = args
        await asyncio.wait_for(crawler_main.main(), timeout=timeout_seconds)
        return {
            "platform": platform,
            "status": "success",
            "duration_ms": int((time.monotonic() - started) * 1000),
        }
    except asyncio.TimeoutError:
        return {
            "platform": platform,
            "status": "failed_timeout",
            "message": f"platform run exceeded {timeout_seconds}s",
            "duration_ms": int((time.monotonic() - started) * 1000),
        }
    except Exception as exc:
        return {
            "platform": platform,
            "status": classify_error(exc),
            "message": str(exc),
            "duration_ms": int((time.monotonic() - started) * 1000),
        }
    finally:
        sys.argv = previous_argv
        try:
            await crawler_main.async_cleanup()
        except Exception:
            pass


def classify_error(exc: Exception) -> str:
    text = str(exc).lower()
    if "login" in text or "cookie" in text or "captcha" in text or "verify" in text:
        return "needs_login"
    if "403" in text or "forbidden" in text or "blocked" in text or "risk" in text:
        return "blocked"
    if "timeout" in text:
        return "failed_timeout"
    return "failed"


def read_jsonl_items() -> list[dict[str, Any]]:
    items: list[dict[str, Any]] = []
    for child in sorted(SAVE_DATA_PATH.glob("*/jsonl/search_contents_*.jsonl")):
        platform = child.parts[-3]
        for line in child.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if not line:
                continue
            try:
                raw = json.loads(line)
            except json.JSONDecodeError:
                continue
            normalized = normalize_item(raw, platform)
            if normalized["title"] and normalized["url"]:
                items.append(normalized)
    return items


def first(raw: dict[str, Any], *keys: str, default: Any = "") -> Any:
    for key in keys:
        value = raw.get(key)
        if value not in (None, ""):
            return value
    return default


def normalize_item(raw: dict[str, Any], platform: str) -> dict[str, Any]:
    title = str(first(raw, "title", "display_title", "desc", "content", "keyword")).strip()
    summary = str(first(raw, "summary", "desc", "content", "text", "note_desc")).strip()
    url = str(first(raw, "url", "note_url", "video_url", "link", "share_url", "web_url")).strip()
    if not url and raw.get("video_id"):
        url = f"https://www.bilibili.com/video/{raw['video_id']}"
    if not title and summary:
        title = summary[:80]
    return {
        "source": f"MediaCrawler-{platform}",
        "source_role": "external_enrichment",
        "platform": platform,
        "title": title,
        "url": url,
        "summary": summary,
        "published_at": first(raw, "published_at", "pub_time", "publish_time", "created_at", "create_time", default=now_iso()),
        "hot_score": float(first(raw, "hot_score", "score", "heat", "rank_score", default=0) or 0),
        "category": "hotspot",
        "raw_payload": {
            "source_role": "external_enrichment",
            "source_kind": "railway_mediacrawler",
            "platform": platform,
            "raw": raw,
        },
    }


async def write_output(payload: dict[str, Any]) -> None:
    OUTPUT_PATH.parent.mkdir(parents=True, exist_ok=True)
    tmp = OUTPUT_PATH.with_suffix(f"{OUTPUT_PATH.suffix}.tmp")
    tmp.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    tmp.replace(OUTPUT_PATH)
    if GITHUB_TOKEN and OUTPUT_REPO:
        publish = await publish_to_github(payload)
        payload["publish"] = publish
        tmp.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        tmp.replace(OUTPUT_PATH)


def github_request(api_path: str, method: str = "GET", body: dict[str, Any] | None = None) -> Any:
    request = urllib.request.Request(
        f"https://api.github.com{api_path}",
        method=method,
        headers={
            "Accept": "application/vnd.github+json",
            "Authorization": f"Bearer {GITHUB_TOKEN}",
            "X-GitHub-Api-Version": "2022-11-28",
            "Content-Type": "application/json",
        },
        data=json.dumps(body).encode("utf-8") if body is not None else None,
    )
    try:
        with urllib.request.urlopen(request, timeout=30) as response:
            return json.loads(response.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:
        if exc.code == 404:
            return None
        raise


async def publish_to_github(payload: dict[str, Any]) -> dict[str, Any]:
    content_path = GITHUB_OUTPUT_PATH.lstrip("/")
    api_path = f"/repos/{OUTPUT_REPO}/contents/{urllib.parse.quote(content_path)}".replace("%2F", "/")
    existing = github_request(f"{api_path}?ref={OUTPUT_BRANCH}")
    body = {
        "message": f"Update MediaCrawler hotspots {payload['finished_at']}",
        "branch": OUTPUT_BRANCH,
        "content": base64.b64encode((json.dumps(payload, ensure_ascii=False, indent=2) + "\n").encode("utf-8")).decode("ascii"),
    }
    if existing and existing.get("sha"):
        body["sha"] = existing["sha"]
    github_request(api_path, method="PUT", body=body)
    return {"status": "success", "repo": OUTPUT_REPO, "path": content_path, "branch": OUTPUT_BRANCH}


async def main() -> None:
    selected_platforms = platforms()
    results = []
    for platform in selected_platforms:
        results.append(await run_platform(platform))
    items = read_jsonl_items()
    failures = [result for result in results if result["status"] != "success"]
    status = "success" if items and not failures else "partial_failed" if items else "failed"
    payload = {
        "status": status,
        "source": "Railway-MediaCrawler",
        "source_role": "external_enrichment",
        "finished_at": now_iso(),
        "platforms": selected_platforms,
        "items": items,
        "errors": failures,
        "runs": results,
    }
    if not items and not failures:
        payload["status"] = "empty"
    await write_output(payload)
    print(json.dumps({"status": payload["status"], "items": len(items), "errors": len(failures), "output_path": str(OUTPUT_PATH)}, ensure_ascii=False))
    if not items:
        sys.exit(1)


if __name__ == "__main__":
    asyncio.run(main())
