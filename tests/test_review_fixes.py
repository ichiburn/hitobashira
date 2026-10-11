"""外部レビュー（2026-10-11 15:41 JST）の指摘ごとの再現テスト。標準ライブラリ unittest のみ。"""
import contextlib, io, json, os, subprocess, sys, tempfile, threading, unittest, urllib.error, urllib.request
from tests import _helpers  # noqa: F401  (sys.path と環境変数の設定)
import common, ingest_email, report

ROOT = _helpers.ROOT


def svc(**kw):
    s = {"service": "svc-a", "tag": "k7q2x", "signed_up_at": "2026-10-11T13:00:00+09:00",
         "required_fields": [], "prechecked_optins": [], "dark_patterns": [], "emails": [],
         "expected_sender_domains": ["shop.example"], "observed_until": "2026-10-12T13:00:00+09:00",
         "unsubscribed_at": "2026-10-11T14:00:00+09:00"}
    s.update(kw)
    return s


def mail(t, typ="marketing", **kw):
    e = {"received_at": t, "from": "news@shop.example", "subject": "s", "type": typ,
         "has_list_unsubscribe": True, "attribution": "verified"}
    e.update(kw)
    return e


# 2026-10-11 21時台の仕様変更（fail-closed の例外をなくした）で、「検出せず」には受信経路の稼働確認が
# 停止前と観測期限後の両方で必要になった。evidence_policy の無い台帳も strict 扱い。
RC = [{"at": "2026-10-11T13:59:00+09:00", "kind": "canary", "ok": True},
      {"at": "2026-10-12T13:00:05+09:00", "kind": "canary", "ok": True}]


def quiet(fn, *a, **kw):
    with contextlib.redirect_stderr(io.StringIO()), contextlib.redirect_stdout(io.StringIO()):
        return fn(*a, **kw)


# ---------- 最優先1: 配信停止の判定 ----------
class TestUnsubVerdict(unittest.TestCase):
    def test_no_observation_after_unsub_is_pending(self):  # 観測0通でも「効いた」になっていた
        v = common.unsub_verdict(svc(observed_until=None))
        self.assertEqual(v["status"], "pending")

    def test_observed_until_before_unsub_is_pending(self):
        v = common.unsub_verdict(svc(observed_until="2026-10-11T13:30:00+09:00"))
        self.assertEqual(v["status"], "pending")

    def test_effective_needs_observed_until_and_receiver_checks(self):
        # 仕様変更: 以前は observed_until だけで effective。evidence_policy の無い台帳も strict として扱うようにしたため、
        # 受信経路の稼働確認（receiver_checks）が無ければ判定保留、あれば effective（観測期間内に検出せず）。
        self.assertEqual(common.unsub_verdict(svc())["status"], "pending")
        self.assertEqual(common.unsub_verdict(svc(receiver_checks=RC))["status"], "effective")

    def test_unsub_confirmation_mail_does_not_count(self):  # 停止完了通知1通で「効かず」になっていた
        # 仕様変更: effective には受信経路の稼働確認が必要になったため receiver_checks を付けた（確かめたい点は同じ）
        s = svc(emails=[mail("2026-10-11T14:01:00+09:00", "transactional", subject="配信停止を受け付けました")],
                receiver_checks=RC)
        v = common.unsub_verdict(s)
        self.assertEqual((v["status"], v["after"]), ("effective", 0))

    def test_promo_after_unsub_legacy_verified_is_pending_trusted_is_ineffective(self):
        # 仕様変更: 旧形式の attribution "verified"（差出人ドメイン一致のみ・認証結果の記録なし）は信用しない。
        # 以前は ineffective だったが、いまは判定保留。認証済み（trusted）なら ineffective。
        v = common.unsub_verdict(svc(emails=[mail("2026-10-11T15:00:00+09:00")]))
        self.assertEqual((v["status"], v["after"]), ("pending", None))
        self.assertIn("旧形式のため未確認", v["reason"])
        v = common.unsub_verdict(svc(emails=[mail("2026-10-11T15:00:00+09:00", attribution="trusted")]))
        self.assertEqual((v["status"], v["after"]), ("ineffective", 1))

    def test_missing_received_at_is_pending(self):  # 欠損を黙って除外→「効いた」になっていた
        for bad in (None, "", "あとで", "2026-10-11 15:00", "Sun, 11 Oct 2026 06:00:00 -0000"):
            with self.subTest(bad=bad):
                v = common.unsub_verdict(svc(emails=[mail(bad)]))
                self.assertEqual(v["status"], "pending")

    def test_date_only_unsubscribed_at_is_pending(self):  # 日付のみを 00:00 JST 扱いしていた
        v = common.unsub_verdict(svc(unsubscribed_at="2026-10-11",
                                     emails=[mail("2026-10-11T10:00:00+09:00")]))
        self.assertEqual(v["status"], "pending")

    def test_unobserved_when_no_unsub(self):
        self.assertEqual(common.unsub_verdict(svc(unsubscribed_at=None))["status"], "unobserved")

    def test_unverified_sender_not_counted(self):
        v = common.unsub_verdict(svc(emails=[mail("2026-10-11T15:00:00+09:00", attribution="unverified")]))
        self.assertEqual(v["status"], "pending")


