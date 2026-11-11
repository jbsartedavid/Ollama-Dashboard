import os
import re
import sqlite3
import sys
import time
import ctypes
import ctypes.wintypes
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterator
from urllib.parse import urlsplit

import requests
from flask import Flask, jsonify, render_template, request

app = Flask(__name__)
ENV_OLLAMA_BASE_URL = os.getenv("OLLAMA_BASE_URL", "http://localhost:11434").rstrip("/")
ENV_DEFAULT_MODEL = os.getenv("OLLAMA_MODEL", "llama3.1")
if getattr(sys, "frozen", False):
    data_directory = Path(os.getenv("LOCALAPPDATA", Path.home())) / "OllamaDashboard"
    data_directory.mkdir(parents=True, exist_ok=True)
    default_database_path = data_directory / "dashboard.db"
else:
    default_database_path = Path(app.root_path) / "dashboard.db"
DATABASE_PATH = os.getenv("DASHBOARD_DATABASE", str(default_database_path))
_DPAPI_ENTROPY = b"OllamaDashboard.Settings.v1"


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
                eval_duration_ns INTEGER
            )
            """
        )
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


def _get_setting(name: str, fallback: str) -> str:
    with _db_session() as connection:
        row = connection.execute("SELECT value FROM settings WHERE name = ?", (name,)).fetchone()
    return row["value"] if row else fallback


def _get_api_key() -> str:
    with _db_session() as connection:
        row = connection.execute("SELECT value FROM secrets WHERE name = 'ollama_api_key'").fetchone()
    return _unprotect_secret(row["value"]) if row else ""


def _get_ollama_config() -> dict[str, str]:
    return {
        "base_url": _get_setting("base_url", ENV_OLLAMA_BASE_URL),
        "default_model": _get_setting("default_model", ENV_DEFAULT_MODEL),
        "keep_alive": _get_setting("keep_alive", "5m"),
    }


def _validate_settings(body: dict[str, Any]) -> tuple[dict[str, Any] | None, str | None]:
    base_url = body.get("baseUrl")
    default_model = body.get("defaultModel")
    keep_alive = body.get("keepAlive", "5m")
    api_key = body.get("apiKey", "")
    clear_api_key = body.get("clearApiKey", False)

    if not all(isinstance(value, str) for value in (base_url, default_model, keep_alive, api_key)):
        return None, "Server URL, default model, keep-alive, and API key must be strings."
    if not isinstance(clear_api_key, bool):
        return None, "clearApiKey must be a boolean."

    base_url = base_url.strip().rstrip("/")
    default_model = default_model.strip()
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
    if not default_model or len(default_model) > 256 or any(char.isspace() for char in default_model):
        return None, "Default model is required and must not contain whitespace."
    if not re.fullmatch(r"(?:-1|0|[1-9]\d*(?:ns|us|µs|ms|s|m|h)?)", keep_alive):
        return None, "Keep-alive must be -1, 0, or a duration such as 5m or 30s."
    if len(api_key) > 4096:
        return None, "API key is too long."
    if api_key and sys.platform != "win32":
        return None, "API key storage is supported on Windows using the current user's DPAPI."

    return {
        "base_url": base_url,
        "default_model": default_model,
        "keep_alive": keep_alive,
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
                ("default_model", values["default_model"]),
                ("keep_alive", values["keep_alive"]),
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


def _record_activity(
    *,
    model: str,
    prompt: str,
    response: str = "",
    status: str,
    error: str | None,
    wall_duration_ms: float,
    ollama_data: dict[str, Any] | None = None,
) -> None:
    data = ollama_data or {}
    with _db_session() as connection:
        connection.execute(
            """
            INSERT INTO activity (
                created_at, model, prompt, response, status, error, wall_duration_ms,
                total_duration_ns, load_duration_ns, prompt_eval_count,
                prompt_eval_duration_ns, eval_count, eval_duration_ns
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
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
    preferred_model = _get_ollama_config()["default_model"]
    default_model = preferred_model if preferred_model in models else (models[0] if models else preferred_model)
    return jsonify({"models": models, "defaultModel": default_model})


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
                "defaultModel": config["default_model"],
                "keepAlive": config["keep_alive"],
                "apiKeyConfigured": api_key_configured,
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


@app.route("/api/generate", methods=["POST"])
def generate_text():
    body = request.get_json(silent=True)
    if not isinstance(body, dict):
        return jsonify({"error": "Request body must be a JSON object."}), 400

    raw_prompt = body.get("prompt", "")
    config = _get_ollama_config()
    raw_model = body.get("model", config["default_model"])
    if not isinstance(raw_prompt, str) or not isinstance(raw_model, str):
        return jsonify({"error": "Prompt and model must be strings."}), 400

    prompt = raw_prompt.strip()
    model = raw_model.strip() or DEFAULT_MODEL

    if not prompt:
        return jsonify({"error": "Prompt is required."}), 400

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
                   prompt_eval_duration_ns, eval_count, eval_duration_ns
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
    app.run(host="127.0.0.1", port=5000, debug=False)
