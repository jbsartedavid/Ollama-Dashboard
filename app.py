import os
import sqlite3
import time
from contextlib import contextmanager
from datetime import datetime, timezone
from typing import Any, Iterator

import requests
from flask import Flask, jsonify, render_template, request

app = Flask(__name__)
OLLAMA_BASE_URL = os.getenv("OLLAMA_BASE_URL", "http://localhost:11434").rstrip("/")
DEFAULT_MODEL = os.getenv("OLLAMA_MODEL", "llama3.1")
DATABASE_PATH = os.getenv("DASHBOARD_DATABASE", os.path.join(app.root_path, "dashboard.db"))


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
    url = f"{OLLAMA_BASE_URL}{path}"
    try:
        if method == "GET":
            response = requests.get(url, timeout=timeout)
        else:
            response = requests.post(url, json=payload, timeout=timeout)
    except requests.RequestException as exc:
        return None, f"Unable to reach Ollama at {OLLAMA_BASE_URL}: {exc}", 503

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
    default_model = DEFAULT_MODEL if DEFAULT_MODEL in models else (models[0] if models else DEFAULT_MODEL)
    return jsonify({"models": models, "defaultModel": default_model})


@app.route("/api/generate", methods=["POST"])
def generate_text():
    body = request.get_json(silent=True)
    if not isinstance(body, dict):
        return jsonify({"error": "Request body must be a JSON object."}), 400

    raw_prompt = body.get("prompt", "")
    raw_model = body.get("model", DEFAULT_MODEL)
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
        {"model": model, "prompt": prompt, "stream": False},
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
    started_at = time.perf_counter()
    tags, error, status = _fetch_json("GET", "/api/tags", timeout=4)
    api_latency_ms = round((time.perf_counter() - started_at) * 1000, 2)

    if error:
        return jsonify(
            {
                "status": "offline",
                "endpoint": OLLAMA_BASE_URL,
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
            "endpoint": OLLAMA_BASE_URL,
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
