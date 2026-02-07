"""
Structured JSON Logger for ThinkSub2
Zero Script QA compliant logging infrastructure
"""

import logging
import json
import uuid
import os
from datetime import datetime
from typing import Any, Dict, Optional
from pathlib import Path


class JsonFormatter(logging.Formatter):
    """
    Custom formatter that outputs logs in JSON format.

    Format:
    {
      "timestamp": "2026-01-31T10:30:00.000Z",
      "level": "INFO",
      "service": "thinksub2",
      "request_id": "req_abc123",
      "message": "API Request completed",
      "data": {...}
    }
    """

    def format(self, record: logging.LogRecord) -> str:
        log_record = {
            "timestamp": self.formatTime(record, self.datefmt),
            "level": record.levelname,
            "service": record.name,
            "request_id": getattr(record, "request_id", "N/A"),
            "message": record.getMessage(),
        }

        # Add data field if exists
        if hasattr(record, "data"):
            log_record["data"] = record.data

        return json.dumps(log_record, ensure_ascii=False)


class JsonColoredFormatter(logging.Formatter):
    """
    JSON formatter with ANSI color codes for console output.
    """

    COLORS = {
        "DEBUG": "\033[0;35m",  # Magenta
        "INFO": "\033[0;36m",  # Cyan
        "WARNING": "\033[0;33m",  # Yellow
        "ERROR": "\033[0;31m",  # Red
        "CRITICAL": "\033[1;31m",  # Bold Red
    }
    RESET = "\033[0m"

    def format(self, record: logging.LogRecord) -> str:
        color = self.COLORS.get(record.levelname, self.RESET)
        json_str = json.dumps(
            {
                "timestamp": self.formatTime(record, self.datefmt),
                "level": record.levelname,
                "service": record.name,
                "request_id": getattr(record, "request_id", "N/A"),
                "message": record.getMessage(),
                "data": getattr(record, "data", None),
            }
        )
        return f"{color}{json_str}{self.RESET}"


def generate_request_id() -> str:
    """Generate a unique request ID for tracking."""
    return f"req_{uuid.uuid4().hex[:8]}"


def get_logger(
    name: str,
    log_file: Optional[str] = None,
    log_level: Optional[str] = None,
    colored: bool = True,
) -> logging.Logger:
    """
    Get or create a configured logger.

    Args:
        name: Logger name (e.g., 'transcriber', 'audio', 'main_window')
        log_file: Optional path to log file (creates file handler if provided)
        log_level: Log level (DEBUG, INFO, WARNING, ERROR)
        colored: Enable colored console output (default: True)

    Returns:
        Configured logger instance
    """
    logger = logging.getLogger(name)

    # Avoid duplicate handlers
    if logger.handlers:
        return logger

    # Set log level (env override if not provided)
    if log_level is None:
        log_level = os.getenv("LOG_LEVEL", "INFO")
    level = getattr(logging, log_level.upper(), logging.INFO)
    logger.setLevel(level)

    # Console handler with colored JSON output
    console_handler = logging.StreamHandler()
    formatter = JsonColoredFormatter() if colored else JsonFormatter()
    console_handler.setFormatter(formatter)
    console_handler.setLevel(level)
    logger.addHandler(console_handler)

    # File handler (optional)
    if log_file:
        # Ensure log directory exists
        log_path = Path(log_file)
        log_path.parent.mkdir(parents=True, exist_ok=True)

        file_handler = logging.FileHandler(log_file, encoding="utf-8")
        file_handler.setFormatter(JsonFormatter())
        file_handler.setLevel(level)
        logger.addHandler(file_handler)

    return logger


def log_with_request_id(
    logger: logging.Logger,
    message: str,
    level: int = logging.INFO,
    request_id: Optional[str] = None,
    data: Optional[Dict[str, Any]] = None,
) -> None:
    """
    Log a message with optional request ID and structured data.

    Args:
        logger: Logger instance
        message: Log message
        level: Log level (default: INFO)
        request_id: Optional request ID (generated if None)
        data: Optional structured data dict
    """
    if request_id is None:
        request_id = generate_request_id()

    extra = {"request_id": request_id}
    if data is not None:
        extra["data"] = data

    logger.log(level, message, extra=extra)


def log_with_request_id_synced(
    logger: logging.Logger,
    message: str,
    level: int = logging.INFO,
    request_id: Optional[str] = None,
    data: Optional[Dict[str, Any]] = None,
) -> str:
    """
    Log and return the request ID (for propagation).

    Useful when you need to log and pass the request ID to another function.

    Returns:
        The request ID used (generated if None)
    """
    if request_id is None:
        request_id = generate_request_id()

    log_with_request_id(logger, message, level, request_id, data)
    return request_id


class RequestContext:
    """
    Context manager for request-scoped logging.

    Usage:
        with RequestContext(logger, request_id) as ctx:
            ctx.info("Processing request", data={'method': 'POST'})
            ctx.debug("Step 1")
            ctx.warning("Potential issue")
    """

    def __init__(self, logger: logging.Logger, request_id: Optional[str] = None):
        self.logger = logger
        self.request_id = request_id or generate_request_id()

    def info(self, message: str, data: Optional[Dict[str, Any]] = None):
        log_with_request_id(self.logger, message, logging.INFO, self.request_id, data)

    def warning(self, message: str, data: Optional[Dict[str, Any]] = None):
        log_with_request_id(
            self.logger, message, logging.WARNING, self.request_id, data
        )

    def error(self, message: str, data: Optional[Dict[str, Any]] = None):
        log_with_request_id(self.logger, message, logging.ERROR, self.request_id, data)

    def debug(self, message: str, data: Optional[Dict[str, Any]] = None):
        log_with_request_id(self.logger, message, logging.DEBUG, self.request_id, data)

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc_val, exc_tb):
        # Log context exit if needed
        pass


# Example usage (commented out, for reference)
"""
# Simple usage:
logger = get_logger("example")
logger.info("Application started")
logger.error("Something failed", data={'error': 'message'})

# With request ID:
request_id = log_with_request_id_synced(logger, "API request started")
# Later...
log_with_request_id(logger, "API request completed", request_id=request_id)

# With structured data:
logger.info(
    "User action",
    data={
        'user_id': 123,
        'action': 'login',
        'ip': '192.168.1.1'
    }
)

# Context manager:
with RequestContext(logger) as ctx:
    ctx.info("Processing user request", data={'path': '/api/users'})
    ctx.debug("Database query executed")

# Error with stack trace:
try:
    risky_operation()
except Exception as e:
    logger.error(
        "Operation failed",
        data={
            'error': str(e),
            'type': type(e).__name__,
            'traceback': traceback.format_exc()
        }
    )
"""
