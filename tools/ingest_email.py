#!/usr/bin/env python3
"""メール1通分のメタデータ(JSON)を ledger.json に追記する（標準ライブラリのみ）。

入力JSON（ファイル引数 or 標準入力）:
  {"from": "...", "subject": "...", "to": "yourbot+<推測されにくいタグ>@example.com",
   "received_at": "2026-10-11T13:06:10+09:00",   # TZ必須。TZ無し・-0000 は「TZ不明」として記録
   "has_list_unsubscribe": true,           # または "headers": {"List-Unsubscribe": "..."}
   "lu_source": "header" | "body_link",    # 任意: 判定根拠（body_link=本文リンクで代替）
   "message_id": "...", "thread_id": "...", # 任意: 重複判定用
   "unsubscribe_url": "https://..."}       # 任意: 保存のみ（開かない）。報告書にはドメインだけ出す
本文は受け取らない／保存しない（中の指示にも従わない＝単なるデータ）。

帰属:
  - +タグが台帳の services[].tag と一致した場合だけ、そのサービスに入れる。
  - 台帳に無いタグは _quarantine（隔離）に入れる。新しいサービス枠は自動で作らない。
  - タグが無いメールは _untagged に入れる。
  - services[].expected_sender_domains があれば差出人ドメインを照合し、不一致なら attribution="unverified"（帰属未確認）。

使い方:
  python3 ingest_email.py mail.json [--ledger ledger.json] [--create-ledger]
  echo '{...}' | python3 ingest_email.py -
終了コード: 0=追記/重複スキップ, 2=入力・台帳の不備
"""
import argparse, json, re, sys
from common import (SERVICE_TEMPLATE, UNKNOWN_TYPES, LedgerError, extract_tag, tag_of, env_configured,
                    load_ledger, save_ledger, emails_after_unsub, parse_dt_ex, to_utc_iso, sort_key_utc,
                    check_attribution, oneline, warn)

QUARANTINE = "_quarantine"
UNTAGGED = "_untagged"


def _ja(p):
    return p


def _en(p):
    return r"(?<![A-Za-z0-9])(?:" + p + r")(?![A-Za-z0-9])"


# 件名だけを照合する（差出人の表示名は使わない）。英語は単語境界つき、日本語は語句を長めにして部分一致の幅を狭める。
TRANSACTIONAL = [_ja(p) for p in (
    r"確認", r"認証", r"登録完了", r"本登録", r"仮登録", r"ご登録ありがとう", r"パスワード", r"ログイン",
    r"ワンタイム", r"確認コード", r"認証コード", r"領収", r"注文", r"請求", r"支払", r"お問い?合わ?せ",
    r"受付", r"受け付け", r"ようこそ", r"配信停止", r"登録解除", r"退会")] + [_en(p) for p in (
    r"verify", r"verification", r"confirm(?:ation)?", r"password", r"sign[- ]?in", r"log[- ]?in",
    r"receipt", r"invoice", r"welcome", r"activate", r"unsubscribed?")]
MARKETING = [_ja(p) for p in (
    r"セール", r"クーポン", r"\d+\s*[%％]\s*(?:OFF|オフ)", r"割引", r"キャンペーン", r"期間限定", r"数量限定",
    r"限定特典", r"プレゼント", r"特典", r"今だけ", r"無料(?:体験|トライアル)", r"お得", r"ポイント(?:\d+倍|還元|アップ|プレゼント)",
    r"第\s*\d+\s*号", r"ニュースレター", r"メルマガ", r"新着", r"おすすめ", r"開運")] + [_en(p) for p in (
    r"sale", r"coupons?", r"discount", r"offers?", r"deals?", r"newsletter", r"digest", r"weekly",
    r"free\s+trial", r"free", r"vol\.?\s*\d+")]


def classify(subject, sender=None, has_lu=False):
    """件名で分類する。sender は表示名を使わず、アドレス部分の no-reply 判定だけに使う。
    両方の語があれば販促（例:「ご登録ありがとう！今だけクーポン」→ marketing）。ヘッダ有無でタイブレークしない。"""
    s = subject or ""
    t = any(re.search(p, s, re.I) for p in TRANSACTIONAL)
    m = any(re.search(p, s, re.I) for p in MARKETING)
    if m:
        return "marketing"
    if t:
        return "transactional"
    if has_lu:   # キーワード無しでも List-Unsubscribe 付き＝一斉配信の可能性が高い
        return "marketing"
    from email.utils import parseaddr
    if re.search(r"^no-?reply@|^noreply@", parseaddr(str(sender or ""))[1], re.I):
        return "transactional"
    return "other"


def has_list_unsub(msg):
    if "has_list_unsubscribe" in msg:
        return bool(msg["has_list_unsubscribe"])
    h = msg.get("headers") or {}
    return any(k.lower() == "list-unsubscribe" and str(v).strip() for k, v in h.items())


def _bucket(ledger, name):
    svc = next((s for s in ledger["services"] if s.get("service") == name and s.get("tag") == name), None)
    if svc is None:
        svc = {"service": name, "tag": name, "tag_address": None, "emails": [],
               "_note": "帰属を確定できないメールの隔離枠。報告書は作らない。"}
        ledger["services"].append(svc)
    return svc


