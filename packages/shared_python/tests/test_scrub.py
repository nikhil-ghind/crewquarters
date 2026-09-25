"""Strict redaction for diagnostics bundles (crewquarters_shared.redaction.scrub_text)."""

from __future__ import annotations

import pytest

from crewquarters_shared.redaction import scrub, scrub_text

pytestmark = pytest.mark.no_db


@pytest.mark.parametrize(
    ("raw", "secret"),
    [
        ("Cookie: cq_session=abc123def; cq_csrf=xyz", "abc123def"),
        ("Set-Cookie: cq_session=abc123def; HttpOnly", "abc123def"),
        ('{"refreshToken": "1//0gabcdefghijklmnopqrstu"}', "0gabcdefghij"),
        ("GET /cb?code=4/0AbCdEfGh&state=xyz123", "0AbCdEfGh"),
        ("GET /cb?code=4/0AbCdEfGh&state=xyz123", "xyz123"),
        ("postgresql+psycopg://crewquarters:hunter2@postgres:5432/db", "hunter2"),
        ("key sk-ant-api03-abcdefghijklmnop", "abcdefghijklmnop"),
        ("key sk-proj-abcdefghijklmnop", "abcdefghijklmnop"),
        ("call +14155550123", "4155550123"),
        ("call (415) 555-0199", "555-0199"),
        ("mail bob.smith@example.com", "bob.smith"),
        ("AC" + "0123456789abcdef" * 2, "0123456789abcdef0123"),
        ("keyring 1:" + "ab" * 32, "ab" * 32),
        ("Authorization: Bearer abc.def.ghi", "abc.def.ghi"),
        ("Authorization: Basic dXNlcjpwYXNz", "dXNlcjpwYXNz"),
        ("password=pa55word other=1", "pa55word"),
        ("x-api-key: live-key-123", "live-key-123"),
        ("token Q9xV_3kLmN8pR2sT5uW7yZ0aB4cD6eF1gH", "Q9xV_3kLmN8pR2sT5uW7yZ0aB4cD6eF1gH"),
        ("jwt eyJhbGciOiJIUzI1NiJ9.eyJzdWIiOiJ4In0.sig12345", "eyJzdWIiOiJ4In0"),
        ("google ya29.a0AfH6SMBxyz", "a0AfH6SMBxyz"),
    ],
)
def test_secrets_are_removed(raw: str, secret: str) -> None:
    assert secret not in scrub_text(raw)


@pytest.mark.parametrize(
    "keep",
    [
        "run 0192f5c2-7a3b-7c3d-9e4f-0123456789ab failed",
        "at 2026-09-25T10:00:00.123456+00:00 took 12.5ms",
        "logger crewquarters_scheduler.worker",
        "GET /api/v1/runs/{run_id} 200",
        "state=RUNNING",
    ],
)
def test_ordinary_text_is_kept(keep: str) -> None:
    assert scrub_text(keep) == keep


def test_scrub_structures() -> None:
    data = {"apiKey": "x", "nested": [{"note": "mail a@b.co"}], "count": 3}
    assert scrub(data) == {"apiKey": "[REDACTED]", "nested": [{"note": "mail [EMAIL]"}], "count": 3}
