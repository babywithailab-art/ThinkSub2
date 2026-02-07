"""
Test script for JSON logger
Demonstrates structured logging with request IDs
"""

import os
import sys

# Add project root to path
project_root = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, project_root)

from src.utils.json_logger import get_logger, generate_request_id, RequestContext

# Get logger
logger = get_logger("test")

print("=== JSON Logger Test ===\n")

# Test 1: Basic logging
logger.info("Test started")

# Test 2: Request ID generation
request_id_1 = generate_request_id()
logger.info(
    "Operation with request ID",
    extra={"data": {"request_id": request_id_1, "operation": "test"}},
)

# Test 3: Different log levels
logger.debug("Debug message", extra={"data": {"value": 42}})
logger.info("Info message")
logger.warning("Warning message", extra={"data": {"warning_code": "W001"}})

try:
    1 / 0
except Exception as e:
    logger.error(
        "Error occurred", extra={"data": {"error": str(e), "type": type(e).__name__}}
    )

# Test 4: Context manager
print("\n=== Test RequestContext ===\n")
with RequestContext(logger, request_id="req_context_test") as ctx:
    ctx.info("Step 1: Initialize")
    ctx.debug("Step 2: Process", data={"items": 100})
    ctx.warning("Step 3: Warning", data={"warning": "low_memory"})
    ctx.info("Step 4: Complete")

# Test 5: File logging (optional)
print("\n=== Test File Logging ===\n")
file_logger = get_logger("file_test", log_file="logs/test.log")
file_logger.info("This will be written to file")

print("\n=== Test Complete ===")
print(f"Check logs/ directory for test.log")
