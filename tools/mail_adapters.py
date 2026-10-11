"""受信基盤のアダプタ（標準ライブラリのみ）。いまは Mailpit（ローカル再現用）だけ。

Mailpit は本物の配信サービス・受信サーバの代わりではない。SPF/DKIM/DMARC を検証せず、Authentication-Results も付けない。
このアダプタは:
  - 接続先を http://127.0.0.1:<port> に限る（それ以外は ValueError）
  - Mailpit の API から**ヘッダとメタデータだけ**を取り出す（本文・Snippet は取り出さない／保存しない）
  - 受信時刻は Mailpit が受信した時刻（Created）を使い、出所を provenance に残す
  - ingest_email.ingest() に渡す evidence を作る。closed_env=True は「自分で起動した Mailpit に、自分で起動したテストサイトだけが送る」
    構成を呼び出し側が保証した場合だけ使う（送信ドメイン認証の代わりではない）
"""
import json, smtplib, time, urllib.parse, urllib.request
from datetime import datetime
from email.message import EmailMessage
from email.header import decode_header, make_header
from email.utils import format_datetime, make_msgid
from common import JST

# Received は受信ホスト名（内部の機械名）を含むので保存しない
KEEP_HEADERS = ("From", "To", "Subject", "Date", "Message-ID", "List-Unsubscribe", "List-Unsubscribe-Post",
                "Authentication-Results")
_KEEP = {k.lower(): k for k in KEEP_HEADERS}


def _check_loopback(base):
    u = urllib.parse.urlsplit(base)
    if u.scheme != "http" or u.hostname != "127.0.0.1" or not u.port:
        raise ValueError(f"Mailpit の API は http://127.0.0.1:<port> だけを許可する: {base}")
    return f"http://127.0.0.1:{u.port}"


class MailpitAdapter:
    source = "mailpit_api"

    def __init__(self, api_base, smtp_port=None, trusted_authserv_ids=(), closed_env=False):
        self.api = _check_loopback(api_base)
        self.smtp_port = smtp_port
        self.trusted_authserv_ids = list(trusted_authserv_ids)
        self.closed_env = bool(closed_env)

    def _get(self, path):
        with urllib.request.urlopen(self.api + path, timeout=10) as r:
            return json.loads(r.read().decode("utf-8"))

    def alive(self):
        try:
            self._get("/api/v1/info")
            return True
        except Exception:
            return False

    def list_for(self, to_addr, limit=500):
        """宛先が to_addr のメッセージを、ヘッダ付きで返す（受信の古い順）。本文は取らない。"""
        res = self._get(f"/api/v1/messages?limit={int(limit)}")
        out = []
        for m in res.get("messages") or []:
            tos = [t.get("Address", "").lower() for t in (m.get("To") or [])]
            if to_addr.lower() not in tos:
                continue
            hdr = self._get(f"/api/v1/message/{urllib.parse.quote(m['ID'])}/headers")
            out.append({"id": m["ID"], "created": m.get("Created"),
                        "headers": {_KEEP[k.lower()]: v for k, v in hdr.items() if k.lower() in _KEEP}})
        out.sort(key=lambda x: x["created"] or "")
        return out

    def to_ingest(self, rec, fetched_at=None):
        """list_for() の1件を ingest_email.ingest() の (msg, evidence) にする。"""
        h = rec["headers"]

        def one(k):
            v = (h.get(k) or [None])[0]
            if v is None:
                return None
            try:
                return str(make_header(decode_header(v)))   # =?utf-8?b?...?= を戻す
            except Exception:
                return v
        lu = one("List-Unsubscribe")
        url = None
        if lu:
            for part in lu.split(","):
                part = part.strip().strip("<>").strip()
                if part.startswith("http"):
                    url = part
                    break
        msg = {"from": one("From"), "to": one("To"), "subject": one("Subject"), "received_at": rec["created"],
               "message_id": one("Message-ID"), "has_list_unsubscribe": bool(lu),
               "headers": {"Authentication-Results": h.get("Authentication-Results") or []}}
        if url:
            msg["unsubscribe_url"] = url
        ev = {"source": self.source, "trusted_source": True, "trusted_authserv_ids": self.trusted_authserv_ids,
              "closed_env": self.closed_env, "ref": rec["id"],
              "fetched_at": fetched_at or datetime.now(JST).isoformat(timespec="seconds"),
              "received_at_source": "mailpit.Created（Mailpit が受信した時刻）"}
        return msg, ev

    def send_canary(self, to_addr):
        """受信経路の稼働確認用に、ローカル SMTP（127.0.0.1）へ確認用メールを1通送る。戻り値 Message-ID。"""
        m = EmailMessage()
        m["From"] = "canary@hitobashira.test"
        m["To"] = to_addr
        m["Subject"] = "hitobashira receiver canary"
        m["Date"] = format_datetime(datetime.now(JST))
        mid = make_msgid(domain="hitobashira.test")
        m["Message-ID"] = mid
        m.set_content("canary")
        with smtplib.SMTP("127.0.0.1", self.smtp_port, timeout=10) as s:
            s.send_message(m)
        return mid

    def wait_for(self, to_addr, pred, timeout=10.0, interval=0.2):
        """pred(list_for(to_addr)) が真になるまで待つ。戻り値 (成否, 最後の一覧)。"""
        end = time.time() + timeout
        last = []
        while time.time() < end:
            last = self.list_for(to_addr)
            if pred(last):
                return True, last
            time.sleep(interval)
        return False, last

    def receiver_check(self, canary_addr, timeout=10.0):
        """カナリアを送り、Mailpit の API で受信を確認する。receiver_checks に入れる dict を返す。"""
        sent_at = datetime.now(JST).isoformat(timespec="milliseconds")
        try:
            mid = self.send_canary(canary_addr)
            ok, _ = self.wait_for(canary_addr, lambda L: any((r["headers"].get("Message-ID") or [""])[0] == mid for r in L),
                                  timeout)
        except Exception as ex:  # 送れない・API が落ちている
            return {"at": datetime.now(JST).isoformat(timespec="milliseconds"), "kind": "canary", "ok": False,
                    "error": str(ex)[:200], "sent_at": sent_at}
        return {"at": datetime.now(JST).isoformat(timespec="milliseconds"), "kind": "canary", "ok": bool(ok),
                "sent_at": sent_at}


def snapshot(records):
    """証拠用のスナップショット（ヘッダとメタデータだけ）。"""
    return [{"mailpit_id": r["id"], "created": r["created"], "headers": r["headers"]} for r in records]
