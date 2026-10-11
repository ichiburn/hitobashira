#!/usr/bin/env python3
"""ledger.json → サービスごとの被害報告書(Markdown) + 絵馬風PNG

使い方: python3 report.py [--ledger ledger.json] [--out out/] [--generated-at "2026-10-11 16:00 JST"] [--no-png]
数値は ledger の実データのみ使用。データが無い項目は「未観測」、観測して無かった項目は「なし（0件）」。
Markdown 生成は標準ライブラリのみ。PNG（絵馬）は Pillow と日本語フォントがある場合だけ生成する（無ければ警告）。
報告書・絵馬には +タグ付きアドレスを出さない。メール由来の文字列は md_cell でエスケープし、URL はドメインだけ出す。
終了コード: 0=正常, 1=警告あり（PNG未生成など）, 2=台帳の不備
"""
import argparse, glob, hashlib, os, re, sys
from datetime import datetime
from common import (NA, PENDING, UNKNOWN_TYPES, JST, LedgerError, load_ledger, fmt_jst, parse_dt, unsub_verdict,
                    tag_of, emails_of, md_cell, oneline, url_domain, warn)

ZERO = "なし（0件）"
ATTR_LABEL = {"trusted": "認証済み", "verified": "ドメイン一致（旧形式）", "domain_only": "ドメイン一致のみ（認証なし）",
              "unverified": "未確認", "unchecked": "照合なし"}


def slug(s):
    return re.sub(r"[^A-Za-z0-9_-]+", "_", s or "service").strip("_") or "service"


def out_basename(svc):
    """出力ファイル名。可読部分＋サービス識別子のハッシュで衝突を避ける（a.b と a_b、日本語名同士など）。"""
    ident = f"{svc.get('service') or ''}\x00{tag_of(svc) or ''}"
    h = hashlib.sha256(ident.encode("utf-8")).hexdigest()[:10]
    return f"{slug(tag_of(svc) or svc.get('service'))[:40]}_{h}"


def _list_metric(svc, key, observed):
    """null/キー無し → 未観測。[] → 観測済みなら「なし（0件）」、未観測なら「未観測」。"""
    v = svc.get(key)
    if v is None:
        return None, NA
    if not isinstance(v, list):
        v = [v]
    v = [oneline(x) for x in v if oneline(x)]
    if not v:
        return [], (ZERO if observed else NA)
    return v, None


