<div align="center">

# Ollama Dashboard

**A local observability console for your Ollama server and all its models.**

Monitor the Ollama server, inventory, and loaded models together. Compare request activity by model without setting a preferred model.

[![Python](https://img.shields.io/badge/Python-3.10%2B-3776AB?logo=python&logoColor=white)](https://www.python.org/)
[![Flask](https://img.shields.io/badge/Flask-3.1-000000?logo=flask&logoColor=white)](https://flask.palletsprojects.com/)
[![Ollama](https://img.shields.io/badge/Powered%20by-Ollama-111827)](https://ollama.com/)

</div>

---

## At a glance

| All-model activity | Ollama server |
| --- | --- |
| Request totals and success/failure counts | Connection status and API latency |
| Recent prompts, responses, and errors | Ollama version and installed model inventory |
| Average dashboard response time | Currently loaded models and their memory use |
| Prompt and generated token counts | Configurable status refresh (5–60 seconds or off) |
| Tokens per second, per-model usage, and optional retrieved conversation memory | CPU, physical/virtual memory, GPU/VRAM, disk capacity and I/O |

Activity is stored in a local SQLite database and remains available after restarting the dashboard. The dashboard can record its own generations and client Ollama/OpenAI-compatible generation and chat requests routed through its proxy. Direct connections to an Ollama process bypass the dashboard and are not recorded.

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

Double-click **Ollama Dashboard** on your Desktop to start the app and open it in your default browser. The app includes a small control window with **Open dashboard** and **Stop** buttons. If a compatible dashboard is already running anywhere in the configured port range, it opens that instance. If Ollama or another app already owns the configured dashboard port, the dashboard skips busy ports and starts on the next available one; you do not need to stop Ollama. The selected port is shown in the control window and dashboard. The shortcut does not configure automatic startup when you sign in to Windows.

The executable keeps activity data and diagnostic logs under `%LOCALAPPDATA%\OllamaDashboard`; it does not write into the installation folder.

To rebuild from source, create `.venv`, install `requirements-build.txt`, and run `.\build_windows.ps1`.

## Metrics and data

- **Recent activity:** the 25 most recent requests, including their prompts, responses, model, time, and outcome.
- **Request summary:** all-time request counts, successful and failed requests, and average end-to-end dashboard latency.
- **Generation metrics:** prompt tokens, generated tokens, and generation speed when Ollama reports token counts and timing.
- **Model breakdown:** request, generated-token, and failure counts for each model used.
- **Server status:** Ollama API reachability, API latency, server version, installed models, and currently loaded models.
- **Host resources:** CPU utilization, physical memory, pagefile/swap use, system-drive capacity, disk read/write throughput, and NVIDIA GPU utilization, VRAM, temperature, and power when `nvidia-smi` is available. These are sampled from the computer running the dashboard; NVIDIA-only metrics show an explicit unavailable status when unsupported.
- **Connection settings:** configure the Ollama server URL, model keep-alive duration, and optional upstream bearer API key in the dashboard. No model is preferred; all installed and loaded models are monitored together. On Windows, keys are encrypted with DPAPI for the current user.
- **Remote clients:** when the dashboard owns port 11434, existing LAN clients can use the same Ollama URL without a dashboard key. Proxied `/api/generate`, `/api/chat`, `/v1/chat/completions`, and `/v1/completions` requests are recorded with the client IP, including streamed responses.
- **Conversation memory:** optional local storage captures successful dashboard and monitored client prompts/responses. Relevant earlier turns are added as reference context to later supported requests. Memory is off by default, can be isolated by exact model name, and prunes the oldest turns to honor its configurable text-size limit.
- **Local history:** source runs save request data in `dashboard.db` next to the app. The packaged Windows app stores it under `%LOCALAPPDATA%\OllamaDashboard`. Choose another location with `DASHBOARD_DATABASE`.

Ollama status, activity, and host metrics use the refresh interval selected in the dashboard header (5, 10, 15, 30, or 60 seconds, or off). Host metrics describe the machine running this dashboard, not a remote Ollama host.

## Monitor clients on your LAN

The desktop app serves its management dashboard only on the host's loopback interface. When it owns port 11434, the **Ollama-compatible monitoring proxy** accepts requests from trusted private networks without requiring client endpoint or key changes. The dashboard UI and settings remain inaccessible from other computers. Unauthenticated access is restricted to RFC1918, IPv6 ULA, and link-local addresses. Direct connections to a separate Ollama process bypass the dashboard and are not recorded.

### Keep clients on the default port 11434

To keep existing clients pointed at `http://<server-name-or-IP>:11434`, the dashboard must own port 11434 and Ollama must move to a loopback-only backend port. The bundled script sets the current Windows user's Ollama backend to `127.0.0.1:11435` and dashboard listener to `11434`; it does not stop or restart running apps:

```powershell
.\configure_monitored_ollama_ports.ps1
```

Confirm the script's change. This machine is using standalone `ollama.exe serve` on `127.0.0.1:11435`; the Ollama Desktop app ignores the custom backend setting and must not own port 11434 while the dashboard proxy is running. Start the standalone Ollama server, then the dashboard. The dashboard's **Ollama address for clients** should show port 11434. Existing Ollama clients can keep the same host/IP, port, and API routes; no dashboard key or client changes are needed on trusted private networks.

On the Ollama host, allow inbound port 11434 on the **Private** firewall profile and local subnet:

```powershell
New-NetFirewallRule -DisplayName "Ollama Dashboard LAN Proxy" `
  -Direction Inbound -Action Allow -Protocol TCP -LocalPort 11434 `
  -RemoteAddress LocalSubnet -Profile Private
```

Port takeover automatically enables the proxy. OmniRoute can use its OpenAI-compatible base URL `http://<server-name-or-IP>:11434/v1` or Ollama API base URL `http://<server-name-or-IP>:11434`, depending on the selected provider. The proxy supports `/v1/chat/completions` and `/v1/completions` as well as Ollama `/api/chat` and `/api/generate`.

### Use the dashboard's default gateway port

If Ollama owns port 11434, the dashboard automatically uses the next available port in its configured range (for example 11435 or 11436). Use the displayed **Ollama address for clients** in clients you want monitored and allow that port through the Private-profile/local-subnet firewall. Requests sent directly to Ollama on port 11434 still work normally, but bypass dashboard monitoring; the same TCP port cannot be shared by both Ollama and the dashboard. The dashboard control window shows the selected address.

Example Ollama API request from a client (for the port-11434 takeover setup):

```powershell
$body = @{ model = "qwen2.5-coder:latest"; prompt = "Say hello"; stream = $false } | ConvertTo-Json
Invoke-RestMethod -Uri "http://<server-name-or-IP>:11434/api/generate" `
  -Method Post -ContentType "application/json" -Body $body
```

Replace the example address and model with the values for your setup. Clients do not need a dashboard-specific Authorization header. The proxy forwards supported Ollama and OpenAI-compatible routes, but only generation and chat requests are added to response history.

**Network security:** access is limited to private and link-local source IPs, but still exposes model inference to those networks. Restrict port 11434 to a trusted LAN or VPN and never expose it directly to the public internet.

## Conversation memory

Enable **Save and use conversation memory** in the dashboard to persist successful dashboard prompts and monitored Ollama chat/generation requests. For later requests, a local SQLite full-text index searches saved prompt/response excerpts and adds up to three relevant results as reference context; no extra model call or external service is used. Context retrieval supports Ollama `/api/chat` and `/api/generate`, OpenAI-compatible `/v1/chat/completions` and text `/v1/completions`, plus dashboard generations.

Memory is disabled by default. When enabled, the default folder is `memory` next to the source app, or `%LOCALAPPDATA%\OllamaDashboard\memory` for the packaged app. Set another absolute folder and choose a **1–4096 MB** database-size limit (default 512 MB). The limit includes the SQLite full-text index; oldest turns are pruned first. The dashboard displays usage and storage errors, and **Clear saved memories** deletes the memory records in the selected folder. Changing folders selects a different database and leaves the previous one untouched.

**Keep memory separate for each model** is enabled by default so models only retrieve memories saved under their exact model name. Disable it to share retrieval across models. Memories include full prompts and responses; store them on a trusted local disk and disable the feature if you do not want monitored client conversations persisted.

## Configuration

| Variable | Default | Description |
| --- | --- | --- |
| `OLLAMA_BASE_URL` | `http://localhost:11434` | Base URL for the Ollama API |
| `OLLAMA_HOST` | Ollama default | Ollama listener; the port setup script sets it to `127.0.0.1:11435` |
| `OLLAMA_DASHBOARD_PORT` | `5000` | Dashboard and proxy listener port; the port setup script sets it to `11434` |
| `DASHBOARD_DATABASE` | Project directory for source; `%LOCALAPPDATA%\OllamaDashboard` for the packaged app | SQLite file used for request history |

These environment variables provide initial defaults; changes made in the dashboard are saved locally and take precedence. Configure remote endpoints with HTTPS. An API key is optional and is sent as an `Authorization: Bearer` header only to the configured Ollama server. Leave the key field blank to keep the saved key, or select **Remove saved API key** to delete it.

Example PowerShell configuration:

```powershell
$env:OLLAMA_BASE_URL = "http://localhost:11434"
python app.py
```

## API

The Flask app serves the dashboard and exposes a small local API:

| Route | Method | Purpose |
| --- | --- | --- |
| `/api/health` | `GET` | Ollama status, latency, version, and model inventory |
| `/api/system` | `GET` | Local host CPU, memory, disk, and GPU metrics |
| `/api/models` | `GET` | Installed model names |
| `/api/settings` | `GET`, `POST` | Read or update the Ollama server connection settings |
| `/api/settings/refresh-interval` | `POST` | Save the dashboard's automatic refresh interval |
| `/api/memory/settings` | `GET`, `POST` | Read or update local conversation-memory settings and usage |
| `/api/memory/clear` | `POST` | Delete all memories in the selected memory store |
| `/api/metrics` | `GET` | Activity totals, per-model usage, and recent requests |
| `/api/generate` | `POST` | Generate a response and record its metrics |
| `/api/*`, `/ollama/*`, `/v1/*` (from LAN) | Ollama methods | Private-LAN Ollama/OpenAI-compatible proxy; optional bearer key; management APIs remain loopback-only |

## Troubleshooting

- **Ollama offline:** start Ollama and confirm its API is reachable at `OLLAMA_BASE_URL`.
- **No models available:** pull a model with `ollama pull <model-name>`, then refresh the dashboard.
- **Client requests do not appear:** direct calls to the Ollama backend bypass monitoring. For clients to keep using port 11434, move Ollama to port 11435 and run the dashboard proxy on port 11434. No dashboard key is required for trusted private-network clients.
- **GPU metrics unavailable:** NVIDIA metrics require a working NVIDIA driver and `nvidia-smi` on the dashboard host. CPU, memory, pagefile, and disk metrics remain available independently.
- **Shortcut does not start:** run `.\start_dashboard.ps1` directly to see any PowerShell error. Packaged-app diagnostics are written to `%LOCALAPPDATA%\OllamaDashboard\desktop.log`.
- **Executable build fails:** make sure `.venv` is active and install build dependencies with `python -m pip install -r requirements-build.txt`.
- **Ollama and dashboard conflict on port 11434:** the desktop app now leaves Ollama running and starts the dashboard on the next available port. Keep Ollama clients on port 11434 for direct access, or change monitored clients to the dashboard's displayed address. Both apps cannot listen on the same address and port at once.
- **Dashboard port range is full:** the desktop app checks the configured port and the next 20 ports. Set `OLLAMA_DASHBOARD_PORT` to a free port with available ports nearby, then restart the dashboard.
- **Client cannot connect:** confirm the displayed Ollama address for clients and port, allow the app on the host's Private firewall profile, and keep the Ollama Desktop app from reclaiming port 11434. Clients must connect from a private/link-local address; dashboard management APIs intentionally reject LAN clients.

## Project layout

```text
.
├── app.py
├── desktop_app.py
├── build_windows.ps1
├── create_desktop_shortcut.ps1
├── configure_monitored_ollama_ports.ps1
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
