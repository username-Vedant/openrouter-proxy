#!/usr/bin/env python3
"""
API routes for OpenRouter API Proxy.
"""

from typing import Optional

import httpx
from fastapi import APIRouter, Request, Header, HTTPException
from fastapi.responses import StreamingResponse

from config import config, logger
from constants import OPENROUTER_BASE_URL, PUBLIC_ENDPOINTS, BINARY_ENDPOINTS
from key_manager import KeyManager
from utils import (
    verify_access_key,
    prepare_request,
    check_rate_limit_error,
    parse_rate_limit_from_sse,
)

# Create router
router = APIRouter()

# Initialize key manager
key_manager = KeyManager(
    keys=config["openrouter"]["keys"],
    cooldown_seconds=config["openrouter"]["rate_limit_cooldown"],
)

@router.api_route("/api/v1{path:path}", methods=["GET", "POST", "PUT", "DELETE", "OPTIONS", "HEAD", "PATCH"])
async def proxy_endpoint(
    request: Request,
    path: str,
    authorization: Optional[str] = Header(None)
):
    """
    Main proxy endpoint for handling all requests to OpenRouter API.
    """
    is_public = any(f"/api/v1{path}".startswith(ep) for ep in PUBLIC_ENDPOINTS)
    is_binary = any(f"/api/v1{path}".startswith(ep) for ep in BINARY_ENDPOINTS)

    # Verify authorization for non-public endpoints
    if not is_public:
        await verify_access_key(
            request=request,
            authorization=authorization,
            access_key=config["server"]["access_key"],
            public_endpoints=PUBLIC_ENDPOINTS
        )

    # Log the full request URL including query parameters
    full_url = str(request.url).replace(str(request.base_url), "/")
    logger.info("Proxying request to %(full_url)s (Public: %(is_public)s, Binary: %(is_binary)s)", )

    async with httpx.AsyncClient(timeout=None) as client:
        try:
            if is_public:
                # For public endpoints, just forward without authentication
                req_kwargs = {
                    "method": request.method,
                    "url": f"{OPENROUTER_BASE_URL}{path}",
                    "headers": {k: v for k, v in request.headers.items()
                               if k.lower() not in ["host", "content-length", "connection"]},
                    "content": await request.body(),
                }
                # Add query parameters if they exist
                if request.query_params:
                    req_kwargs["url"] = f"{req_kwargs['url']}?{request.url.query}"
            else:
                # For authenticated endpoints, use API key rotation
                api_key = await key_manager.get_next_key()
                req_kwargs = await prepare_request(request, f"{OPENROUTER_BASE_URL}{path}", api_key)
                # Add query parameters if they exist
                if request.query_params:
                    req_kwargs["url"] = f"{req_kwargs['url']}?{request.url.query}"

            # Get the API key we're using for this request
            current_key = req_kwargs[
    "headers"]["Authorization"].replace("Bearer ", "") if "Authorization" in req_kwargs["headers"] else None

            # Make the request to OpenRouter
            openrouter_resp = await client.request(**req_kwargs)

            # Check if this is a streaming response (SSE) or binary response
            is_stream = False
            content_type = openrouter_resp.headers.get('content-type', '')

            if 'text/event-stream' in content_type:
                is_stream = True
                logger.info("Detected streaming response (SSE)")

            # Check for rate limit errors in response
            has_rate_limit_error = False
            reset_time_ms = None

            # Process response differently based on whether it's streaming, binary or regular JSON
            if is_stream:
                # For streaming responses
                sse_buffer = ""
                async for chunk in openrouter_resp.aiter_bytes(65536):  # Get a large enough chunk to check
                    sse_buffer += chunk.decode('utf-8', errors='replace')
                    break  # Only check the first chunk

                # Parse errors in SSE format
                has_rate_limit_error, reset_time_ms = parse_rate_limit_from_sse(sse_buffer)
            elif is_binary or 'application/octet-stream' in content_type or 'image/' in content_type:
                # Skip JSON parsing for binary responses
                if openrouter_resp.status_code >= 400:
                    # For generation endpoint, a 404 is often just "not ready yet"
                    if is_binary and 'generation' in path and openrouter_resp.status_code == 404:
                        logger.debug("Generation not ready yet: %(full_url)s (Status: 404)", )
                    else:
                        logger.error("Binary response error (%(openrouter_resp.status_code)s)", )
                else:
                    logger.debug("Received binary response (%(openrouter_resp.status_code)s)", )
            else:
                # For regular responses, check for errors and rate limits
                has_rate_limit_error, reset_time_ms = check_rate_limit_error(openrouter_resp)

            # Handle rate limit error if detected
            if has_rate_limit_error and current_key:
                logger.warning(f"Rate limit reached for API key. Disabling key and retrying.")

                # Disable the key with reset time if available
                await key_manager.disable_key(current_key, reset_time_ms)

                # Retry with a new key
                api_key = await key_manager.get_next_key()
                req_kwargs = await prepare_request(request, f"{OPENROUTER_BASE_URL}{path}", api_key)
                # Add query parameters if they exist
                if request.query_params:
                    req_kwargs["url"] = f"{req_kwargs['url']}?{request.url.query}"
                openrouter_resp = await client.request(**req_kwargs)

            # Prepare response headers
            response_headers = dict(openrouter_resp.headers)

            # Stream the response
            async def stream_response():
                async for chunk in openrouter_resp.aiter_bytes():
                    yield chunk

            return StreamingResponse(
                stream_response(),
                status_code=openrouter_resp.status_code,
                headers=response_headers
            )

        except Exception as e:
            logger.error("Error proxying request: %(e)s", )
            raise HTTPException(status_code=500, detail=f"Proxy error: {str(e)}")

@router.get("/health")
async def health_check():
    """Health check endpoint."""
    return {"status": "ok"}
