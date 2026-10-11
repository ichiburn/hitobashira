#!/usr/bin/env python3
"""人柱Bot（調査）→ 観測Bot → 検証官 → 統括 の引き継ぎを、ファイルの受け渡しで通しで再現する（issue #5）。

  python3 e2e/pipeline.py                         # DEMO の honest / evil を起動して通しで実行
  python3 e2e/pipeline.py --target http://127.0.0.1:8781/ --sender-domain honest-news.test \\
      --mailpit-api http://127.0.0.1:8025 --mailpit-smtp-port 1025   # 起動済みのテストサイト（testsite_mail 互換）に URL を1回渡す

各役割はこのリポジトリのスクリプト（下の関数）が演じる。Grok Bot 同士が実際にメッセージを送り合う部分は実装していない（README の構想）。
すべて同じ機械・同じプロセスで動くため、**完全なブラインド検証ではない**。検証官は前段の結論ファイルを受け取らず
（handoff の withheld）、一次証拠から判定を作り直して固定（時刻とハッシュ）してから、前段の結論を開いて比べる。

中間成果物（<out>/<run_id>/）:
  derived/survey.json            人柱Bot: フォームの事前監査（約束・同意の初期状態・ページ内の指示）
  derived/mail_events.json       観測Bot: 受信メールの時系列（ヘッダ由来のメタデータのみ）
  derived/unsub_observation.json 観測Bot: 配信停止の受理・確認メール・停止後の販促
  verdict/observer_verdict.json  観測Bot の結論（検証官には渡さない）
  verdict/verify.json            検証官: 一次証拠からの独立判定（固定時刻とハッシュは handoff の verify_lock）
  verdict/compare.json           検証官: 固定後に観測Bot の結論と比較
  verdict/report.md              統括: 体験レポート（約束と現実の差分、検証の条件と限界）
  handoff/NN_*.json              引き継ぎ（schemas/handoff.schema.json）
終了コード: 0=完走（期待どおり）/ 1=完走したが期待と違う / 2=途中停止（ABORTED）/ 3=Mailpit が無い
"""
import argparse, json, os, re, secrets, sys, time, urllib.parse
from datetime import datetime

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
for p in (ROOT, os.path.join(ROOT, "tools"), os.path.join(ROOT, "testsite_mail")):
    if p not in sys.path:
        sys.path.insert(0, p)
os.environ.setdefault("HITOBASHIRA_BOT_LOCAL", "yourbot")
os.environ.setdefault("HITOBASHIRA_BOT_DOMAIN", "example.com")

import common, ingest_email, report                                  # noqa: E402
from mail_adapters import MailpitAdapter, snapshot                   # noqa: E402
from harness.egress import EgressPolicy                              # noqa: E402
from harness.runctx import RunContext, now_iso, sha256_file          # noqa: E402
from e2e.handoff import Chain                                        # noqa: E402
from e2e.run_mail_e2e import (MailpitProcess, ExternalMailpit, find_mailpit, guarded_http,  # noqa: E402
                              form_state_from_html, have_playwright, EXPECTED)
import server_mail                                                   # noqa: E402

P = common.md_cell


def _load(path):
    with open(path, encoding="utf-8") as f:
        return json.load(f)


def _by_name(paths, name):
    return next(p for p in paths if os.path.basename(p) == name)


# ---------- 統括 ----------
def coordinator_start(ctx, chain, target, sender_domain, use_browser, closed_env):
    port = urllib.parse.urlsplit(target).port
    policy = EgressPolicy([port], {port: ["/register", "/unsubscribe"]})
    scope = {"target": target, "egress_policy": policy.describe(), "sender_domain": sender_domain,
             "real_sites": "対象外（閲覧も含めてこのパイプラインでは扱わない）", "browser": use_browser,
             "closed_env_trust": closed_env, "goal": "登録→メール追跡→配信停止→独立検証→体験レポート"}
    p = ctx.put_derived("task.json", scope, role="coordinator")
    chain.emit("coordinator", "hitobashira", "CREATED", [p], scope=scope)
    return policy


