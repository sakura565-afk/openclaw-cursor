#!/usr/bin/env python3
"""
Ollama Bridge — HTTP proxy between OpenClaw and Ollama API.
Listens on http://localhost:11434/v1/chat/completions and proxies to Ollama.

Usage:
    python -m scripts.ollama_bridge                    # Start server
    python -m scripts.ollama_bridge --port 8080          # Custom port
    python -m scripts.ollama_bridge --ollama-url http://localhost:11434
"""

import argparse
import json
import logging
import sys
import urllib.request
import urllib.error
from datetime import datetime
from pathlib import Path
from typing import Any

# Configure logging
LOG_DIR = Path(__file__).parent.parent / "logs"
LOG_DIR.mkdir(exist_ok=True)
LOG_FILE = LOG_DIR / "ollama_bridge.log"

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    handlers=[
        logging.FileHandler(LOG_FILE),
        logging.StreamHandler(sys.stdout)
    ]
)
logger = logging.getLogger(__name__)

DEFAULT_PORT = 11435  # Different from Ollama's 11434
DEFAULT_OLLAMA_URL = "http://localhost:11434"


class OllamaBridge:
    """HTTP proxy that translates OpenAI-compatible requests to Ollama."""
    
    def __init__(self, ollama_url: str = DEFAULT_OLLAMA_URL, port: int = DEFAULT_PORT):
        self.ollama_url = ollama_url.rstrip("/")
        self.port = port
        self.base_url = f"http://localhost:{port}"
        logger.info(f"Ollama Bridge initialized")
        logger.info(f"  Proxy target: {self.ollama_url}")
        logger.info(f"  Listening on: {self.base_url}")
    
    def translate_request(self, body: dict) -> dict:
        """Convert OpenAI request format to Ollama format."""
        messages = body.get("messages", [])
        model = body.get("model", "llama3.2")
        
        # Build Ollama prompt
        prompt_parts = []
        for msg in messages:
            role = msg.get("role", "user")
            content = msg.get("content", "")
            prompt_parts.append(f"<|{role}|>\n{content}")
        prompt_parts.append("<|assistant|>")
        prompt = "\n".join(prompt_parts)
        
        ollama_request = {
            "model": model,
            "prompt": prompt,
            "stream": body.get("stream", False),
        }
        
        # Options
        if "temperature" in body:
            ollama_request["options"] = ollama_request.get("options", {})
            ollama_request["options"]["temperature"] = body["temperature"]
        
        if "max_tokens" in body:
            ollama_request["options"] = ollama_request.get("options", {})
            ollama_request["options"]["num_predict"] = body["max_tokens"]
        
        if "top_p" in body:
            ollama_request["options"] = ollama_request.get("options", {})
            ollama_request["options"]["top_p"] = body["top_p"]
        
        if "repeat_penalty" in body:
            ollama_request["options"] = ollama_request.get("options", {})
            ollama_request["options"]["repeat_penalty"] = body["repeat_penalty"]
        
        return ollama_request
    
    def translate_response(self, ollama_response: dict, model: str) -> dict:
        """Convert Ollama response to OpenAI-compatible format."""
        return {
            "id": f"chatcmpl-{datetime.now().strftime('%Y%m%d%H%M%S')}",
            "object": "chat.completion",
            "created": int(datetime.now().timestamp()),
            "model": model,
            "choices": [{
                "index": 0,
                "message": {
                    "role": "assistant",
                    "content": ollama_response.get("response", "")
                },
                "finish_reason": "stop"
            }],
            "usage": {
                "prompt_tokens": ollama_response.get("prompt_eval_count", 0),
                "completion_tokens": ollama_response.get("eval_count", 0),
                "total_tokens": ollama_response.get("prompt_eval_count", 0) + ollama_response.get("eval_count", 0)
            }
        }
    
    def handle_chat(self, body: dict) -> tuple[dict, int]:
        """Handle /v1/chat/completions request."""
        model = body.get("model", "llama3.2")
        stream = body.get("stream", False)
        
        logger.info(f"Chat request: model={model}, stream={stream}")
        
        try:
            # Translate to Ollama format
            ollama_body = self.translate_request(body)
            logger.info(f"Translated to Ollama request")
            
            # Send to Ollama
            url = f"{self.ollama_url}/api/generate"
            data = json.dumps(ollama_body).encode("utf-8")
            req = urllib.request.Request(
                url,
                data=data,
                headers={"Content-Type": "application/json"},
                method="POST"
            )
            
            with urllib.request.urlopen(req, timeout=120) as resp:
                ollama_resp = json.loads(resp.read().decode("utf-8"))
            
            # Translate back to OpenAI format
            result = self.translate_response(ollama_resp, model)
            logger.info(f"Response: {result['usage']}")
            
            return result, 200
            
        except urllib.error.URLError as e:
            logger.error(f"Ollama connection error: {e}")
            return {"error": {"message": f"Connection to Ollama failed: {e}", "type": "api_error"}}, 502
        except Exception as e:
            logger.error(f"Error: {e}")
            return {"error": {"message": str(e), "type": "api_error"}}, 500
    
    def handle_models(self) -> tuple[dict, int]:
        """Handle /v1/models request — list available models."""
        try:
            url = f"{self.ollama_url}/api/tags"
            req = urllib.request.Request(url, method="GET")
            
            with urllib.request.urlopen(req, timeout=10) as resp:
                ollama_resp = json.loads(resp.read().decode("utf-8"))
            
            models = []
            for model in ollama_resp.get("models", []):
                models.append({
                    "id": model["name"],
                    "object": "model",
                    "created": int(datetime.now().timestamp()),
                    "owned_by": "ollama"
                })
            
            return {"object": "list", "data": models}, 200
            
        except Exception as e:
            logger.error(f"Error listing models: {e}")
            return {"error": {"message": str(e)}}, 500
    
    def handle_health(self) -> tuple[dict, int]:
        """Health check endpoint."""
        try:
            url = f"{self.ollama_url}/api/tags"
            req = urllib.request.Request(url, method="GET")
            urllib.request.urlopen(req, timeout=5)
            return {"status": "ok", "ollama": "connected"}, 200
        except Exception:
            return {"status": "ok", "ollama": "disconnected"}, 200
    
    def route(self, path: str, method: str, body: dict | None) -> tuple[dict, int]:
        """Route request to appropriate handler."""
        if path == "/v1/chat/completions":
            if method == "POST" and body:
                return self.handle_chat(body)
        elif path == "/v1/models":
            if method == "GET":
                return self.handle_models()
        elif path in ["/health", "/v1/health"]:
            return self.handle_health()
        
        return {"error": {"message": f"Not found: {method} {path}", "type": "not_found"}}, 404


