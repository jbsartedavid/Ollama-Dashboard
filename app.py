import csv
import ctypes
import ctypes.wintypes
import hmac
import ipaddress
import json
import os
import re
import secrets
import shutil
import socket
import sqlite3
import subprocess
import sys
import threading
import time
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterator
from urllib.parse import urlsplit

import psutil
import requests
from flask import (
    Flask,
    Response,
    has_request_context,
    jsonify,
    render_template,
    request,
    stream_with_context,
)

app = Flask(__name__)
ENV_OLLAMA_BASE_URL = os.getenv("OLLAMA_BASE_URL", "http://localhost:11434").rstrip("/")
if getattr(sys, "frozen", False):
    data_directory = Path(os.getenv("LOCALAPPDATA", Path.home())) / "OllamaDashboard"
    data_directory.mkdir(parents=True, exist_ok=True)
    default_database_path = data_directory / "dashboard.db"
else:
    default_database_path = Path(app.root_path) / "dashboard.db"
DATABASE_PATH = os.getenv("DASHBOARD_DATABASE", str(default_database_path))
_DPAPI_ENTROPY = b"OllamaDashboard.Settings.v1"
_telemetry_lock = threading.Lock()
_last_disk_io = psutil.disk_io_counters()
_last_disk_io_at = time.monotonic()
psutil.cpu_percent(interval=None)


class _DataBlob(ctypes.Structure):
    _fields_ = [("cbData", ctypes.wintypes.DWORD), ("pbData", ctypes.POINTER(ctypes.c_byte))]


def _protect_secret(value: str) -> bytes:
    if sys.platform != "win32":
        raise RuntimeError("Saving an API key requires Windows Data Protection.")
    raw = value.encode("utf-8")
    raw_buffer = ctypes.create_string_buffer(raw)
    input_blob = _DataBlob(len(raw), ctypes.cast(raw_buffer, ctypes.POINTER(ctypes.c_byte)))
    entropy_buffer = ctypes.create_string_buffer(_DPAPI_ENTROPY)
    entropy_blob = _DataBlob(
        len(_DPAPI_ENTROPY),
        ctypes.cast(entropy_buffer, ctypes.POINTER(ctypes.c_byte)),
    )
    output_blob = _DataBlob()
    crypt32 = ctypes.WinDLL("crypt32", use_last_error=True)
    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    crypt32.CryptProtectData.argtypes = [
        ctypes.POINTER(_DataBlob), ctypes.wintypes.LPCWSTR, ctypes.POINTER(_DataBlob),
        ctypes.c_void_p, ctypes.c_void_p, ctypes.wintypes.DWORD, ctypes.POINTER(_DataBlob),
    ]
    crypt32.CryptProtectData.restype = ctypes.wintypes.BOOL
    kernel32.LocalFree.argtypes = [ctypes.c_void_p]
    kernel32.LocalFree.restype = ctypes.c_void_p
    if not crypt32.CryptProtectData(
        ctypes.byref(input_blob),
        "Ollama Dashboard API key",
        ctypes.byref(entropy_blob),
        None,
        None,
        0x1,
        ctypes.byref(output_blob),
    ):
        raise ctypes.WinError(ctypes.get_last_error(), "Could not protect the API key with Windows DPAPI.")
    try:
        return ctypes.string_at(output_blob.pbData, output_blob.cbData)
    finally:
        kernel32.LocalFree(output_blob.pbData)


def _unprotect_secret(value: bytes) -> str:
    if sys.platform != "win32":
        raise RuntimeError("Reading a saved API key requires Windows Data Protection.")
    raw_buffer = ctypes.create_string_buffer(value)
    input_blob = _DataBlob(len(value), ctypes.cast(raw_buffer, ctypes.POINTER(ctypes.c_byte)))
    entropy_buffer = ctypes.create_string_buffer(_DPAPI_ENTROPY)
    entropy_blob = _DataBlob(
        len(_DPAPI_ENTROPY),
        ctypes.cast(entropy_buffer, ctypes.POINTER(ctypes.c_byte)),
    )
    output_blob = _DataBlob()
    crypt32 = ctypes.WinDLL("crypt32", use_last_error=True)
    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    crypt32.CryptUnprotectData.argtypes = [
        ctypes.POINTER(_DataBlob), ctypes.POINTER(ctypes.wintypes.LPWSTR),
        ctypes.POINTER(_DataBlob), ctypes.c_void_p, ctypes.c_void_p,
        ctypes.wintypes.DWORD, ctypes.POINTER(_DataBlob),
    ]
    crypt32.CryptUnprotectData.restype = ctypes.wintypes.BOOL
    kernel32.LocalFree.argtypes = [ctypes.c_void_p]
    kernel32.LocalFree.restype = ctypes.c_void_p
    if not crypt32.CryptUnprotectData(
        ctypes.byref(input_blob),
        None,
        ctypes.byref(entropy_blob),
        None,
        None,
        0x1,
        ctypes.byref(output_blob),
    ):
        raise ctypes.WinError(ctypes.get_last_error(), "Could not decrypt the saved API key.")
    try:
        return ctypes.string_at(output_blob.pbData, output_blob.cbData).decode("utf-8")
    finally:
        kernel32.LocalFree(output_blob.pbData)