# ---------- 人柱Bot（調査・登録）----------
def role_hitobashira(ctx, chain, addr):
    arts, _ = chain.accept("hitobashira")
    task = _load(_by_name(arts, "task.json"))
    target = task["target"]
    port = urllib.parse.urlsplit(target).port
    policy = EgressPolicy([port], {port: ["/register"]})
    started = now_iso()
    browser = None
    try:
        if task["browser"]:
            from harness.browser import GuardedBrowser
            browser = GuardedBrowser(policy, ctx)
            page = browser.open(target)
            s = browser.audit(page, "01_signup_form")
            fields = [f for fm in s["forms"] for f in fm["fields"]]
            promises, inj, title = s["promises_on_page"], s["untrusted_instructions_detected"], s["title"]
            consent = ctx.put_evidence("01_consent_state.json", {"captured_at": now_iso(), "fields": fields}, role="hitobashira")
            browser.submit(page, "#signup", {"email": addr})
            resp = page.content()
        else:
            _, html_text = guarded_http(policy, ctx, target, role="hitobashira")
            ctx.put_evidence("01_signup_form.html", html_text, role="hitobashira")
            fields = form_state_from_html(html_text)
            mt = re.search(r"<title>(.*?)</title>", html_text, re.S)
            title = mt.group(1).strip() if mt else None
            text = re.sub(r"<[^>]+>", "\n", html_text)
            lines = [l.strip() for l in text.splitlines() if l.strip()]
            promises = [l for l in lines if re.search(r"配信停止|停止後|週\s*\d+\s*回|毎週|毎日|第三者", l)]
            inj = [l for l in lines if re.search(r"無視|ignore", l, re.I)]
            consent = ctx.put_evidence("01_consent_state.json", {"captured_at": now_iso(), "fields": fields}, role="hitobashira")
            data = {"email": addr, "name": ""}
            data.update({f["name"]: "yes" for f in fields if f["checked"]})
            _, resp = guarded_http(policy, ctx, urllib.parse.urljoin(target, "/register"), data, role="hitobashira")
    finally:
        if browser:
            browser.close()
    signed = now_iso()
    regp = ctx.put_evidence("01_register_response.html", resp, role="hitobashira")
    survey = {"role": "hitobashira", "page_title": title, "started_at": started, "finished_at": signed, "target_domain": common.url_domain(target),
              "promises_on_page": promises, "untrusted_instructions_detected": inj,
              "required_fields": [f["name"] for f in fields if f["required"]],
              "prechecked_optins": [f["name"] for f in fields if f["checked"]],
              "submitted": True, "signed_up_at": signed,
              "registration_accepted": "ご登録ありがとうございます" in resp,
              "actions_taken": ["フォームの閲覧・記録", "初期状態のまま登録"], "stopped_reason": None}
    sp = ctx.put_derived("survey.json", survey, role="hitobashira")
    chain.emit("hitobashira", "observer", "SURVEYED", [sp, consent, regp])
    return survey


