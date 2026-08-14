#!/usr/bin/env python3
"""Google Calendar OAuth 一次性授权脚本（仿飞书 oauth.py 的模式）。

前置：~/.config/calendar-sync/google_client_secret.json（GCP 桌面应用 OAuth 客户端凭证）
跑通后：~/.config/calendar-sync/google_tokens.json（access_token + refresh_token）
"""
import http.server
import json
import os
import secrets
import socketserver
import sys
import threading
import time
import urllib.parse
import urllib.request
import webbrowser
from pathlib import Path

CFG_DIR = Path.home() / ".config" / "calendar-sync"
CLIENT_SECRET_PATH = CFG_DIR / "google_client_secret.json"
TOKENS_PATH = CFG_DIR / "google_tokens.json"

PORT = 8765
REDIRECT_URI = f"http://localhost:{PORT}/callback"
SCOPE = "https://www.googleapis.com/auth/calendar.events"

CAPTURED = {}
EXPECTED_STATE = secrets.token_urlsafe(16)


class CallbackHandler(http.server.BaseHTTPRequestHandler):
    def log_message(self, *args, **kwargs):
        pass

    def do_GET(self):
        parsed = urllib.parse.urlparse(self.path)
        if parsed.path != "/callback":
            self.send_response(404)
            self.end_headers()
            return
        params = urllib.parse.parse_qs(parsed.query)
        err = params.get("error", [None])[0]
        state = params.get("state", [None])[0]
        if err:
            CAPTURED["error"] = err
            self._html(f"<h1>授权失败</h1><pre>{err}</pre>")
            return
        if state != EXPECTED_STATE:
            CAPTURED["error"] = "state mismatch"
            self._html("<h1>state 不匹配，请重新跑脚本。</h1>")
            return
        CAPTURED["code"] = params.get("code", [None])[0]
        self._html("<h1>✅ Google 授权成功</h1><p>这个页面可以关掉，回终端看结果。</p>")

    def _html(self, body):
        self.send_response(200)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.end_headers()
        self.wfile.write(body.encode())


def run_server():
    socketserver.TCPServer.allow_reuse_address = True
    with socketserver.TCPServer(("127.0.0.1", PORT), CallbackHandler) as httpd:
        httpd.timeout = 1
        deadline = time.time() + 300
        while "code" not in CAPTURED and "error" not in CAPTURED:
            if time.time() > deadline:
                CAPTURED["error"] = "timeout (5min)"
                break
            httpd.handle_request()


def main():
    if not CLIENT_SECRET_PATH.exists():
        print(f"❌ 缺少 {CLIENT_SECRET_PATH}")
        print("   先按 README「Google 侧一次性设置」在 GCP 创建桌面应用 OAuth 客户端并下载 JSON 放到该路径。")
        sys.exit(1)
    data = json.loads(CLIENT_SECRET_PATH.read_text())
    client = data.get("installed") or data.get("web") or data
    client_id, client_secret = client["client_id"], client["client_secret"]

    t = threading.Thread(target=run_server, daemon=True)
    t.start()
    time.sleep(0.3)

    auth_url = "https://accounts.google.com/o/oauth2/v2/auth?" + urllib.parse.urlencode({
        "client_id": client_id,
        "redirect_uri": REDIRECT_URI,
        "response_type": "code",
        "scope": SCOPE,
        "access_type": "offline",
        "prompt": "consent",
        "state": EXPECTED_STATE,
    })
    print(f"\n打开浏览器授权（如果没自动打开，手动访问）：\n{auth_url}\n")
    webbrowser.open(auth_url)

    t.join(timeout=310)
    if "error" in CAPTURED or not CAPTURED.get("code"):
        print(f"❌ 授权失败：{CAPTURED.get('error', '没拿到 code')}")
        sys.exit(1)

    payload = urllib.parse.urlencode({
        "grant_type": "authorization_code",
        "code": CAPTURED["code"],
        "client_id": client_id,
        "client_secret": client_secret,
        "redirect_uri": REDIRECT_URI,
    }).encode()
    req = urllib.request.Request("https://oauth2.googleapis.com/token", data=payload, method="POST")
    req.add_header("Content-Type", "application/x-www-form-urlencoded")
    with urllib.request.urlopen(req, timeout=30) as resp:
        body = json.loads(resp.read().decode())

    if "access_token" not in body:
        print(f"❌ 换 token 失败：{body}")
        sys.exit(1)
    if not body.get("refresh_token"):
        print(f"❌ 响应里没有 refresh_token（去 https://myaccount.google.com/permissions 移除该应用的授权后重跑）")
        sys.exit(1)

    tokens = {
        "access_token": body["access_token"],
        "refresh_token": body["refresh_token"],
        "expires_in": body.get("expires_in"),
        "scope": body.get("scope"),
        "obtained_at": int(time.time()),
    }
    CFG_DIR.mkdir(parents=True, exist_ok=True)
    TOKENS_PATH.write_text(json.dumps(tokens, indent=2))
    os.chmod(TOKENS_PATH, 0o600)
    print(f"✅ tokens 写入 {TOKENS_PATH}")
    print("   现在可以运行：python3 sync.py --dry-run")


if __name__ == "__main__":
    main()
