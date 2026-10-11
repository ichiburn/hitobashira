"""issue #3: Playwright ハーネスの送信先制御（Playwright が無ければスキップ）。
実際に通信するのは、このテストが 127.0.0.1 で起動する自作ページだけ。外部宛ての通信はすべて遮断される前提で、
宛先には文書用アドレス（203.0.113.0/24, RFC 5737）を使う。"""
import json, os, sys, tempfile, unittest
from tests import _helpers  # noqa: F401
sys.path.insert(0, _helpers.ROOT)

try:
    import playwright.sync_api  # noqa: F401
    HAVE_PW = True
except Exception:
    HAVE_PW = False


@unittest.skipUnless(HAVE_PW, "Playwright（python）が無い")
class TestGuardedBrowser(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        from harness import fixture_site
        from harness.egress import EgressPolicy
        from harness.runctx import RunContext
        from harness.browser import GuardedBrowser
        cls.canary, cls.cport, cls.cstate = fixture_site.start_canary()
        cls.site, cls.port, cls.state = fixture_site.start(other_port=cls.cport)
        cls.ctx = RunContext(tempfile.mkdtemp())
        try:
            cls.b = GuardedBrowser(EgressPolicy([cls.port], {cls.port: ["/submit"]}), cls.ctx)
        except Exception as ex:  # ブラウザ本体が無い
            cls.site.shutdown(); cls.canary.shutdown()
            raise unittest.SkipTest(f"ブラウザを起動できない: {ex}")
        cls.base = f"http://127.0.0.1:{cls.port}"
        cls.page = cls.b.open(cls.base + "/")
        cls.survey = cls.b.audit(cls.page, "fixture")

    @classmethod
    def tearDownClass(cls):
        cls.b.close(); cls.site.shutdown(); cls.canary.shutdown()

    def blocked(self, frag):
        return [r for r in self.b.blocked if frag in r["url"]]

    def test_injection_does_not_stop_audit(self):
        self.assertTrue(self.survey["untrusted_instructions_detected"])
        self.assertIn("無視", " ".join(self.survey["untrusted_instructions_detected"]))
        self.assertTrue(self.survey["forms"])                          # 監査は継続
        self.assertEqual(self.survey["prechecked_optins"], ["partner_optin"])
        self.assertEqual(self.survey["required_fields"], ["email"])

    def test_external_fetch_iframe_ws_blocked(self):
        self.assertTrue(self.blocked("203.0.113.10/beacon"))
        self.assertTrue(self.blocked("203.0.113.10/frame"))
        self.assertTrue(any(r["kind"] == "websocket" for r in self.b.blocked))
        self.assertTrue(self.blocked("localhost:"))

    def test_unlisted_port_not_reached(self):
        self.assertTrue(self.blocked(f"127.0.0.1:{self.cport}/steal"))
        self.assertEqual(self.cstate["hits"], [])

    def test_redirects_checked(self):
        from playwright.sync_api import Error
        for path in ("/go-out", "/go-other-port"):   # 1ページ1回（遮断後に同じページを使い回さない）
            p = self.b.context.new_page()
            with self.assertRaises(Error):
                p.goto(self.base + path)
            p.close()
        self.assertTrue(any(r["kind"] == "redirect" for r in self.b.blocked))
        p = self.b.context.new_page()
        p.goto(self.base + "/go-in")
        self.assertEqual(p.url, self.base + "/")
        self.assertEqual(self.cstate["hits"], [])
        p.close()

    def test_external_form_post_refused(self):
        with self.assertRaises(PermissionError):
            self.b.submit(self.page, "#ext", {"email": "bot@example.com"})
        p = self.b.open(self.base + "/")
        p.evaluate("document.getElementById('ext').submit()")       # ページのスクリプトから直接送っても
        p.wait_for_timeout(500)
        self.assertTrue(any(r["method"] == "POST" and "203.0.113.10/collect" in r["url"] for r in self.b.blocked))
        p.close()

    def test_open_refuses_external(self):
        with self.assertRaises(PermissionError):
            self.b.open("https://example.com/")

    def test_allowed_post_succeeds_and_logged(self):
        p = self.b.open(self.base + "/")
        self.b.submit(p, "#ok", {"email": "bot@example.com"})
        self.assertIn({"path": "/submit", "body": "email=bot%40example.com&partner_optin=on"}, self.state["posts"])
        with open(self.ctx.log_path, encoding="utf-8") as f:
            events = [json.loads(l) for l in f]
        kinds = {e["event"] for e in events}
        for k in ("egress_allow", "egress_block", "op_submit", "evidence_saved"):
            self.assertIn(k, kinds)
        p.close()


if __name__ == "__main__":
    unittest.main()
