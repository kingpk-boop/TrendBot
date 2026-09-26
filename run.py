"""Start TrendBot: the web app plus every bot that was running last time.

    py -3 run.py                  # this computer only, opens your browser
    py -3 run.py --host 0.0.0.0   # also reachable from your phone on the same Wi-Fi
                                  # (needs BOT_UI_PASSWORD in .env)
"""
import argparse
import os
import socket
import sys
import threading
import webbrowser
from pathlib import Path

ROOT = Path(__file__).resolve().parent
os.chdir(ROOT)
sys.path.insert(0, str(ROOT))

from app.config import load_dotenv  # noqa: E402

LOCAL = {"127.0.0.1", "localhost", "::1"}


def lan_ip() -> str | None:
    """This computer's address on the home network (no traffic is actually sent)."""
    try:
        with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as s:
            s.connect(("10.255.255.255", 1))
            return s.getsockname()[0]
    except OSError:
        return None


def port_in_use(host: str, port: int) -> bool:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.settimeout(0.5)
        return s.connect_ex(("127.0.0.1" if host in ("0.0.0.0", "localhost") else host, port)) == 0


def main() -> int:
    ap = argparse.ArgumentParser(description="Run the TrendBot web app and bots.")
    ap.add_argument("--host", default="127.0.0.1",
                    help="address to listen on (default 127.0.0.1 = this computer only; 0.0.0.0 = your network)")
    ap.add_argument("--port", type=int, default=int(os.environ.get("PORT") or 8765),
                    help="port (default 8765, or $PORT when a cloud host sets it)")
    ap.add_argument("--no-browser", action="store_true", help="don't open a browser window")
    args = ap.parse_args()

    load_dotenv()
    if args.host not in LOCAL and not os.environ.get("BOT_UI_PASSWORD", "").strip():
        print("\nRefusing to open TrendBot to your network without a password.\n"
              "Add a line like  BOT_UI_PASSWORD=pick-a-long-password  to the .env file, then try again.\n")
        return 2

    local_url = f"http://localhost:{args.port}/"
    if port_in_use(args.host, args.port):
        print(f"\nSomething is already using port {args.port} - TrendBot is probably already running.")
        print(f"Opening {local_url}  (close the other TrendBot window first if you want to restart it,")
        print(f"or pick another port with --port).\n")
        if not args.no_browser:
            webbrowser.open(local_url)
        return 1

    print("\n  TrendBot is starting.")
    print(f"  On this computer:  {local_url}")
    if args.host not in LOCAL:
        ip = lan_ip()
        if ip:
            print(f"  On your phone:     http://{ip}:{args.port}/   (same Wi-Fi, log in with BOT_UI_PASSWORD)")
    print("  Keep this window open - closing it stops the bots.  Ctrl+C to quit.\n")

    if not args.no_browser:
        threading.Timer(1.5, webbrowser.open, args=(local_url,)).start()

    import uvicorn
    uvicorn.run("app.server:app", host=args.host, port=args.port, log_level="warning")
    return 0


if __name__ == "__main__":
    sys.exit(main())
