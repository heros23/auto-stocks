#!/usr/bin/env python3
import argparse
import json
import threading
import time
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.request import Request, urlopen


class WatchlistRelay:
    def __init__(self, upstream_base: str, poll_seconds: int):
        self.upstream_base = upstream_base.rstrip("/")
        self.poll_seconds = poll_seconds
        self.lock = threading.Lock()
        self.sequence = 0
        self.payload = {"items": [], "source": "bootstrap"}
        self.error = None

    def poll_once(self) -> None:
        request = Request(f"{self.upstream_base}/api/watchlist/update", data=b"", method="POST")
        with urlopen(request, timeout=60) as response:
            data = json.loads(response.read().decode("utf-8"))
        with self.lock:
            self.sequence += 1
            self.payload = {
                "sequence": self.sequence,
                "published_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
                "items": data.get("items", []),
            }
            self.error = None

    def loop(self) -> None:
        while True:
            try:
                self.poll_once()
            except Exception as exc:  # noqa: BLE001
                with self.lock:
                    self.error = str(exc)
            time.sleep(self.poll_seconds)

    def snapshot(self) -> dict:
        with self.lock:
            return {"payload": self.payload, "error": self.error, "poll_seconds": self.poll_seconds}


class RelayHandler(BaseHTTPRequestHandler):
    relay: WatchlistRelay | None = None

    def _write_json(self, body: dict, status: int = HTTPStatus.OK) -> None:
        encoded = json.dumps(body, ensure_ascii=False).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Cache-Control", "no-store")
        self.send_header("Content-Length", str(len(encoded)))
        self.end_headers()
        self.wfile.write(encoded)

    def do_GET(self) -> None:  # noqa: N802
        if self.path == "/health":
            self._write_json({"ok": True, "relay": self.relay.snapshot() if self.relay else None})
            return

        if self.path != "/events":
            self._write_json({"error": "not_found"}, status=HTTPStatus.NOT_FOUND)
            return

        self.send_response(HTTPStatus.OK)
        self.send_header("Content-Type", "text/event-stream; charset=utf-8")
        self.send_header("Cache-Control", "no-cache, no-store, must-revalidate")
        self.send_header("Connection", "keep-alive")
        self.send_header("Access-Control-Allow-Origin", "*")
        self.end_headers()

        last_sequence = -1
        while True:
            relay = self.relay.snapshot() if self.relay else {"payload": {}, "error": "relay_not_ready"}
            payload = relay.get("payload", {})
            sequence = payload.get("sequence", -1)
            if sequence != last_sequence:
                last_sequence = sequence
                frame = f"event: watchlist\ndata: {json.dumps(relay, ensure_ascii=False)}\n\n"
                self.wfile.write(frame.encode("utf-8"))
                self.wfile.flush()
            time.sleep(1)

    def log_message(self, format: str, *args) -> None:  # noqa: A003
        return


def main() -> int:
    parser = argparse.ArgumentParser(description="Relay watchlist polling into a simple SSE stream.")
    parser.add_argument("--upstream-base", required=True, help="Deployed dashboard base URL")
    parser.add_argument("--listen-host", default="0.0.0.0")
    parser.add_argument("--listen-port", type=int, default=8765)
    parser.add_argument("--poll-seconds", type=int, default=10)
    args = parser.parse_args()

    relay = WatchlistRelay(args.upstream_base, args.poll_seconds)
    RelayHandler.relay = relay
    thread = threading.Thread(target=relay.loop, daemon=True)
    thread.start()

    server = ThreadingHTTPServer((args.listen_host, args.listen_port), RelayHandler)
    print(f"SSE relay listening on http://{args.listen_host}:{args.listen_port}")
    server.serve_forever()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
