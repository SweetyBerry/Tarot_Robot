from __future__ import annotations

import json
import socket
import struct
import threading
import time
import uuid
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any, Literal


Mode = Literal["general", "love", "career", "money"]
ALLOWED_MODES: set[str] = {"general", "love", "career", "money"}

# ========= RPC config (laptop -> ssh tunnel -> server rpc_server.py) =========
RPC_HOST = "127.0.0.1"
RPC_PORT = 5555  # 如果你用 ssh -L 改成別的 port，這裡同步改


INDEX_HTML = """<!doctype html>
<html lang="zh-Hant">
<head>
    <meta charset="utf-8" />
    <meta name="viewport" content="width=device-width, initial-scale=1" />
    <title>塔羅牌運勢占卜</title>
    <style>
        body { font-family: sans-serif; max-width: 860px; margin: 24px auto; padding: 0 12px; line-height: 1.6; }
        label { font-weight: 600; display: block; margin-top: 12px; }
        select, input, textarea, button { font-size: 16px; padding: 10px; width: 100%; box-sizing: border-box; }
        textarea { min-height: 92px; resize: vertical; }
        button { margin-top: 14px; }

        .small { color: #666; font-size: 13px; margin-top: 4px; }
        hr { margin: 18px 0; }

        .result { margin-top: 16px; }
        .grid { display: grid; grid-template-columns: 1fr; gap: 14px; }
        @media (min-width: 720px) {
            .grid { grid-template-columns: 1fr 1fr; }
        }
        .cardbox { border: 1px solid #ddd; border-radius: 10px; padding: 12px; background: #fff; }
        .role { font-weight: 700; margin-bottom: 8px; }
        .excerpt { background: #f6f6f6; padding: 10px; border-radius: 8px; white-space: pre-wrap; overflow: auto; }
        .imgwrap { margin-top: 10px; display: flex; justify-content: center; }
        img.cardface { max-width: 100%; height: auto; border-radius: 10px; border: 1px solid #eee; }
        img.reversed { transform: rotate(180deg); }

        .answer { margin-top: 14px; border: 1px solid #ddd; border-radius: 10px; padding: 12px; background: #fff; }
        .answer pre { margin: 0; white-space: pre-wrap; }
    </style>
</head>
<body>
    <h2>塔羅牌運勢占卜</h2>

    <form id="form">
        <label>想占卜的問題主題</label>
        <select name="mode" required>
            <option value="general">一般（general）</option>
            <option value="love">愛情（love）</option>
            <option value="career">事業（career）</option>
            <option value="money">金錢（money）</option>
        </select>

        <label>想問的問題</label>
        <textarea name="question" placeholder="例如：我接下來三個月的感情走向？" required></textarea>

        <label>提問者個人資訊</label>
        <textarea name="information" placeholder="例如：碩二，電機系，24歲…"></textarea>

        <button type="submit">送出占卜</button>
    </form>

    <p id="status"></p>
    <div id="out" class="result"></div>

    <script>
        const statusEl = document.getElementById("status");
        const outEl = document.getElementById("out");
        const form = document.getElementById("form");

        function sleep(ms) { return new Promise(r => setTimeout(r, ms)); }

        function safeGet(obj, path, fallback=null) {
            try {
                return path.split(".").reduce((acc, k) => acc && acc[k], obj) ?? fallback;
            } catch {
                return fallback;
            }
        }

        function escHtml(s) {
            return String(s)
                .replaceAll("&", "&amp;")
                .replaceAll("<", "&lt;")
                .replaceAll(">", "&gt;")
                .replaceAll('"', "&quot;")
                .replaceAll("'", "&#039;");
        }

        function cardImgHtml(number, orientation) {
            const n = Number(number);
            if (!Number.isFinite(n) || n < 0 || n > 77) return "";
            const cls = (String(orientation) === "reversed") ? "cardface reversed" : "cardface";
            return `<div class="imgwrap"><img class="${cls}" src="/card_scan/${n}.jpg" alt="card ${n}"></div>`;
        }

        function renderCardBlock(roleZh, excerptText, number, orientation) {
            const ex = escHtml(excerptText || "");
            return `
                <div class="cardbox">
                    <div class="role">${escHtml(roleZh)}</div>
                    <div class="excerpt">${ex}</div>
                    ${cardImgHtml(number, orientation)}
                </div>
            `;
        }

        function renderTarotResult(result) {
            if (!result) return `<div>❌ result is empty</div>`;
            if (!result.ok) {
                const err = result.error || "unknown error";
                return `<div>❌ 發生錯誤：${escHtml(err)}</div>`;
            }

            const pastEx = String(safeGet(result, "excerpts.past", "") || "").trim();
            const presentEx = String(safeGet(result, "excerpts.present", "") || "").trim();
            const futureEx = String(safeGet(result, "excerpts.future", "") || "").trim();

            const pastNum = safeGet(result, "cards.past.number", null);
            const presNum = safeGet(result, "cards.present.number", null);
            const futNum = safeGet(result, "cards.future.number", null);

            const pastOri = safeGet(result, "cards.past.orientation", "upright");
            const presOri = safeGet(result, "cards.present.orientation", "upright");
            const futOri = safeGet(result, "cards.future.orientation", "upright");

            const answer = String(safeGet(result, "answer", "") || "").trim();

            const gridHtml = `
                <div class="grid">
                    ${renderCardBlock("代表過去的牌", pastEx, pastNum, pastOri)}
                    ${renderCardBlock("代表現在的牌", presentEx, presNum, presOri)}
                    ${renderCardBlock("代表未來的牌", futureEx, futNum, futOri)}
                </div>
            `;

            const ansHtml = answer
                ? `<div class="answer"><div class="role">牌義解釋</div><pre>${escHtml(answer)}</pre></div>`
                : "";

            return gridHtml + ansHtml;
        }

        async function poll(jobId) {
            statusEl.textContent = "處理中…";
            while (true) {
                const r = await fetch(`/api/job/${jobId}`);
                const j = await r.json();

                if (!r.ok) {
                    statusEl.textContent = "查詢失敗";
                    outEl.textContent = JSON.stringify(j, null, 2);
                    return;
                }

                if (j.status === "done") {
                    statusEl.textContent = "完成！";
                    outEl.innerHTML = renderTarotResult(j.result);
                    return;
                }

                if (j.status === "running") {
                    statusEl.textContent = "占卜中（模型推理中）…";
                } else {
                    statusEl.textContent = "排隊中…";
                }

                await sleep(900);
            }
        }

        form.addEventListener("submit", async (e) => {
            e.preventDefault();
            outEl.innerHTML = "";
            statusEl.textContent = "送出中…";

            const fd = new FormData(form);
            const payload = {
                mode: fd.get("mode"),
                question: fd.get("question"),
                information: fd.get("information"),
            };

            try {
                const r = await fetch("/api/submit", {
                    method: "POST",
                    headers: { "content-type": "application/json" },
                    body: JSON.stringify(payload),
                });
                const j = await r.json();

                if (!r.ok) {
                    statusEl.textContent = "送出失敗";
                    outEl.textContent = JSON.stringify(j, null, 2);
                    return;
                }

                await poll(j.job_id);
            } catch (err) {
                statusEl.textContent = "連線失敗（請確認手機能連到你的電腦）";
                outEl.textContent = String(err);
            }
        });
    </script>
</body>
</html>
"""