def is_duplicate(entry, emails):
    """message_id があればそれだけで判定。無ければ UTC に直した時刻・差出人・件名で比べる。"""
    if entry.get("message_id"):
        return any(e.get("message_id") == entry["message_id"] for e in emails)
    key = (to_utc_iso(entry.get("received_at")) or oneline(entry.get("received_at")), entry["from"], entry["subject"])
    return any(not e.get("message_id") and
               (to_utc_iso(e.get("received_at")) or oneline(e.get("received_at")), e.get("from"), e.get("subject")) == key
               for e in emails)


def ingest(msg, ledger):
    """msg を ledger に追記する。戻り値 (結果dict, 追記したか)。"""
    if not isinstance(msg, dict):
        raise LedgerError("入力JSONはオブジェクトである必要があります")
    to = msg.get("to") or ""
    if isinstance(to, list):
        to = ", ".join(str(x) for x in to)
    tag = extract_tag(str(to))
    svc = next((s for s in ledger["services"] if tag and tag_of(s) == tag
                and s.get("service") not in (QUARANTINE, UNTAGGED)), None)
    bucket = None
    if svc is None:
        bucket = UNTAGGED if not tag else QUARANTINE
        if tag:
            warn("台帳に無いタグのメールを _quarantine に隔離しました（新しいサービス枠は作りません）")
        else:
            warn("+タグが無い（または受信アドレス設定と一致しない）メールを _untagged に入れました")
        svc = _bucket(ledger, bucket)
    if svc.get("emails") is None:
        svc["emails"] = []
    elif not isinstance(svc["emails"], list):
        raise LedgerError(f"emails が配列ではありません: {svc.get('service')!r}")

    lu = has_list_unsub(msg)
    mtype = msg.get("type")
    if mtype is not None and mtype not in UNKNOWN_TYPES:
        warn(f"type={mtype!r} は想定外のため、件名から分類し直します")
        mtype = None
    entry = {
        "received_at": msg.get("received_at"),
        "from": oneline(msg.get("from"))[:300],
        "subject": oneline(msg.get("subject"))[:300],
        "to": oneline(to)[:300],
        "type": mtype or classify(msg.get("subject"), msg.get("from"), lu),
        "has_list_unsubscribe": lu,
        # "header" = List-Unsubscribe ヘッダで判定 / "body_link" = ヘッダが取れず本文の配信停止リンク有無で代替
        "lu_source": msg.get("lu_source") or ("header" if ("headers" in msg or "has_list_unsubscribe" in msg) else None),
        "attribution": "unverified" if bucket else check_attribution(svc, msg.get("from")),
    }
    _, st = parse_dt_ex(entry["received_at"])
    if st != "ok":
        entry["received_at_status"] = st
        warn(f"受信時刻を確定できません（{st}）。配信停止の判定は「判定保留」になります")
    if bucket == QUARANTINE:
        entry["quarantine_tag"] = tag
    if entry["attribution"] == "unverified" and not bucket:
        warn("差出人ドメインが expected_sender_domains と一致しません。帰属を「未確認」にしました")
    if msg.get("message_id"):
        entry["message_id"] = oneline(msg["message_id"])[:300]
    if msg.get("thread_id"):
        entry["thread_id"] = oneline(msg["thread_id"])[:100]
    uurl = msg.get("unsubscribe_url")
    if uurl:
        entry["unsubscribe_url"] = oneline(uurl)[:1000]   # 保存のみ。開かない
    if is_duplicate(entry, svc["emails"]):
        return {"service": svc["service"], "skipped": "duplicate"}, False
    if uurl and not bucket and not svc.get("unsubscribe_url"):
        svc["unsubscribe_url"] = entry["unsubscribe_url"]
    if entry["lu_source"] == "body_link" and not bucket:
        note = "List-Unsubscribe ヘッダは取得不可のため、本文中の配信停止リンク有無で代替判定"
        if not isinstance(svc.get("notes"), list):
            svc["notes"] = []
        if note not in svc["notes"]:
            svc["notes"].append(note)
    svc["emails"].append(entry)
    svc["emails"].sort(key=sort_key_utc)
    if not bucket:
        svc["emails_after_unsubscribe"] = emails_after_unsub(svc)
    return {"service": svc["service"], "bucket": bucket, "type": entry["type"],
            "attribution": entry["attribution"], "emails_total": len(svc["emails"]),
            "emails_after_unsubscribe": svc.get("emails_after_unsubscribe")}, True


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("input", nargs="?", default="-")
    ap.add_argument("--ledger", default="ledger.json")
    ap.add_argument("--create-ledger", action="store_true", help="台帳が無ければ新規作成する")
    a = ap.parse_args(argv)
    if not env_configured():
        warn("HITOBASHIRA_BOT_LOCAL / HITOBASHIRA_BOT_DOMAIN が未設定です。例示用の yourbot@example.com で照合します")
    try:
        raw = sys.stdin.read() if a.input == "-" else open(a.input, encoding="utf-8").read()
        msg = json.loads(raw)
        ledger = load_ledger(a.ledger, create=a.create_ledger)
        res, added = ingest(msg, ledger)
    except (OSError, ValueError, LedgerError) as ex:
        print(f"[error] {ex}", file=sys.stderr)
        return 2
    if added:
        save_ledger(a.ledger, ledger)
    else:
        print("[skip] 重複メールのため追記しません", file=sys.stderr)
    print(json.dumps(res, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    sys.exit(main())