def metrics(svc):
    emails = emails_of(svc)
    signed = parse_dt(svc.get("signed_up_at"))
    observed = svc.get("signed_up_at") is not None
    m = {}
    req, z = _list_metric(svc, "required_fields", observed)
    m["required"] = z or "、".join(req)
    po, z = _list_metric(svc, "prechecked_optins", observed)
    m["prechecked"] = z or f"{len(po)}件（{'、'.join(po)}）"
    dp, z = _list_metric(svc, "dark_patterns", observed)
    m["dark"] = dp or []
    m["dark_n"] = z or f"{len(dp)}件"
    ou = svc.get("observed_until")
    if emails:
        cnt = {k: sum(1 for e in emails if e.get("type") == k) for k in UNKNOWN_TYPES}
        unk = len(emails) - sum(cnt.values())
        if unk:
            warn(f"{svc.get('service')!r}: 分類が想定外のメールが{unk}通あります（「不明」として数えます）")
        m["mail"] = (f"{len(emails)}通（販促{cnt['marketing']}・手続き{cnt['transactional']}・他{cnt['other']}"
                     + (f"・不明{unk}" if unk else "") + "）")
        lu = sum(1 for e in emails if e.get("has_list_unsubscribe"))
        m["lu"] = f"{lu}/{len(emails)}通"
        ac = {k: sum(1 for e in emails if e.get("attribution") == k)
              for k in ("trusted", "verified", "domain_only", "unverified", "unchecked")}
        other = len(emails) - sum(ac.values())
        parts = [f"{ATTR_LABEL[k]}{n}" for k, n in ac.items() if n]
        if other:
            parts.append(f"記録なし{other}")
        m["attr"] = "・".join(parts) + "（通）"
    elif emails == [] and parse_dt(ou):
        m["mail"] = f"0通（{fmt_jst(ou)} まで観測）"
        m["lu"] = m["attr"] = "—"
    else:
        m["mail"] = m["lu"] = m["attr"] = NA
    emails = emails or []
    times = sorted(t for t in (parse_dt(e.get("received_at")) for e in emails) if t)
    m["first"] = m["span"] = NA
    if signed and times:
        sec = int((times[0] - signed).total_seconds())
        m["first"] = f"{sec // 60}分{sec % 60}秒" if sec >= 0 else NA
        span_h = max((times[-1] - signed).total_seconds() / 3600, 0)
        m["span"] = f"登録後{span_h:.1f}時間で{len(times)}通（時刻が確定した分のみ）"
    m["observed_until"] = fmt_jst(ou) or NA
    v = unsub_verdict(svc)
    m["unsub"] = fmt_jst(svc.get("unsubscribed_at")) or NA
    m["verdict_status"] = v["status"]
    if v["status"] == "unobserved":
        m["verdict"] = "配信停止: " + NA
    elif v["status"] == "pending":
        m["verdict"] = "配信停止: " + PENDING
    elif v["status"] == "effective":
        m["verdict"] = "配信停止: 観測期間内に停止後の販促を検出せず"
    else:
        m["verdict"] = f"配信停止: 効かず（停止後 販促{v['after']}通）"
    m["verdict_reason"] = v["reason"]
    m["after"] = f"{v['after']}通" if v["after"] is not None else (PENDING if v["status"] == "pending" else NA)
    return m