JOBS: dict[str, dict[str, Any]] = {}
JOBS_LOCK = threading.Lock()


# ========= http helpers =========

def send_json(h: BaseHTTPRequestHandler, status: int, payload: dict[str, Any]) -> None:
    data = json.dumps(payload, ensure_ascii=False).encode("utf-8")
    h.send_response(status)
    h.send_header("Content-Type", "application/json; charset=utf-8")
    h.send_header("Content-Length", str(len(data)))
    h.end_headers()
    h.wfile.write(data)


def send_html(h: BaseHTTPRequestHandler, html: str) -> None:
    body = html.encode("utf-8")
    h.send_response(200)
    h.send_header("Content-Type", "text/html; charset=utf-8")
    h.send_header("Content-Length", str(len(body)))
    h.end_headers()
    h.wfile.write(body)


def send_jpg_file(h: BaseHTTPRequestHandler, file_path: Path) -> None:
    data = file_path.read_bytes()
    h.send_response(200)
    h.send_header("Content-Type", "image/jpeg")
    h.send_header("Content-Length", str(len(data)))
    h.end_headers()
    h.wfile.write(data)


# ========= RPC client (same protocol as rpc_server.py) =========

def _recvall(conn: socket.socket, n: int) -> bytes:
    buf = b""
    while len(buf) < n:
        chunk = conn.recv(n - len(buf))
        if not chunk:
            raise ConnectionError("socket closed")
        buf += chunk
    return buf


def rpc_call_tarot(payload: dict[str, Any], host: str = RPC_HOST, port: int = RPC_PORT, timeout: float = 600.0) -> dict[str, Any]:
    data = json.dumps(payload, ensure_ascii=False).encode("utf-8")
    header = struct.pack("!I", len(data))

    with socket.create_connection((host, port), timeout=timeout) as conn:
        conn.sendall(header + data)

        raw_len = _recvall(conn, 4)
        (msg_len,) = struct.unpack("!I", raw_len)
        raw = _recvall(conn, msg_len)
        return json.loads(raw.decode("utf-8"))


# ========= job logic =========

def _validate_submit(payload: dict[str, Any]) -> tuple[bool, str]:
    mode = str(payload.get("mode", "")).strip()
    question = str(payload.get("question", "")).strip()
    information = str(payload.get("information", "")).strip()

    if mode not in ALLOWED_MODES:
        return False, "mode must be one of: general/love/career/money"
    if len(question) < 3:
        return False, "question too short"
    if len(question) > 5000 or len(information) > 5000:
        return False, "text too long"
    return True, ""