def _connect_db() -> sqlite3.Connection:
    connection = sqlite3.connect(DATABASE_PATH, timeout=10)
    connection.row_factory = sqlite3.Row
    return connection


@contextmanager
def _db_session() -> Iterator[sqlite3.Connection]:
    connection = _connect_db()
    try:
        with connection:
            yield connection
    finally:
        connection.close()


def _initialize_db() -> None:
    with _db_session() as connection:
        connection.execute(
            """
            CREATE TABLE IF NOT EXISTS activity (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                created_at TEXT NOT NULL,
                model TEXT NOT NULL,
                prompt TEXT NOT NULL,
                response TEXT NOT NULL,
                status TEXT NOT NULL,
                error TEXT,
                wall_duration_ms REAL NOT NULL,
                total_duration_ns INTEGER,
                load_duration_ns INTEGER,
                prompt_eval_count INTEGER,
                prompt_eval_duration_ns INTEGER,
                eval_count INTEGER,
                eval_duration_ns INTEGER,
                client_ip TEXT
            )
            """
        )
        activity_columns = {
            row["name"] for row in connection.execute("PRAGMA table_info(activity)").fetchall()
        }
        if "client_ip" not in activity_columns:
            connection.execute("ALTER TABLE activity ADD COLUMN client_ip TEXT")
        connection.execute(
            """
            CREATE TABLE IF NOT EXISTS settings (
                name TEXT PRIMARY KEY,
                value TEXT NOT NULL
            )
            """
        )
        connection.execute(
            """
            CREATE TABLE IF NOT EXISTS secrets (
                name TEXT PRIMARY KEY,
                value BLOB NOT NULL
            )
            """
        )
        connection.execute("DELETE FROM settings WHERE name = 'default_model'")


def _get_setting(name: str, fallback: str) -> str:
    with _db_session() as connection:
        row = connection.execute("SELECT value FROM settings WHERE name = ?", (name,)).fetchone()
    return row["value"] if row else fallback


def _get_api_key() -> str:
    return _get_secret("ollama_api_key")


def _get_secret(name: str) -> str:
    with _db_session() as connection:
        row = connection.execute("SELECT value FROM secrets WHERE name = ?", (name,)).fetchone()
    return _unprotect_secret(row["value"]) if row else ""


def _has_secret(name: str) -> bool:
    with _db_session() as connection:
        row = connection.execute("SELECT 1 FROM secrets WHERE name = ?", (name,)).fetchone()
    return row is not None


def _get_ollama_config() -> dict[str, Any]:
    base_url = _get_setting("base_url", ENV_OLLAMA_BASE_URL)
    parsed_url = urlsplit(base_url)
    hostname = parsed_url.hostname or ""
    try:
        is_loopback = ipaddress.ip_address(hostname).is_loopback
    except ValueError:
        is_loopback = hostname.lower() == "localhost"

    listener_port = (
        request.environ.get("SERVER_PORT", "")
        if has_request_context()
        else os.getenv("OLLAMA_DASHBOARD_PORT", "")
    )
    uses_default_client_port = listener_port == "11434"
    if uses_default_client_port and is_loopback and parsed_url.port == 11434:
        netloc = f"[{hostname}]:11435" if ":" in hostname else f"{hostname}:11435"
        base_url = parsed_url._replace(netloc=netloc).geturl()
        parsed_url = urlsplit(base_url)
    port_takeover = uses_default_client_port and is_loopback and parsed_url.port == 11435
    return {
        "base_url": base_url,
        "keep_alive": _get_setting("keep_alive", "5m"),
        "gateway_enabled": (
            port_takeover or _get_setting("gateway_enabled", "false") == "true"
        ),
        "gateway_auth_required": (
            False if port_takeover else _get_setting("gateway_auth_required", "false") == "true"
        ),
    }


def _get_auto_refresh_seconds() -> int:
    value = _get_setting("auto_refresh_seconds", "10")
    return int(value) if value in {"0", "5", "10", "15", "30", "60"} else 10


