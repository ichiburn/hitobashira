#!/usr/bin/env python3
"""Mailpit を使ったローカル E2E（issue #4）: 登録 → 受信 → ingest → 配信停止 → 再観測 → 判定。

  python3 e2e/run_mail_e2e.py                    # honest と evil の両方を通しで実行
  python3 e2e/run_mail_e2e.py --sites evil --no-browser

- すべて 127.0.0.1 の上だけで動く。Mailpit は公式バイナリを 127.0.0.1 だけで待ち受けさせて起動し、終わったら止める
  （--mailpit-bin / 環境変数 MAILPIT_BIN / PATH / /tmp/mailpit の順に探す。見つからなければ終了コード 3）
- ブラウザ操作は harness/（Playwright）があれば使い、無ければ urllib で同じ許可判定をしてから送る（--no-browser で強制）
- 各段階の証跡は <out>/<run_id>/ に残す（evidence/ 生の証拠、derived/ 台帳など、verdict/ 判定、log.jsonl、manifest.json）
- 時間経過の代わりに、テストサイトの /_test/campaign を叩いて「次の販促メール」を送らせる。観測期間は数秒で、
  実サービスの観測期間とは比べられない
終了コード: 0=完走し期待どおりの判定 / 1=期待と違う判定 / 2=途中で失敗 / 3=Mailpit が無い
"""
import argparse, json, os, secrets, shutil, socket, subprocess, sys, tempfile, time, urllib.parse, urllib.request
from datetime import datetime

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
for p in (ROOT, os.path.join(ROOT, "tools"), os.path.join(ROOT, "testsite_mail")):
    if p not in sys.path:
        sys.path.insert(0, p)
os.environ.setdefault("HITOBASHIRA_BOT_LOCAL", "yourbot")
os.environ.setdefault("HITOBASHIRA_BOT_DOMAIN", "example.com")

import common, ingest_email, report                      # noqa: E402
from mail_adapters import MailpitAdapter, snapshot       # noqa: E402
from harness.egress import EgressPolicy                  # noqa: E402
from harness.runctx import RunContext, now_iso           # noqa: E402
import server_mail                                       # noqa: E402

EXPECTED = {"honest": "effective", "evil": "ineffective"}


# ---------- Mailpit ----------
def free_port():
    s = socket.socket()
    s.bind(("127.0.0.1", 0))
    p = s.getsockname()[1]
    s.close()
    return p


def find_mailpit(explicit=None):
    for c in (explicit, os.environ.get("MAILPIT_BIN"), shutil.which("mailpit"), "/tmp/mailpit"):
        if c and os.path.isfile(c) and os.access(c, os.X_OK):
            return c
    return None


class MailpitProcess:
    """Mailpit を 127.0.0.1 だけで起動する（外部への通信を伴う機能は使わない）。"""

    def __init__(self, binary):
        self.http_port, self.smtp_port = free_port(), free_port()
        self.tmp = tempfile.mkdtemp(prefix="mailpit-")
        args = [binary, "--listen", f"127.0.0.1:{self.http_port}", "--smtp", f"127.0.0.1:{self.smtp_port}",
                "--database", os.path.join(self.tmp, "mailpit.db"), "--disable-version-check", "--smtp-disable-rdns",
                "--block-remote-css-and-fonts", "--tags-disable", "plus-addresses,x-tags"]
        self.log = open(os.path.join(self.tmp, "mailpit.log"), "w")
        self.proc = subprocess.Popen(args, stdout=self.log, stderr=subprocess.STDOUT)
        self.api = f"http://127.0.0.1:{self.http_port}"
        end = time.time() + 15
        while time.time() < end:
            if MailpitAdapter(self.api).alive():
                return
            if self.proc.poll() is not None:
                break
            time.sleep(0.2)
        self.stop()
        raise RuntimeError("Mailpit が起動しなかった")

    def stop(self):
        if self.proc.poll() is None:
            self.proc.terminate()
            try:
                self.proc.wait(5)
            except subprocess.TimeoutExpired:
                self.proc.kill()
        self.log.close()
        shutil.rmtree(self.tmp, ignore_errors=True)


