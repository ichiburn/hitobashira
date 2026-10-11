#!/usr/bin/env python3
"""ledger.json → サービスごとの被害報告書(Markdown) + 絵馬風PNG

使い方: python3 report.py [--ledger ledger.json] [--out out/]
数値は ledger の実データのみ使用。データが無い項目は「未観測」。
"""
import argparse, glob, os, re
from datetime import datetime
from PIL import Image, ImageDraw, ImageFont
from common import load_ledger, fmt_jst, parse_dt, emails_after_unsub, tag_of, JST

NA = "未観測"


def slug(s):
    return re.sub(r"[^A-Za-z0-9_-]+", "_", s or "service").strip("_") or "service"


def metrics(svc):
    emails = svc.get("emails") or []
    signed = parse_dt(svc.get("signed_up_at"))
    m = {}
    m["required"] = "、".join(svc.get("required_fields") or []) or NA
    po = svc.get("prechecked_optins") or []
    m["prechecked"] = (f"{len(po)}件（{'、'.join(po)}）" if po
                      else ("なし（0件）" if svc.get("signed_up_at") and "prechecked_optins" in svc else NA))
    dp = svc.get("dark_patterns") or []
    m["dark"] = dp
    m["dark_n"] = f"{len(dp)}件" if dp else NA
    if emails:
        cnt = {k: sum(1 for e in emails if e.get("type") == k) for k in ("transactional", "marketing", "other")}
        m["mail"] = f"{len(emails)}通（販促{cnt['marketing']}・手続き{cnt['transactional']}・他{cnt['other']}）"
        lu = sum(1 for e in emails if e.get("has_list_unsubscribe"))
        m["lu"] = f"{lu}/{len(emails)}通"
    else:
        m["mail"] = m["lu"] = NA
    times = sorted(t for t in (parse_dt(e.get("received_at")) for e in emails) if t)
    if signed and times:
        first = times[0]
        sec = int((first - signed).total_seconds())
        m["first"] = f"{sec // 60}分{sec % 60}秒" if sec >= 0 else NA
        if len(times) >= 1:
            span_h = max((times[-1] - signed).total_seconds() / 3600, 0)
            m["span"] = f"登録後{span_h:.1f}時間で{len(times)}通"
    else:
        m["first"] = NA
    m.setdefault("span", NA)
    unsub = parse_dt(svc.get("unsubscribed_at"))
    after = emails_after_unsub(svc)
    if unsub is None:
        m["unsub"] = NA
        m["verdict"] = "配信停止: " + NA
    else:
        m["unsub"] = fmt_jst(svc.get("unsubscribed_at"))
        m["verdict"] = ("配信停止: 効いた（停止後 0通）" if after == 0
                        else f"配信停止: 効かず（停止後 {after}通）")
    m["after"] = f"{after}通" if after is not None else NA
    return m


