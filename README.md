<div align="center">

# Ollama Dashboard

**A local observability console for your Ollama server and model activity.**

Track requests, inspect responses, compare model usage, and check server health from one clean dashboard.

[![Python](https://img.shields.io/badge/Python-3.10%2B-3776AB?logo=python&logoColor=white)](https://www.python.org/)
[![Flask](https://img.shields.io/badge/Flask-3.1-000000?logo=flask&logoColor=white)](https://flask.palletsprojects.com/)
[![Ollama](https://img.shields.io/badge/Powered%20by-Ollama-111827)](https://ollama.com/)

</div>

---

## At a glance

| Model activity | Ollama server |
| --- | --- |
| Request totals and success/failure counts | Connection status and API latency |
| Recent prompts, responses, and errors | Ollama version and installed model inventory |
| Average dashboard response time | Currently loaded models and their memory use |
| Prompt and generated token counts | Automatic status refresh every 10 seconds |
| Tokens per second and per-model usage | |

Activity is stored in a local SQLite database and remains available after restarting the dashboard. The dashboard only measures requests sent **through the dashboard**; it cannot see requests made directly to Ollama by other applications.

## Get started

### 1. Prerequisites

- Python 3.10 or later
- [Ollama](https://ollama.com/download) installed and running
- At least one model pulled, for example:

  ```powershell
  ollama pull qwen2.5-coder
  ```

### 2. Install and start

From the project directory:

```powershell
python -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install -r requirements.txt
python app.py
```

Open **http://127.0.0.1:5000** in your browser.

### 3. Build the Windows app (optional)

From PowerShell in the project directory, run:

```powershell
.\build_windows.ps1
```

The standalone, windowed executable is created at `release\OllamaDashboard.exe`. It includes the dashboard and its Python dependencies; Python does not need to be installed on a computer that runs the executable. Ollama must still be installed and running.

To add a Desktop shortcut to the executable, run:

```powershell
.\create_desktop_shortcut.ps1
```

Double-click **Ollama Dashboard** on your Desktop to start the app and open it in your default browser. The app includes a small control window with **Open dashboard** and **Stop** buttons. If the dashboard is already running, it opens the existing instance. The shortcut does not configure automatic startup when you sign in to Windows.

The executable keeps activity data and diagnostic logs under `%LOCALAPPDATA%\OllamaDashboard`; it does not write into the installation folder.

To rebuild from source, create `.venv`, install `requirements-build.txt`, and run `.\build_windows.ps1`.

## Metrics and data

- **Recent activity:** the 25 most recent requests, including their prompts, responses, model, time, and outcome.
- **Request summary:** all-time request counts, successful and failed requests, and average end-to-end dashboard latency.
- **Generation metrics:** prompt tokens, generated tokens, and generation speed when Ollama reports token counts and timing.
- **Model breakdown:** request, generated-token, and failure counts for each model used.
- **Server status:** Ollama API reachability, API latency, server version, installed models, and currently loaded models.
- **Local history:** source runs save request data in `dashboard.db` next to the app. The packaged Windows app stores it under `%LOCALAPPDATA%\OllamaDashboard`. Choose another location with `DASHBOARD_DATABASE`.

The Ollama server status is refreshed every 10 seconds. This application does not collect host-wide CPU, GPU, or system memory metrics.

## Configuration

| Variable | Default | Description |
| --- | --- | --- |
| `OLLAMA_BASE_URL` | `http://localhost:11434` | Base URL for the Ollama API |
| `OLLAMA_MODEL` | `llama3.1` | Preferred default model when it is installed |
| `DASHBOARD_DATABASE` | Project directory for source; `%LOCALAPPDATA%\OllamaDashboard` for the packaged app | SQLite file used for request history |

Example PowerShell configuration:

```powershell
$env:OLLAMA_BASE_URL = "http://localhost:11434"
$env:OLLAMA_MODEL = "qwen2.5-coder:latest"
python app.py
```

## API

The Flask app serves the dashboard and exposes a small local API:

| Route | Method | Purpose |
| --- | --- | --- |
| `/api/health` | `GET` | Ollama status, latency, version, and model inventory |
| `/api/models` | `GET` | Installed model names and the preferred default |
| `/api/metrics` | `GET` | Activity totals, per-model usage, and recent requests |
| `/api/generate` | `POST` | Generate a response and record its metrics |

## Troubleshooting

- **Ollama offline:** start Ollama and confirm its API is reachable at `OLLAMA_BASE_URL`.
- **No models available:** pull a model with `ollama pull <model-name>`, then refresh the dashboard.
- **Shortcut does not start:** run `.\start_dashboard.ps1` directly to see any PowerShell error. Packaged-app diagnostics are written to `%LOCALAPPDATA%\OllamaDashboard\desktop.log`.
- **Executable build fails:** make sure `.venv` is active and install build dependencies with `python -m pip install -r requirements-build.txt`.
- **Port 5000 is already in use:** stop the other service using that port before starting this dashboard.

## Project layout

```text
.
├── app.py
├── desktop_app.py
├── build_windows.ps1
├── create_desktop_shortcut.ps1
├── start_dashboard.ps1
├── requirements.txt
├── requirements-build.txt
├── release/
│   └── OllamaDashboard.exe
├── static/
│   ├── app.js
│   └── style.css
└── templates/
    └── index.html
```
