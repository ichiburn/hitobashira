"""人柱くん 共通ユーティリティ"""
import json, os, re, tempfile
from datetime import datetime, timezone, timedelta

JST = timezone(timedelta(hours=9))
# 自分専用の受信アドレスを環境変数で指定する（例: yourbot@example.com → BOT_LOCAL=yourbot, BOT_DOMAIN=example.com）
BOT_LOCAL = os.environ.get("HITOBASHIRA_BOT_LOCAL", "yourbot")
BOT_DOMAIN = os.environ.get("HITOBASHIRA_BOT_DOMAIN", "example.com")
TAG_RE = re.compile(re.escape(BOT_LOCAL) + r"\+([A-Za-z0-9._-]+)@" + re.escape(BOT_DOMAIN), re.I)

SERVICE_TEMPLATE = {
    "service": None, "tag": None, "tag_address": None, "signup_url": None,
    "signed_up_at": None, "required_fields": [], "prechecked_optins": [],
    "dark_patterns": [], "screenshots": [], "emails": [],
    "unsubscribed_at": None, "emails_after_unsubscribe": None,
}


def parse_dt(s):
    if not s:
        return None
    try:
        d = datetime.fromisoformat(str(s).replace("Z", "+00:00"))
    except ValueError:
        from email.utils import parsedate_to_datetime
        try:
            d = parsedate_to_datetime(str(s))
        except Exception:
            return None
    if d.tzinfo is None:
        d = d.replace(tzinfo=JST)
    return d


def fmt_jst(s):
    d = parse_dt(s)
    return d.astimezone(JST).strftime("%Y-%m-%d %H:%M JST") if d else None


def extract_tag(addr_field):
    """To欄（複数アドレス/表示名付き可）から +タグ を取り出す"""
    m = TAG_RE.search(addr_field or "")
    return m.group(1).lower() if m else None


def tag_of(svc):
    return (svc.get("tag") or extract_tag(svc.get("tag_address") or "") or "").lower() or None


def load_ledger(path):
    if not os.path.exists(path):
        return {"version": 1, "bot_address": f"{BOT_LOCAL}@{BOT_DOMAIN}", "services": []}
    with open(path, encoding="utf-8") as f:
        return json.load(f)


def save_ledger(path, ledger):
    d = os.path.dirname(os.path.abspath(path))
    fd, tmp = tempfile.mkstemp(dir=d, suffix=".tmp")
    with os.fdopen(fd, "w", encoding="utf-8") as f:
        json.dump(ledger, f, ensure_ascii=False, indent=2)
    os.replace(tmp, path)


def emails_after_unsub(svc):
    u = parse_dt(svc.get("unsubscribed_at"))
    if not u:
        return None
    return sum(1 for e in svc.get("emails", []) if (parse_dt(e.get("received_at")) or u) > u)
