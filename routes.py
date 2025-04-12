#!/usr/bin/env python3
"""
API routes for OpenRouter API Proxy.
"""

import json
from contextlib import asynccontextmanager
from typing import Optional, Dict, Any, AsyncGenerator

import httpx
from fastapi import APIRouter, Request, Header, HTTPException, FastAPI
from fastapi.responses import StreamingResponse, Response
from openai import AsyncOpenAI, APIError

from config import config, logger
from constants import (
    OPENROUTER_BASE_URL,
    PUBLIC_ENDPOINTS,
    OPENAI_ENDPOINTS,
    COMPLETION_ENDPOINTS,
    MODELS_ENDPOINTS,
)
from key_manager import KeyManager
from utils import (
    verify_access_key,
    check_rate_limit_chat,
    check_rate_limit
)

# Create router
router = APIRouter()

# Initialize key manager
key_manager = KeyManager(
    keys=config["openrouter"]["keys"],
    cooldown_seconds=config["openrouter"]["rate_limit_cooldown"],
)


@asynccontextmanager
async def lifespan(app_: FastAPI):
    client_kwargs = {"timeout": 60.0}  # Increase default timeout
    # Add proxy configuration if enabled
    if config.get("requestProxy", {}).get("enabled", False):
        proxy_url = config["requestProxy"]["url"]
        client_kwargs["proxy"] = proxy_url
        logger.info("Using proxy for httpx client: %s", proxy_url)
    app_.state.http_client = httpx.AsyncClient(**client_kwargs)
    yield
    await app_.state.http_client.aclose()


async def get_async_client(request: Request) -> httpx.AsyncClient:
    return request.app.state.http_client


