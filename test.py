#!/usr/bin/env python3
"""
Test script for OpenRouter API Proxy with streaming responses.
Tests the proxy using configuration from config.yml.
"""

import asyncio
import os

import yaml
from openai import AsyncOpenAI  # Use the OpenAI library


# Load configuration from config.yml
def load_config():
    with open("config.yml", "r") as file:
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

MODEL =  "deepseek/deepseek-r1:free"
# MODEL = "google/gemini-2.0-pro-exp-02-05:free"
async def test_openrouter_streaming():
    """
    Test the OpenRouter proxy with streaming mode.
    """
    print(f"Testing OpenRouter Proxy at {PROXY_URL} with model {MODEL} in streaming mode...")

    # Initialize OpenAI client with proxy URL
    client = AsyncOpenAI(
        base_url=PROXY_URL + "/api/v1",  # Append /api/v1 for OpenAI compatibility
        api_key=ACCESS_KEY if ACCESS_KEY else "dummy" # Use a dummy key if no access key
    )

    headers = {
        "HTTP-Referer": config.get("test", {}).get("http_referer", "http://localhost"),  # Optional. Site URL for rankings on openrouter.ai.
        "X-Title": config.get("test", {}).get("x_title", "Local Test"),  # Optional. Site title for rankings on openrouter.ai.
    }
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
        "stream": True,  # Enable streaming
        "max_tokens": 600
    }

    try:
        # Use OpenAI's async streaming
        stream = await client.chat.completions.create(
            **request_data,
            extra_headers=headers,
            extra_body={"include_reasoning": True}
        )

        print("\nStarting to receive stream...\n")
        print("-" * 50)
        start_reasoning = False
        end_reasoning = False

        if request_data["stream"]:
            async for chunk in stream:
                if chunk.choices:
                    content = chunk.choices[0].delta.content
                    if content:
                        if start_reasoning and not end_reasoning:
                            end_reasoning = True
                            print("</reasoning>\n")
                        print(content, end='', flush=True)

                    # Check for reasoning, if supported by the model
                    if not end_reasoning and hasattr(chunk.choices[0].delta, 'reasoning'):
                        reasoning = chunk.choices[0].delta.reasoning
                        if reasoning:
                            if not start_reasoning:
                                print("<reasoning>")
                                start_reasoning = True
                            print(reasoning, end='', flush=True)
        else:
            if stream.choices:
                if hasattr(stream.choices[0].message, 'reasoning'):
                    reasoning = stream.choices[0].message.reasoning
                    if reasoning:
                        print("<reasoning>")
                        print(reasoning, end='', flush=True)
                        print("</reasoning>\n")
                content = stream.choices[0].message.content
                if content:
                    print(content, end='', flush=True)

        print("\n" + "-" * 50)
        if request_data["stream"]:
          print("\nStream completed!")
        else:
          print("\nNon-streaming response completed!")

    except Exception as e:
        print(f"Error occurred during test: {str(e)}")


if __name__ == "__main__":
    asyncio.run(test_openrouter_streaming())