# ---------- 観測Bot ----------
def role_observer(ctx, chain, adapter, addr, tag, sender_domain, observe_seconds):
    arts, _ = chain.accept("observer")
    survey = _load(_by_name(arts, "survey.json"))
    task = _load(os.path.join(ctx.derived_dir, "task.json"))
    target = task["target"]
    port = urllib.parse.urlsplit(target).port
    policy = EgressPolicy([port], {port: ["/unsubscribe"]})
    policy_ops = EgressPolicy([port], {port: ["/_test/campaign"]})
    canary = f"{common.bot_local()}+canary{secrets.token_hex(4)}@{common.bot_domain()}"
    ok, recs = adapter.wait_for(addr, lambda L: len(L) >= 2, 10)
    s1 = ctx.put_evidence("02_mailpit_before_unsub.json", snapshot(recs), role="observer")
    rc1 = adapter.receiver_check(canary)
    svc = {"service": f"DEMO {survey['target_domain']}", "tag": tag, "tag_address": addr, "signup_url": target,
           "signed_up_at": survey["signed_up_at"], "required_fields": survey["required_fields"],
           "prechecked_optins": survey["prechecked_optins"], "dark_patterns": [], "screenshots": [], "emails": [],
           "expected_sender_domains": [sender_domain] if sender_domain else [], "observed_until": None,
           "unsubscribed_at": None, "emails_after_unsubscribe": None, "evidence_policy": "strict", "receiver_checks": [rc1]}
    ledger = {"version": 1, "sample": False, "services": [svc]}
    fetched = now_iso()
    for r in recs:
        m, ev = adapter.to_ingest(r, fetched)
        ingest_email.ingest(m, ledger, evidence=ev)
    uurl = svc.get("unsubscribe_url")
    if not uurl:
        raise RuntimeError("配信停止 URL（List-Unsubscribe）が取れなかった")
    _, uhtml = guarded_http(policy, ctx, uurl, role="observer")
    ctx.put_evidence("04_unsubscribe_page.html", uhtml, role="observer")
    t = urllib.parse.parse_qs(urllib.parse.urlsplit(uurl).query).get("t", [""])[0]
    clicked = now_iso()
    _, resp = guarded_http(policy, ctx, urllib.parse.urljoin(target, "/unsubscribe"), {"t": t}, role="observer")
    accepted = "配信停止が完了しました" in resp
    ctx.put_evidence("04_unsubscribe_response.html", resp, role="observer")
    act = ctx.put_evidence("04_unsubscribe_action.json", {"clicked_at": clicked, "url_domain": common.url_domain(uurl),
                                                          "acceptance_page_says_done": accepted}, role="observer")
    svc["unsubscribed_at"] = clicked
    _, trig = guarded_http(policy_ops, ctx, urllib.parse.urljoin(target, "/_test/campaign"), b"", role="test_control")
    ctx.put_evidence("05_campaign_trigger.json", {"at": now_iso(), "response": json.loads(trig)}, role="test_control")
    time.sleep(observe_seconds)
    svc["observed_until"] = now_iso()
    rc2 = adapter.receiver_check(canary)
    svc["receiver_checks"].append(rc2)
    win = ctx.put_evidence("05_observation_window.json", {"observed_until": svc["observed_until"],
                                                          "receiver_checks": svc["receiver_checks"],
                                                          "note": "時間経過の代わりに配信トリガーを1回叩き、数秒だけ観測した"},
                           role="observer")
    recs2 = adapter.list_for(addr)
    s2 = ctx.put_evidence("05_mailpit_after_unsub.json", snapshot(recs2), role="observer")
    fetched = now_iso()
    for r in recs2:
        m, ev = adapter.to_ingest(r, fetched)
        ingest_email.ingest(m, ledger, evidence=ev)
    ctx.put_derived("ledger.json", ledger, role="observer")
    events = [{k: e.get(k) for k in ("received_at", "from", "subject", "type", "has_list_unsubscribe", "attribution")}
              for e in svc["emails"]]
    ctx.put_derived("mail_events.json", {"role": "observer", "events": events}, role="observer")
    u = common.parse_dt(clicked)
    after = [e for e in svc["emails"] if common.parse_dt(e["received_at"]) > u]
    obs = {"role": "observer", "unsubscribed_at": clicked, "observed_until": svc["observed_until"],
           "acceptance_page_says_done": accepted,
           "confirmation_mails": sum(1 for e in after if e["type"] == "transactional" and "配信停止" in e["subject"]),
           "marketing_after_unsub": sum(1 for e in after if e["type"] == "marketing"),
           "other_after_unsub": sum(1 for e in after if e["type"] not in ("marketing", "transactional"))}
    ctx.put_derived("unsub_observation.json", obs, role="observer")
    v = common.unsub_verdict(svc)
    ov = ctx.put_verdict("observer_verdict.json", {"role": "observer", "verdict": v}, role="observer")
    # 検証官には一次証拠だけを渡す（観測Botの結論・台帳・集計は渡さない）
    primary = [os.path.join(ctx.evidence_dir, n) for n in sorted(os.listdir(ctx.evidence_dir))]
    primary.append(os.path.join(ctx.derived_dir, "task.json"))
    withheld = [ctx.rel(ov), "derived/ledger.json", "derived/mail_events.json", "derived/unsub_observation.json", "derived/survey.json"]
    chain.emit("observer", "verifier", "OBSERVED", primary, withheld=withheld)
    return v, obs, len(recs2)