# ---------- 最優先2: +タグだけで帰属 ----------
class TestTagAttribution(unittest.TestCase):
    def test_tag_re_boundaries(self):
        self.assertIsNone(common.extract_tag("notyourbot+shop@example.com"))
        self.assertIsNone(common.extract_tag("yourbot+shop@example.com.evil.org"))
        self.assertIsNone(common.extract_tag("yourbot+shop@example.community"))
        self.assertEqual(common.extract_tag("Bot <yourbot+shop@example.com>"), "shop")
        self.assertEqual(common.extract_tag("a@x.jp, yourbot+Shop@EXAMPLE.com."), "shop")

    def test_unknown_tag_is_quarantined_not_new_service(self):
        ledger = {"services": [svc()]}
        quiet(ingest_email.ingest, {"to": "yourbot+zzz@example.com", "from": "a@b.example", "subject": "x",
                                    "received_at": "2026-10-11T15:00:00+09:00"}, ledger)
        names = [s["service"] for s in ledger["services"]]
        self.assertEqual(names, ["svc-a", "_quarantine"])
        self.assertNotIn("zzz", names)

    def test_sender_domain_mismatch_is_unverified(self):
        ledger = {"services": [svc()]}
        quiet(ingest_email.ingest, {"to": "yourbot+k7q2x@example.com", "from": "Shop <x@evil.example>",
                                    "subject": "セール", "received_at": "2026-10-11T15:00:00+09:00"}, ledger)
        e = ledger["services"][0]["emails"][0]
        self.assertEqual(e["attribution"], "unverified")
        self.assertEqual(common.unsub_verdict(ledger["services"][0])["status"], "pending")

    def test_sender_subdomain_verified(self):
        self.assertEqual(common.check_attribution(svc(), "a <n@mail.shop.example>"), "verified")
        self.assertEqual(common.check_attribution(svc(), "a <n@shop.example.evil.org>"), "unverified")
        self.assertEqual(common.check_attribution(svc(expected_sender_domains=[]), "n@x"), "unchecked")

    def test_report_and_ema_have_no_tag_address(self):
        s = svc(tag_address="yourbot+k7q2x@example.com",
                emails=[mail("2026-10-11T15:00:00+09:00", to="yourbot+k7q2x@example.com")])
        md = report.render_md(s, report.metrics(s), False, "G")
        self.assertNotIn("yourbot+", md)
        self.assertNotIn("k7q2x", md)

    def test_docs_mention_unguessable_tags(self):
        for p in ("README.md", "docs/SETUP.md"):
            with open(os.path.join(ROOT, p), encoding="utf-8") as f:
                self.assertIn("推測されにくい", f.read(), p)


