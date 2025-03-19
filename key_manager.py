#!/usr/bin/env python3
"""
API Key management module for OpenRouter API Proxy.
Implements key rotation and rate limit handling.
"""

import asyncio
import sys
from datetime import datetime, timedelta
from typing import Dict, List, Optional

from fastapi import HTTPException

from config import logger


@staticmethod
def _mask_key(key: str) -> str:
    """Mask an API key for logging purposes."""
    if len(key) <= 8:
        return "****"
    return key[:4] + "****" + key[-4:]

class KeyManager:
    """Manages OpenRouter API keys, including rotation and rate limit handling."""
    def __init__(self, keys: List[str], cooldown_seconds: int):
        self.keys = keys
        self.cooldown_seconds = cooldown_seconds
        self.current_index = 0
        self.disabled_until: Dict[str, datetime] = {}
        self.lock = asyncio.Lock()

        if not keys:
            logger.error("No API keys provided in configuration.")
            sys.exit(1)

    async def get_next_key(self) -> str:
        """Get the next available API key using round-robin selection."""
        async with self.lock:
            # Find the next available key
            for _ in range(len(self.keys)):
                key = self.keys[self.current_index]
                self.current_index = (self.current_index + 1) % len(self.keys)

                # Check if the key is disabled
                if key in self.disabled_until:
                    if datetime.now() >= self.disabled_until[key]:
                        # Key cooldown period has expired
                        del self.disabled_until[key]
                        logger.info("API key %s is now enabled again.", _mask_key(key))
                        return key
                else:
                    # Key is not disabled
                    return key

            # All keys are disabled
            soonest_available = min(self.disabled_until.values())
            wait_seconds = (soonest_available - datetime.now()).total_seconds()
            logger.error(
                "All API keys are currently disabled. The next key will be available in %.2f seconds.", wait_seconds
            )
            raise HTTPException(
                status_code=503,
                detail="All API keys are currently disabled due to rate limits. Please try again later."
            )

    async def disable_key(self, key: str, reset_time_ms: Optional[int] = None):
        """
        Disable a key until reset time or for the configured cooldown period.

        Args:
            key: The API key to disable
            reset_time_ms: Optional reset time in milliseconds since epoch. If provided,
                          the key will be disabled until this time. Otherwise, the default
                          cooldown period will be used.
        """
        async with self.lock:
            if reset_time_ms:
                try:
                    # Convert milliseconds to seconds and create datetime
                    reset_datetime = datetime.fromtimestamp(reset_time_ms / 1000)

                    # Ensure reset time is in the future
                    if reset_datetime > datetime.now():
                        disabled_until = reset_datetime
                        logger.info("Using server-provided reset time: %s", str(disabled_until))
                    else:
                        # Fallback to default cooldown if reset time is in the past
                        disabled_until = datetime.now() + timedelta(seconds=self.cooldown_seconds)
                        logger.warning(
"Server-provided reset time is in the past, using default cooldown of %s seconds", self.cooldown_seconds)
                except Exception as e:
                    # Fallback to default cooldown on error
                    disabled_until = datetime.now() + timedelta(seconds=self.cooldown_seconds)
                    logger.error(
"Error processing reset time %s, using default cooldown: %s", reset_time_ms, e)
            else:
                # Use default cooldown period
                disabled_until = datetime.now() + timedelta(seconds=self.cooldown_seconds)
                logger.info(
"No reset time provided, using default cooldown of %s seconds", self.cooldown_seconds)

            self.disabled_until[key] = disabled_until
            logger.warning(
    "API key %s has been disabled until %s.", _mask_key(key), disabled_until)