# ---------- 検証官 ----------
def role_verifier(ctx, chain, tag):
    arts, h = chain.accept("verifier")
    opened = [ctx.rel(p) for p in arts]
    for w in h.get("withheld") or []:
        if w in opened:
            raise RuntimeError("検証官に前段の結論が渡されている")
    task = _load(_by_name(arts, "task.json"))
    snap = _load(_by_name(arts, "05_mailpit_after_unsub.json"))
    act = _load(_by_name(arts, "04_unsubscribe_action.json"))
    win = _load(_by_name(arts, "05_observation_window.json"))
    consent = _load(_by_name(arts, "01_consent_state.json"))
    reg_html = open(_by_name(arts, "01_register_response.html"), encoding="utf-8").read()
    # 一次証拠（Mailpit のヘッダのスナップショット）から台帳を作り直す
    svc = {"service": "verifier-rebuild", "tag": tag, "emails": [],
           "expected_sender_domains": [task["sender_domain"]] if task.get("sender_domain") else [],
           "unsubscribed_at": act["clicked_at"], "observed_until": win["observed_until"],
           "receiver_checks": win["receiver_checks"], "evidence_policy": "strict"}
    ledger = {"services": [svc]}
    ad = MailpitAdapter("http://127.0.0.1:1", closed_env=task["closed_env_trust"])   # 変換だけに使う（接続しない）
    for r in snap:
        m, ev = ad.to_ingest({"id": r["mailpit_id"], "created": r["created"], "headers": r["headers"]}, now_iso())
        ev["source"] = "evidence/05_mailpit_after_unsub.json（Mailpit のスナップショット）"
        ingest_email.ingest(m, ledger, evidence=ev)
    v = common.unsub_verdict(svc)
    findings = {
        "registration_accepted": "ご登録ありがとうございます" in reg_html,
        "prechecked_optins": [f["name"] for f in consent["fields"] if f.get("checked")],
        "acceptance_page_says_done": act["acceptance_page_says_done"],
        "mails_total": len(svc["emails"]),
        "marketing_after_unsub": sum(1 for e in svc["emails"]
                                     if e["type"] == "marketing" and common.parse_dt(e["received_at"]) > common.parse_dt(act["clicked_at"])),
    }
    verify = {"role": "verifier", "inputs": opened, "verdict": v, "findings": findings,
              "method": "一次証拠（Mailpit のヘッダのスナップショット・停止操作の記録・観測期間の記録）から台帳を作り直し、unsub_verdict で判定",
              "independence_note": "前段の結論ファイルは受け取っていない。ただし同じ機械・同じコード（分類・判定）を使うため、完全な独立検証ではない"}
    vp = ctx.put_verdict("verify.json", verify, role="verifier")
    lock = {"path": ctx.rel(vp), "sha256": sha256_file(vp), "locked_at": _load(vp)["decided_at"]}
    ctx.log("verifier_locked", **lock)
    # 固定したあとで、初めて観測Botの結論を開く
    opened_at = now_iso()
    ov = _load(os.path.join(ctx.verdict_dir, "observer_verdict.json"))
    cmp_ = {"role": "verifier", "opened_observer_verdict_at": opened_at, "verify_locked_at": lock["locked_at"],
            "verify_sha256": lock["sha256"], "observer_status": ov["verdict"]["status"], "verifier_status": v["status"],
            "agreement": "一致" if ov["verdict"]["status"] == v["status"] else "不一致",
            "observer_after": ov["verdict"]["after"], "verifier_after": v["after"]}
    cp = ctx.put_verdict("compare.json", cmp_, role="verifier")
    chain.emit("verifier", "coordinator", "VERIFIED", [vp, cp], verify_lock=lock)
    return verify, cmp_


