# 注意: 自作の実験環境専用。所有する環境でのみ起動すること。実際の個人情報を入力しないこと。
# 待受アドレスは既定で 127.0.0.1（ループバック）のみ。メールはローカルの Mailpit（127.0.0.1 の SMTP）にだけ送る。
"""メール配信つきの DEMO テストサイト（issue #4）。標準ライブラリのみ。

  python3 server_mail.py --mode honest --port 8781 --smtp-port 1025
  python3 server_mail.py --mode evil   --port 8782 --smtp-port 1025

mode:
  honest … 配信停止を受け付けたら、その宛先には販促メールを送らない
  evil   … 配信停止の完了画面と完了通知は出すが、実際には販促メールを送り続ける（悪質ケースの再現）
ルート:
  GET  /                 登録フォーム
  POST /register         登録（確認メール＝手続き、販促メール1通を送る）
  GET  /unsubscribe?t=…  配信停止の確認画面（メールの List-Unsubscribe / 本文リンクの行き先）
  POST /unsubscribe      配信停止（停止完了通知を送る）
  POST /_test/campaign   テスト用の配信トリガー（時間経過の代わりに「次の販促メール」を送る。E2E スクリプトが叩く）
送信先 SMTP は 127.0.0.1 以外を受け付けない（--allow-smtp-host で compose 用のサービス名だけ追加可）。
"""
import argparse, html, http.server, json, secrets, smtplib, threading, urllib.parse
from datetime import datetime, timezone, timedelta
from email.message import EmailMessage
from email.utils import format_datetime, make_msgid

JST = timezone(timedelta(hours=9))
BANNER = ('<div style="background:#222;color:#ff0;padding:6px">DEMO：人柱くん検証用の自作テストサイト（実在のサービスではありません）。'
          '自作の実験環境専用。所有する環境でのみ起動すること。実際の個人情報を入力しないこと。</div>')
SITES = {
    "honest": {"title": "【DEMO】まじめ通信", "domain": "honest-news.test",
               "promise": "配信は週1回です。配信停止はメール末尾のリンクから。停止後は一切お送りしません。", "optin_checked": False},
    "evil": {"title": "【DEMO】おトク速報", "domain": "otoku-sokuho.test",
             "promise": "お得な情報をお届けします。配信停止はメール末尾のリンクから。停止後は一切お送りしません。", "optin_checked": True},
}
MAX_BODY = 16 * 1024


class Site:
    def __init__(self, mode, smtp_host="127.0.0.1", smtp_port=1025, base_url=None, allowed_smtp_hosts=("127.0.0.1",)):
        if mode not in SITES:
            raise ValueError(mode)
        if smtp_port is not None and smtp_host not in allowed_smtp_hosts:
            raise ValueError(f"SMTP の送信先 {smtp_host} は許可していない（127.0.0.1 のみ）")
        self.mode, self.cfg = mode, SITES[mode]
        self.smtp_host, self.smtp_port = smtp_host, smtp_port
        self.base_url = base_url
        self.subs = {}          # token -> {"email", "subscribed", "partner_optin", "registered_at", "unsubscribed_at"}
        self.outbox = []        # smtp_port=None のとき（テスト用）送ったメールをここに貯める
        self.campaign_no = 0
        self.lock = threading.Lock()

    @property
    def sender(self):
        return f"news@{self.cfg['domain']}"

    def send(self, to, subject, kind, token=None, body=""):
        m = EmailMessage()
        m["From"] = f"{self.cfg['title']} <{self.sender}>"
        m["To"] = to
        m["Subject"] = subject
        m["Date"] = format_datetime(datetime.now(JST))
        m["Message-ID"] = make_msgid(domain=self.cfg["domain"])
        if token and kind == "marketing":
            u = f"{self.base_url}/unsubscribe?t={token}"
            m["List-Unsubscribe"] = f"<{u}>"
            body += f"\n\n配信停止はこちら: {u}\n"
        m.set_content(body or subject)
        if self.smtp_port is None:
            self.outbox.append({"to": to, "subject": subject, "kind": kind, "headers": dict(m.items())})
            return
        with smtplib.SMTP(self.smtp_host, self.smtp_port, timeout=10) as s:
            s.send_message(m)

    def register(self, fields):
        email = (fields.get("email") or "").strip()
        if "@" not in email:
            return None
        tok = secrets.token_urlsafe(12)
        with self.lock:
            self.subs[tok] = {"email": email, "subscribed": True, "partner_optin": bool(fields.get("partner_optin")),
                              "registered_at": datetime.now(JST).isoformat(), "unsubscribed_at": None}
        self.send(email, f"【確認】{self.cfg['title']}へのご登録ありがとうございます", "transactional", tok,
                  "ご登録を受け付けました。")
        self.campaign_no += 1
        self.send(email, f"今だけ使えるクーポンのお知らせ（第{self.campaign_no}号）", "marketing", tok, "DEMO の販促メールです。")
        return tok

    def unsubscribe(self, tok):
        with self.lock:
            s = self.subs.get(tok)
            if not s:
                return False
            s["unsubscribe_requested_at"] = datetime.now(JST).isoformat()
            if self.mode == "honest":
                s["subscribed"] = False
                s["unsubscribed_at"] = s["unsubscribe_requested_at"]
            # evil: 受け付けたふりをするだけで subscribed のまま
        self.send(s["email"], "配信停止を受け付けました", "transactional", tok, "配信停止の手続きが完了しました。")
        return True

    def campaign(self):
        self.campaign_no += 1
        n = 0
        for tok, s in list(self.subs.items()):
            if s["subscribed"]:
                self.send(s["email"], f"今だけ使えるクーポンのお知らせ（第{self.campaign_no}号）", "marketing", tok,
                          "DEMO の販促メールです。")
                n += 1
        return n