def _validate_settings(body: dict[str, Any]) -> tuple[dict[str, Any] | None, str | None]:
    base_url = body.get("baseUrl")
    keep_alive = body.get("keepAlive", "5m")
    api_key = body.get("apiKey", "")
    clear_api_key = body.get("clearApiKey", False)
    current_config = _get_ollama_config()
    gateway_enabled = body.get("gatewayEnabled", current_config["gateway_enabled"])
    gateway_auth_required = body.get(
        "gatewayAuthRequired", current_config["gateway_auth_required"]
    )

    if not all(isinstance(value, str) for value in (base_url, keep_alive, api_key)):
        return None, "Server URL, keep-alive, and API key must be strings."
    if not isinstance(clear_api_key, bool):
        return None, "clearApiKey must be a boolean."
    if not isinstance(gateway_enabled, bool):
        return None, "gatewayEnabled must be a boolean."
    if not isinstance(gateway_auth_required, bool):
        return None, "gatewayAuthRequired must be a boolean."

    base_url = base_url.strip().rstrip("/")
    keep_alive = keep_alive.strip()
    api_key = api_key.strip()

    try:
        parsed_url = urlsplit(base_url)
        valid_url = (
            parsed_url.scheme in {"http", "https"}
            and bool(parsed_url.hostname)
            and parsed_url.username is None
            and parsed_url.password is None
            and not parsed_url.query
            and not parsed_url.fragment
            and (parsed_url.port is None or 1 <= parsed_url.port <= 65535)
        )
    except ValueError:
        valid_url = False
    if not valid_url or len(base_url) > 2048:
        return None, "Enter a valid HTTP or HTTPS Ollama server URL, without credentials or query parameters."
    if not re.fullmatch(r"(?:-1|0|[1-9]\d*(?:ns|us|µs|ms|s|m|h)?)", keep_alive):
        return None, "Keep-alive must be -1, 0, or a duration such as 5m or 30s."
    if len(api_key) > 4096:
        return None, "API key is too long."
    if api_key and sys.platform != "win32":
        return None, "API key storage is supported on Windows using the current user's DPAPI."
    if gateway_enabled and gateway_auth_required and not _has_secret("gateway_access_key"):
        return None, "Generate a client access key before enabling the network proxy."

    return {
        "base_url": base_url,
        "keep_alive": keep_alive,
        "gateway_enabled": gateway_enabled,
        "gateway_auth_required": gateway_auth_required,
        "api_key": api_key,
        "clear_api_key": clear_api_key,
    }, None


def _save_ollama_config(values: dict[str, Any]) -> None:
    protected_key = _protect_secret(values["api_key"]) if values["api_key"] else None
    with _db_session() as connection:
        connection.executemany(
            "INSERT INTO settings (name, value) VALUES (?, ?) "
            "ON CONFLICT(name) DO UPDATE SET value = excluded.value",
            [
                ("base_url", values["base_url"]),
                ("keep_alive", values["keep_alive"]),
                ("gateway_enabled", "true" if values["gateway_enabled"] else "false"),
                (
                    "gateway_auth_required",
                    "true" if values["gateway_auth_required"] else "false",
                ),
            ],
        )
        if values["clear_api_key"]:
            connection.execute("DELETE FROM secrets WHERE name = 'ollama_api_key'")
        elif protected_key is not None:
            connection.execute(
                "INSERT INTO secrets (name, value) VALUES ('ollama_api_key', ?) "
                "ON CONFLICT(name) DO UPDATE SET value = excluded.value",
                (protected_key,),
            )


def _get_lan_ip() -> str | None:
    try:
        with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as probe:
            probe.connect(("192.0.2.1", 80))
            address = probe.getsockname()[0]
            return address if not address.startswith("127.") else None
    except OSError:
        return None


def _client_gateway_url() -> str:
    address = _get_lan_ip() or "<server-lan-ip>"
    port = request.environ.get("SERVER_PORT", "5000")
    return f"http://{address}:{port}"


def _is_loopback_request() -> bool:
    try:
        return ipaddress.ip_address(request.remote_addr or "").is_loopback
    except ValueError:
        return False


def _is_trusted_lan_request() -> bool:
    try:
        address = ipaddress.ip_address(request.remote_addr or "")
    except ValueError:
        return False
    if isinstance(address, ipaddress.IPv6Address) and address.ipv4_mapped:
        address = address.ipv4_mapped
    if address.is_loopback or address.is_link_local:
        return True
    trusted_networks = (
        ipaddress.ip_network("10.0.0.0/8"),
        ipaddress.ip_network("172.16.0.0/12"),
        ipaddress.ip_network("192.168.0.0/16"),
        ipaddress.ip_network("fc00::/7"),
    )
    return any(address.version == network.version and address in network for network in trusted_networks)


@app.before_request
def restrict_remote_dashboard_access():
    if not _is_loopback_request() and request.endpoint not in {
        "generate_text",
        "ollama_client_proxy",
        "openai_client_proxy",
        "ollama_proxy",
    }:
        return jsonify({"error": "The dashboard management interface is available on this computer only."}), 403
    return None


def _copy_proxy_headers() -> dict[str, str]:
    headers = {}
    for name in ("Accept", "Content-Type", "User-Agent"):
        value = request.headers.get(name)
        if value:
            headers[name] = value
    try:
        upstream_key = _get_api_key()
    except (OSError, RuntimeError, UnicodeDecodeError):
        app.logger.exception("Could not read the encrypted Ollama API key")
        raise
    if upstream_key:
        headers["Authorization"] = f"Bearer {upstream_key}"
    return headers


def _activity_prompt(path: str, body: dict[str, Any]) -> str:
    if path in {"api/generate", "v1/completions"}:
        prompt = body.get("prompt", "")
        return prompt if isinstance(prompt, str) else json.dumps(prompt, ensure_ascii=False)
    messages = body.get("messages", [])
    if not isinstance(messages, list):
        return ""
    return "\n".join(
        f"{message.get('role', 'user')}: {message.get('content', '')}"
        for message in messages
        if isinstance(message, dict)
    )


def _activity_data(path: str, data: dict[str, Any]) -> dict[str, Any]:
    if not path.startswith("v1/"):
        return data
    usage = data.get("usage")
    if not isinstance(usage, dict):
        return {}
    return {
        "prompt_eval_count": usage.get("prompt_tokens"),
        "eval_count": usage.get("completion_tokens"),
    }