# ---------- 統括: 体験レポート ----------
VERDICT_JA = {"effective": "観測期間内に停止後の販促を検出せず", "ineffective": "効かず（停止後も販促が届いた）",
              "pending": "判定保留", "unobserved": "未観測"}


def promise_rows(promises, verify, obs):
    rows = []
    text = " ".join(promises)
    f = verify["findings"]
    st = verify["verdict"]["status"]
    if re.search(r"配信停止.*リンク", text):
        rows.append(("配信停止はメール末尾のリンクから", "List-Unsubscribe のリンクで停止画面に到達し、完了画面が出た"
                     if f["acceptance_page_says_done"] else "停止の完了画面を確認できなかった",
                     "守った" if f["acceptance_page_says_done"] else "未確認", "evidence/04_unsubscribe_*.html"))
    if re.search(r"停止後.*(送りしません|配信しません)", text):
        real = {"effective": "観測期間内に停止後の販促メールを検出しなかった",
                "ineffective": f"停止後に販促メールが{verify['verdict']['after']}通届いた",
                "pending": "判定保留（" + verify["verdict"]["reason"] + "）", "unobserved": "未観測"}[st]
        rows.append(("停止後は一切送らない", real,
                     {"effective": "破った形跡なし（観測期間内）", "ineffective": "破った", "pending": "未確認", "unobserved": "未確認"}[st],
                     "evidence/05_mailpit_after_unsub.json, verdict/verify.json"))
    m = re.search(r"(週\s*\d+\s*回|毎週|毎日)", text)
    if m:
        rows.append((f"配信頻度（{m.group(1)}）", "観測期間が数秒のため頻度は測れない", "未確認", "—"))
    if f["prechecked_optins"]:
        rows.append(("（約束ではない）同意の初期状態", "提携企業からのお知らせ等のチェックが最初から入っていた: "
                     + "、".join(f["prechecked_optins"]), "要注意（事前チェック）", "evidence/01_consent_state.json"))
    return rows


