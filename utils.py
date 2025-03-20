#!/usr/bin/env python3
"""
Utility functions for OpenRouter API Proxy.
"""

import socket
import json
from typing import Optional, Tuple

from fastapi import Header, HTTPException
from openai import APIError

from config import logger
from constants import RATE_LIMIT_ERROR_MESSAGE, RATE_LIMIT_ERROR_CODE


def get_local_ip() -> str:
    """Get local IP address for displaying in logs."""
    try:
        # Create a socket that connects to a public address
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        # No actual connection is made
        s.connect(("8.8.8.8", 80))
        local_ip = s.getsockname()[0]
        s.close()
        return local_ip
    except Exception:
        return "localhost"

async def verify_access_key(
    authorization: Optional[str] = Header(None),
    access_key: str = None,
) -> bool:
    """
    Verify the local access key for authentication.

    Args:
        authorization: Authorization header
        access_key: Access key to verify

    Returns:
        True if authentication is successful

    Raises:
        HTTPException: If authentication fails
    """

    if not authorization:
        raise HTTPException(status_code=401, detail="Authorization header missing")

    scheme, _, token = authorization.partition(" ")
    if scheme.lower() != "bearer":
        raise HTTPException(status_code=401, detail="Invalid authentication scheme")

    if token != access_key:
        raise HTTPException(status_code=401, detail="Invalid access key")

    return True

def check_rate_limit_openai(err: APIError) -> Tuple[bool, Optional[int]]:
    """
    Check for rate limit error.

    Args:
        err: OpenAI APIError

    Returns:
        Tuple (has_rate_limit_error, reset_time_ms)
    """
    has_rate_limit_error = False
    reset_time_ms = None

    if err.code == RATE_LIMIT_ERROR_CODE and isinstance(err.body, dict):
        try:
            reset_time_ms = int(err.body["metadata"]["headers"]["X-RateLimit-Reset"])
            has_rate_limit_error = True
        except Exception as _:
            pass

    if reset_time_ms is None and RATE_LIMIT_ERROR_MESSAGE in err.message:
        has_rate_limit_error = True

    return has_rate_limit_error, reset_time_ms


def check_rate_limit(data: str) -> Tuple[bool, Optional[int]]:
    """
    Check for rate limit error.

    Args:
        data: response line

    Returns:
        Tuple (has_rate_limit_error, reset_time_ms)
    """
    has_rate_limit_error = False
    reset_time_ms = None
    try:
        err = json.loads(data)
    except Exception as e:
        logger.warning('Json.loads error %s', e)
        return has_rate_limit_error, reset_time_ms
    if not isinstance(err, dict) or "error" not in err:
        return has_rate_limit_error, reset_time_ms

    code = err["error"].get("code", 0)
    msg = err["error"].get("message", 0)
    try:
        x_rate_limit = int(err["error"]["metadata"]["headers"]["X-RateLimit-Reset"])
    except (TypeError, KeyError):
        x_rate_limit = None

    if x_rate_limit :
        has_rate_limit_error = True
        reset_time_ms = x_rate_limit
    elif code == RATE_LIMIT_ERROR_CODE and msg == RATE_LIMIT_ERROR_MESSAGE:
        has_rate_limit_error = True

    return has_rate_limit_error, reset_time_ms
