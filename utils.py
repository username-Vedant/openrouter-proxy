#!/usr/bin/env python3
"""
Utility functions for OpenRouter API Proxy.
"""

import socket
import time
import json
from typing import Optional, Tuple

from fastapi import Header, HTTPException
from openai import APIError

from config import config, logger
from constants import RATE_LIMIT_ERROR_CODE


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
) -> bool:
    """
    Verify the local access key for authentication.

    Args:
        authorization: Authorization header

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

    if token != config["server"]["access_key"]:
        raise HTTPException(status_code=401, detail="Invalid access key")

    return True


def parse_google_rate_error(data: str) -> Optional[int]:
    # data = {
    #     'error': {
    #         'code': 429,
    #         'message': 'You exceeded your current quota, please check your plan and billing details.',
    #         'status': 'RESOURCE_EXHAUSTED',
    #         'details': [
    #             {'@type': 'type.googleapis.com/google.rpc.QuotaFailure', 'violations': [
    #                 {'quotaMetric': 'generativelanguage.googleapis.com/generate_content_paid_tier_input_token_count',
    #                  'quotaId': 'GenerateContentPaidTierInputTokensPerModelPerMinute',
    #                  'quotaDimensions': {'model': 'gemini-2.0-pro-exp', 'location': 'global'},
    #                  'quotaValue': '10000000'}
    #             ]},
    #             {'@type': 'type.googleapis.com/google.rpc.Help', 'links': [
    #                 {'description': 'Learn more about Gemini API quotas',
    #                  'url': 'https://ai.google.dev/gemini-api/docs/rate-limits'}
    #             ]},
    #             {'@type': 'type.googleapis.com/google.rpc.RetryInfo', 'retryDelay': '5s'}
    #         ]
    #     }
    # }
    reset_time_ms = None
    time_units = {'s': 1000, 'm': 60000, 'h': 3600000}
    try:
        data = json.loads(data)
    except Exception as e:
        logger.info("Json.loads error %s", e)
    else:
        retry_delay_ms = None
        try:
            message = data["error"].get("message", "")

            retry_info = next((item for item in data['error']['details'] if
                               item.get('@type') == 'type.googleapis.com/google.rpc.RetryInfo'), {})
            retry_delay = retry_info.get('retryDelay', '0s')

            num_part = ''.join(c for c in retry_delay if c.isdigit())
            unit_part = ''.join(c for c in retry_delay if c.isalpha())

            retry_delay_ms = int(num_part) * time_units.get(unit_part, 1000) if num_part else 0
        except (TypeError, KeyError) as err:
            logger.info("google reply parsing error %s", err)
        else:
            logger.info("google rate limit %s, retry: %s", message, retry_delay)

        if retry_delay_ms:
            reset_time_ms = int(time.time() * 1000) + retry_delay_ms

    return reset_time_ms

def check_rate_limit_chat(err: APIError) -> Tuple[bool, Optional[int]]:
    """
    Check for rate limit error.

    Args:
        err: OpenAI APIError

    Returns:
        Tuple (has_rate_limit_error, reset_time_ms)
    """
    has_rate_limit_error = False
    reset_time_ms = None

    if err.code == RATE_LIMIT_ERROR_CODE:
        has_rate_limit_error = True
        if isinstance(err.body, dict):
            try:
                reset_time_ms = int(err.body["metadata"]["headers"]["X-RateLimit-Reset"])
            except (TypeError, KeyError):
                raw = err.body.get("metadata", {}).get("raw", "")
                if raw and has_rate_limit_error:
                    reset_time_ms = parse_google_rate_error(raw)

    return has_rate_limit_error, reset_time_ms


def check_rate_limit(data: str or bytes) -> Tuple[bool, Optional[int]]:
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
    else:
        if isinstance(err, dict) and "error" in err:
            code = err["error"].get("code", 0)
            try:
                x_rate_limit = int(err["error"]["metadata"]["headers"]["X-RateLimit-Reset"])
            except (TypeError, KeyError):
                raw = err["error"].get("metadata", {}).get("raw", "")
                if raw and code == RATE_LIMIT_ERROR_CODE:
                    x_rate_limit = parse_google_rate_error(raw)
                else:
                    x_rate_limit = 0

            if x_rate_limit > 0:
                has_rate_limit_error = True
                reset_time_ms = x_rate_limit
            elif code == RATE_LIMIT_ERROR_CODE:
                has_rate_limit_error = True

    return has_rate_limit_error, reset_time_ms