def coordinator_report(ctx, chain, survey, verify, cmp_, obs, timings, mails_seen, aborted=None):
    arts, _ = chain.accept("coordinator")
    st = verify["verdict"]["status"]
    rows = promise_rows(survey["promises_on_page"], verify, obs)
    problems = sum(1 for r in rows if r[2] in ("破った", "要注意（事前チェック）"))
    L = ["# 体験レポート：" + P(survey.get("page_title") or "DEMO テストサイト") + "（" + P(survey["target_domain"]) + "）", "",
         "> 自作の DEMO テストサイトをローカル（127.0.0.1）で動かした結果です。実在のサービスの観測ではありません。", "",
         f"- run_id: `{ctx.run_id}`",
         f"- 実行時間（実測）: {timings['total']:.1f} 秒（調査 {timings['hitobashira']:.1f} / 観測 {timings['observer']:.1f} / 検証 {timings['verifier']:.1f}）",
         f"- 受信数（実測）: {mails_seen} 通（テスト用アドレス宛て。カナリアは除く）",
         f"- 問題の検出数: {problems} 件（下の表の「破った」「要注意」）",
         f"- 最終判定（検証官）: **{VERDICT_JA[st]}**（{P(verify['verdict']['reason'])}）",
         f"- 観測Bot の判定との比較: {cmp_['agreement']}（観測Bot: {VERDICT_JA[cmp_['observer_status']]}）", "",
         "## 約束と現実の差分", "", "| ページ上の約束 | 現実（観測） | 判定 | 根拠 |", "|---|---|---|---|"]
    L += [f"| {P(a)} | {P(b)} | {P(c)} | {P(d)} |" for a, b, c, d in rows] or ["| （約束を検出できず） | — | 未確認 | — |"]
    L += ["", "## 引き継ぎ（役割・時刻・ハッシュ）", "", "（このレポートを書いたあとに、統括が REPORTED の引き継ぎを追加する）", "", "| # | 役割 | 状態 | 時刻 | 引き継ぎファイル | SHA-256（先頭12桁） |", "|---|---|---|---|---|---|"]
    for p, h in chain.items:
        L.append(f"| {h['seq']} | {h['from_role']} → {h['to_role']} | {h['state']} | {h['created_at']} | "
                 f"{P(ctx.rel(p))} | {sha256_file(p)[:12]} |")
    L += ["", f"- 検証官は判定を {cmp_['verify_locked_at']} に固定（verify.json の SHA-256 先頭12桁 {cmp_['verify_sha256'][:12]}）し、"
          f"そのあと {cmp_['opened_observer_verdict_at']} に観測Bot の結論を開いた。",
          "- 検証官に渡したのは一次証拠（evidence/）と task.json だけ。観測Bot の結論・台帳・集計は渡していない（handoff の withheld）。", "",
          "## 実施した工程 / 実施していない工程", "",
          "| 工程 | 状況 |", "|---|---|",
          "| フォームの事前監査（閲覧） | 実施（自作 DEMO） |",
          "| 登録（初期状態のまま送信） | 実施（自作 DEMO、127.0.0.1 のみ） |",
          "| メール受信の追跡 | 実施（Mailpit。数秒間） |",
          "| 配信停止 | 実施（List-Unsubscribe のリンク） |",
          "| 停止後の再観測 | 実施（配信トリガー1回＋数秒の観測） |",
          "| 独立検証 | 実施（同じ機械・同じコード。完全なブラインド検証ではない） |",
          "| 実在サイトでの登録・停止 | 未実施（対象外） |",
          "| 長期の観測（頻度・数日〜数週間） | 未実施 |",
          "| 送信ドメイン認証（SPF/DKIM/DMARC）の検証 | 未実施（Mailpit は認証ヘッダを付けない） |",
          "| Grok Bot 同士のメッセージでの引き継ぎ | 未実装（構想。README 参照） |", ""]
    if aborted:
        L += [f"**途中で停止しました**: {P(aborted)}", ""]
    L += ["## 検証の条件と限界", "",
          "- 対象は自作の DEMO テストサイト（honest / evil の2種類）だけ。実在のサービスについては何も示さない。",
          "- 観測期間は数秒で、時間経過の代わりに配信トリガーを叩いた。「検出せず」は観測期間内の結果で、恒久的な停止成功ではない。",
          "- 帰属は「自分で起動した Mailpit に自分で起動したテストサイトだけが送る」構成を根拠にした（closed_env）。"
          "Authentication-Results はヘッダの解釈だけで、SPF/DKIM/DMARC の検証はしていない。"
          if verify.get("closed_env_trust", True) else
          "- 帰属の根拠に閉じた環境を使っていない（closed_env なし）。認証結果が無いため、帰属は domain_only になる。",
          "- メールの分類は件名キーワードによる推定。検証官も同じ分類コードを使うため、分類の誤りは両者で同じように起きうる。",
          "- 役割（人柱Bot・観測Bot・検証官・統括）はこのリポジトリのスクリプトが演じた。同じ機械・同じプロセスなので、"
          "検証官が前段の結論を「受け取っていない」ことはファイルの受け渡しで示せるが、読めなかったことまでは示せない。",
          "- SHA-256（manifest.json・handoff）は固定後の改変検知のためのもので、取得時の真正性は保証しない。",
          "- 受信メールの本文・Received ヘッダは保存していない。テスト用アドレス（+タグ）はこのレポートに出さない。", "",
          "---", f"生成: 人柱くん（統括スクリプト） / {now_iso()}"]
    md = "\n".join(x for x in L if x is not None) + "\n"
    rp = ctx.put_verdict("report.md", md, role="coordinator")
    chain.emit("coordinator", "coordinator", "REPORTED", [rp])
    return rp


