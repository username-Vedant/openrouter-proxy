# OpenRouter Proxy

A simple proxy server for OpenRouter API that helps bypass rate limits on free API keys by rotating through multiple keys in a round-robin fashion.

## Features

- Proxies all requests to OpenRouter API v1
- Rotates multiple API keys to bypass rate limits
- Automatically disables API keys temporarily when rate limits are reached
- Streams responses chunk by chunk for efficient data transfer
- Simple authentication for accessing the proxy

## Setup

1. Clone the repository
2. Create a virtual environment and install dependencies:
```
python -m venv venv
source venv/bin/activate  # On Windows: venv\Scripts\activate
pip install -r requirements.txt
```
3. Create a configuration file:
```
cp config.yml.example config.yml
```
4. Edit `config.yml` to add your OpenRouter API keys and configure the server

## Configuration

The `config.yml` file supports the following settings:

```yaml
# Server settings
server:
  host: "0.0.0.0"  # Interface to bind to
  port: 5555       # Port to listen on
  access_key: "your_local_access_key_here"  # Authentication key
  log_level: "INFO"  # Logging level (DEBUG, INFO, WARNING, ERROR, CRITICAL)

# OpenRouter API keys
openrouter:
  keys:
    - "sk-or-v1-your-first-api-key"
    - "sk-or-v1-your-second-api-key"
  rate_limit_cooldown: 7200  # Seconds to disable key after rate limit (2 hours)
```

## Usage

### Running Manually

Start the server:
```
python main.py
```

The proxy will be available at `http://localhost:5555` (or the host/port configured in your config file).

### Installing as a Systemd Service

For Linux systems with systemd, you can install the proxy as a system service:

1. Make sure you've created and configured your `config.yml` file
2. Run the installation script:
```
sudo ./service_install.sh
```

This will create a systemd service that starts automatically on boot.

To check the service status:
```
sudo systemctl status openrouter-proxy
```

To view logs:
```
sudo journalctl -u openrouter-proxy -f
```

To uninstall the service:
```
sudo ./service_uninstall.sh
```

### Authentication

Add your local access key to requests:
```
Authorization: Bearer your_local_access_key_here
```

## API Endpoints

The proxy supports all OpenRouter API v1 endpoints, including:

- `/api/v1/chat/completions` - Chat completions
- `/api/v1/completions` - Text completions
- `/api/v1/embeddings` - Text embeddings
- `/api/v1/models` - List available models (no auth required)
- `/api/v1/models/:author/:slug/endpoints` - Get specific model endpoints (no auth required)

## License

MIT 