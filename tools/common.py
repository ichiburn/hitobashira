"""人柱くん 共通ユーティリティ（標準ライブラリのみ）"""
import html, json, os, re, sys, tempfile, urllib.parse
from datetime import datetime, timezone, timedelta
from email.utils import parsedate_to_datetime, parseaddr

JST = timezone(timedelta(hours=9))
UTC = timezone.utc
NA = "未観測"
PENDING = "判定保留"
UNKNOWN_TYPES = ("transactional", "marketing", "other")

SERVICE_TEMPLATE = {
    "service": None, "tag": None, "tag_address": None, "signup_url": None,
    "signed_up_at": None, "required_fields": None, "prechecked_optins": None,
    "dark_patterns": None, "screenshots": [], "emails": [],
    "expected_sender_domains": [], "observed_until": None,
    "unsubscribed_at": None, "emails_after_unsubscribe": None,
}


def warn(msg):
    print(f"[warn] {msg}", file=sys.stderr)


# ---------- 受信アドレス / +タグ ----------
def bot_local():
    return os.environ.get("HITOBASHIRA_BOT_LOCAL", "yourbot")


def bot_domain():
    return os.environ.get("HITOBASHIRA_BOT_DOMAIN", "example.com")


def env_configured():
    return bool(os.environ.get("HITOBASHIRA_BOT_LOCAL")) and bool(os.environ.get("HITOBASHIRA_BOT_DOMAIN"))


def tag_re(local=None, domain=None):
    """local+tag@domain を、前後に境界がありドメインが完全一致する場合だけ拾う。
    例: notyourbot+shop@example.com / yourbot+shop@example.com.evil.org は拾わない。"""
    local = local or bot_local()
    domain = domain or bot_domain()
    return re.compile(
        r"(?<![A-Za-z0-9._%+\-])" + re.escape(local) + r"\+([A-Za-z0-9._\-]+)@" + re.escape(domain)
        + r"(?![A-Za-z0-9\-])(?!\.[A-Za-z0-9])", re.I)


def extract_tag(addr_field):
    """To欄（複数アドレス/表示名付き可）から +タグ を取り出す"""
    m = tag_re().search(addr_field or "")
    return m.group(1).lower() if m else None


def tag_of(svc):
    return (svc.get("tag") or extract_tag(svc.get("tag_address") or "") or "").lower() or None


def sender_domain(from_field):
    addr = parseaddr(str(from_field or ""))[1]
    if "@" not in addr:
        return None
    return addr.rsplit("@", 1)[1].strip().strip(".").lower() or None


def check_attribution(svc, from_field):
    """台帳の expected_sender_domains と差出人ドメインを照合する。
    戻り値: "verified"（一致） / "unverified"（不一致・差出人不明＝帰属未確認） / "unchecked"（照合先なし）"""
    doms = [str(d).strip().lower().strip(".") for d in (svc.get("expected_sender_domains") or []) if str(d).strip()]
    if not doms:
        return "unchecked"
    sd = sender_domain(from_field)
    if sd and any(sd == d or sd.endswith("." + d) for d in doms):
        return "verified"
    return "unverified"


# ---------- 時刻 ----------
_SLASH_RE = re.compile(r"^\s*(\d{4})[/.-](\d{1,2})[/.-](\d{1,2})(?:[ T](\d{1,2}):(\d{2})(?::(\d{2}))?)?\s*$")


def parse_dt_ex(s):
    """時刻文字列を解釈する。戻り値 (datetime|None, 状態)。
    状態: ok（TZ付きで解釈できた） / missing / tz_unknown（TZ無し・-0000） / date_only / invalid
    TZ不明のものは JST と決めつけず、datetime は naive のまま返す（比較には使わない）。"""
    if s is None or str(s).strip() == "":
        return None, "missing"
    s = str(s).strip()
    m = _SLASH_RE.match(s)
    if m:
        y, mo, d, hh, mi, ss = m.groups()
        try:
            dt = datetime(int(y), int(mo), int(d), int(hh or 0), int(mi or 0), int(ss or 0))
        except ValueError:
            return None, "invalid"
        return dt, ("date_only" if hh is None else "tz_unknown")
    try:
        dt = datetime.fromisoformat(s.replace("Z", "+00:00").replace("z", "+00:00"))
    except ValueError:
        try:
            dt = parsedate_to_datetime(s)  # RFC 2822。-0000 は naive で返る
        except Exception:
            return None, "invalid"
        if dt is None:
            return None, "invalid"
    if dt.tzinfo is None:
        return dt, "tz_unknown"
    return dt, "ok"


def parse_dt(s):
    """TZ付きで解釈できた場合だけ aware datetime を返す。それ以外は None。"""
    dt, st = parse_dt_ex(s)
    return dt if st == "ok" else None


def to_utc_iso(s):
    d = parse_dt(s)
    return d.astimezone(UTC).strftime("%Y-%m-%dT%H:%M:%SZ") if d else None


def fmt_jst(s):
    dt, st = parse_dt_ex(s)
    if st == "ok":
        return dt.astimezone(JST).strftime("%Y-%m-%d %H:%M JST")
    if st == "tz_unknown":
        return dt.strftime("%Y-%m-%d %H:%M（TZ不明）")
    if st == "date_only":
        return dt.strftime("%Y-%m-%d（時刻不明）")
    if st == "invalid":
        return "解釈不能な時刻"
    return None