def _openai_response_text(path: str, data: dict[str, Any]) -> str:
    choices = data.get("choices")
    if not isinstance(choices, list) or not choices or not isinstance(choices[0], dict):
        return ""
    choice = choices[0]
    if path == "v1/completions":
        text = choice.get("text", "")
        return text if isinstance(text, str) else ""
    message = choice.get("message", {})
    text = message.get("content", "") if isinstance(message, dict) else ""
    if isinstance(text, str):
        return text
    return json.dumps(text, ensure_ascii=False) if isinstance(text, list) else ""


def _stream_activity_item(path: str, line: bytes) -> tuple[dict[str, Any] | None, str]:
    payload = line
    if path.startswith("v1/"):
        if not line.startswith(b"data:"):
            return None, ""
        payload = line[5:].strip()
        if payload == b"[DONE]":
            return None, ""
    try:
        item = json.loads(payload)
    except (json.JSONDecodeError, UnicodeDecodeError):
        return None, ""
    if not isinstance(item, dict):
        return None, ""
    if path.startswith("v1/"):
        choices = item.get("choices")
        if not isinstance(choices, list) or not choices or not isinstance(choices[0], dict):
            return item, ""
        choice = choices[0]
        delta = choice.get("delta", {})
        if isinstance(delta, dict):
            part = delta.get("content", "")
            if isinstance(part, str):
                return item, part
        text = choice.get("text", "")
        return item, text if isinstance(text, str) else ""
    if path == "api/generate":
        part = item.get("response", "")
    else:
        message = item.get("message", {})
        part = message.get("content", "") if isinstance(message, dict) else ""
    return item, part if isinstance(part, str) else ""


def _record_proxy_activity(
    *,
    path: str,
    model: str,
    prompt: str,
    response_text: str,
    status: str,
    error: str | None,
    started_at: float,
    ollama_data: dict[str, Any] | None = None,
) -> None:
    data = ollama_data or {}
    _record_activity(
        model=model or "(unspecified)",
        prompt=prompt,
        response=response_text,
        status=status,
        error=error,
        wall_duration_ms=(time.perf_counter() - started_at) * 1000,
        ollama_data=data,
        client_ip=request.remote_addr if not _is_loopback_request() else None,
    )