# ---------- 通し ----------
def run_pipeline(target, sender_domain, mailpit, out_dir, use_browser=True, closed_env=True, observe_seconds=2.0):
    ctx = RunContext(out_dir)
    chain = Chain(ctx)
    tag = secrets.token_hex(5)
    addr = f"{common.bot_local()}+{tag}@{common.bot_domain()}"
    adapter = MailpitAdapter(mailpit.api, mailpit.smtp_port, closed_env=closed_env)
    t0 = time.time()
    timings = {"hitobashira": 0.0, "observer": 0.0, "verifier": 0.0}
    res = {"run_id": ctx.run_id, "run_dir": ctx.dir, "target_domain": common.url_domain(target)}
    try:
        coordinator_start(ctx, chain, target, sender_domain, use_browser, closed_env)
        t = time.time(); survey = role_hitobashira(ctx, chain, addr); timings["hitobashira"] = time.time() - t
        t = time.time(); ov, obs, n = role_observer(ctx, chain, adapter, addr, tag, sender_domain, observe_seconds)
        timings["observer"] = time.time() - t
        t = time.time(); verify, cmp_ = role_verifier(ctx, chain, tag); timings["verifier"] = time.time() - t
        timings["total"] = time.time() - t0
        rp = coordinator_report(ctx, chain, survey, verify, cmp_, obs, timings, n)
        res.update(status=verify["verdict"]["status"], observer_status=ov["status"], agreement=cmp_["agreement"],
                   report=rp, timings=timings, mails=n, state=chain.state)
    except Exception as ex:
        reason = f"{type(ex).__name__}: {ex}"[:300]
        ctx.log("error", error=reason)
        chain.emit(chain.items[-1][1]["to_role"] if chain.items else "coordinator", "coordinator", "ABORTED", reason=reason)
        res.update(status=None, state="ABORTED", error=reason)
    finally:
        res["manifest"] = ctx.write_manifest()
    return res


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("--target", default=None, help="起動済みのテストサイト（http://127.0.0.1:<port>/）。省略時は DEMO を起動")
    ap.add_argument("--sender-domain", default=None)
    ap.add_argument("--sites", default="honest,evil")
    ap.add_argument("--out", default=os.path.join(ROOT, "runs_local"))
    ap.add_argument("--mailpit-bin", default=None)
    ap.add_argument("--mailpit-api", default=None)
    ap.add_argument("--mailpit-smtp-port", type=int, default=1025)
    ap.add_argument("--no-browser", action="store_true")
    ap.add_argument("--observe-seconds", type=float, default=2.0)
    a = ap.parse_args(argv)
    use_browser = (not a.no_browser) and have_playwright()
    if a.mailpit_api:
        mp = ExternalMailpit(a.mailpit_api, a.mailpit_smtp_port)
    else:
        b = find_mailpit(a.mailpit_bin)
        if not b:
            print("[skip] Mailpit が見つかりません", file=sys.stderr)
            return 3
        mp = MailpitProcess(b)
    results = []
    try:
        if a.target:
            results.append(run_pipeline(a.target, a.sender_domain, mp, a.out, use_browser, True, a.observe_seconds))
        else:
            for mode in [s for s in a.sites.split(",") if s]:
                httpd, site = server_mail.serve(mode, 0, "127.0.0.1", mp.smtp_port)
                try:
                    r = run_pipeline(site.base_url + "/", server_mail.SITES[mode]["domain"], mp, a.out, use_browser, True,
                                     a.observe_seconds)
                finally:
                    httpd.shutdown(); httpd.server_close()
                r["site_mode"], r["expected"] = mode, EXPECTED[mode]
                results.append(r)
    finally:
        mp.stop()
    print(json.dumps(results, ensure_ascii=False, indent=2))
    if any(r["state"] == "ABORTED" for r in results):
        return 2
    return 0 if all(r.get("expected") in (None, r["status"]) for r in results) else 1


if __name__ == "__main__":
    sys.exit(main())
