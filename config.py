#!/usr/bin/env python3
"""
Configuration module for OpenRouter API Proxy.
Loads settings from a YAML file and initializes logging.
"""

import logging
import sys
from typing import Dict, Any

import yaml

from constants import CONFIG_FILE


def load_config() -> Dict[str, Any]:
    """Load configuration from YAML file."""
    try:
        with open(CONFIG_FILE, encoding="utf-8") as file:
            return yaml.safe_load(file)
    except FileNotFoundError:
        print(f"Configuration file {CONFIG_FILE} not found. "
              "Please create it based on config.yml.example.")
        sys.exit(1)
    except yaml.YAMLError as e:
        print(f"Error parsing configuration file: {e}")
        sys.exit(1)


def setup_logging(config_: Dict[str, Any]) -> logging.Logger:
    """Configure logging based on configuration."""
    log_level_str = config_.get("server", {}).get("log_level", "INFO")
    log_level = getattr(logging, log_level_str.upper(), logging.INFO)

    logging.basicConfig(
        level=log_level,
        format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    )

    logger_ = logging.getLogger("openrouter-proxy")
    logger_.info("Logging level set to %s", log_level_str)

    return logger_


# Load configuration
config = load_config()

# Initialize logging
logger = setup_logging(config)