@app.route("/ollama/", defaults={"ollama_path": ""}, methods=["GET", "POST", "DELETE"])
@app.route("/ollama/<path:ollama_path>", methods=["GET", "POST", "DELETE"])
def ollama_proxy(ollama_path: str):
    config = _get_ollama_config()
    if not config["gateway_enabled"]:
        return jsonify({"error": "The authenticated Ollama network proxy is disabled in dashboard settings."}), 403

    try:
        expected_key = (
            _get_secret("gateway_access_key")
            if config["gateway_auth_required"]
            else "private-lan-client"
        )
    except (OSError, RuntimeError, UnicodeDecodeError):
        app.logger.exception("Could not read the encrypted client access key")
        return jsonify({"error": "Could not read the saved client access key."}), 500
    provided_key = request.headers.get("Authorization", "")
    if not config["gateway_auth_required"]:
        provided_key = f"Bearer {expected_key}"
    if not expected_key or not hmac.compare_digest(provided_key, f"Bearer {expected_key}"):
        return jsonify({"error": "A valid dashboard client access key is required."}), 401

    if not config["gateway_auth_required"] and not _is_trusted_lan_request():
        return jsonify({"error": "Unauthenticated Ollama access is limited to trusted private networks."}), 403

    path = ollama_path.lstrip("/")
    allowed_paths = {
        "api/chat",
        "api/copy",
        "api/create",
        "api/delete",
        "api/embed",
        "api/embeddings",
        "api/generate",
        "api/ps",
        "api/pull",
        "api/push",
        "api/show",
        "api/tags",
        "api/version",
        "v1/chat/completions",
        "v1/completions",
        "v1/embeddings",
        "v1/models",
    }
    if path not in allowed_paths and not path.startswith("api/blobs/"):
        return jsonify({"error": "Only supported Ollama and OpenAI-compatible API routes can be proxied."}), 404

    body_bytes = request.get_data(cache=True) if request.method in {"POST", "DELETE"} else None
    body = request.get_json(silent=True)
    body = body if isinstance(body, dict) else {}
    model = body.get("model", "")
    model = model if isinstance(model, str) else ""
    monitored = path in {
        "api/generate",
        "api/chat",
        "v1/chat/completions",
        "v1/completions",
    }
    prompt = _activity_prompt(path, body) if monitored else ""
    started_at = time.perf_counter()

    try:
        upstream = requests.request(
            request.method,
            f"{config['base_url']}/{path}",
            params=request.args,
            data=body_bytes,
            headers=_copy_proxy_headers(),
            stream=True,
            timeout=(10, 600),
        )
    except requests.RequestException as exc:
        if monitored:
            _record_proxy_activity(
                path=path,
                model=model,
                prompt=prompt,
                response_text="",
                status="error",
                error=str(exc),
                started_at=started_at,
            )
        return jsonify({"error": f"Unable to reach Ollama at {config['base_url']}: {exc}"}), 502
    except (OSError, RuntimeError, UnicodeDecodeError):
        return jsonify({"error": "Could not prepare the configured upstream authorization."}), 500

    response_headers = {}
    for name in ("Content-Type", "Cache-Control"):
        if name in upstream.headers:
            response_headers[name] = upstream.headers[name]

    if upstream.status_code >= 400:
        content = upstream.content
        upstream.close()
        if monitored:
            _record_proxy_activity(
                path=path,
                model=model,
                prompt=prompt,
                response_text="",
                status="error",
                error=content.decode("utf-8", errors="replace")[:2000],
                started_at=started_at,
            )
        return Response(content, status=upstream.status_code, headers=response_headers)


    if not monitored:
        def relay_response():
            try:
                for chunk in upstream.iter_content(chunk_size=8192):
                    if chunk:
                        yield chunk
            finally:
                upstream.close()

        return Response(
            stream_with_context(relay_response()),
            status=upstream.status_code,
            headers=response_headers,
        )

    is_streaming = body.get("stream", True) is not False
    if is_streaming:
        def relay_generation():
            generated_parts = []
            final_data = {}
            try:
                for line in upstream.iter_lines():
                    if not line:
                        if path.startswith("v1/"):
                            yield b"\n"
                        continue
                    item, part = _stream_activity_item(path, line)
                    if part:
                        generated_parts.append(part)
                    if item and (item.get("done") or item.get("usage")):
                        final_data = _activity_data(path, item)
                    yield line + b"\n"
                _record_proxy_activity(
                    path=path,
                    model=model,
                    prompt=prompt,
                    response_text="".join(generated_parts),
                    status="success",
                    error=None,
                    started_at=started_at,
                    ollama_data=final_data,
                )
            except requests.RequestException as exc:
                _record_proxy_activity(
                    path=path,
                    model=model,
                    prompt=prompt,
                    response_text="".join(generated_parts),
                    status="error",
                    error=str(exc),
                    started_at=started_at,
                    ollama_data=final_data,
                )
                app.logger.exception("Ollama stream failed")
            finally:
                upstream.close()

        return Response(
            stream_with_context(relay_generation()),
            status=upstream.status_code,
            headers=response_headers,
        )

    try:
        content = upstream.content
        result = json.loads(content)
    except (requests.RequestException, json.JSONDecodeError, UnicodeDecodeError) as exc:
        upstream.close()
        _record_proxy_activity(
            path=path,
            model=model,
            prompt=prompt,
            response_text="",
            status="error",
            error=str(exc),
            started_at=started_at,
        )
        return jsonify({"error": "Ollama returned an unreadable generation response."}), 502
    finally:
        upstream.close()

    if path.startswith("v1/"):
        response_text = _openai_response_text(path, result)
        activity_data = _activity_data(path, result)
    elif path == "api/generate":
        response_text = result.get("response", "")
        activity_data = result
    else:
        message = result.get("message", {})
        response_text = message.get("content", "") if isinstance(message, dict) else ""
        activity_data = result
    _record_proxy_activity(
        path=path,
        model=result.get("model", model),
        prompt=prompt,
        response_text=response_text if isinstance(response_text, str) else "",
        status="success",
        error=None,
        started_at=started_at,
        ollama_data=activity_data,
    )
    return Response(content, status=upstream.status_code, headers=response_headers)


@app.route("/api/", defaults={"ollama_path": ""}, methods=["GET", "POST", "DELETE"], endpoint="ollama_client_proxy")
@app.route("/api/<path:ollama_path>", methods=["GET", "POST", "DELETE"], endpoint="ollama_client_proxy")
def ollama_client_proxy(ollama_path: str):
    if _is_loopback_request():
        return jsonify({"error": "The root API proxy is reserved for remote authenticated clients."}), 404
    return ollama_proxy(f"api/{ollama_path}" if ollama_path else "api")


@app.route("/v1/", defaults={"openai_path": ""}, methods=["GET", "POST", "DELETE"], endpoint="openai_client_proxy")
@app.route("/v1/<path:openai_path>", methods=["GET", "POST", "DELETE"], endpoint="openai_client_proxy")
def openai_client_proxy(openai_path: str):
    if _is_loopback_request():
        return jsonify({"error": "The root OpenAI-compatible proxy is reserved for remote authenticated clients."}), 404
    return ollama_proxy(f"v1/{openai_path}" if openai_path else "v1")


