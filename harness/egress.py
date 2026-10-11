"""送信先の許可判定（標準ライブラリのみ）。

テスト用ブラウザが通信してよいのは「自分で起動した 127.0.0.1 の許可ポート」だけ。
- スキームは http のみ（data: / about: / blob: はネットワークに出ないので許可）
- ホストは文字列として 127.0.0.1 のみ（localhost・::1・127.0.0.2・10進表記などは拒否）
- ポートは許可リストにあるものだけ
- 書き込み（GET/HEAD 以外）は、ポートごとの許可パスだけ
- WebSocket は常に拒否（このハーネスでは使わない）
許可できない経路は拒否し、理由を返す。判定はすべて呼び出し側がログに残す。
"""
import urllib.parse

LOOPBACK = "127.0.0.1"
READ_METHODS = ("GET", "HEAD")
LOCAL_SCHEMES = ("data", "about", "blob")


class EgressPolicy:
    def __init__(self, allowed_ports, write_paths=None):
        """allowed_ports: 許可するポート番号の集合。
        write_paths: {port: ["/submit", ...]}。キーの無いポートへの書き込みは拒否。"""
        self.allowed_ports = {int(p) for p in allowed_ports}
        self.write_paths = {int(k): set(v) for k, v in (write_paths or {}).items()}

    def decide(self, url, method="GET", kind="http"):
        """戻り値 (allow: bool, reason: str)。kind は http / websocket / navigation など。"""
        method = (method or "GET").upper()
        try:
            u = urllib.parse.urlsplit(str(url))
        except ValueError:
            return False, "URL を解釈できない"
        scheme = (u.scheme or "").lower()
        if kind == "websocket" or scheme in ("ws", "wss"):
            return False, "WebSocket は許可しない"
        if scheme in LOCAL_SCHEMES:
            return (True, f"{scheme}: はネットワークに出ない") if method in READ_METHODS else (False, f"{scheme}: への書き込み")
        if scheme != "http":
            return False, f"スキーム {scheme or '（なし）'} は許可しない（http のみ）"
        try:
            host, port = u.hostname, u.port
        except ValueError:
            return False, "ポートを解釈できない"
        if u.username or u.password:
            return False, "URL に認証情報が含まれる"
        if host != LOOPBACK:
            return False, f"ホスト {host} は許可しない（127.0.0.1 のみ）"
        port = port or 80
        if port not in self.allowed_ports:
            return False, f"ポート {port} は許可リストにない"
        if method not in READ_METHODS:
            path = u.path or "/"
            if path not in self.write_paths.get(port, set()):
                return False, f"{method} {path} はポート {port} の書き込み許可パスにない"
            return True, f"許可された書き込み {method} 127.0.0.1:{port}{path}"
        return True, f"許可ポート 127.0.0.1:{port} の読み取り"

    def describe(self):
        return {"allowed_host": LOOPBACK, "allowed_ports": sorted(self.allowed_ports),
                "write_paths": {str(k): sorted(v) for k, v in sorted(self.write_paths.items())},
                "websocket": "deny", "service_workers": "block"}