# ---------- 最優先3: エスケープ ----------
class TestEscape(unittest.TestCase):
    EVIL = "a|b\n| 偽行 | x |\r\n![img](https://evil.example/p.png) [link](https://evil.example) <img src=x onerror=1>"

    def test_md_cell(self):
        c = common.md_cell(self.EVIL)
        self.assertNotIn("\n", c)
        self.assertNotIn("\r", c)
        self.assertNotIn("<img", c)
        self.assertNotIn("](", c)
        self.assertNotIn("![", c)
        for i, ch in enumerate(c):
            if ch == "|":
                self.assertEqual(c[i - 1], "\\")

    def test_table_rows_not_broken(self):
        s = svc(emails=[mail("2026-10-11T15:00:00+09:00", subject=self.EVIL, **{"from": self.EVIL})])
        md = report.render_md(s, report.metrics(s), False, "G")
        rows = [l for l in md.splitlines() if l.startswith("| 2026-10-11")]
        self.assertEqual(len(rows), 1)
        self.assertNotIn("偽行 |", md.replace("\\|", ""))

    def test_unsubscribe_url_domain_only(self):
        s = svc(unsubscribe_url="https://lists.shop.example/u?token=SECRET123&id=9")
        md = report.render_md(s, report.metrics(s), False, "G")
        self.assertIn("lists.shop.example", md)
        self.assertNotIn("SECRET123", md)

    def test_ledger_newlines_do_not_crash(self):
        s = svc(service="名前\n改行", required_fields=["a\nb"], dark_patterns=["x\ny"], notes="n\nm",
                signup_url="https://shop.example/\nx")
        md = report.render_md(s, report.metrics(s), False, "G")
        self.assertIn("名前 改行", md)

    def test_png_with_newlines_and_long_name(self):
        try:
            import PIL  # noqa: F401
            report.find_font_cached()
        except Exception:
            self.skipTest("Pillow または日本語フォントが無い")
        s = svc(service="とても長いサービス名" * 8 + "\n改行", dark_patterns=["長い説明" * 40])
        with tempfile.TemporaryDirectory() as t:
            report.render_png(s, report.metrics(s), False, os.path.join(t, "a.png"), "G")
            self.assertTrue(os.path.exists(os.path.join(t, "a.png")))


# ---------- 最優先4: 重複判定 ----------
class TestDedupe(unittest.TestCase):
    def _ingest(self, ledger, **kw):
        m = {"to": "yourbot+k7q2x@example.com", "from": "n@shop.example", "subject": "週刊",
             "received_at": "2026-10-11T15:00:00+09:00"}
        m.update(kw)
        return quiet(ingest_email.ingest, m, ledger)[1]

    def test_different_message_id_same_second_both_kept(self):
        L = {"services": [svc()]}
        self.assertTrue(self._ingest(L, message_id="<1@x>"))
        self.assertTrue(self._ingest(L, message_id="<2@x>"))
        self.assertEqual(len(L["services"][0]["emails"]), 2)

    def test_same_message_id_skipped(self):
        L = {"services": [svc()]}
        self._ingest(L, message_id="<1@x>")
        self.assertFalse(self._ingest(L, message_id="<1@x>", subject="別件名"))

    def test_tz_notation_difference_is_duplicate(self):
        L = {"services": [svc()]}
        self.assertTrue(self._ingest(L))
        self.assertFalse(self._ingest(L, received_at="2026-10-11T06:00:00Z"))
        self.assertFalse(self._ingest(L, received_at="Sun, 11 Oct 2026 06:00:00 +0000"))
        self.assertEqual(len(L["services"][0]["emails"]), 1)


# ---------- 次に直すもの ----------
class TestZeroVsUnobserved(unittest.TestCase):
    def test_zero_vs_unobserved(self):
        m = report.metrics(svc())
        self.assertEqual(m["required"], "なし（0件）")
        self.assertEqual(m["dark_n"], "なし（0件）")
        self.assertTrue(m["mail"].startswith("0通"))
        m2 = report.metrics(svc(required_fields=None, dark_patterns=None, emails=None, observed_until=None))
        self.assertEqual((m2["required"], m2["dark_n"], m2["mail"]), ("未観測",) * 3)
        m3 = report.metrics(svc(signed_up_at=None))
        self.assertEqual(m3["required"], "未観測")


class TestTime(unittest.TestCase):
    def test_slash_format(self):
        self.assertEqual(common.parse_dt_ex("2026/10/11 14:00")[1], "tz_unknown")
        self.assertIn("TZ不明", common.fmt_jst("2026/10/11 14:00"))
        self.assertEqual(common.unsub_verdict(svc(unsubscribed_at="2026/10/11 14:00"))["status"], "pending")
        self.assertIsNotNone(common.parse_dt("2026/10/11 14:00+09:00") or common.parse_dt("2026-10-11T14:00+09:00"))

    def test_minus0000_and_naive_are_tz_unknown(self):
        self.assertEqual(common.parse_dt_ex("Sun, 11 Oct 2026 06:00:00 -0000")[1], "tz_unknown")
        self.assertEqual(common.parse_dt_ex("2026-10-11T14:00:00")[1], "tz_unknown")
        self.assertIsNone(common.parse_dt("2026-10-11T14:00:00"))
        self.assertEqual(common.parse_dt_ex("2026-10-11")[1], "date_only")

    def test_sort_by_utc(self):
        L = {"services": [svc()]}
        for t in ("2026-10-11T10:30:00+09:00", "2026-10-11T01:00:00Z", "2026-10-11T09:45:00+09:00"):
            quiet(ingest_email.ingest, {"to": "yourbot+k7q2x@example.com", "from": "n@shop.example",
                                        "subject": t, "received_at": t}, L)
        got = [e["received_at"] for e in L["services"][0]["emails"]]
        self.assertEqual(got, ["2026-10-11T09:45:00+09:00", "2026-10-11T01:00:00Z", "2026-10-11T10:30:00+09:00"])


