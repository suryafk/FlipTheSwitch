import json
import logging

import pytest
from pydantic import ValidationError

from app.core.config import Settings
from app.core.logging import JsonFormatter, request_id_var


def test_settings_read_environment(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("BLACKBOX_ENV", "production")
    monkeypatch.setenv("BLACKBOX_LOG_LEVEL", "WARNING")
    monkeypatch.setenv("BLACKBOX_CORS_ORIGINS", '["http://example.test"]')
    s = Settings()
    assert s.app_env == "production"
    assert s.log_level == "WARNING"
    assert s.cors_origins == ["http://example.test"]


def test_invalid_setting_fails_loudly(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("BLACKBOX_ENV", "staging-ish")
    with pytest.raises(ValidationError):
        Settings()


def test_json_formatter_includes_request_id_and_extras() -> None:
    record = logging.LogRecord("app.test", logging.INFO, __file__, 1, "hello %s", ("world",), None)
    record.incident_id = "INC-1"
    token = request_id_var.set("req-1")
    try:
        line = JsonFormatter().format(record)
    finally:
        request_id_var.reset(token)
    data = json.loads(line)
    assert data["message"] == "hello world"
    assert data["level"] == "INFO"
    assert data["request_id"] == "req-1"
    assert data["incident_id"] == "INC-1"
