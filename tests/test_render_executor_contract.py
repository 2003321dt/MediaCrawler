from __future__ import annotations

import json
from datetime import datetime, timezone

import pytest
from fastapi import HTTPException

import executor_contract
import render_executor


def test_initial_state_restores_latest_output_when_state_file_is_missing(tmp_path, monkeypatch):
    output = tmp_path / "latest-hotspots.json"
    output.write_text(
        json.dumps(
            {
                "status": "partial_failed",
                "run_id": "run-1",
                "started_at": "2026-07-15T00:00:00+00:00",
                "finished_at": "2026-07-15T00:05:00+00:00",
                "items": [{"title": "one"}],
                "errors": [{"platform": "bili"}],
            }
        ),
        encoding="utf-8",
    )
    monkeypatch.setattr(render_executor, "STATE_PATH", tmp_path / "missing-state.json")
    monkeypatch.setattr(render_executor, "OUTPUT_PATH", output)

    restored = render_executor.initial_state()

    assert restored["status"] == "restored_from_output"
    assert restored["output_status"] == "partial_failed"
    assert restored["item_count"] == 1
    assert restored["error_count"] == 1


def test_state_round_trip(tmp_path, monkeypatch):
    state_path = tmp_path / "executor-state.json"
    monkeypatch.setattr(render_executor, "STATE_PATH", state_path)
    value = {"status": "success", "run_id": "run-2"}

    render_executor.write_state(value)

    assert render_executor.read_state() == value


def test_run_requires_configured_and_matching_token(monkeypatch):
    monkeypatch.setattr(render_executor, "RUN_TRIGGER_TOKEN", "")
    monkeypatch.setattr(render_executor, "GITHUB_TOKEN", "")
    with pytest.raises(HTTPException) as missing:
        render_executor.authorize(None)
    assert missing.value.status_code == 503

    monkeypatch.setattr(render_executor, "RUN_TRIGGER_TOKEN", "secret-value")
    with pytest.raises(HTTPException) as invalid:
        render_executor.authorize("wrong")
    assert invalid.value.status_code == 401
    render_executor.authorize("secret-value")


def test_run_token_can_be_derived_without_exposing_github_token(monkeypatch):
    monkeypatch.setattr(render_executor, "RUN_TRIGGER_TOKEN", "")
    monkeypatch.setattr(render_executor, "GITHUB_TOKEN", "github-secret")

    derived = render_executor.effective_run_token()

    assert derived != "github-secret"
    assert len(derived) == 64
    assert render_executor.trigger_auth_mode() == "derived"
    render_executor.authorize(derived)


def test_freshness_filter_keeps_only_valid_last_24_hours():
    now = datetime(2026, 7, 15, 12, 0, tzinfo=timezone.utc)
    items = [
        {"title": "fresh", "published_at": "2026-07-15 18:30:00"},
        {"title": "old", "published_at": "2026-07-13T00:00:00+00:00"},
        {"title": "missing", "published_at": ""},
        {"title": "future", "published_at": "2026-07-16T00:00:00+00:00"},
    ]

    kept, stats = executor_contract.filter_recent_items(items, now=now, window_hours=24)

    assert [item["title"] for item in kept] == ["fresh"]
    assert stats == {"input": 4, "kept": 1, "dropped_old": 1, "dropped_invalid_time": 2}


def test_freshness_parser_accepts_epoch_milliseconds():
    parsed = executor_contract.parse_published_at(1784116800000)
    assert parsed == datetime(2026, 7, 15, 12, 0, tzinfo=timezone.utc)
