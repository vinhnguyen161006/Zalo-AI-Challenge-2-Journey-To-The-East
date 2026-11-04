"""Structured stderr logging without raw source values or exception tracebacks."""

from __future__ import annotations

import json
import logging


class EventFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        event = {"event": record.getMessage(), "level": record.levelname}
        for name in ("question_id", "error_type"):
            if hasattr(record, name):
                event[name] = getattr(record, name)
        return json.dumps(event, ensure_ascii=False)


def configure_logging() -> None:
    handler = logging.StreamHandler()
    handler.setFormatter(EventFormatter())
    logger = logging.getLogger("datamate")
    logger.handlers = [handler]
    logger.setLevel(logging.INFO)
    logger.propagate = False
