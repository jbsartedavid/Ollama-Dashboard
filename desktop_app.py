import logging
import json
import os
import socket
import threading
import urllib.error
import urllib.request
import webbrowser
from pathlib import Path
from tkinter import Button, Label, StringVar, Tk, messagebox

from werkzeug.serving import make_server

from app import app

HOST = "127.0.0.1"
BIND_HOST = "0.0.0.0"


def _configured_port() -> int:
    value = os.getenv("OLLAMA_DASHBOARD_PORT")
    if value is None and os.name == "nt":
        import winreg

        try:
            with winreg.OpenKey(winreg.HKEY_CURRENT_USER, "Environment") as key:
                value, _ = winreg.QueryValueEx(key, "OLLAMA_DASHBOARD_PORT")
        except OSError:
            value = None
    return int(value or "5000")


PORT = _configured_port()
DASHBOARD_URL = f"http://{HOST}:{PORT}/"


def _is_dashboard_running() -> bool:
    try:
        with urllib.request.urlopen(DASHBOARD_URL, timeout=2) as response:
            is_dashboard = response.status == 200 and b"<title>Ollama Dashboard</title>" in response.read(8192)
        if not is_dashboard:
            return False
        with urllib.request.urlopen(f"{DASHBOARD_URL}api/settings", timeout=2) as response:
            settings = json.loads(response.read().decode("utf-8"))
            return (
                response.status == 200
                and "clientGatewayUrl" in settings
                and "gatewayEnabled" in settings
            )
    except (OSError, urllib.error.URLError):
        return False


def _find_available_port() -> int:
    last_port = min(PORT + 20, 65535)
    for port in range(PORT, last_port + 1):
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as probe:
            probe.settimeout(0.2)
            if probe.connect_ex((HOST, port)) == 0:
                if port == PORT == 11434:
                    raise OSError(
                        "Port 11434 is reserved for the Ollama monitoring proxy but is already in use. "
                        "Stop the Ollama Desktop server before starting the dashboard."
                    )
                continue
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as candidate:
            try:
                candidate.bind((BIND_HOST, port))
                return port
            except OSError:
                continue
    raise OSError(f"No free local port found between {PORT} and {last_port}.")


def main() -> None:
    data_directory = Path(os.getenv("LOCALAPPDATA", Path.home())) / "OllamaDashboard"
    data_directory.mkdir(parents=True, exist_ok=True)
    logging.basicConfig(
        filename=data_directory / "desktop.log",
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(message)s",
    )

    root = Tk()
    root.title("Ollama Dashboard")
    root.geometry("430x230")
    root.resizable(False, False)
    root.configure(bg="#0b1020")

    status = StringVar(value="Starting dashboard…")
    server = None
    owns_server = False
    dashboard_url = DASHBOARD_URL

    Label(
        root,
        text="Ollama Dashboard",
        bg="#0b1020",
        fg="#e9edf9",
        font=("Segoe UI", 18, "bold"),
    ).pack(pady=(24, 4))
    Label(
        root,
        text="Your local model activity monitor",
        bg="#0b1020",
        fg="#9aa8be",
        font=("Segoe UI", 10),
    ).pack()
    Label(
        root,
        textvariable=status,
        bg="#0b1020",
        fg="#a3e635",
        font=("Segoe UI", 10),
    ).pack(pady=(16, 12))

    buttons = Label(root, bg="#0b1020")
    buttons.pack()

    def open_dashboard() -> None:
        webbrowser.open(dashboard_url)

    def close_app() -> None:
        if owns_server and server is not None:
            server.shutdown()
            server.server_close()
        root.destroy()

    Button(
        buttons,
        text="Open dashboard",
        command=open_dashboard,
        bg="#a3e635",
        fg="#10170a",
        activebackground="#b7f35c",
        relief="flat",
        padx=18,
        pady=8,
        font=("Segoe UI", 10, "bold"),
    ).pack(side="left", padx=6)
    Button(
        buttons,
        text="Stop",
        command=close_app,
        bg="#1b2740",
        fg="#e9edf9",
        activebackground="#293957",
        relief="flat",
        padx=18,
        pady=8,
        font=("Segoe UI", 10),
    ).pack(side="left", padx=6)

    try:
        if _is_dashboard_running():
            status.set("Dashboard is already running.")
        else:
            selected_port = _find_available_port()
            dashboard_url = f"http://{HOST}:{selected_port}/"
            server = make_server(BIND_HOST, selected_port, app, threaded=True)
            owns_server = True
            threading.Thread(target=server.serve_forever, daemon=True).start()
            status.set(f"Running locally at {dashboard_url}")
        open_dashboard()
    except OSError as exc:
        logging.exception("Could not start dashboard server")
        status.set("Could not start the dashboard.")
        messagebox.showerror(
            "Ollama Dashboard",
            "Could not start the local dashboard server.\n\n"
            "Close other local services or set OLLAMA_DASHBOARD_PORT to an available port.\n\n"
            f"Details: {exc}",
            parent=root,
        )

    root.protocol("WM_DELETE_WINDOW", close_app)
    root.mainloop()


if __name__ == "__main__":
    main()
