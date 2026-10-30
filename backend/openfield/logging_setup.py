"""Logging with secret redaction. Logs go to <home>/logs/openworkflow.log (rotating)."""
from __future__ import annotations

import logging
import re
from logging.handlers import RotatingFileHandler
from pathlib import Path

_SECRET = re.compile(r"(?i)(api[_-]?key|token|password|secret|authorization)(\s*[=:]\s*)([^\s,;'\"]+)")


class RedactingFilter(logging.Filter):
    def filter(self, record: logging.LogRecord) -> bool:
        record.msg = _SECRET.sub(r"\1\2[REDACTED]", str(record.getMessage()))
        record.args = ()
        return True


def setup(home: Path) -> logging.Logger:
    log = logging.getLogger("openfield")
    if log.handlers:
        return log
    log.setLevel(logging.INFO)
    (home / "logs").mkdir(parents=True, exist_ok=True)
    h = RotatingFileHandler(home / "logs" / "openworkflow.log", maxBytes=1_000_000, backupCount=3, encoding="utf-8")
    h.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(name)s %(message)s"))
    h.addFilter(RedactingFilter())
    log.addHandler(h)
    return log
