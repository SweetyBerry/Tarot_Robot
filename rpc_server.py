from __future__ import annotations

import json
import socket
import struct
import threading
import time
from typing import Any, Literal

from llm_ask import ask_tarot

Mode = Literal["general", "love", "career", "money"]
ALLOWED_MODES = {"general", "love", "career", "money"}


# ========= socket helpers =========

def recvall(conn: socket.socket, n: int) -> bytes:
    buf = b""
    while len(buf) < n:
        chunk = conn.recv(n - len(buf))
        if not chunk:
            raise ConnectionError("socket closed")
        buf += chunk
    return buf


def recv_msg(conn: socket.socket) -> dict[str, Any]:
    raw_len = recvall(conn, 4)
    (msg_len,) = struct.unpack("!I", raw_len)
    raw = recvall(conn, msg_len)
    return json.loads(raw.decode("utf-8"))


def send_msg(conn: socket.socket, obj: dict[str, Any]) -> None:
    data = json.dumps(obj, ensure_ascii=False).encode("utf-8")
    header = struct.pack("!I", len(data))
    conn.sendall(header + data)


# ========= client handler =========

def handle_client(conn: socket.socket, addr: tuple[str, int]) -> None:
    client_ip, client_port = addr
    start_ts = time.time()

    print(f"[CONNECT] {client_ip}:{client_port}")

    try:
        req = recv_msg(conn)

        mode = str(req.get("mode", "")).strip()
        question = str(req.get("question", "")).strip()
        information = str(req.get("information", "")).strip()

        print(
            f"[REQUEST] {client_ip}:{client_port} "
            f"mode={mode!r} qlen={len(question)} ilen={len(information)}"
        )

        # ---------- validation ----------
        if mode not in ALLOWED_MODES:
            print(f"[REJECT] bad mode from {client_ip}:{client_port}")
            send_msg(conn, {"ok": False, "error": "bad mode"})
            return

        if len(question) < 3:
            print(f"[REJECT] question too short from {client_ip}:{client_port}")
            send_msg(conn, {"ok": False, "error": "question too short"})
            return

        # ---------- run tarot ----------
        print(f"[RUN] tarot start for {client_ip}:{client_port}")

        result = ask_tarot(
            mode=mode,
            question=question,
            information=information,
        )

        # ---------- slim response ----------
        if result.get("ok"):
            slim_cards: dict[str, dict[str, Any]] = {}
            for role, c in (result.get("cards") or {}).items():
                slim_cards[role] = {
                    "number": c.get("number"),
                    "orientation": c.get("orientation"),
                    "id": c.get("id"),
                    "name_zh": c.get("name_zh"),
                    "name_en": c.get("name_en"),
                    "arcana": c.get("arcana"),
                }
            result["cards"] = slim_cards

        send_msg(conn, result)

        elapsed = time.time() - start_ts
        print(f"[DONE] {client_ip}:{client_port} in {elapsed:.2f}s")

    except Exception as e:
        print(f"[ERROR] {client_ip}:{client_port} {type(e).__name__}: {e}")
        try:
            send_msg(conn, {"ok": False, "error": f"{type(e).__name__}: {e}"})
        except Exception:
            pass

    finally:
        try:
            conn.close()
        except Exception:
            pass
        print(f"[DISCONNECT] {client_ip}:{client_port}")


# ========= main loop =========

def main() -> None:
    host = "127.0.0.1"
    port = 5555

    srv = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    srv.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    srv.bind((host, port))
    srv.listen(50)

    print(f"[START] Tarot RPC server listening on {host}:{port}")

    while True:
        conn, addr = srv.accept()
        threading.Thread(
            target=handle_client,
            args=(conn, addr),
            daemon=True,
        ).start()


if __name__ == "__main__":
    main()
