"""issue #4: Mailpit を使ったローカル E2E（Mailpit が無ければスキップ）。
通信はすべて 127.0.0.1（このテストが起動する Mailpit とテストサイト）だけ。"""
import json, os, sys, tempfile, unittest
from tests import _helpers  # noqa: F401
sys.path.insert(0, _helpers.ROOT)
sys.path.insert(0, os.path.join(_helpers.ROOT, "testsite_mail"))
from e2e import run_mail_e2e as E
from harness.runctx import verify_manifest

BIN = E.find_mailpit()


@unittest.skipUnless(BIN, "Mailpit のバイナリが無い（MAILPIT_BIN / PATH / /tmp/mailpit）")
class TestMailpitE2E(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.mp = E.MailpitProcess(BIN)
        cls.out = tempfile.mkdtemp()
        cls.res = {m: E.run_site(m, cls.mp, cls.out, use_browser=False, observe_seconds=1.0) for m in ("honest", "evil")}

    @classmethod
    def tearDownClass(cls):
        cls.mp.stop()

    def test_verdicts_split(self):
        self.assertEqual(self.res["honest"]["status"], "effective")
        self.assertEqual(self.res["evil"]["status"], "ineffective")

    def test_all_stages_ok(self):
        for m, r in self.res.items():
            for st in ("register", "receive", "ingest", "unsubscribe", "reobserve"):
                with self.subTest(site=m, stage=st):
                    self.assertTrue(r["stages"][st]["ok"], r["stages"])

    def test_confirmation_not_counted(self):  # 停止完了通知は販促の停止失敗に数えない
        for m in ("honest", "evil"):
            obs = json.load(open(os.path.join(self.res[m]["run_dir"], "derived", "unsub_observation.json"), encoding="utf-8"))
            self.assertEqual(obs["confirmation_mails"], 1)
            self.assertTrue(obs["acceptance_page"]["accepted"])
        hv = json.load(open(os.path.join(self.res["honest"]["run_dir"], "verdict", "07_unsub_verdict.json"), encoding="utf-8"))
        self.assertEqual(hv["verdict"]["after"], 0)

    def test_evidence_separated_and_manifest(self):
        for r in self.res.values():
            d = r["run_dir"]
            for sub, f in (("evidence", "02_mailpit_before_unsub.json"), ("evidence", "05_mailpit_after_unsub.json"),
                           ("evidence", "01_consent_state.json"), ("derived", "06_ledger_final.json"),
                           ("verdict", "07_unsub_verdict.json"), ("verdict", "07_report.md")):
                self.assertTrue(os.path.exists(os.path.join(d, sub, f)), (sub, f))
            self.assertEqual(verify_manifest(d), [])
        self.assertNotEqual(self.res["honest"]["run_dir"], self.res["evil"]["run_dir"])

    def test_no_body_or_received_header_saved(self):
        d = self.res["evil"]["run_dir"]
        snap = json.load(open(os.path.join(d, "evidence", "05_mailpit_after_unsub.json"), encoding="utf-8"))
        for r in snap:
            self.assertNotIn("Received", r["headers"])
            self.assertNotIn("Snippet", r)

    def test_report_regenerable_from_ledger(self):  # 証拠と判定を分け、判定は台帳から再生成できる
        import common, report
        d = self.res["evil"]["run_dir"]
        L = json.load(open(os.path.join(d, "derived", "06_ledger_final.json"), encoding="utf-8"))
        self.assertEqual(common.unsub_verdict(L["services"][0])["status"], "ineffective")
        md = report.render_md(L["services"][0], report.metrics(L["services"][0]), False, "G")
        self.assertNotIn(L["services"][0]["tag"], md)

    @unittest.skipUnless(E.have_playwright(), "Playwright（python）が無い")
    def test_browser_path(self):  # ブラウザ（harness/）で操作しても同じ判定になる
        r = E.run_site("evil", self.mp, self.out, use_browser=True, observe_seconds=0.5)
        self.assertEqual(r["status"], "ineffective", r["stages"])
        with open(os.path.join(r["run_dir"], "log.jsonl"), encoding="utf-8") as f:
            ev = [json.loads(l) for l in f]
        self.assertTrue(any(e["event"] == "egress_allow" and e.get("method") == "POST" for e in ev))

    def test_without_closed_env_trust_is_pending(self):  # 認証なし（domain_only）では「効かず」と断定しない
        r = E.run_site("evil", self.mp, self.out, use_browser=False, closed_env=False, observe_seconds=0.5)
        self.assertEqual(r["status"], "pending")


class TestMailSiteOffline(unittest.TestCase):
    """Mailpit 無しでも動く、テストサイトの配信停止の挙動（送信は outbox に貯める）。"""

    def _run(self, mode):
        import server_mail
        s = server_mail.Site(mode, smtp_port=None)
        s.base_url = "http://127.0.0.1:1"
        tok = s.register({"email": "yourbot+t@example.com"})
        s.unsubscribe(tok)
        s.campaign()
        return [m["kind"] for m in s.outbox]

    def test_honest_stops(self):
        self.assertEqual(self._run("honest"), ["transactional", "marketing", "transactional"])

    def test_evil_keeps_sending(self):
        self.assertEqual(self._run("evil"), ["transactional", "marketing", "transactional", "marketing"])

    def test_smtp_host_restricted(self):
        import server_mail
        with self.assertRaises(ValueError):
            server_mail.Site("honest", smtp_host="smtp.example.com", smtp_port=25)

    def test_adapter_loopback_only(self):
        from mail_adapters import MailpitAdapter
        for u in ("http://localhost:8025", "http://example.com:8025", "https://127.0.0.1:8025", "http://127.0.0.1"):
            with self.subTest(u=u):
                with self.assertRaises(ValueError):
                    MailpitAdapter(u)

    def test_adapter_decodes_headers_and_skips_received(self):
        from mail_adapters import MailpitAdapter
        a = MailpitAdapter("http://127.0.0.1:8025", closed_env=True)
        m, ev = a.to_ingest({"id": "X", "created": "2026-10-11T19:00:00+09:00", "headers": {
            "Subject": ["=?utf-8?b?6YWN5L+h5YGc5q2i44KS5Y+X44GR5LuY44GR44G+44GX44Gf?="], "From": ["a@x.test"],
            "To": ["yourbot+t@example.com"], "List-Unsubscribe": ["<http://127.0.0.1:1/unsubscribe?t=1>"]}})
        self.assertEqual(m["subject"], "配信停止を受け付けました")
        self.assertEqual(m["unsubscribe_url"], "http://127.0.0.1:1/unsubscribe?t=1")
        self.assertEqual((ev["source"], ev["ref"], ev["closed_env"]), ("mailpit_api", "X", True))


if __name__ == "__main__":
    unittest.main()