def render_md(svc, m, sample, generated_at):
    c = md_cell
    L = []
    if sample:
        L += ["> **【SAMPLE】これはダミーデータによる見本です。実在サービスの観測結果ではありません。**", ""]
    L += [f"# 被害報告書：{c(svc.get('service'))}", "",
          "- 人柱アドレス: （非表示。+タグは推測・偽造を防ぐため成果物に出さない）",
          f"- 登録ページ: {c(url_domain(svc['signup_url'])) + '（ドメインのみ）' if svc.get('signup_url') else NA}",
          f"- 登録日時: {fmt_jst(svc.get('signed_up_at')) or NA}",
          f"- 観測期限: {m['observed_until']}", "",
          "## 登録時の観測", "",
          "| 項目 | 結果 |", "|---|---|",
          f"| 必須入力項目 | {c(m['required'])} |",
          f"| 最初からチェック済みのオプトイン | {c(m['prechecked'])} |",
          f"| ダークパターン | {c(m['dark_n'])} |", ""]
    if m["dark"]:
        L += [f"- {c(d)}" for d in m["dark"]] + [""]
    uurl = svc.get("unsubscribe_url")
    L += ["## 登録後の受信メール", "",
          "| 項目 | 結果 |", "|---|---|",
          f"| 受信数 | {c(m['mail'])} |",
          f"| 最初のメールまで | {c(m['first'])} |",
          f"| 期間 | {c(m['span'])} |",
          f"| List-Unsubscribe ヘッダ付き | {c(m['lu'])} |",
          f"| 帰属（ドメイン照合＋認証結果） | {c(m['attr'])} |",
          f"| 配信停止URL（未アクセス・ドメインのみ） | {c(url_domain(uurl)) if uurl else NA} |", ""]
    emails = emails_of(svc) or []
    if emails:
        L += ["| 受信日時 | 差出人 | 件名 | 分類 | List-Unsub | 帰属 |", "|---|---|---|---|---|---|"]
        attr = ATTR_LABEL
        for e in emails:
            L.append(f"| {c(fmt_jst(e.get('received_at')) or NA)} | {c(e.get('from') or NA)} | {c(e.get('subject'))} | "
                     f"{c(e.get('type') or NA)} | {'あり' if e.get('has_list_unsubscribe') else 'なし'} | "
                     f"{attr.get(e.get('attribution'), '照合なし')} |")
        L.append("")
    else:
        L += [f"受信メール: {c(m['mail'])}", ""]
    L += ["## 配信停止", "",
          f"- 配信停止操作: {c(m['unsub'])}",
          f"- 停止後に届いた販促メール: {c(m['after'])}",
          f"- 判定: **{c(m['verdict'])}**（{c(m['verdict_reason'])}）",
          "- 判定は販促メール（分類 marketing）のうち、帰属が「認証済み」のものだけで行う。受信時刻が不明なメールが1通でもある場合、"
          "配信停止時刻が日付だけ・TZ不明の場合、観測期限が無い場合、停止後に分類不明のメールがある場合、"
          "帰属を確定できないメールしかない場合は「判定保留」。",
          "- 「検出せず」は観測期間内の結果で、恒久的な停止成功を示さない。", ""]
    notes = svc.get("notes")
    if isinstance(notes, str):
        notes = [notes]
    if notes:
        L += ["## メモ", ""] + [f"- {c(n)}" for n in notes] + [""]
    shots = svc.get("screenshots") or []
    if isinstance(shots, str):
        shots = [shots]
    L += ["## スクリーンショット", ""] + ([f"- {c(s)}" for s in shots] if shots else [NA]) + [""]
    L += ["## 検証の条件と限界", "",
          "- 数値は台帳（ledger.json）の記録だけから集計した。台帳に無い項目は「未観測」。",
          "- 帰属は +タグ・差出人ドメイン（expected_sender_domains）・受信基盤が付けた Authentication-Results の解釈で判断した。"
          "SPF/DKIM/DMARC の検証そのもの（DNS 参照・署名検証）はしていない。任意入力の認証ヘッダは信用しない。",
          "- メールの分類は件名のキーワードによる推定で、精度は未評価。", ""]
    L += ["---", f"生成: 人柱くん / {c(generated_at)}"]
    return "\n".join(L) + "\n"


# ---------- 絵馬画像 ----------
def find_font(bold=True):
    from PIL import ImageFont
    pats = ["/usr/share/fonts/**/NotoSerifCJK-Bold.ttc", "/usr/share/fonts/**/NotoSansCJK-Bold.ttc",
            "/usr/share/fonts/**/NotoSerifCJK*.ttc", "/usr/share/fonts/**/NotoSansCJK*.ttc",
            "/usr/share/fonts/**/*CJK*", "/usr/share/fonts/**/*IPA*", "/usr/share/fonts/**/*Gothic*"]
    for p in pats:
        for f in sorted(glob.glob(p, recursive=True)):
            for idx in range(10):
                try:
                    fo = ImageFont.truetype(f, 20, index=idx)
                except Exception:
                    break
                if "JP" in " ".join(fo.getname()):
                    return f, idx
            return f, 0
    raise RuntimeError("日本語フォントがありません: sudo apt-get install -y fonts-noto-cjk")


FONT = None


def F(size):
    from PIL import ImageFont
    global FONT
    FONT = FONT or find_font()
    return ImageFont.truetype(FONT[0], size, index=FONT[1])


def wrap(draw, text, font, width):
    lines, cur = [], ""
    for ch in text:
        if draw.textlength(cur + ch, font=font) > width:
            lines.append(cur); cur = ch
        else:
            cur += ch
    return lines + ([cur] if cur else [])


def fit_font(d, text, width, size, min_size):
    """幅に収まるまで縮小する。最小サイズでも入らなければ省略記号で切る。"""
    while size > min_size and d.textlength(text, font=F(size)) > width:
        size -= 2
    f = F(size)
    if d.textlength(text, font=f) > width:
        while text and d.textlength(text + "…", font=f) > width:
            text = text[:-1]
        text += "…"
    return f, text