class ExternalMailpit:
    """既に動いている Mailpit（例: compose.yaml で起動したもの）を使う。127.0.0.1 のみ。"""

    def __init__(self, api, smtp_port):
        self.api = MailpitAdapter(api).api
        self.smtp_port = int(smtp_port)
        if not MailpitAdapter(self.api).alive():
            raise RuntimeError(f"Mailpit の API に接続できない: {self.api}")

    def stop(self):
        pass


# ---------- HTTP（ブラウザ無しのとき）----------
class _NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, *a, **kw):
        return None


def guarded_http(policy, ctx, url, data=None, role=None):
    """許可判定をしてから urllib で送る。リダイレクトは追わない。"""
    method = "POST" if data is not None else "GET"
    ok, why = policy.decide(url, method)
    ctx.log("egress_" + ("allow" if ok else "block"), url=url, method=method, kind="urllib", reason=why, role=role)
    if not ok:
        raise PermissionError(f"許可されていない送信先: {url}（{why}）")
    body = urllib.parse.urlencode(data).encode() if isinstance(data, dict) else data
    op = urllib.request.build_opener(_NoRedirect)
    with op.open(urllib.request.Request(url, data=body, method=method), timeout=10) as r:
        return r.status, r.read().decode("utf-8", "replace")


def form_state_from_html(html_text):
    """ブラウザ無しのときの簡易フォーム解析（このテストサイトの形式だけを想定）。"""
    import html.parser

    class P(html.parser.HTMLParser):
        def __init__(self):
            super().__init__()
            self.fields = []

        def handle_starttag(self, tag, attrs):
            a = dict(attrs)
            if tag == "input" and a.get("name"):
                self.fields.append({"name": a["name"], "type": a.get("type", "text"), "required": "required" in a,
                                    "checked": ("checked" in a) if a.get("type") in ("checkbox", "radio") else None})
    p = P()
    p.feed(html_text)
    return p.fields