async def get_openai_client(api_key: str, request: Request) -> AsyncOpenAI:
    """Create an OpenAI client with the specified API key."""
    client_params = {
        "api_key": api_key,
        "base_url": OPENROUTER_BASE_URL,
        "http_client": await get_async_client(request)
    }
    return AsyncOpenAI(**client_params)


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
    is_openai = any(f"/api/v1{path}".startswith(ep) for ep in OPENAI_ENDPOINTS)

    # Verify authorization for non-public endpoints
    if not is_public:
        await verify_access_key(authorization=authorization)

    # Log the full request URL including query parameters
    full_url = str(request.url).replace(str(request.base_url), "/")
    logger.info(
        "Proxying request to %s (Public: %s, Completion: %s, OpenAI: %s)",
        full_url, is_public, is_completion, is_openai
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

    try:
        # For OpenAI-compatible endpoints, use the OpenAI library
        if is_openai:
            return await handle_completions(
                request, request_body, api_key, is_stream
            )
        else:
            # Fallback for other endpoints
            return await proxy_with_httpx(
                request, path, api_key, is_stream, is_completion
            )

    except (Exception, HTTPException) as e:
        logger.error("Error proxying request: %s", str(e))
        if isinstance(e, HTTPException):
            raise e
        raise HTTPException(status_code=500, detail=f"Proxy error: {str(e)}") from e


async def handle_completions(
    request: Request,
    request_body: Dict[str, Any],
    api_key: str,
    is_stream: bool = False,
) -> Response:
    """Handle chat completions using the OpenAI client."""
    try:
        # Extract headers to forward
        forward_headers = {
            k: v
            for k, v in request.headers.items()
            if k.lower()
               not in ["host", "content-length", "connection", "authorization"]
        }

        # Create a copy of the request body to modify
        completion_args = request_body.copy()

        # Ensure we don't pass 'stream' twice
        if "stream" in completion_args:
            del completion_args["stream"]

        # Move non-standard parameters that OpenAI SDK doesn't support directly to extra_body
        extra_body = {}
        openai_unsupported_params = ["include_reasoning", "transforms", "route", "provider"]
        for param in openai_unsupported_params:
            if param in completion_args:
                extra_body[param] = completion_args.pop(param)

        # Create an OpenAI client
        client = await get_openai_client(api_key, request)

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
                    logger.error("Error in streaming response %s: %s", err.code, err)
                    logger.debug("Error body: %s", err.body)
                    # Check if this is a rate limit error
                    if api_key:
                        has_rate_limit_error_, reset_time_ms_ = check_rate_limit_chat(err)
                        if has_rate_limit_error_:
                            logger.warning("Rate limit detected in stream. Disabling key.")
                            await key_manager.disable_key(
                                api_key, reset_time_ms_
                            )
                        elif isinstance(err.body, dict):
                            yield json.dumps(err.body).encode("utf-8")


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
        code = 500
        detail = f"Error processing chat completion: {str(e)}"
        if isinstance(e, APIError):
            logger.debug("Error body: %s", e.body)
            # Check if this is a rate limit error
            if api_key:
                has_rate_limit_error, reset_time_ms = check_rate_limit_chat(e)
                if has_rate_limit_error:
                    logger.warning("Rate limit detected in stream. Disabling key.")
                    await key_manager.disable_key(api_key, reset_time_ms)

                    # Try again with a new key
                    new_api_key = await key_manager.get_next_key()
                    if new_api_key:
                        return await handle_completions(
                            request, request_body, new_api_key, is_stream
                        )
            code = e.code or code
            detail = e.body or detail
        # Raise the exception
        raise HTTPException(code, detail) from e


async def _check_httpx_err(body: str | bytes, api_key: str | None):
    # too big for error
    if len(body) > 4000 or not api_key:
        return
    if (isinstance(body, str) and body.startswith("data: ") or (
            isinstance(body, bytes) and body.startswith(b"data: "))):
        body = body[6:]
    has_rate_limit_error, reset_time_ms = check_rate_limit(body)
    if has_rate_limit_error:
        logger.warning("Rate limit detected in stream. Disabling key.")
        await key_manager.disable_key(api_key, reset_time_ms)

def _remove_paid_models(body: bytes) -> bytes:
    # {'prompt': '0', 'completion': '0', 'request': '0', 'image': '0', 'web_search': '0', 'internal_reasoning': '0'}
    prices =['prompt', 'completion', 'request', 'image', 'web_search', 'internal_reasoning']
    try:
        data = json.loads(body)
    except (json.JSONDecodeError, ValueError) as e:
        logger.warning("Error models deserialize: %s", str(e))
    else:
        if isinstance(data.get("data"), list):
            clear_data = []
            for model in data["data"]:
                if all(model.get("pricing", {}).get(k, "1") == "0" for k in prices):
                    clear_data.append(model)
            if clear_data:
                data["data"] = clear_data
                body = json.dumps(data, ensure_ascii=False).encode("utf-8")
    return body

async def proxy_with_httpx(
    request: Request,
    path: str,
    api_key: str,
    is_stream: bool,
    is_completion: bool,
) -> Response:
    """Fall back to httpx for endpoints not supported by the OpenAI SDK."""
    free_only = (any(f"/api/v1{path}" == ep for ep in MODELS_ENDPOINTS) and
                 config["openrouter"].get("free_only", False))
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

    client = await get_async_client(request)
    try:
        openrouter_resp = await client.request(**req_kwargs)
        headers = dict(openrouter_resp.headers)
        # Content has already been decoded
        headers.pop("content-encoding", None)
        headers.pop("Content-Encoding", None)

        if not is_stream:
            body = await openrouter_resp.aread()
            await _check_httpx_err(body, api_key)
            if free_only:
                body = _remove_paid_models(body)
            return Response(
                content=body,
                status_code=openrouter_resp.status_code,
                headers=headers,
            )
        if not api_key and not is_completion:
            return StreamingResponse(
                openrouter_resp.aiter_bytes(),
                status_code=openrouter_resp.status_code,
                headers=headers,
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
            headers=headers,
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
