from __future__ import annotations

import json

import pytest

from hnids.errors import ConfigError, ErrorCode, HnidsError, PathSecurityError
from hnids.logging import configure_logging, get_logger


def test_error_to_dict_is_machine_readable() -> None:
    err = PathSecurityError("nope", details={"path": "/x"})
    assert err.to_dict() == {
        "code": "PATH_OUTSIDE_ROOT",
        "message": "nope",
        "details": {"path": "/x"},
    }
    assert str(err) == "[PATH_OUTSIDE_ROOT] nope"


def test_explicit_code_overrides_default() -> None:
    err = ConfigError("missing", code=ErrorCode.CONFIG_NOT_FOUND)
    assert err.code is ErrorCode.CONFIG_NOT_FOUND
    assert isinstance(err, HnidsError)


def test_json_logs_are_parseable(capsys: pytest.CaptureFixture[str]) -> None:
    configure_logging("INFO", json=True)
    get_logger("test.component", run_id="r1").info("hello", rows=3)
    line = capsys.readouterr().err.strip().splitlines()[-1]
    record = json.loads(line)
    assert record["event"] == "hello"
    assert record["component"] == "test.component"
    assert record["run_id"] == "r1"
    assert record["rows"] == 3
    assert record["level"] == "info"


def test_level_filtering(capsys: pytest.CaptureFixture[str]) -> None:
    configure_logging("WARNING", json=True)
    get_logger("t").info("hidden")
    assert capsys.readouterr().err == ""


def test_console_renderer(capsys: pytest.CaptureFixture[str]) -> None:
    configure_logging("INFO", json=False)
    get_logger("t").info("visible")
    assert "visible" in capsys.readouterr().err


def test_bad_level_rejected() -> None:
    with pytest.raises(ValueError, match="unknown log level"):
        configure_logging("LOUD")