# ---------- 1サイト分 ----------
def run_site(mode, mailpit, out_dir, use_browser=True, closed_env=True, observe_seconds=2.0, role_log=None):
    """1つのテストサイトについて全段階を実行し、要約 dict を返す。"""
    site_httpd, site = server_mail.serve(mode, 0, "127.0.0.1", mailpit.smtp_port)
    port = site_httpd.server_address[1]
    base = f"http://127.0.0.1:{port}"
    ctx = RunContext(out_dir)
    policy_bot = EgressPolicy([port], {port: ["/register", "/unsubscribe"]})          # 人柱Bot（ブラウザ）用
    policy_ops = EgressPolicy([port], {port: ["/_test/campaign"]})                     # 配信トリガー用
    adapter = MailpitAdapter(mailpit.api, mailpit.smtp_port, closed_env=closed_env)
    local, domain = common.bot_local(), common.bot_domain()
    tag = secrets.token_hex(5)
    addr = f"{local}+{tag}@{domain}"
    canary = f"{local}+canary{secrets.token_hex(4)}@{domain}"
    ctx.log("stage", stage="setup", site_mode=mode, site=base, mailpit_api=mailpit.api, closed_env=closed_env,
            browser=use_browser)
    stages = {}
    browser = None
    try:
        # 1. 登録（事前同意状態を証拠に残す）
        if use_browser:
            from harness.browser import GuardedBrowser
            browser = GuardedBrowser(policy_bot, ctx)
            page = browser.open(base + "/")
            survey = browser.audit(page, "01_signup_form")
            fields = [f for fm in survey["forms"] for f in fm["fields"]]
            ctx.put_evidence("01_consent_state.json", {"captured_at": now_iso(), "fields": fields,
                                                        "promises_on_page": survey["promises_on_page"]}, role="hitobashira")
            browser.submit(page, "#signup", {"email": addr})
            resp_html = page.content()
            page.close()
        else:
            _, form_html = guarded_http(policy_bot, ctx, base + "/", role="hitobashira")
            ctx.put_evidence("01_signup_form.html", form_html, role="hitobashira")
            fields = form_state_from_html(form_html)
            import re
            promises = re.findall(r'<p id="promise">(.*?)</p>', form_html)
            ctx.put_evidence("01_consent_state.json", {"captured_at": now_iso(), "fields": fields,
                                                        "promises_on_page": promises}, role="hitobashira")
            data = {"email": addr, "name": ""}
            data.update({f["name"]: "yes" for f in fields if f["checked"]})   # 初期状態のまま送る
            _, resp_html = guarded_http(policy_bot, ctx, base + "/register", data, role="hitobashira")
        signed_up_at = now_iso()
        ctx.put_evidence("01_register_response.html", resp_html, role="hitobashira")
        stages["register"] = {"ok": "ご登録ありがとうございます" in resp_html, "at": signed_up_at}

        # 2. 受信（Mailpit API）
        ok, recs = adapter.wait_for(addr, lambda L: len(L) >= 2, 10)
        ctx.put_evidence("02_mailpit_before_unsub.json", snapshot(recs), role="observer")
        stages["receive"] = {"ok": ok, "messages": len(recs)}
        rc_before = adapter.receiver_check(canary)
        ctx.log("receiver_check", **rc_before)

        # 3. ingest
        svc = {"service": f"DEMO {mode}", "tag": tag, "tag_address": addr, "signup_url": base + "/",
               "signed_up_at": signed_up_at,
               "required_fields": [f["name"] for f in fields if f["required"]],
               "prechecked_optins": [f["name"] for f in fields if f["checked"]],
               "dark_patterns": [], "screenshots": [], "emails": [],
               "expected_sender_domains": [server_mail.SITES[mode]["domain"]],
               "observed_until": None, "unsubscribed_at": None, "emails_after_unsubscribe": None,
               "evidence_policy": "strict", "receiver_checks": [rc_before]}
        ledger = {"version": 1, "sample": False, "services": [svc]}
        fetched = now_iso()
        for r in recs:
            m, ev = adapter.to_ingest(r, fetched)
            ingest_email.ingest(m, ledger, evidence=ev)
        ctx.put_derived("03_ledger_after_ingest.json", ledger, role="observer")
        stages["ingest"] = {"ok": len(svc["emails"]) == len(recs), "emails": len(svc["emails"])}

        # 4. 配信停止のリンクを踏む（List-Unsubscribe の URL。許可判定を通ったものだけ）
        uurl = svc.get("unsubscribe_url")
        if not uurl:
            raise RuntimeError("配信停止 URL が取れなかった")
        unsub_at = now_iso()                      # 停止操作の時刻（ボタンを押す直前）
        if use_browser:
            page = browser.open(uurl)
            ctx.put_evidence("04_unsubscribe_page.html", page.content(), role="observer")
            svc["unsubscribed_at"] = unsub_at = now_iso()
            browser.submit(page, "#unsub", {})
            unsub_html = page.content()
            page.close()
        else:
            _, uhtml = guarded_http(policy_bot, ctx, uurl, role="observer")
            ctx.put_evidence("04_unsubscribe_page.html", uhtml, role="observer")
            t = urllib.parse.parse_qs(urllib.parse.urlsplit(uurl).query).get("t", [""])[0]
            svc["unsubscribed_at"] = unsub_at = now_iso()
            _, unsub_html = guarded_http(policy_bot, ctx, base + "/unsubscribe", {"t": t}, role="observer")
        ctx.put_evidence("04_unsubscribe_response.html", unsub_html, role="observer")
        accepted = "配信停止が完了しました" in unsub_html
        stages["unsubscribe"] = {"ok": accepted, "at": unsub_at, "url_domain": common.url_domain(uurl)}

        # 5. 再観測（配信トリガー → 一定時間待つ → カナリアで受信経路を確認 → 取り直す）
        _, trig = guarded_http(policy_ops, ctx, base + "/_test/campaign", b"", role="test_control")
        ctx.put_evidence("05_campaign_trigger.json", {"at": now_iso(), "response": json.loads(trig)}, role="test_control")
        time.sleep(observe_seconds)
        svc["observed_until"] = now_iso()
        rc_after = adapter.receiver_check(canary)
        svc["receiver_checks"].append(rc_after)
        ctx.log("receiver_check", **rc_after)
        recs2 = adapter.list_for(addr)
        ctx.put_evidence("05_mailpit_after_unsub.json", snapshot(recs2), role="observer")
        fetched = now_iso()
        for r in recs2:
            m, ev = adapter.to_ingest(r, fetched)
            ingest_email.ingest(m, ledger, evidence=ev)
        ctx.put_derived("06_ledger_final.json", ledger, role="observer")
        u = common.parse_dt(unsub_at)
        after = [e for e in svc["emails"] if common.parse_dt(e["received_at"]) > u]
        obs = {"unsubscribed_at": unsub_at, "observed_until": svc["observed_until"],
               "acceptance_page": {"accepted": accepted},
               "confirmation_mails": sum(1 for e in after if e["type"] == "transactional" and "配信停止" in e["subject"]),
               "marketing_after_unsub": sum(1 for e in after if e["type"] == "marketing"),
               "other_after_unsub": sum(1 for e in after if e["type"] not in ("marketing", "transactional")),
               "receiver_checks": svc["receiver_checks"],
               "observation_window_note": "時間経過の代わりに配信トリガーを1回叩き、数秒だけ観測した"}
        ctx.put_derived("unsub_observation.json", obs, role="observer")
        stages["reobserve"] = {"ok": rc_after["ok"], "messages": len(recs2)}

        # 6. 判定（証拠とは別ファイル）
        v = common.unsub_verdict(svc)
        ctx.put_verdict("07_unsub_verdict.json", {"site_mode": mode, "verdict": v, "decided_by": "tools/common.py unsub_verdict"},
                        role="observer")
        md = report.render_md(svc, report.metrics(svc), False, now_iso())
        ctx.put_verdict("07_report.md", md, role="observer")
        stages["verdict"] = {"status": v["status"], "reason": v["reason"]}
    except Exception as ex:
        ctx.log("error", error=f"{type(ex).__name__}: {ex}"[:500])
        stages["error"] = f"{type(ex).__name__}: {ex}"
    finally:
        if browser:
            browser.close()
        site_httpd.shutdown()
        site_httpd.server_close()
        man = ctx.write_manifest()
    return {"site_mode": mode, "run_id": ctx.run_id, "run_dir": ctx.dir, "manifest": man, "stages": stages,
            "status": stages.get("verdict", {}).get("status"), "expected": EXPECTED.get(mode),
            "browser": use_browser, "closed_env": closed_env}


