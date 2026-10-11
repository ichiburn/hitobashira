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
    """台帳の expected_sender_domains と差出人ドメインを照合する（ドメインの文字列照合だけ）。
    戻り値: "verified"（一致） / "unverified"（不一致・差出人不明＝帰属未確認） / "unchecked"（照合先なし）
    ※ From のドメイン一致は本人性の証明ではない。帰属の最終判定は attribute() を使う。"""
    doms = [str(d).strip().lower().strip(".") for d in (svc.get("expected_sender_domains") or []) if str(d).strip()]
    if not doms:
        return "unchecked"
    sd = sender_domain(from_field)
    if sd and any(sd == d or sd.endswith("." + d) for d in doms):
        return "verified"
    return "unverified"


# 帰属の状態（ledger の emails[].attribution）
#   trusted     … 差出人ドメインが一致し、かつ信頼できる受信基盤の認証結果が pass（または自己所有の閉じた環境で送信元が既知）
#   domain_only … 差出人ドメインは一致したが、信頼できる認証結果がない（From は偽装できるので確定根拠にしない）
#   unverified  … 差出人ドメイン不一致、差出人不明、または信頼できる認証結果が fail
#   unchecked   … 照合先（expected_sender_domains）が無い
#   verified    … 旧形式（#2 以前の ingest が書いた値）。認証結果の記録が無いので信用しない（数えない＝判定保留）。新しい ingest は書かない
COUNTED_ATTRIBUTIONS = ("trusted",)


def attribute(svc, from_field, auth=None, closed_env=False):
    """帰属を根拠付きで決める。auth は auth_results.evaluate() の戻り値（信頼できない場合は status=none）。
    closed_env=True は「自分で起動したローカル受信箱に、自分で起動したテストサイトだけが送る」構成を
    呼び出し側（アダプタ）が保証した場合だけ。送信ドメイン認証の代わりではない。
    戻り値: (attribution, basis)"""
    dom = check_attribution(svc, from_field)
    if dom == "unchecked":
        return "unchecked", "照合先（expected_sender_domains）なし"
    if dom == "unverified":
        return "unverified", "差出人ドメインが照合先と不一致"
    st = (auth or {}).get("status", "none")
    if (auth or {}).get("trusted") and st == "fail":
        return "unverified", "信頼できる認証結果が fail: " + str((auth or {}).get("basis"))
    if (auth or {}).get("trusted") and st == "pass":
        return "trusted", str(auth.get("basis"))
    if closed_env:
        return "trusted", "自己所有の閉じたローカル環境（送信元はテストサイトのみ。SPF/DKIM/DMARC の検証ではない）"
    return "domain_only", "差出人ドメインは一致。信頼できる認証結果なし" + (
        f"（{auth.get('basis')}）" if auth and auth.get("basis") else "")


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
def receiver_ok(svc):
    """受信経路が観測期限まで動いていたことの記録（receiver_checks）を確認する。
    receiver_checks: [{"at": TZ付き時刻, "kind": "canary" など, "ok": true}]
    停止時刻以前と observed_until 以降の両方に ok の確認があれば True。"""
    checks = svc.get("receiver_checks")
    if not isinstance(checks, list):
        return False, "受信経路の稼働確認（receiver_checks）なし"
    u, ou = parse_dt(svc.get("unsubscribed_at")), parse_dt(svc.get("observed_until"))
    oks = [parse_dt(c.get("at")) for c in checks if isinstance(c, dict) and c.get("ok") is True]
    oks = [t for t in oks if t]
    if any(not (isinstance(c, dict) and c.get("ok") is True) for c in checks):
        return False, "受信経路の稼働確認に失敗が含まれる"
    if not (u and ou and any(t <= u for t in oks) and any(t >= ou for t in oks)):
        return False, "停止前と観測期限後の両方で受信経路を確認できていない"
    return True, "停止前と観測期限後に受信経路の稼働を確認"


def unsub_verdict(svc):
    """配信停止が効いたかを、販促メール（type=marketing）だけで判定する（fail-closed）。
    戻り値 dict: status = unobserved | pending | effective | ineffective, after = 通数 or None, reason
    - unsubscribed_at が無い → unobserved（未観測）
    - unsubscribed_at が日付だけ・TZ不明・解釈不能 → pending（日付だけの場合は「その日の終わり」とはみなさない）
    - observed_until が無い、または停止時刻以前 → pending（停止後を観測していない）
    - 受信時刻が不明（欠損・TZ不明・解釈不能）なメールが1通でもある → pending
    - 停止後の販促メールは帰属が trusted のものだけ数える。
      domain_only / unchecked / unverified / 旧形式の verified は「効かず」の確定根拠にしない（それだけなら pending）
    - 停止後に分類 other・不明のメールがある場合、0通とは断定しない（pending）
    - effective は「観測期間内に停止後の販促メールを検出せず」の意味で、恒久的な停止成功を示さない。
      どの台帳も strict として扱い（evidence_policy の有無にかかわらず）、受信経路の稼働確認（receiver_checks）が無ければ pending
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
    after = [e for e in emails if parse_dt(e["received_at"]) > u]
    promo_after = [e for e in after if e.get("type") == "marketing"]
    counted = [e for e in promo_after if e.get("attribution") in COUNTED_ATTRIBUTIONS]
    excluded = len(promo_after) - len(counted)
    legacy = sum(1 for e in promo_after if e.get("attribution") == "verified")
    legacy_note = f"（うち旧形式の帰属 verified {legacy}通は、旧形式のため未確認）" if legacy else ""
    if counted:
        return {"status": "ineffective", "after": len(counted), "reason": f"停止後に販促メール{len(counted)}通"
                + (f"（帰属を確定できない{excluded}通は除外）" if excluded else "") + legacy_note}
    if excluded:
        return {"status": "pending", "after": None,
                "reason": f"停止後の販促メールは帰属を確定できない{excluded}通のみ（domain_only / unchecked / unverified / 旧形式 verified）"
                          + legacy_note}
    unclear = [e for e in after if e.get("type") not in ("marketing", "transactional")]
    if unclear:
        return {"status": "pending", "after": None, "reason": f"停止後に分類が other・不明のメールが{len(unclear)}通ある"}
    ok, why = receiver_ok(svc)   # evidence_policy が無い旧形式の台帳も strict として扱う
    if not ok:
        return {"status": "pending", "after": None, "reason": why}
    return {"status": "effective", "after": 0,
            "reason": f"{fmt_jst(svc.get('observed_until'))} までの観測期間内に、停止後の販促メールを検出せず"
                      "（恒久的な停止成功を示すものではない）"}


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
