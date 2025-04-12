#!/usr/bin/env python3
"""
Constants used in OpenRouter API Proxy.
"""

# Config
CONFIG_FILE = "config.yml"

# OpenRouter API base URL
OPENROUTER_BASE_URL = "https://openrouter.ai/api/v1"

# Rate limit error code
RATE_LIMIT_ERROR_CODE = 429

# Public endpoints that don't require authentication
PUBLIC_ENDPOINTS = ["/api/v1/models"]

MODELS_ENDPOINTS = ["/api/v1/models"]

# Use openai for proxy
OPENAI_ENDPOINTS = ["/api/v1/chat/completions"]

# Read line by line
COMPLETION_ENDPOINTS = ["/api/v1/completions", "/api/v1/chat/completions"]