def render_md(svc, m, sample):
    L = []
    if sample:
        L += ["> **【SAMPLE】これはダミーデータによる見本です。実在サービスの観測結果ではありません。**", ""]
    L += [f"# 被害報告書：{svc.get('service')}", "",
          f"- 人柱アドレス: `{svc.get('tag_address') or NA}`",
          f"- 登録ページ: {svc.get('signup_url') or NA}",
          f"- 登録日時: {fmt_jst(svc.get('signed_up_at')) or NA}", "",
          "## 登録時の観測", "",
          "| 項目 | 結果 |", "|---|---|",
          f"| 必須入力項目 | {m['required']} |",
          f"| 最初からチェック済みのオプトイン | {m['prechecked']} |",
          f"| ダークパターン | {m['dark_n']} |", ""]
    if m["dark"]:
        L += [f"- {d}" for d in m["dark"]] + [""]
    L += ["## 登録後の受信メール", "",
          "| 項目 | 結果 |", "|---|---|",
          f"| 受信数 | {m['mail']} |",
          f"| 最初のメールまで | {m['first']} |",
          f"| 期間 | {m['span']} |",
          f"| List-Unsubscribe ヘッダ付き | {m['lu']} |",
          f"| 配信停止URL（未アクセス） | {svc.get('unsubscribe_url') or NA} |", ""]
    emails = svc.get("emails") or []
    if emails:
        L += ["| 受信日時 | 差出人 | 件名 | 分類 | List-Unsub |", "|---|---|---|---|---|"]
        for e in emails:
            subj = (e.get("subject") or "").replace("|", "｜")
            L.append(f"| {fmt_jst(e.get('received_at')) or NA} | {e.get('from') or NA} | {subj} | "
                     f"{e.get('type') or NA} | {'あり' if e.get('has_list_unsubscribe') else 'なし'} |")
        L.append("")
    else:
        L += [f"受信メール: {NA}", ""]
    L += ["## 配信停止", "",
          f"- 配信停止操作: {m['unsub']}",
          f"- 停止後に届いたメール: {m['after']}",
          f"- 判定: **{m['verdict']}**", ""]
    notes = svc.get("notes") or []
    if notes:
        L += ["## メモ", ""] + [f"- {n}" for n in notes] + [""]
    shots = svc.get("screenshots") or []
    L += ["## スクリーンショット", ""] + ([f"- {s}" for s in shots] if shots else [NA]) + [""]
    L += [f"---\n生成: 人柱くん / {datetime.now(JST).strftime('%Y-%m-%d %H:%M JST')}"]
    return "\n".join(L)


# ---------- 絵馬画像 ----------
def find_font(bold=True):
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
    raise SystemExit("日本語フォントがありません: sudo apt-get install -y fonts-noto-cjk")


FONT = None


def F(size):
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


def render_png(svc, m, sample, path):
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
    name = svc.get("service") or "?"
    f = F(46); d.text(((W - d.textlength(name, font=f)) / 2, top + 175), name, font=f, fill=ink)

    rows = [("必須項目", m["required"]), ("事前チェック済", m["prechecked"]),
            ("ダークパターン", m["dark_n"] + (("：" + "／".join(m["dark"])) if m["dark"] else "")),
            ("受信メール", m["mail"]), ("初メールまで", m["first"]),
            ("List-Unsub付", m["lu"]), ("停止後の受信", m["after"])]
    y, lx, vx, maxw = top + 260, x0 + 60, x0 + 330, x1 - 60 - (x0 + 330)
    fl, fv = F(30), F(30)
    for label, val in rows:
        d.text((lx, y), label, font=fl, fill=(120, 60, 20))
        lines = wrap(d, val, fv, maxw)[:2]
        for ln in lines:
            d.text((vx, y), ln, font=fv, fill=(130, 130, 130) if val == NA else ink); y += 40
        y += 8
    # 判定
    v = m["verdict"]
    f = F(42); tw = d.textlength(v, font=f)
    col = (130, 130, 130) if NA in v else ((30, 110, 50) if "効いた" in v else (180, 20, 20))
    d.rounded_rectangle([(W - tw) / 2 - 24, y + 8, (W + tw) / 2 + 24, y + 70], 12, outline=col, width=4)
    d.text(((W - tw) / 2, y + 14), v, font=f, fill=col)
    foot = f"人柱くん　{svc.get('tag_address') or ''}　{datetime.now(JST).strftime('%Y-%m-%d')}"
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


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--ledger", default="ledger.json")
    ap.add_argument("--out", default="out")
    a = ap.parse_args()
    ledger = load_ledger(a.ledger)
    sample = bool(ledger.get("sample"))
    os.makedirs(a.out, exist_ok=True)
    if not ledger.get("services"):
        print("services が空です（まだ登録データがありません）"); return
    for svc in ledger["services"]:
        m = metrics(svc)
        base = os.path.join(a.out, ("SAMPLE_" if sample else "") + slug(tag_of(svc) or svc.get("service")))
        with open(base + ".md", "w", encoding="utf-8") as f:
            f.write(render_md(svc, m, sample))
        render_png(svc, m, sample, base + ".png")
        print(base + ".md", base + ".png")


if __name__ == "__main__":
    main()
