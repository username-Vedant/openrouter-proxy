#!/usr/bin/env python3
"""
Utility functions for OpenRouter API Proxy.
"""

import socket
import json
from typing import Dict, Any, Optional, Tuple

from fastapi import Request, Header, HTTPException
from httpx import Response

from config import logger
from constants import RATE_LIMIT_ERROR_MESSAGE

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
    request: Request,
    authorization: Optional[str] = Header(None),
    access_key: str = None,
    public_endpoints: list = None
) -> bool:
    """
    Verify the local access key for authentication.

    Args:
        request: FastAPI request object
        authorization: Authorization header
        access_key: Access key to verify
        public_endpoints: List of public endpoints

    Returns:
        True if authentication is successful

    Raises:
        HTTPException: If authentication fails
    """
    # Check if endpoint is public
    if public_endpoints and any(request.url.path.startswith(ep) for ep in public_endpoints):
        return True

    if not authorization:
        raise HTTPException(status_code=401, detail="Authorization header missing")

    scheme, _, token = authorization.partition(" ")
    if scheme.lower() != "bearer":
        raise HTTPException(status_code=401, detail="Invalid authentication scheme")

    if token != access_key:
        raise HTTPException(status_code=401, detail="Invalid access key")

    return True

async def prepare_request(request: Request, target_path: str, api_key: str) -> Dict[str, Any]:
    """
    Prepare the request to be forwarded to OpenRouter.

    Args:
        request: Original request
        target_path: Target API path
        api_key: API key to use

    Returns:
        Dictionary with request parameters for httpx
    """
    # Read request body
    body = await request.body()

    # Copy headers
    headers = dict(request.headers)

    # These headers should not be forwarded
    headers_to_remove = [
        "host",
        "content-length",
        "connection",
        "authorization"  # We'll set our own authorization header
    ]

    for header in headers_to_remove:
        if header in headers:
            del headers[header]

    # Set OpenRouter authorization header
    headers["Authorization"] = f"Bearer {api_key}"

    return {
        "method": request.method,
        "url": target_path,
        "headers": headers,
        "content": body,
    }

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
            logger.info("Found X-RateLimit-Reset in headers: %(reset_time_ms)s", )
        except (ValueError, TypeError) as e:
            logger.warning("Failed to parse X-RateLimit-Reset header: %(e)s", )

    # Check response content if it's JSON
    content_type = response.headers.get('content-type', '')
    if 'application/json' in content_type:
        try:
            data = response.json()

            if (
                "error" in data and
                "message" in data["error"] and
                data["error"]["message"] == RATE_LIMIT_ERROR_MESSAGE
            ):
                has_rate_limit_error = True

                # Extract reset time from metadata if available
                if (
                    "metadata" in data["error"] and
                    "headers" in data["error"]["metadata"] and
                    "X-RateLimit-Reset" in data["error"]["metadata"]["headers"]
                ):
                    try:
                        reset_time_ms = int(data[
    "error"]["metadata"]["headers"]["X-RateLimit-Reset"])
                        logger.info(
    f"Found X-RateLimit-Reset in response metadata: {reset_time_ms}")
                    except (ValueError, TypeError) as e:
                        logger.warning("Failed to parse X-RateLimit-Reset from metadata: %(e)s", )
        except Exception as e:
            logger.debug("Error parsing JSON response: %(e)s", )

    return has_rate_limit_error, reset_time_ms

def parse_rate_limit_from_sse(sse_chunk: str) -> Tuple[bool, Optional[int]]:
    """
    Parse SSE data for rate limit errors.

    Args:
        sse_chunk: SSE data chunk

    Returns:
        Tuple (has_rate_limit_error, reset_time_ms)
    """
    has_rate_limit_error = False
    reset_time_ms = None

    for line in sse_chunk.splitlines():
        if line.startswith('data: '):
            try:
                data_part = line[6:]  # Remove 'data: ' prefix
                if data_part and data_part != '[DONE]':
                    data_json = json.loads(data_part)

                    # Check for rate limit error in data
                    if (
                        "error" in data_json and
                        "message" in data_json["error"] and
                        data_json["error"]["message"] == RATE_LIMIT_ERROR_MESSAGE
                    ):
                        has_rate_limit_error = True

                        # Extract reset time if available
                        if "metadata" in data_json[
    "error"] and "headers" in data_json["error"]["metadata"]:
                            headers = data_json["error"]["metadata"]["headers"]
                            if "X-RateLimit-Reset" in headers:
                                try:
                                    reset_time_ms = int(headers["X-RateLimit-Reset"])
                                    logger.info(
    f"Found X-RateLimit-Reset in SSE data: {reset_time_ms}")
                                except (ValueError, TypeError) as e:
                                    logger.warning(
    f"Failed to parse X-RateLimit-Reset from SSE: {e}")
                        break
            except json.JSONDecodeError:
                pass  # Skip non-JSON data lines

    return has_rate_limit_error, reset_time_ms
