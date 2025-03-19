#!/usr/bin/env python3
"""
Utility functions for OpenRouter API Proxy.
"""

import socket
from typing import Optional, Tuple

from fastapi import Header, HTTPException
from httpx import Response

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


def check_rate_limit_error(response: Response) -> Tuple[bool, Optional[int]]:
    """
    Check for rate limit error in response.

    Args:
        response: Response from OpenRouter API

    Returns:
        Tuple (has_rate_limit_error, reset_time_ms)
    """
    has_rate_limit_error = False
    reset_time_ms = None

    # Check headers
    if "X-RateLimit-Reset" in response.headers:
        try:
            reset_time_ms = int(response.headers["X-RateLimit-Reset"])
            logger.info(f"Found X-RateLimit-Reset in headers: {reset_time_ms}s", )
        except (ValueError, TypeError) as e:
            logger.warning(f"Failed to parse X-RateLimit-Reset header: {e}s", )

    # Check response content if it's JSON
    content_type = response.headers.get('content-type', '')
    if 'application/json' in content_type:
        try:
            data = response.json()
            if (data.get("error", {}).get("status", '') == RATE_LIMIT_ERROR_CODE and
                    data.get("error", {}).get("message", '') == RATE_LIMIT_ERROR_MESSAGE):
                has_rate_limit_error = True

                # Extract reset time from metadata if available
                if "X-RateLimit-Reset" in data.get("error", {}).get("metadata", {}).get("headers", {}):
                    try:
                        reset_time_ms = int(data[
    "error"]["metadata"]["headers"]["X-RateLimit-Reset"])
                        logger.info(
    f"Found X-RateLimit-Reset in response metadata: {reset_time_ms}")
                    except (ValueError, TypeError) as e:
                        logger.warning(f"Failed to parse X-RateLimit-Reset from metadata: {e}s", )
        except Exception as e:
            logger.debug(f"Error parsing JSON response: {e}s", )

    return has_rate_limit_error, reset_time_ms