def run_server(port: int, ollama_url: str):
    """Simple HTTP server using stdlib."""
    from http.server import HTTPServer, BaseHTTPRequestHandler
    import urllib.parse
    
    bridge = OllamaBridge(ollama_url=ollama_url, port=port)
    
    class Handler(BaseHTTPRequestHandler):
        def log_message(self, format, *args):
            logger.info(f"HTTP: {args[0]}")
        
        def do_POST(self):
            if self.path.startswith("/v1/") or self.path == "/health":
                content_length = int(self.headers.get("Content-Length", 0))
                body = {}
                if content_length > 0:
                    body = json.loads(self.rfile.read(content_length).decode("utf-8"))
                
                result, status = bridge.route(self.path, "POST", body)
                self.send_response(status)
                self.send_header("Content-Type", "application/json")
                self.end_headers()
                self.wfile.write(json.dumps(result).encode("utf-8"))
            else:
                self.send_response(404)
                self.end_headers()
        
        def do_GET(self):
            if self.path.startswith("/v1/") or self.path == "/health":
                result, status = bridge.route(self.path, "GET", None)
                self.send_response(status)
                self.send_header("Content-Type", "application/json")
                self.end_headers()
                self.wfile.write(json.dumps(result).encode("utf-8"))
            else:
                self.send_response(404)
                self.end_headers()
    
    server = HTTPServer(("localhost", port), Handler)
    logger.info(f"Starting Ollama Bridge on http://localhost:{port}")
    logger.info(f"Press Ctrl+C to stop")
    
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        logger.info("Shutting down...")
        server.shutdown()


def main():
    parser = argparse.ArgumentParser(description="Ollama Bridge — OpenAI-to-Ollama proxy")
    parser.add_argument("--port", type=int, default=DEFAULT_PORT,
                        help=f"Listen port (default: {DEFAULT_PORT})")
    parser.add_argument("--ollama-url", default=DEFAULT_OLLAMA_URL,
                        help=f"Ollama URL (default: {DEFAULT_OLLAMA_URL})")
    
    args = parser.parse_args()
    run_server(args.port, args.ollama_url)


if __name__ == "__main__":
    main()