def _record_activity(
    *,
    model: str,
    prompt: str,
    response: str = "",
    status: str,
    error: str | None,
    wall_duration_ms: float,
    ollama_data: dict[str, Any] | None = None,
    client_ip: str | None = None,
) -> None:
    data = ollama_data or {}
    with _db_session() as connection:
        connection.execute(
            """
            INSERT INTO activity (
                created_at, model, prompt, response, status, error, wall_duration_ms,
                total_duration_ns, load_duration_ns, prompt_eval_count,
                prompt_eval_duration_ns, eval_count, eval_duration_ns, client_ip
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                datetime.now(timezone.utc).isoformat(),
                model,
                prompt,
                response,
                status,
                error,
                wall_duration_ms,
                data.get("total_duration"),
                data.get("load_duration"),
                data.get("prompt_eval_count"),
                data.get("prompt_eval_duration"),
                data.get("eval_count"),
                data.get("eval_duration"),
                client_ip,
            ),
        )


def _fetch_json(
    method: str,
    path: str,
    payload: dict[str, Any] | None = None,
    timeout: int = 20,
) -> tuple[dict[str, Any] | None, str | None, int]:
    config = _get_ollama_config()
    url = f"{config['base_url']}{path}"
    headers = {}
    try:
        api_key = _get_api_key()
    except (OSError, RuntimeError, UnicodeDecodeError):
        app.logger.exception("Could not read the encrypted Ollama API key")
        return None, "Could not read the saved API key. Re-enter it in Ollama settings.", 500
    if api_key:
        headers["Authorization"] = f"Bearer {api_key}"
    try:
        if method == "GET":
            response = requests.get(url, headers=headers, timeout=timeout)
        else:
            response = requests.post(url, json=payload, headers=headers, timeout=timeout)
    except requests.RequestException as exc:
        return None, f"Unable to reach Ollama at {config['base_url']}: {exc}", 503

    if response.status_code >= 400:
        body = response.text
        return None, body or f"Request failed with status {response.status_code}", response.status_code

    try:
        return response.json(), None, response.status_code
    except ValueError:
        return None, "Ollama returned a non-JSON response.", 500


_initialize_db()


@app.route("/")
def index() -> str:
    return render_template("index.html")


@app.route("/api/models")
def list_models():
    data, error, status = _fetch_json("GET", "/api/tags")
    if error:
        return jsonify({"error": error}), status

    models = [item["name"] for item in data.get("models", []) if item.get("name")]
    return jsonify({"models": models})


@app.route("/api/settings", methods=["GET", "POST"])
def ollama_settings():
    if request.method == "GET":
        config = _get_ollama_config()
        try:
            api_key_configured = bool(_get_api_key())
        except (OSError, RuntimeError, UnicodeDecodeError):
            app.logger.exception("Could not read the encrypted Ollama API key")
            return jsonify({"error": "Could not read the saved API key. Re-enter it in settings."}), 500
        return jsonify(
            {
                "baseUrl": config["base_url"],
                "keepAlive": config["keep_alive"],
                "apiKeyConfigured": api_key_configured,
                "gatewayEnabled": config["gateway_enabled"],
                "gatewayAuthRequired": config["gateway_auth_required"],
                "clientAccessKeyConfigured": _has_secret("gateway_access_key"),
                "clientGatewayUrl": _client_gateway_url(),
                "autoRefreshSeconds": _get_auto_refresh_seconds(),
            }
        )

    body = request.get_json(silent=True)
    if not isinstance(body, dict):
        return jsonify({"error": "Request body must be a JSON object."}), 400
    values, error = _validate_settings(body)
    if error:
        return jsonify({"error": error}), 400
    try:
        _save_ollama_config(values)
    except (OSError, RuntimeError):
        app.logger.exception("Could not save Ollama settings")
        return jsonify({"error": "Could not securely save the API key on this Windows account."}), 500
    return jsonify({"saved": True})


@app.route("/api/settings/refresh-interval", methods=["POST"])
def save_refresh_interval():
    if not _is_loopback_request():
        return jsonify({"error": "Settings are available only on the dashboard host."}), 403
    body = request.get_json(silent=True)
    if not isinstance(body, dict):
        return jsonify({"error": "Request body must be a JSON object."}), 400
    seconds = body.get("seconds")
    if (
        isinstance(seconds, bool)
        or not isinstance(seconds, int)
        or seconds not in {0, 5, 10, 15, 30, 60}
    ):
        return jsonify({"error": "Refresh interval must be 0, 5, 10, 15, 30, or 60 seconds."}), 400
    with _db_session() as connection:
        connection.execute(
            "INSERT INTO settings (name, value) VALUES ('auto_refresh_seconds', ?) "
            "ON CONFLICT(name) DO UPDATE SET value = excluded.value",
            (str(seconds),),
        )
    return jsonify({"saved": True, "autoRefreshSeconds": seconds})


@app.route("/api/settings/gateway-key", methods=["POST"])
def generate_gateway_key():
    access_key = secrets.token_urlsafe(32)
    try:
        protected_key = _protect_secret(access_key)
        with _db_session() as connection:
            connection.execute(
                "INSERT INTO secrets (name, value) VALUES ('gateway_access_key', ?) "
                "ON CONFLICT(name) DO UPDATE SET value = excluded.value",
                (protected_key,),
            )
    except (OSError, RuntimeError):
        app.logger.exception("Could not save the encrypted client access key")
        return jsonify({"error": "Could not securely save the client access key on this Windows account."}), 500
    return jsonify({"clientAccessKey": access_key})


@app.route("/api/generate", methods=["POST"])
def generate_text():
    if not _is_loopback_request():
        return ollama_proxy("api/generate")

    body = request.get_json(silent=True)
    if not isinstance(body, dict):
        return jsonify({"error": "Request body must be a JSON object."}), 400

    raw_prompt = body.get("prompt", "")
    raw_model = body.get("model", "")
    if not isinstance(raw_prompt, str) or not isinstance(raw_model, str):
        return jsonify({"error": "Prompt and model must be strings."}), 400

    prompt = raw_prompt.strip()
    model = raw_model.strip()

    if not prompt:
        return jsonify({"error": "Prompt is required."}), 400
    if not model:
        return jsonify({"error": "Choose an installed model before generating."}), 400

    config = _get_ollama_config()
    started_at = time.perf_counter()
    data, error, status = _fetch_json(
        "POST",
        "/api/generate",
        {"model": model, "prompt": prompt, "stream": False, "keep_alive": config["keep_alive"]},
        timeout=300,
    )
    wall_duration_ms = (time.perf_counter() - started_at) * 1000

    if error:
        _record_activity(
            model=model,
            prompt=prompt,
            status="error",
            error=error,
            wall_duration_ms=wall_duration_ms,
        )
        return jsonify({"error": error}), status

    response_text = data.get("response", "")
    response_model = data.get("model", model)
    _record_activity(
        model=response_model,
        prompt=prompt,
        response=response_text,
        status="success",
        error=None,
        wall_duration_ms=wall_duration_ms,
        ollama_data=data,
    )
    return jsonify(
        {
            "model": response_model,
            "prompt": prompt,
            "response": response_text,
            "done": data.get("done", True),
            "totalDuration": data.get("total_duration"),
            "loadDuration": data.get("load_duration"),
            "promptEvalCount": data.get("prompt_eval_count"),
            "promptEvalDuration": data.get("prompt_eval_duration"),
            "evalCount": data.get("eval_count"),
            "evalDuration": data.get("eval_duration"),
            "wallDurationMs": round(wall_duration_ms, 2),
        }
    )


@app.route("/api/health")
def health_check():
    config = _get_ollama_config()
    started_at = time.perf_counter()
    tags, error, status = _fetch_json("GET", "/api/tags", timeout=4)
    api_latency_ms = round((time.perf_counter() - started_at) * 1000, 2)

    if error:
        return jsonify(
            {
                "status": "offline",
                "endpoint": config["base_url"],
                "apiLatencyMs": api_latency_ms,
                "error": error,
                "models": [],
                "runningModels": [],
                "version": None,
            }
        ), status

    running, running_error, _ = _fetch_json("GET", "/api/ps", timeout=4)
    version, version_error, _ = _fetch_json("GET", "/api/version", timeout=4)
    models = tags.get("models", [])
    return jsonify(
        {
            "status": "online",
            "endpoint": config["base_url"],
            "apiLatencyMs": api_latency_ms,
            "models": [
                {
                    "name": item.get("name"),
                    "size": item.get("size"),
                    "modifiedAt": item.get("modified_at"),
                    "parameterSize": item.get("details", {}).get("parameter_size"),
                    "quantization": item.get("details", {}).get("quantization_level"),
                }
                for item in models
                if item.get("name")
            ],
            "runningModels": (running or {}).get("models", []),
            "version": (version or {}).get("version"),
            "warnings": [message for message in (running_error, version_error) if message],
        }
    )


def _nvidia_metrics() -> dict[str, Any]:
    executable = shutil.which("nvidia-smi")
    if not executable:
        return {
            "status": "unavailable",
            "error": "nvidia-smi was not found; NVIDIA GPU telemetry is unavailable.",
            "devices": [],
        }
    try:
        result = subprocess.run(
            [
                executable,
                "--query-gpu=name,utilization.gpu,memory.used,memory.total,temperature.gpu,power.draw",
                "--format=csv,noheader,nounits",
            ],
            capture_output=True,
            check=False,
            text=True,
            timeout=3,
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
        )
    except (OSError, subprocess.SubprocessError) as exc:
        app.logger.exception("Could not read NVIDIA GPU metrics")
        return {"status": "unavailable", "error": str(exc), "devices": []}
    if result.returncode:
        error = result.stderr.strip() or f"nvidia-smi exited with code {result.returncode}."
        return {"status": "unavailable", "error": error, "devices": []}

    def optional_number(value: str) -> float | None:
        value = value.strip()
        if not value or value.upper() in {"N/A", "[N/A]", "NOT SUPPORTED"}:
            return None
        return float(value)

    try:
        devices = []
        for row in csv.reader(result.stdout.splitlines()):
            if len(row) != 6:
                raise ValueError("nvidia-smi returned an unexpected GPU metrics row.")
            devices.append(
                {
                    "name": row[0].strip(),
                    "utilizationPercent": optional_number(row[1]),
                    "memoryUsedMb": optional_number(row[2]),
                    "memoryTotalMb": optional_number(row[3]),
                    "temperatureC": optional_number(row[4]),
                    "powerWatts": optional_number(row[5]),
                }
            )
    except ValueError as exc:
        app.logger.exception("Could not parse NVIDIA GPU metrics")
        return {"status": "unavailable", "error": str(exc), "devices": []}
    if not devices:
        return {"status": "unavailable", "error": "No NVIDIA GPUs were reported.", "devices": []}
    return {"status": "available", "error": None, "devices": devices}


def _disk_io_rates() -> tuple[float, float]:
    global _last_disk_io, _last_disk_io_at
    sampled_at = time.monotonic()
    with _telemetry_lock:
        current = psutil.disk_io_counters()
        previous = _last_disk_io
        elapsed = sampled_at - _last_disk_io_at
        _last_disk_io = current
        _last_disk_io_at = sampled_at
    if previous is None or current is None or elapsed <= 0:
        return 0.0, 0.0
    read_delta = max(0, current.read_bytes - previous.read_bytes)
    write_delta = max(0, current.write_bytes - previous.write_bytes)
    return read_delta / elapsed, write_delta / elapsed


@app.route("/api/system")
def system_metrics():
    try:
        cpu_percent = psutil.cpu_percent(interval=None)
        memory = psutil.virtual_memory()
        virtual_memory = psutil.swap_memory()
        system_volume = os.path.abspath(os.sep)
        disk_space = psutil.disk_usage(system_volume)
        read_bytes_per_second, write_bytes_per_second = _disk_io_rates()
    except (OSError, psutil.Error) as exc:
        app.logger.exception("Could not read host system metrics")
        return jsonify({"error": f"Could not read host system metrics: {exc}"}), 500

    return jsonify(
        {
            "sampledAt": datetime.now(timezone.utc).isoformat(),
            "cpu": {
                "percent": cpu_percent,
                "logicalCores": psutil.cpu_count(logical=True) or 0,
            },
            "memory": {
                "totalBytes": memory.total,
                "usedBytes": memory.used,
                "availableBytes": memory.available,
                "percent": memory.percent,
            },
            "virtualMemory": {
                "totalBytes": virtual_memory.total,
                "usedBytes": virtual_memory.used,
                "availableBytes": max(0, virtual_memory.total - virtual_memory.used),
                "percent": virtual_memory.percent,
            },
            "disk": {
                "volume": system_volume,
                "totalBytes": disk_space.total,
                "usedBytes": disk_space.used,
                "freeBytes": disk_space.free,
                "percent": disk_space.percent,
                "readBytesPerSecond": read_bytes_per_second,
                "writeBytesPerSecond": write_bytes_per_second,
            },
            "gpu": _nvidia_metrics(),
        }
    )


@app.route("/api/metrics")
def activity_metrics():
    with _db_session() as connection:
        summary = connection.execute(
            """
            SELECT
                COUNT(*) AS total_requests,
                SUM(CASE WHEN status = 'success' THEN 1 ELSE 0 END) AS successful_requests,
                SUM(CASE WHEN status = 'error' THEN 1 ELSE 0 END) AS failed_requests,
                AVG(CASE WHEN status = 'success' THEN wall_duration_ms END) AS avg_wall_duration_ms,
                SUM(CASE WHEN status = 'success' THEN prompt_eval_count ELSE 0 END) AS prompt_tokens,
                SUM(CASE WHEN status = 'success' THEN eval_count ELSE 0 END) AS generated_tokens,
                SUM(CASE WHEN status = 'success' THEN eval_duration_ns ELSE 0 END) AS eval_duration_ns,
                SUM(CASE WHEN status = 'success' THEN load_duration_ns ELSE 0 END) AS load_duration_ns
            FROM activity
            """
        ).fetchone()
        by_model = connection.execute(
            """
            SELECT model, COUNT(*) AS requests,
                   SUM(CASE WHEN status = 'success' THEN 1 ELSE 0 END) AS successful_requests,
                   SUM(CASE WHEN status = 'error' THEN 1 ELSE 0 END) AS failed_requests,
                   SUM(CASE WHEN status = 'success' THEN eval_count ELSE 0 END) AS generated_tokens
            FROM activity GROUP BY model ORDER BY requests DESC, model ASC
            """
        ).fetchall()
        recent = connection.execute(
            """
            SELECT id, created_at, model, prompt, response, status, error, wall_duration_ms,
                   total_duration_ns, load_duration_ns, prompt_eval_count,
                   prompt_eval_duration_ns, eval_count, eval_duration_ns, client_ip
            FROM activity ORDER BY id DESC LIMIT 25
            """
        ).fetchall()

    generated_tokens = summary["generated_tokens"] or 0
    eval_duration_ns = summary["eval_duration_ns"] or 0
    tokens_per_second = generated_tokens * 1_000_000_000 / eval_duration_ns if eval_duration_ns else 0

    def serialize_activity(row: sqlite3.Row) -> dict[str, Any]:
        item = dict(row)
        item["tokens_per_second"] = (
            item["eval_count"] * 1_000_000_000 / item["eval_duration_ns"]
            if item["eval_count"] and item["eval_duration_ns"]
            else 0
        )
        return item

    return jsonify(
        {
            "summary": {
                "totalRequests": summary["total_requests"] or 0,
                "successfulRequests": summary["successful_requests"] or 0,
                "failedRequests": summary["failed_requests"] or 0,
                "avgWallDurationMs": round(summary["avg_wall_duration_ms"] or 0, 2),
                "promptTokens": summary["prompt_tokens"] or 0,
                "generatedTokens": generated_tokens,
                "tokensPerSecond": round(tokens_per_second, 2),
                "loadDurationNs": summary["load_duration_ns"] or 0,
            },
            "byModel": [dict(row) for row in by_model],
            "recent": [serialize_activity(row) for row in recent],
        }
    )


if __name__ == "__main__":
    app.run(
        host="0.0.0.0",
        port=int(os.getenv("OLLAMA_DASHBOARD_PORT", "5000")),
        debug=False,
    )
