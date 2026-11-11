import logging
import os
import threading
import urllib.error
import urllib.request
import webbrowser
from pathlib import Path
from tkinter import Button, Label, StringVar, Tk, messagebox

from werkzeug.serving import make_server

from app import app

HOST = "127.0.0.1"
PORT = int(os.getenv("OLLAMA_DASHBOARD_PORT", "5000"))
DASHBOARD_URL = f"http://{HOST}:{PORT}/"


def _is_dashboard_running() -> bool:
    try:
        with urllib.request.urlopen(DASHBOARD_URL, timeout=2) as response:
            return response.status == 200 and b"<title>Ollama Dashboard</title>" in response.read(8192)
    except (OSError, urllib.error.URLError):
        return False


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
        webbrowser.open(DASHBOARD_URL)

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
            server = make_server(HOST, PORT, app, threaded=True)
            owns_server = True
            threading.Thread(target=server.serve_forever, daemon=True).start()
            status.set(f"Running locally at {DASHBOARD_URL}")
        open_dashboard()
    except OSError as exc:
        logging.exception("Could not start dashboard server")
        status.set("Could not start the dashboard.")
        messagebox.showerror(
            "Ollama Dashboard",
            f"Could not start the local server on port {PORT}.\n\n"
            "Close the application using this port, or set "
            "OLLAMA_DASHBOARD_PORT to another port and restart.\n\n"
            f"Details: {exc}",
            parent=root,
        )

    root.protocol("WM_DELETE_WINDOW", close_app)
    root.mainloop()


if __name__ == "__main__":
    main()
