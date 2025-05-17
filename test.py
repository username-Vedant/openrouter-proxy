#!/usr/bin/env python3
"""
Test script for OpenRouter API Proxy with streaming responses.
Tests the proxy using configuration from config.yml.
"""

import asyncio
import json
import os

import httpx
import yaml

MODEL =  "deepseek/deepseek-r1:free"
STREAM = True
MAX_TOKENS = 600
INCLUDE_REASONING = True

def load_config():
    """
    Load configuration from config.yml
    """
    with open("config.yml", encoding="utf-8") as file:
        return yaml.safe_load(file)


# Get configuration
config = load_config()
server_config = config["server"]

# Configure proxy settings from config
host = server_config["host"]
# Replace 0.0.0.0 with 127.0.0.1 for client connections
if host == "0.0.0.0":
    host = "127.0.0.1"
port = server_config["port"]
PROXY_URL = f"http://{host}:{port}"
ACCESS_KEY = server_config["access_key"]

# Override with environment variable if set
if os.environ.get("ACCESS_KEY"):
    ACCESS_KEY = os.environ.get("ACCESS_KEY")


async def test_openrouter_streaming():
    """
    Test the OpenRouter proxy with streaming mode.
    """
    print(f"Testing OpenRouter Proxy at {PROXY_URL} with model {MODEL}")

    url = f"{PROXY_URL}/api/v1/chat/completions"
    headers = {"Authorization": f"Bearer {ACCESS_KEY or 'dummy'}"}
    if not ACCESS_KEY:
        print("No valid access key found. Request may fail if server requires authentication.")
    else:
        print(f"Using access key: {ACCESS_KEY[:5]}...{ACCESS_KEY[-5:]}")


    # Request body following OpenRouter API format
    request_data = {
        "model": MODEL,
        "messages": [
            {"role": "user", "content": "Write a short poem about AI and humanity working together"}
        ],
        "stream": STREAM,
        "max_tokens": MAX_TOKENS,
        "include_reasoning": INCLUDE_REASONING,
    }

    client = httpx.AsyncClient(timeout=httpx.Timeout(15.0, read=600.0))
    req = client.build_request("POST", url, headers=headers, json=request_data)

    print(f"\nStarting to receive data streaming: {STREAM}...\n")
    print("-" * 50)

    resp = await client.send(req, stream=STREAM)
    try:
        resp.raise_for_status()
        if STREAM:
            reasoning_phase = False
            async for line in resp.aiter_lines():
                if not line.startswith("data: "):
                    continue
                if (line := line[6:]) == "[DONE]":
                    break
                data = json.loads(line)
                if "error" in data:
                    raise ValueError(str(data))
                choice = data["choices"][0]["delta"]
                if content := choice.get("content"):
                    if reasoning_phase:
                        reasoning_phase = False
                        print("</reasoning>\n")
                    print(content, end='', flush=True)
                elif reasoning := choice.get("reasoning"):
                    if not reasoning_phase:
                        reasoning_phase = True
                        print("<reasoning>")
                    print(reasoning, end='', flush=True)
        else:
            data = resp.json()
            if "error" in data:
                raise ValueError(str(data))
            choice = data["choices"][0]["message"]
            if reasoning := choice.get("reasoning"):
                print(f"<reasoning>\n{reasoning}</reasoning>\n")
            if content := choice.get("content"):
                print(content, end='')
    except Exception as e:
        print(f"Error occurred during test: {str(e)}")
    finally:
        if STREAM:
            await resp.aclose()
    print("\n" + "-" * 50)
    if STREAM:
        print("\nStream completed!")
    else:
        print("\nNon-streaming response completed!")


if __name__ == "__main__":
    asyncio.run(test_openrouter_streaming())