class TestClassify(unittest.TestCase):
    CASES = [("お問い合わせ番号 12345 について", "サポート <support@x.example>", False, "transactional"),
             ("請求書を発行しました", "freee <no-reply@freee.example>", False, "transactional"),
             ("お支払い方法の変更について", "楽天ポイント <info@x.example>", False, "transactional"),
             ("ご登録ありがとう！今だけクーポン", "shop <info@x.example>", False, "marketing"),
             ("配信停止を受け付けました", "shop <info@x.example>", True, "transactional")]

    def test_review_examples(self):
        for subj, frm, lu, want in self.CASES:
            with self.subTest(subj=subj):
                self.assertEqual(ingest_email.classify(subj, frm, lu), want)

    def test_display_name_not_used(self):
        self.assertEqual(ingest_email.classify("ご注文の確認", "セール速報 <a@x.example>", False), "transactional")

    def test_word_boundary(self):
        self.assertEqual(ingest_email.classify("freee からのお知らせ", "a@x", False), "other")
        self.assertEqual(ingest_email.classify("Free shipping this week", "a@x", False), "marketing")


class TestFileNames(unittest.TestCase):
    def test_no_collision(self):
        names = {report.out_basename({"service": n, "tag": t}) for n, t in
                 (("a.b", "a.b"), ("a_b", "a_b"), ("日本語A", None), ("日本語B", None))}
        self.assertEqual(len(names), 4)


class TestEmaEllipsis(unittest.TestCase):
    def test_ellipsis_in_code(self):
        with open(os.path.join(ROOT, "tools", "report.py"), encoding="utf-8") as f:
            src = f.read()
        self.assertIn('last + "…"', src)
        self.assertIn("def fit_font", src)


class TestNoSilentFailure(unittest.TestCase):
    def run_tool(self, args, stdin=None, env_extra=None, unset_env=False):
        env = dict(os.environ)
        if unset_env:
            env.pop("HITOBASHIRA_BOT_LOCAL", None); env.pop("HITOBASHIRA_BOT_DOMAIN", None)
        env.update(env_extra or {})
        return subprocess.run([sys.executable] + args, input=stdin, capture_output=True, text=True, env=env,
                              cwd=os.path.join(ROOT, "tools"))

    def test_missing_ledger_report_nonzero(self):
        r = self.run_tool(["report.py", "--ledger", "/nonexistent/ledger.json", "--out", tempfile.mkdtemp()])
        self.assertEqual(r.returncode, 2)

    def test_missing_ledger_ingest_nonzero_without_create(self):
        r = self.run_tool(["ingest_email.py", "-", "--ledger", os.path.join(tempfile.mkdtemp(), "l.json")],
                          stdin='{"to":"yourbot+a@example.com"}')
        self.assertEqual(r.returncode, 2)

    def test_env_unset_warns(self):
        d = tempfile.mkdtemp()
        r = self.run_tool(["ingest_email.py", "-", "--ledger", os.path.join(d, "l.json"), "--create-ledger"],
                          stdin='{"to":"yourbot+a@example.com"}', unset_env=True)
        self.assertEqual(r.returncode, 0)
        self.assertIn("未設定", r.stderr)

    def test_emails_key_missing_or_null_or_bad(self):
        self.assertEqual(report.metrics(svc(emails=None))["mail"], "未観測")
        s = svc(); del s["emails"]
        self.assertEqual(report.metrics(s)["mail"], "未観測")
        with self.assertRaises(common.LedgerError):
            report.metrics(svc(emails="oops"))

    def test_unknown_type_counted(self):
        m = report.metrics(svc(emails=[mail("2026-10-11T15:00:00+09:00", "spam")]))
        self.assertIn("不明1", quiet(lambda: m)["mail"])


