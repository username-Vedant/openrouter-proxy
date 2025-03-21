#!/usr/bin/env python3
"""
API routes for OpenRouter API Proxy.
"""

import json
from typing import Optional, Dict, Any, AsyncGenerator

import httpx
from fastapi import APIRouter, Request, Header, HTTPException
from fastapi.responses import StreamingResponse, Response
from openai import AsyncOpenAI, APIError

from config import config, logger
from constants import OPENROUTER_BASE_URL, PUBLIC_ENDPOINTS, HTTPX_ENDPOINTS, OPENAI_ENDPOINTS, COMPLETION_ENDPOINTS
from key_manager import KeyManager
from utils import (
    verify_access_key,
    check_rate_limit_openai,
    check_rate_limit
)

# Create router
router = APIRouter()

# Initialize key manager
key_manager = KeyManager(
    keys=config["openrouter"]["keys"],
    cooldown_seconds=config["openrouter"]["rate_limit_cooldown"],
)


# Function to create OpenAI client with the right API key
async def get_openai_client(api_key: str) -> AsyncOpenAI:
    """Create an OpenAI client with the specified API key."""
    return AsyncOpenAI(api_key=api_key, base_url=OPENROUTER_BASE_URL)


@router.api_route(
    "/api/v1{path:path}",
    methods=["GET", "POST", "PUT", "DELETE", "OPTIONS", "HEAD", "PATCH"],
)
async def proxy_endpoint(
    request: Request, path: str, authorization: Optional[str] = Header(None)
):
    """
    Main proxy endpoint for handling all requests to OpenRouter API.
    """
    is_public = any(f"/api/v1{path}".startswith(ep) for ep in PUBLIC_ENDPOINTS)
    is_completion = any(f"/api/v1{path}".startswith(ep) for ep in COMPLETION_ENDPOINTS)
    is_httpx = any(f"/api/v1{path}".startswith(ep) for ep in HTTPX_ENDPOINTS)
    is_openai = any(f"/api/v1{path}".startswith(ep) for ep in OPENAI_ENDPOINTS)

    # Verify authorization for non-public endpoints
    if not is_public:
        await verify_access_key(authorization=authorization)

    # Log the full request URL including query parameters
    full_url = str(request.url).replace(str(request.base_url), "/")
    logger.info(
        "Proxying request to %s (Public: %s, HTTPX: %s, Completion: %s, OpenAI: %s)",
        full_url, is_public, is_httpx, is_completion, is_openai
    )

    # Parse request body (if any)
    request_body = None
    is_stream = False
    # Get API key to use
    if not is_public:
        api_key = await key_manager.get_next_key()
        if not api_key:
            raise HTTPException(status_code=503, detail="No available API keys")
    else:
        # For public endpoints, we don't need an API key
        api_key = ""
    try:
        body_bytes = await request.body()
        if body_bytes:
            request_body = json.loads(body_bytes)
            is_stream = request_body.get("stream", False)

            # Log if this is a streaming request
            if is_stream:
                logger.info("Detected streaming request")

            # Check for model variant
            if is_openai and request.method == "POST":
                model = request_body.get("model", "")
                if (
                    ":" in model
                ):  # This indicates a model variant like :free, :beta, etc.
                    base_model, variant = model.split(":", 1)
                    model_variant = f"{base_model} with {variant} tier"
                    logger.info("Using model variant: %s", model_variant)

    except Exception as e:
        logger.debug("Could not parse request body: %s", str(e))
        request_body = None

    # For models, non-OpenAI-compatible endpoints or requests with model-specific parameters, fall back to httpx
    if is_httpx or not is_openai:
        return await proxy_with_httpx(request, path, api_key, is_stream, is_completion)

    # For OpenAI-compatible endpoints, use the OpenAI library
    try:
        # Create an OpenAI client
        client = await get_openai_client(api_key)

        # Process based on the endpoint
        if is_openai:
            return await handle_completions(
                client, request, request_body, api_key, is_stream
            )
        else:
            # Fallback for other endpoints
            return await proxy_with_httpx(
                request, path, api_key, is_stream, is_completion
            )

    except Exception as e:
        logger.error("Error proxying request: %s", str(e))
        raise HTTPException(status_code=500, detail=f"Proxy error: {str(e)}") from e


