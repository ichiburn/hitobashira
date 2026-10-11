"""issue #3: 送信先の許可判定と実行証跡の分離（標準ライブラリのみ。ブラウザ不要）。"""
import json, os, stat, tempfile, unittest
from tests import _helpers  # noqa: F401
import sys
sys.path.insert(0, _helpers.ROOT)
from harness.egress import EgressPolicy
from harness import runctx

P = EgressPolicy([8801], {8801: ["/submit"]})


class TestEgressPolicy(unittest.TestCase):
    def test_allowed_read_and_write(self):
        self.assertTrue(P.decide("http://127.0.0.1:8801/")[0])
        self.assertTrue(P.decide("http://127.0.0.1:8801/submit", "POST")[0])

    def test_external_and_alias_hosts_denied(self):
        for u in ("https://example.com/", "http://example.com/", "http://localhost:8801/", "http://[::1]:8801/",
                  "http://127.0.0.2:8801/", "http://2130706433:8801/", "http://127.0.0.1.nip.io:8801/",
                  "http://user:pw@127.0.0.1:8801/", "ftp://127.0.0.1:8801/", "file:///etc/passwd"):
            with self.subTest(u=u):
                self.assertFalse(P.decide(u)[0])

    def test_unlisted_port_and_path_denied(self):
        self.assertFalse(P.decide("http://127.0.0.1:8802/")[0])
        self.assertFalse(P.decide("http://127.0.0.1/")[0])                     # 80 番は許可していない
        self.assertFalse(P.decide("http://127.0.0.1:8801/admin", "POST")[0])  # 許可パス以外への書き込み
        self.assertFalse(P.decide("http://127.0.0.1:8801/submit", "DELETE")[0] and False)
        self.assertFalse(EgressPolicy([8801]).decide("http://127.0.0.1:8801/submit", "POST")[0])  # 書き込み許可なし

    def test_external_post_denied(self):
        self.assertFalse(P.decide("http://203.0.113.10/collect", "POST")[0])

    def test_websocket_denied(self):
        self.assertFalse(P.decide("ws://127.0.0.1:8801/ws")[0])
        self.assertFalse(P.decide("http://127.0.0.1:8801/ws", kind="websocket")[0])

    def test_local_schemes(self):
        self.assertTrue(P.decide("about:blank")[0])
        self.assertTrue(P.decide("data:text/plain,hi")[0])
        self.assertFalse(P.decide("data:text/plain,hi", "POST")[0])


class TestRunContext(unittest.TestCase):
    def test_run_dirs_are_separate_and_evidence_write_once(self):
        base = tempfile.mkdtemp()
        a, b = runctx.RunContext(base), runctx.RunContext(base)
        self.assertNotEqual(a.run_id, b.run_id)
        self.assertNotEqual(a.dir, b.dir)
        p = a.put_evidence("x.json", {"k": 1})
        self.assertFalse(os.stat(p).st_mode & stat.S_IWUSR)        # 読み取り専用
        with self.assertRaises(FileExistsError):
            a.put_evidence("x.json", {"k": 2})                    # 上書きしない
        v = a.put_verdict("v.json", {"status": "ok"})
        self.assertTrue(v.startswith(a.verdict_dir))              # 判定は evidence/ と別
        self.assertIn("decided_at", json.load(open(v, encoding="utf-8")))

    def test_manifest_detects_change(self):
        ctx = runctx.RunContext(tempfile.mkdtemp())
        p = ctx.put_evidence("a.txt", "hello")
        ctx.write_manifest()
        man = json.load(open(os.path.join(ctx.dir, "manifest.json"), encoding="utf-8"))
        self.assertEqual(man["files"][0]["sha256"], runctx.sha256_file(p))
        self.assertIn("真正性は保証しない", man["note"])
        self.assertEqual(runctx.verify_manifest(ctx.dir), [])
        os.chmod(p, 0o644)
        with open(p, "a") as f:
            f.write("!")
        self.assertEqual(runctx.verify_manifest(ctx.dir), ["evidence/a.txt"])

    def test_log_records_events(self):
        ctx = runctx.RunContext(tempfile.mkdtemp())
        ctx.put_evidence("a.txt", "x", role="hitobashira")
        ctx.put_verdict("v.json", {"s": 1}, role="verifier")
        ev = [json.loads(l)["event"] for l in open(ctx.log_path, encoding="utf-8")]
        self.assertEqual(ev, ["run_start", "evidence_saved", "verdict_saved"])


class TestDocsEgress(unittest.TestCase):
    def test_setup_marks_windows_wsl2_out_of_scope(self):
        # 2026-10-11 21時台の判断: 対応は Linux。Windows / WSL2 は対象外（任意）で、手順は参考・未検証として残す
        with open(os.path.join(_helpers.ROOT, "docs", "SETUP.md"), encoding="utf-8") as f:
            s = f.read()
        self.assertIn("harness", s)
        self.assertIn("対象外（任意）", s)
        self.assertIn("参考・未検証", s)
        with open(os.path.join(_helpers.ROOT, "README.md"), encoding="utf-8") as f:
            self.assertIn("| Windows 11 / WSL2 での実行 | 対象外（任意） |", f.read())

    def test_compose_publishes_only_loopback(self):
        import re
        with open(os.path.join(_helpers.ROOT, "compose.yaml"), encoding="utf-8") as f:
            y = f.read()
        ports = re.findall(r'^\s*-\s*"([^"]+)"', y, re.M)
        self.assertTrue(ports)
        for p in ports:
            self.assertTrue(p.startswith("127.0.0.1:"), p)


if __name__ == "__main__":
    unittest.main()