class TestTestsiteServer(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        import http.server, server
        cls.tmp = tempfile.mkdtemp()
        server.LOG = os.path.join(cls.tmp, "submissions.jsonl")
        server.H.log_message = lambda *a: None
        cls.mod = server
        cls.httpd = http.server.ThreadingHTTPServer(("127.0.0.1", 0), server.H)
        cls.base = f"http://127.0.0.1:{cls.httpd.server_address[1]}"
        threading.Thread(target=cls.httpd.serve_forever, daemon=True).start()

    @classmethod
    def tearDownClass(cls):
        cls.httpd.shutdown(); cls.httpd.server_close()

    def code(self, path, data=None):
        try:
            return urllib.request.urlopen(self.base + path, data=data, timeout=5).status
        except urllib.error.HTTPError as e:
            return e.code

    def test_routes(self):
        self.assertEqual(self.code("/"), 200)
        for p in ("/submissions.jsonl", "/server.py", "/index.html", "/../README.md"):
            self.assertEqual(self.code(p), 404, p)
        self.assertEqual(self.code("/other", b"a=1"), 404)

    def test_record_keeps_blank_fields_client_ua(self):
        req = urllib.request.Request(self.base + "/submit", data=b"name=&email=a%40b.example&opt=1&opt=2",
                                     headers={"User-Agent": "unittest-UA"})
        urllib.request.urlopen(req, timeout=5).read()
        with open(self.mod.LOG, encoding="utf-8") as f:
            rec = json.loads(f.read().splitlines()[-1])
        self.assertEqual(rec["fields"]["name"], "")
        self.assertEqual(rec["fields"]["opt"], ["1", "2"])
        self.assertEqual(rec["user_agent"], "unittest-UA")
        self.assertTrue(rec["client"].startswith("127.0.0.1"))


class TestSkillScope(unittest.TestCase):
    def test_skill_scope(self):
        with open(os.path.join(ROOT, "skills", "signup-page-audit", "SKILL.md"), encoding="utf-8") as f:
            s = f.read()
        self.assertIn("自分が所有する 127.0.0.1 上のテスト環境のみ", s)
        self.assertNotIn("自動テストが明示的に許された環境", s)


# ---------- 文書 ----------
class TestDocs(unittest.TestCase):
    def test_examples_reproducible(self):
        r = subprocess.run([sys.executable, os.path.join(ROOT, "examples", "rebuild.py"), "--check"],
                           capture_output=True, text=True)
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)

    def test_schema_has_fields(self):
        with open(os.path.join(ROOT, "templates", "ledger.schema.md"), encoding="utf-8") as f:
            s = f.read()
        for k in ("lu_source", "unsubscribe_url", "message_id", "thread_id", "notes", "observed_until",
                  "expected_sender_domains", "attribution"):
            self.assertIn(k, s)

    def test_template_sections_match_output(self):
        s = svc(emails=[mail("2026-10-11T15:00:00+09:00")])
        md = report.render_md(s, report.metrics(s), False, "G")
        heads = [l for l in md.splitlines() if l.startswith("## ")]
        with open(os.path.join(ROOT, "templates", "report_template.md"), encoding="utf-8") as f:
            tpl = [l for l in f.read().splitlines() if l.startswith("## ")]
        self.assertEqual([h for h in heads if h != "## メモ"], [h for h in tpl if h != "## メモ"])

    def test_wilson(self):
        import math
        def wilson(k, n, z=1.959963984540054):
            p = k / n; d = 1 + z * z / n
            c = (p + z * z / (2 * n)) / d; h = z * math.sqrt(p * (1 - p) / n + z * z / (4 * n * n)) / d
            return round(c - h, 3), round(c + h, 3)
        self.assertEqual(wilson(15, 15), (0.796, 1.0))
        self.assertEqual(wilson(15, 16), (0.717, 0.989))
        with open(os.path.join(ROOT, "README.md"), encoding="utf-8") as f:
            r = f.read()
        self.assertIn("0.796", r); self.assertIn("0.717", r); self.assertIn("0.989", r)
        self.assertNotIn("人柱くん単独採点", r)


if __name__ == "__main__":
    unittest.main()