def have_playwright():
    try:
        import playwright.sync_api  # noqa: F401
        return True
    except Exception:
        return False


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("--sites", default="honest,evil")
    ap.add_argument("--out", default=os.path.join(ROOT, "runs_local"))
    ap.add_argument("--mailpit-bin", default=None)
    ap.add_argument("--mailpit-api", default=None, help="既存の Mailpit を使う（例 http://127.0.0.1:8025）。起動も停止もしない")
    ap.add_argument("--mailpit-smtp-port", type=int, default=1025)
    ap.add_argument("--no-browser", action="store_true")
    ap.add_argument("--no-closed-env-trust", action="store_true",
                    help="閉じたローカル環境であることを帰属の根拠にしない（認証なし＝判定保留になることの確認用）")
    ap.add_argument("--observe-seconds", type=float, default=2.0)
    a = ap.parse_args(argv)
    use_browser = (not a.no_browser) and have_playwright()
    if a.mailpit_api:
        mp = ExternalMailpit(a.mailpit_api, a.mailpit_smtp_port)
    else:
        binary = find_mailpit(a.mailpit_bin)
        if not binary:
            print("[skip] Mailpit が見つかりません（--mailpit-bin / MAILPIT_BIN / PATH / /tmp/mailpit）", file=sys.stderr)
            return 3
        mp = MailpitProcess(binary)
    results = []
    try:
        for mode in [s for s in a.sites.split(",") if s]:
            results.append(run_site(mode, mp, a.out, use_browser, not a.no_closed_env_trust, a.observe_seconds))
    finally:
        mp.stop()
    print(json.dumps(results, ensure_ascii=False, indent=2))
    if any("error" in r["stages"] for r in results):
        return 2
    if a.no_closed_env_trust:
        return 0
    return 0 if all(r["status"] == r["expected"] for r in results) else 1


if __name__ == "__main__":
    sys.exit(main())