def sort_key_utc(e):
    d = parse_dt(e.get("received_at"))
    return (d is None, d.astimezone(UTC) if d else datetime.min.replace(tzinfo=UTC))


# ---------- 台帳 ----------
class LedgerError(Exception):
    pass


def load_ledger(path, create=False):
    if not os.path.exists(path):
        if not create:
            raise LedgerError(f"台帳がありません: {path}")
        return {"version": 1, "services": []}
    with open(path, encoding="utf-8") as f:
        ledger = json.load(f)
    if not isinstance(ledger, dict) or not isinstance(ledger.get("services", []), list):
        raise LedgerError("台帳の形式が不正です（services は配列）")
    ledger.setdefault("services", [])
    return ledger


def save_ledger(path, ledger):
    d = os.path.dirname(os.path.abspath(path))
    fd, tmp = tempfile.mkstemp(dir=d, suffix=".tmp")
    with os.fdopen(fd, "w", encoding="utf-8") as f:
        json.dump(ledger, f, ensure_ascii=False, indent=2)
        f.write("\n")
    os.replace(tmp, path)


def emails_of(svc):
    """emails を返す。キー無し・null は None（未観測）。配列以外は LedgerError。"""
    em = svc.get("emails")
    if em is None:
        return None
    if not isinstance(em, list):
        raise LedgerError(f"emails が配列ではありません: {svc.get('service')!r}")
    return em


# ---------- 配信停止の判定 ----------
def unsub_verdict(svc):
    """配信停止が効いたかを、販促メール（type=marketing）だけで判定する。
    戻り値 dict: status = unobserved | pending | effective | ineffective, after = 通数 or None, reason
    - unsubscribed_at が無い → unobserved（未観測）
    - unsubscribed_at が日付だけ・TZ不明・解釈不能 → pending（判定保留。日付だけの場合は「その日の終わり」とはみなさない）
    - observed_until が無い、または停止時刻以前 → pending（停止後を観測していない）
    - 受信時刻が不明（欠損・TZ不明・解釈不能）なメールが1通でもある → pending
    - 停止後の販促メールのうち、差出人ドメインが不一致（帰属未確認）のものがある → 数えず、0通なら pending
    """
    u_raw = svc.get("unsubscribed_at")
    u, ust = parse_dt_ex(u_raw)
    if ust == "missing":
        return {"status": "unobserved", "after": None, "reason": "配信停止操作の記録なし"}
    if ust != "ok":
        why = {"date_only": "配信停止時刻が日付だけ", "tz_unknown": "配信停止時刻のTZが不明"}.get(ust, "配信停止時刻を解釈できない")
        return {"status": "pending", "after": None, "reason": why}
    try:
        emails = emails_of(svc)
    except LedgerError as ex:
        return {"status": "pending", "after": None, "reason": str(ex)}
    ou = parse_dt(svc.get("observed_until"))
    if ou is None or ou <= u:
        return {"status": "pending", "after": None, "reason": "停止後の観測期限（observed_until）が無いか停止時刻以前"}
    emails = emails or []
    bad = [e for e in emails if parse_dt(e.get("received_at")) is None]
    if bad:
        return {"status": "pending", "after": None, "reason": f"受信時刻が不明なメールが{len(bad)}通ある"}
    promo_after = [e for e in emails if e.get("type") == "marketing" and parse_dt(e["received_at"]) > u]
    counted = [e for e in promo_after if e.get("attribution") != "unverified"]
    excluded = len(promo_after) - len(counted)
    if counted:
        return {"status": "ineffective", "after": len(counted), "reason": f"停止後に販促メール{len(counted)}通"
                + (f"（帰属未確認{excluded}通は除外）" if excluded else "")}
    if excluded:
        return {"status": "pending", "after": None, "reason": f"停止後の販促メールは帰属未確認の{excluded}通のみ"}
    return {"status": "effective", "after": 0, "reason": f"{fmt_jst(svc.get('observed_until'))} まで販促0通"}


def emails_after_unsub(svc):
    return unsub_verdict(svc)["after"]


# ---------- 出力用のエスケープ ----------
_CTRL_RE = re.compile(r"[\x00-\x1f\x7f\u2028\u2029\u0085]+")
_MD_RE = re.compile(r"([\\`*_\[\]()!#|~{}+])")


def oneline(s):
    """改行・タブ・制御文字を空白1つに置き換える。"""
    return re.sub(r" {2,}", " ", _CTRL_RE.sub(" ", "" if s is None else str(s))).strip()


def md_cell(s):
    """Markdown の表セル・本文に外部由来の文字列を入れるための共通エスケープ。
    改行→空白、HTMLタグ→実体参照、| と画像/リンク記法などの Markdown 記号→バックスラッシュエスケープ。"""
    t = html.escape(oneline(s), quote=False)
    return _MD_RE.sub(r"\\\1", t)


def url_domain(url):
    """URL はドメインだけを表示する（パス・クエリのトークンは出さない）。"""
    try:
        h = urllib.parse.urlsplit(oneline(url)).hostname
    except ValueError:
        h = None
    return h or "（解析不能なURL）"
