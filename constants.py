#!/usr/bin/env python3
"""
Constants used in OpenRouter API Proxy.
"""

# OpenRouter API base URL
OPENROUTER_BASE_URL = "https://openrouter.ai/api/v1"

# Rate limit error code
RATE_LIMIT_ERROR_CODE = 429

# Rate limit error message
RATE_LIMIT_ERROR_MESSAGE = "Rate limit exceeded: free-models-per-day"

# Public endpoints that don't require authentication
PUBLIC_ENDPOINTS = ["/api/v1/models"]

# Use httpx for proxy
HTTPX_ENDPOINTS = ["/api/v1/generation", "/api/v1/models"]

# Use openai for proxy
OPENAI_ENDPOINTS = ["/api/v1/completions", "/api/v1/chat/completions"]

# Read line by line
COMPLETION_ENDPOINTS = ["/api/v1/completions", "/api/v1/chat/completions"]