def page(title, body):
    return (f'<!doctype html><html><head><meta charset="utf-8"><title>{html.escape(title)}</title></head>'
            f'<body>{BANNER}{body}</body></html>')


def make_handler(site):
    class H(http.server.BaseHTTPRequestHandler):
        server_version = "DemoMailTestsite"
        sys_version = ""

        def log_message(self, *a):
            pass

        def _send(self, code, body, ctype="text/html; charset=utf-8"):
            b = body.encode("utf-8")
            self.send_response(code)
            self.send_header("Content-Type", ctype)
            self.send_header("Content-Length", str(len(b)))
            self.end_headers()
            if self.command != "HEAD":
                self.wfile.write(b)

        def _form(self):
            try:
                n = int(self.headers.get("Content-Length", 0))
            except ValueError:
                return None
            if n < 0 or n > MAX_BODY:
                return None
            d = urllib.parse.parse_qs(self.rfile.read(n).decode("utf-8", "replace"), keep_blank_values=True)
            return {k: v[0] for k, v in d.items()}

        def do_GET(self):
            u = urllib.parse.urlsplit(self.path)
            c = site.cfg
            if u.path == "/":
                chk = " checked" if c["optin_checked"] else ""
                return self._send(200, page(c["title"], f"""
<h2>{html.escape(c['title'])} メルマガ登録</h2>
<p id="promise">{html.escape(c['promise'])}</p>
<form id="signup" method="POST" action="/register">
<p>メールアドレス（必須）<br><input name="email" type="email" required></p>
<p>お名前（任意）<br><input name="name"></p>
<p><label><input type="checkbox" name="partner_optin" value="yes"{chk}> 提携企業からのお知らせも受け取る</label></p>
<button type="submit">登録する</button>
</form>"""))
            if u.path == "/unsubscribe":
                t = urllib.parse.parse_qs(u.query).get("t", [""])[0]
                if t not in site.subs:
                    return self._send(404, page("not found", "<p>リンクが無効です</p>"))
                return self._send(200, page("配信停止", f"""
<h2>配信停止</h2><p>以下のボタンで配信を停止します。</p>
<form id="unsub" method="POST" action="/unsubscribe"><input type="hidden" name="t" value="{html.escape(t)}">
<button type="submit">配信を停止する</button></form>"""))
            return self._send(404, "not found", "text/plain; charset=utf-8")

        do_HEAD = do_GET

        def do_POST(self):
            p = urllib.parse.urlsplit(self.path).path
            f = self._form()
            if f is None:
                return self._send(400, "bad request", "text/plain; charset=utf-8")
            if p == "/register":
                tok = site.register(f)
                if not tok:
                    return self._send(400, page("error", "<p>メールアドレスが必要です</p>"))
                return self._send(200, page("登録完了", "<h2 id='done'>ご登録ありがとうございます</h2><p>確認メールをお送りしました。</p>"))
            if p == "/unsubscribe":
                ok = site.unsubscribe(f.get("t", ""))
                if not ok:
                    return self._send(404, page("not found", "<p>リンクが無効です</p>"))
                return self._send(200, page("配信停止完了", "<h2 id='unsub-done'>配信停止が完了しました</h2><p>今後はお送りしません。</p>"))
            if p == "/_test/campaign":
                n = site.campaign()
                return self._send(200, json.dumps({"sent": n}), "application/json")
            return self._send(404, "not found", "text/plain; charset=utf-8")

        def _not_allowed(self):
            self._send(405, "method not allowed", "text/plain; charset=utf-8")

        do_PUT = do_DELETE = do_PATCH = do_OPTIONS = _not_allowed
    return H


def serve(mode, port=0, smtp_host="127.0.0.1", smtp_port=1025, bind="127.0.0.1", allowed_smtp_hosts=("127.0.0.1",),
          public_base=None):
    """スレッドで起動して (httpd, site) を返す。port=0 なら空きポート。"""
    site = Site(mode, smtp_host, smtp_port, allowed_smtp_hosts=allowed_smtp_hosts)
    httpd = http.server.ThreadingHTTPServer((bind, port), make_handler(site))
    site.base_url = public_base or f"http://127.0.0.1:{httpd.server_address[1]}"
    threading.Thread(target=httpd.serve_forever, daemon=True).start()
    return httpd, site


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--mode", choices=sorted(SITES), required=True)
    ap.add_argument("--port", type=int, default=8781)
    ap.add_argument("--smtp-host", default="127.0.0.1")
    ap.add_argument("--smtp-port", type=int, default=1025)
    ap.add_argument("--bind", default="127.0.0.1", help="コンテナ内で動かすときだけ変更（compose.yaml 参照）。外部に公開しないこと")
    ap.add_argument("--allow-smtp-host", action="append", default=["127.0.0.1"], help="compose のサービス名など")
    ap.add_argument("--public-base", default=None, help="メール内リンクの基底 URL（既定 http://127.0.0.1:<port>）")
    a = ap.parse_args()
    httpd, site = serve(a.mode, a.port, a.smtp_host, a.smtp_port, a.bind, tuple(a.allow_smtp_host),
                        a.public_base or f"http://127.0.0.1:{a.port}")
    print(json.dumps({"mode": a.mode, "url": site.base_url, "smtp": f"{a.smtp_host}:{a.smtp_port}"}), flush=True)
    threading.Event().wait()


if __name__ == "__main__":
    main()
