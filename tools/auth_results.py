"""Authentication-Results ヘッダ（RFC 8601）の解釈（標準ライブラリのみ）。

ここで行うのはヘッダ文字列の**解釈だけ**で、SPF / DKIM / DMARC の検証そのもの（DNS 参照・署名検証）はしない。
ヘッダは送信者が自由に書けるため、次の条件をすべて満たすときだけ「信頼できる認証結果」として扱う:
  1. 受信基盤から直接取り出したヘッダである（任意入力 JSON に書かれた文字列ではない）。呼び出し側が trusted_source=True を渡す。
  2. 一番上（受信基盤が最後に付けた）の Authentication-Results であり、その authserv-id が設定済みの信頼リストにある。
     それより下の Authentication-Results は、authserv-id が一致していても送信者が偽造できるため使わない。
"""
import re

_COMMENT_RE = re.compile(r"\((?:[^()\\]|\\.)*\)")


def _strip_comments(s):
    prev = None
    while prev != s:          # 入れ子のコメントも外す
        prev, s = s, _COMMENT_RE.sub(" ", s)
    return s


def _split_semicolons(s):
    out, cur, q = [], "", False
    for ch in s:
        if ch == '"':
            q = not q
        if ch == ";" and not q:
            out.append(cur); cur = ""
        else:
            cur += ch
    out.append(cur)
    return [x.strip() for x in out]


def parse(value):
    """1つの Authentication-Results ヘッダ値を解釈する。
    戻り値: {"authserv_id": str|None, "results": [{"method", "result", "props": {"header.from": ...}}]}"""
    s = _strip_comments(re.sub(r"\s+", " ", str(value or ""))).strip()
    parts = _split_semicolons(s)
    head = parts[0].split()
    authserv = head[0].lower() if head else None
    results = []
    for p in parts[1:]:
        toks = p.split()
        if not toks:
            continue
        m = re.match(r"^([A-Za-z0-9_.-]+)\s*=\s*([A-Za-z0-9_-]+)$", toks[0])
        if not m:
            continue
        method, result = m.group(1).lower(), m.group(2).lower()
        if method == "none":
            continue
        props = {}
        for t in toks[1:]:
            kv = re.match(r'^([A-Za-z0-9_.-]+)\s*=\s*"?([^"]*)"?$', t)
            if kv:
                props[kv.group(1).lower()] = kv.group(2).strip().lower()
        results.append({"method": method, "result": result, "props": props})
    return {"authserv_id": authserv, "results": results}


def _dom(v):
    v = (v or "").strip().strip(".").lower()
    return v.rsplit("@", 1)[-1] if v else ""


def aligned(a, b):
    """緩い整合（relaxed alignment）の近似: どちらかがもう一方のサブドメインなら整合とみなす。
    公開サフィックスリストは使わないため、組織ドメインの判定は近似である。"""
    a, b = _dom(a), _dom(b)
    return bool(a and b) and (a == b or a.endswith("." + b) or b.endswith("." + a))


def evaluate(ar_headers, from_domain, trusted_authserv_ids=(), trusted_source=False):
    """Authentication-Results ヘッダ群（上から順）を評価する。
    戻り値 dict:
      status: "pass"（信頼できる認証結果で整合あり） / "fail"（信頼できる結果が fail） / "none"（信頼できる結果なし）
      trusted: bool, authserv_id, spf / dkim / dmarc（解釈した結果。無ければ None）, basis（理由）"""
    hdrs = [h for h in (ar_headers or []) if str(h or "").strip()]
    out = {"status": "none", "trusted": False, "authserv_id": None, "spf": None, "dkim": None, "dmarc": None,
           "aligned": False, "basis": ""}
    if not hdrs:
        out["basis"] = "Authentication-Results ヘッダなし"
        return out
    top = parse(hdrs[0])
    out["authserv_id"] = top["authserv_id"]
    for r in top["results"]:
        if r["method"] in ("spf", "dkim", "dmarc") and out[r["method"]] is None:
            out[r["method"]] = r["result"]
    trusted_ids = {str(x).strip().lower() for x in trusted_authserv_ids if str(x).strip()}
    if not trusted_source:
        out["basis"] = "受信基盤から直接取得したヘッダではない（任意入力の文字列は信用しない）"
        return out
    if top["authserv_id"] not in trusted_ids:
        out["basis"] = "一番上の Authentication-Results の authserv-id が信頼リストにない"
        return out
    out["trusted"] = True
    res = top["results"]
    dmarc = [r for r in res if r["method"] == "dmarc"]
    for r in dmarc:
        hf = r["props"].get("header.from")
        if r["result"] == "pass" and (not hf or aligned(hf, from_domain)):
            out.update(status="pass", aligned=True, basis="DMARC pass（From と整合）")
            return out
        if r["result"] in ("fail", "quarantine", "reject"):
            out.update(status="fail", basis="DMARC fail")
            return out
    for r in res:
        if r["method"] == "dkim" and r["result"] == "pass" and aligned(r["props"].get("header.d") or r["props"].get("header.i"), from_domain):
            out.update(status="pass", aligned=True, basis="DKIM pass（署名ドメインが From と整合）")
            return out
    for r in res:
        if r["method"] == "spf" and r["result"] == "pass" and aligned(r["props"].get("smtp.mailfrom"), from_domain):
            out.update(status="pass", aligned=True, basis="SPF pass（エンベロープ送信者が From と整合）")
            return out
    if any(r["method"] in ("spf", "dkim") and r["result"] in ("fail", "softfail", "permerror") for r in res):
        out.update(status="fail", basis="SPF/DKIM が fail で、整合した pass なし")
        return out
    out["basis"] = "信頼できる結果に、From と整合した pass がない"
    return out