async def handle_completions(
    client: AsyncOpenAI,
    request: Request,
    request_body: Dict[str, Any],
    api_key: str,
    is_stream: bool = False,
) -> Response:
    """Handle chat completions using the OpenAI client."""
    try:
        # Extract headers to forward
        forward_headers = {}
        for k, v in request.headers.items():
            if k.lower() in ["http-referer", "x-title"]:
                forward_headers[k] = v

        # Create a copy of the request body to modify
        completion_args = request_body.copy()

        # Move non-standard parameters that OpenAI SDK doesn't support directly to extra_body
        extra_body = {}
        openai_unsupported_params = ["include_reasoning", "transforms", "route", "provider"]
        for param in openai_unsupported_params:
            if param in completion_args:
                extra_body[param] = completion_args.pop(param)

        # Ensure we don't pass 'stream' twice
        if "stream" in completion_args:
            del completion_args["stream"]

        # Create a properly formatted request to the OpenAI API
        if is_stream:
            logger.info("Making streaming chat completion request")

            response = await client.chat.completions.create(
                **completion_args, extra_headers=forward_headers, extra_body=extra_body, stream=True
            )

            # Handle streaming response
            async def stream_response() -> AsyncGenerator[bytes, None]:
                try:
                    async for chunk in response:
                        # Convert chunk to the expected SSE format
                        if chunk.choices:
                            yield f"data: {json.dumps(chunk.model_dump())}\n\n".encode(
                                "utf-8"
                            )

                    # Send the end marker
                    yield b"data: [DONE]\n\n"
                except APIError as err:
                    logger.error("Error in streaming response: %s", err)
                    # Check if this is a rate limit error
                    if api_key:
                        has_rate_limit_error_, reset_time_ms_ = check_rate_limit_openai(err)
                        if has_rate_limit_error_:
                            logger.warning("Rate limit detected in stream. Disabling key.")
                            await key_manager.disable_key(
                                api_key, reset_time_ms_
                            )


            # Return a streaming response
            return StreamingResponse(
                stream_response(),
                media_type="text/event-stream",
                headers={
                    "Cache-Control": "no-cache",
                    "Connection": "keep-alive",
                    "X-Accel-Buffering": "no",
                },
            )
        # Non-streaming request
        logger.info("Making regular chat completion request")

        response = await client.chat.completions.create(
            **completion_args, extra_headers=forward_headers, extra_body=extra_body
        )

        result = response.model_dump()
        if 'error' in result:
            raise APIError(result['error'].get("message", "Error"), None, body=result['error'])

        # Return the response as JSON
        return Response(
            content=json.dumps(result), media_type="application/json"
        )
    except (APIError, Exception) as e:
        logger.error("Error in chat completions: %s", str(e))
        # Check if this is a rate limit error
        if api_key and isinstance(e, APIError):
            has_rate_limit_error, reset_time_ms = check_rate_limit_openai(e)
            if has_rate_limit_error:
                logger.warning("Rate limit detected in stream. Disabling key.")
                await key_manager.disable_key(api_key, reset_time_ms)

                # Try again with a new key
                new_api_key = await key_manager.get_next_key()
                if new_api_key:
                    new_client = await get_openai_client(new_api_key)
                    return await handle_completions(
                        new_client, request, request_body, new_api_key, is_stream
                    )

        # Raise the exception
        raise HTTPException(500, f"Error processing chat completion: {str(e)}") from e


async def _check_httpx_err(body: str or bytes, api_key: str or None):
    if api_key and (isinstance(body, str) and body.startswith("data: ") or (
            isinstance(body, bytes) and body.startswith(b"data: "))):
        body = body[6:]
        has_rate_limit_error, reset_time_ms = check_rate_limit(body)
        if has_rate_limit_error:
            logger.warning("Rate limit detected in stream. Disabling key.")
            await key_manager.disable_key(api_key, reset_time_ms)

async def proxy_with_httpx(
    request: Request,
    path: str,
    api_key: str,
    is_stream: bool,
    is_completion: bool,
) -> Response:
    """Fall back to httpx for endpoints not supported by the OpenAI SDK."""
    async with httpx.AsyncClient(timeout=60.0) as client:  # Increase default timeout
        try:
            headers = {
                k: v
                for k, v in request.headers.items()
                if k.lower()
                not in ["host", "content-length", "connection", "authorization"]
            }
            req_kwargs = {
                "method": request.method,
                "url": f"{OPENROUTER_BASE_URL}{path}",
                "headers": headers,
                "content": await request.body(),
            }
            # Add query parameters if they exist
            if request.query_params:
                req_kwargs["url"] = f"{req_kwargs['url']}?{request.url.query}"

            if api_key:
                req_kwargs["headers"]["Authorization"] = f"Bearer {api_key}"


            openrouter_resp = await client.request(**req_kwargs)
            if not is_stream:
                body = await openrouter_resp.aread()
                await _check_httpx_err(body, api_key)
                return Response(
                    content=body,
                    status_code=openrouter_resp.status_code,
                    headers=dict(openrouter_resp.headers),
                )
            if not api_key and not is_completion:
                return StreamingResponse(
                    openrouter_resp.aiter_bytes(),
                    status_code=openrouter_resp.status_code,
                    headers=dict(openrouter_resp.headers),
                )

            async def stream_completion():
                data = ''
                try:
                    async for line in openrouter_resp.aiter_lines():
                        if line.startswith("data: "):
                            data = line[6:]  # Get data without 'data: ' prefix
                            if data == "[DONE]":
                                yield "data: [DONE]\n\n".encode("utf-8")
                            else:
                                # Forward the original data without reformatting
                                data = line
                                yield f"{line}\n\n".encode("utf-8")
                        elif line:
                            yield f"{line}\n\n".encode("utf-8")
                except Exception as err:
                    logger.error("stream_completion error: %s", err)
                await _check_httpx_err(data, api_key)

            return StreamingResponse(
                stream_completion(),
                status_code=openrouter_resp.status_code,
                headers=dict(openrouter_resp.headers),
            )
        except httpx.ConnectError as e:
            logger.error("Connection error to OpenRouter: %s", str(e))
            raise HTTPException(503, "Unable to connect to OpenRouter API") from e
        except httpx.TimeoutException as e:
            logger.error("Timeout connecting to OpenRouter: %s", str(e))
            raise HTTPException(504, "OpenRouter API request timed out") from e
        except Exception as e:
            logger.error("Error proxying request with httpx: %s", str(e))
            raise HTTPException(500, f"Proxy error: {str(e)}") from e


@router.get("/health")
async def health_check():
    """Health check endpoint."""
    return {"status": "ok"}