def render_png(svc, m, sample, path, generated_at):
    from PIL import Image, ImageDraw
    W, H = 1200, 1000
    img = Image.new("RGB", (W, H), (178, 34, 34))  # 朱色の背景
    d = ImageDraw.Draw(img)
    # 木の板（屋根型）
    x0, x1, top, shoulder, bottom = 90, W - 90, 90, 230, H - 70
    board = [(x0, shoulder), (W // 2, top), (x1, shoulder), (x1, bottom), (x0, bottom)]
    d.polygon([(x + 10, y + 12) for x, y in board], fill=(90, 20, 20))  # 影
    d.polygon(board, fill=(222, 184, 135), outline=(120, 80, 40), width=6)
    grain = Image.new("RGB", (W, H), (222, 184, 135)); gd = ImageDraw.Draw(grain)
    for i in range(16):  # 木目（板の内側だけに描く）
        y = top + 60 + i * 50
        gd.arc([x0 - 200, y - 40, x1 + 200, y + 40], 190, 350, fill=(205, 165, 115), width=2)
    mask = Image.new("L", (W, H), 0); ImageDraw.Draw(mask).polygon(board, fill=255)
    img.paste(grain, (0, 0), mask); d = ImageDraw.Draw(img)
    d.polygon(board, outline=(120, 80, 40), width=6)
    # 屋根の縁取りと紐
    d.line([(x0 + 20, shoulder + 10), (W // 2, top + 22), (x1 - 20, shoulder + 10)], fill=(120, 80, 40), width=5)
    d.ellipse([W // 2 - 16, top + 50, W // 2 + 16, top + 82], fill=(70, 45, 20))
    d.line([(W // 2 - 8, top + 66), (W // 2 - 70, 10)], fill=(220, 20, 60), width=8)
    d.line([(W // 2 + 8, top + 66), (W // 2 + 70, 10)], fill=(220, 20, 60), width=8)

    ink = (40, 25, 15)
    t = "奉納　被害報告"
    f = F(64); d.text(((W - d.textlength(t, font=f)) / 2, top + 95), t, font=f, fill=(150, 20, 20))
    name = oneline(svc.get("service")) or "?"
    f, name = fit_font(d, name, x1 - x0 - 80, 46, 26); d.text(((W - d.textlength(name, font=f)) / 2, top + 175), name, font=f, fill=ink)

    rows = [("必須項目", m["required"]), ("事前チェック済", m["prechecked"]),
            ("ダークパターン", m["dark_n"] + (("：" + "／".join(m["dark"])) if m["dark"] else "")),
            ("受信メール", m["mail"]), ("初メールまで", m["first"]),
            ("List-Unsub付", m["lu"]), ("停止後の受信", m["after"])]
    y, lx, vx, maxw = top + 260, x0 + 60, x0 + 330, x1 - 60 - (x0 + 330)
    fl, fv = F(30), F(30)
    for label, val in rows:
        d.text((lx, y), label, font=fl, fill=(120, 60, 20))
        val = oneline(val)
        lines = wrap(d, val, fv, maxw)
        if len(lines) > 2:  # 2行に収まらない分は省略記号を付けて切る
            last = lines[1]
            while last and d.textlength(last + "…", font=fv) > maxw:
                last = last[:-1]
            lines = [lines[0], last + "…"]
        for ln in lines:
            d.text((vx, y), ln, font=fv, fill=(130, 130, 130) if val == NA else ink); y += 40
        y += 8
    # 判定
    v = m["verdict"]
    f, v = fit_font(d, v, x1 - x0 - 100, 42, 24); tw = d.textlength(v, font=f)
    col = (130, 130, 130) if m["verdict_status"] in ("unobserved", "pending") else ((30, 110, 50) if m["verdict_status"] == "effective" else (180, 20, 20))
    d.rounded_rectangle([(W - tw) / 2 - 24, y + 8, (W + tw) / 2 + 24, y + 70], 12, outline=col, width=4)
    d.text(((W - tw) / 2, y + 14), v, font=f, fill=col)
    foot = f"人柱くん　{oneline(generated_at)}"  # タグ付きアドレスは出さない
    f = F(22); d.text(((W - d.textlength(foot, font=f)) / 2, bottom - 45), foot, font=f, fill=(110, 75, 40))

    if sample:  # 斜めの SAMPLE スタンプ
        st = Image.new("RGBA", (W, H), (0, 0, 0, 0)); sd = ImageDraw.Draw(st)
        f = F(150); txt = "SAMPLE"; tw = sd.textlength(txt, font=f)
        sd.rectangle([(W - tw) / 2 - 30, H / 2 - 100, (W + tw) / 2 + 30, H / 2 + 110], outline=(220, 0, 0, 150), width=12)
        sd.text(((W - tw) / 2, H / 2 - 105), txt, font=f, fill=(220, 0, 0, 130))
        sd.text((W / 2 - 230, H / 2 + 115), "ダミーデータ・実在の観測ではありません", font=F(30), fill=(220, 0, 0, 200))
        st = st.rotate(18, center=(W / 2, H / 2))
        img = Image.alpha_composite(img.convert("RGBA"), st).convert("RGB")
    img.save(path)


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("--ledger", default="ledger.json")
    ap.add_argument("--out", default="out")
    ap.add_argument("--generated-at", default=None, help="生成日時の表記（再現用に固定したいとき）")
    ap.add_argument("--no-png", action="store_true", help="絵馬PNGを作らない")
    a = ap.parse_args(argv)
    gen = a.generated_at or datetime.now(JST).strftime("%Y-%m-%d %H:%M JST")
    try:
        ledger = load_ledger(a.ledger)
    except (OSError, ValueError, LedgerError) as ex:
        print(f"[error] {ex}", file=sys.stderr)
        return 2
    sample = bool(ledger.get("sample"))
    services = [s for s in ledger["services"] if isinstance(s, dict)]
    skipped = [s.get("service") for s in services if str(s.get("service") or "").startswith("_")]
    if skipped:
        warn(f"隔離枠・タグ無し枠は報告書を作りません: {', '.join(map(str, skipped))}")
    services = [s for s in services if not str(s.get("service") or "").startswith("_")]
    if not services:
        print("services が空です（まだ登録データがありません）", file=sys.stderr)
        return 1
    os.makedirs(a.out, exist_ok=True)
    status, png_ok, used = 0, not a.no_png, set()
    if png_ok:
        try:
            import PIL  # noqa: F401
            find_font_cached()
        except Exception as ex:  # Pillow かフォントが無い
            warn(f"絵馬PNGを生成しません（{ex}）")
            png_ok, status = False, 1
    for svc in services:
        try:
            m = metrics(svc)
        except LedgerError as ex:
            print(f"[error] {ex}", file=sys.stderr)
            return 2
        if svc.get("emails") is None:
            warn(f"{svc.get('service')!r}: emails がありません（未観測として扱います）")
        name = ("SAMPLE_" if sample else "") + out_basename(svc)
        if name in used:
            raise RuntimeError(f"出力ファイル名が衝突しました: {name}")
        used.add(name)
        base = os.path.join(a.out, name)
        with open(base + ".md", "w", encoding="utf-8") as f:
            f.write(render_md(svc, m, sample, gen))
        outs = [base + ".md"]
        if png_ok:
            render_png(svc, m, sample, base + ".png", gen)
            outs.append(base + ".png")
        print(*outs)
    return status


def find_font_cached():
    global FONT
    FONT = FONT or find_font()
    return FONT


if __name__ == "__main__":
    sys.exit(main())