def make_safe_job(job: dict[str, Any]) -> dict[str, Any]:
    return {
        "status": job.get("status"),
        "result": job.get("result"),
        "created_at": job.get("created_at"),
        "started_at": job.get("started_at"),
        "finished_at": job.get("finished_at"),
    }


def _cleanup_jobs(now: float, ttl_seconds: float = 3600.0) -> None:
    to_delete: list[str] = []
    for jid, j in JOBS.items():
        created_at = float(j.get("created_at", now))
        if now - created_at > ttl_seconds:
            to_delete.append(jid)
    for jid in to_delete:
        JOBS.pop(jid, None)


def run_tarot_job(job_id: str) -> None:
    with JOBS_LOCK:
        job = JOBS.get(job_id)
        if not job:
            return
        payload = job["payload"]
        job["status"] = "running"
        job["started_at"] = time.time()

    try:
        # ⭐ 改成打 RPC（LLM 在 server）
        result = rpc_call_tarot(payload)

    except Exception as e:
        result = {"ok": False, "error": f"rpc failed: {type(e).__name__}: {e}"}

    with JOBS_LOCK:
        job2 = JOBS.get(job_id)
        if not job2:
            return
        job2["status"] = "done"
        job2["result"] = result
        job2["finished_at"] = time.time()


# ========= http handler =========

class Handler(BaseHTTPRequestHandler):
    def do_GET(self) -> None:
        if self.path == "/favicon.ico":
            self.send_response(204)
            self.end_headers()
            return

        if self.path == "/" or self.path.startswith("/index.html"):
            send_html(self, INDEX_HTML)
            return

        # ⭐ 靜態圖片：/card_scan/12.jpg
        if self.path.startswith("/card_scan/"):
            name = self.path.split("/card_scan/", 1)[1].strip()
            if not name.endswith(".jpg"):
                send_json(self, 404, {"ok": False, "error": "not found"})
                return

            base = Path(__file__).resolve().parent / "card_scan"
            # 防路徑穿越：只允許純檔名
            if "/" in name or "\\" in name:
                send_json(self, 400, {"ok": False, "error": "bad path"})
                return

            try:
                num_str = name[:-4]
                num = int(num_str)
                if not (0 <= num <= 77):
                    raise ValueError("bad number")
            except Exception:
                send_json(self, 400, {"ok": False, "error": "bad image name"})
                return

            file_path = base / f"{num}.jpg"
            if not file_path.exists():
                send_json(self, 404, {"ok": False, "error": "image not found"})
                return

            send_jpg_file(self, file_path)
            return

        if self.path.startswith("/api/job/"):
            job_id = self.path.split("/api/job/", 1)[1].strip()
            with JOBS_LOCK:
                job = JOBS.get(job_id)

            if not job:
                send_json(self, 404, {"ok": False, "error": "job not found"})
            else:
                send_json(self, 200, make_safe_job(job))
            return

        send_json(self, 404, {"ok": False, "error": "not found"})

    def do_POST(self) -> None:
        if self.path != "/api/submit":
            send_json(self, 404, {"ok": False, "error": "not found"})
            return

        try:
            length = int(self.headers.get("Content-Length", "0"))
            raw = self.rfile.read(length)
            payload = json.loads(raw.decode("utf-8"))
        except Exception as e:
            send_json(self, 400, {"ok": False, "error": f"invalid json: {e}"})
            return

        ok, err = _validate_submit(payload)
        if not ok:
            send_json(self, 400, {"ok": False, "error": err})
            return

        clean_payload = {
            "mode": str(payload["mode"]).strip(),
            "question": str(payload["question"]).strip(),
            "information": str(payload.get("information", "")).strip(),
        }

        job_id = uuid.uuid4().hex
        now = time.time()

        with JOBS_LOCK:
            _cleanup_jobs(now, ttl_seconds=3600.0)
            JOBS[job_id] = {
                "status": "pending",
                "payload": clean_payload,
                "result": None,
                "created_at": now,
                "started_at": None,
                "finished_at": None,
            }

        threading.Thread(target=run_tarot_job, args=(job_id,), daemon=True).start()
        send_json(self, 200, {"ok": True, "job_id": job_id})

    def log_message(self, fmt: str, *args: Any) -> None:
        print(f"[HTTP] {self.client_address[0]} - {fmt % args}")


def get_local_ip() -> str:
    try:
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        s.connect(("8.8.8.8", 80))
        ip = s.getsockname()[0]
        s.close()
        return ip
    except Exception:
        return "127.0.0.1"


def main() -> None:
    host = "0.0.0.0"
    port = 8080
    server = ThreadingHTTPServer((host, port), Handler)

    local_ip = get_local_ip()

    print("Web server running:")
    print(f"  本機：http://127.0.0.1:{port}/")
    print(f"  區網：http://{local_ip}:{port}/  （手機用這個）")
    print(f"RPC target: {RPC_HOST}:{RPC_PORT} (via SSH tunnel)")

    server.serve_forever()


if __name__ == "__main__":
    main()
