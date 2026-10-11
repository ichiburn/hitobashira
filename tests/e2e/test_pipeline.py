"""issue #5: 役割間の引き継ぎ（handoff）と通しのパイプライン。
handoff の検証は標準ライブラリのみ。通しの実行は Mailpit が無ければスキップ。通信は 127.0.0.1 だけ。"""
import json, os, re, sys, tempfile, unittest
from tests import _helpers  # noqa: F401
sys.path.insert(0, _helpers.ROOT)
sys.path.insert(0, os.path.join(_helpers.ROOT, "testsite_mail"))
from e2e import handoff as H
from harness.runctx import RunContext, verify_manifest


def base(**kw):
    h = {"schema": H.SCHEMA, "run_id": "r", "seq": 0, "from_role": "coordinator", "to_role": "hitobashira",
         "prev_state": None, "state": "CREATED", "created_at": "2026-10-11T20:00:00+09:00", "artifacts": [],
         "prev_handoff_sha256": None}
    h.update(kw)
    return h


class TestHandoffSchema(unittest.TestCase):
    def test_valid_and_schema_file_in_sync(self):
        self.assertTrue(H.validate(base()))
        with open(os.path.join(_helpers.ROOT, "schemas", "handoff.schema.json"), encoding="utf-8") as f:
            sch = json.load(f)
        self.assertEqual(set(sch["required"]), set(H.REQUIRED))
        self.assertEqual(set(sch["properties"]), H.ALLOWED_KEYS)
        self.assertEqual(sch["properties"]["schema"]["const"], H.SCHEMA)

    def test_invalid(self):
        bad = [base(state="VERIFIED"),                                   # 遷移を飛ばす
               base(from_role="observer"),                               # 担当が違う
               base(prev_state="SURVEYED", state="OBSERVED", from_role="observer", to_role="coordinator"),
               base(prev_state="OBSERVED", state="VERIFIED", from_role="verifier", to_role="coordinator"),  # verify_lock 無し
               base(state="ABORTED"),                                    # 理由なし
               base(artifacts=[{"path": "/etc/passwd", "sha256": "0" * 64}]),
               base(artifacts=[{"path": "../x", "sha256": "0" * 64}]),
               base(extra=1)]
        for h in bad:
            with self.subTest(h=h):
                with self.assertRaises(H.HandoffError):
                    H.validate(h)

    def test_chain_detects_tamper(self):
        ctx = RunContext(tempfile.mkdtemp())
        ch = H.Chain(ctx)
        p = ctx.put_derived("task.json", {"t": 1})
        ch.emit("coordinator", "hitobashira", "CREATED", [p])
        with self.assertRaises(H.HandoffError):
            ch.accept("observer")                       # 宛先違い
        arts, _ = ch.accept("hitobashira")
        self.assertEqual(arts, [p])
        os.chmod(p, 0o644)
        with open(p, "w") as f:
            f.write("{}")
        with self.assertRaises(H.HandoffError):
            ch.accept("hitobashira")                    # 成果物が書き換えられた

    def test_abort_is_recorded(self):
        ctx = RunContext(tempfile.mkdtemp())
        ch = H.Chain(ctx)
        ch.emit("coordinator", "hitobashira", "CREATED", [])
        p, h = ch.emit("hitobashira", "coordinator", "ABORTED", reason="サイトに接続できない")
        self.assertEqual((ch.state, h["prev_state"]), ("ABORTED", "CREATED"))


from e2e import run_mail_e2e as E  # noqa: E402
BIN = E.find_mailpit()


@unittest.skipUnless(BIN, "Mailpit のバイナリが無い（MAILPIT_BIN / PATH / /tmp/mailpit）")
class TestPipelineE2E(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        import server_mail
        from e2e import pipeline
        cls.P = pipeline
        cls.mp = E.MailpitProcess(BIN)
        cls.out = tempfile.mkdtemp()
        cls.res = {}
        for mode in ("honest", "evil"):
            httpd, site = server_mail.serve(mode, 0, "127.0.0.1", cls.mp.smtp_port)
            try:
                cls.res[mode] = pipeline.run_pipeline(site.base_url + "/", server_mail.SITES[mode]["domain"], cls.mp,
                                                      cls.out, use_browser=False, observe_seconds=1.0)
            finally:
                httpd.shutdown(); httpd.server_close()

    @classmethod
    def tearDownClass(cls):
        cls.mp.stop()

    def test_both_sites_reported_and_split(self):
        self.assertEqual(self.res["honest"]["state"], "REPORTED", self.res["honest"])
        self.assertEqual(self.res["evil"]["state"], "REPORTED", self.res["evil"])
        self.assertEqual(self.res["honest"]["status"], "effective")
        self.assertEqual(self.res["evil"]["status"], "ineffective")
        self.assertEqual(self.res["evil"]["agreement"], "一致")

    def test_handoff_chain_valid(self):
        d = os.path.join(self.res["evil"]["run_dir"], "handoff")
        files = sorted(os.listdir(d))
        states = []
        for f in files:
            with open(os.path.join(d, f), encoding="utf-8") as fh:
                h = json.load(fh)
            H.validate(h)
            states.append(h["state"])
        self.assertEqual(states, ["CREATED", "SURVEYED", "OBSERVED", "VERIFIED", "REPORTED"])
        self.assertEqual(verify_manifest(self.res["evil"]["run_dir"]), [])

    def test_verifier_blind_to_observer_conclusion_and_locked_first(self):
        d = self.res["evil"]["run_dir"]
        with open(os.path.join(d, "handoff", "02_observer_to_verifier.json"), encoding="utf-8") as f:
            h = json.load(f)
        paths = [a["path"] for a in h["artifacts"]]
        self.assertNotIn("verdict/observer_verdict.json", paths)
        self.assertIn("verdict/observer_verdict.json", h["withheld"])
        self.assertTrue(all(p.startswith("evidence/") or p == "derived/task.json" for p in paths))
        with open(os.path.join(d, "verdict", "compare.json"), encoding="utf-8") as f:
            c = json.load(f)
        self.assertLessEqual(c["verify_locked_at"], c["opened_observer_verdict_at"])
        with open(os.path.join(d, "log.jsonl"), encoding="utf-8") as f:
            ev = [json.loads(l)["event"] for l in f]
        self.assertLess(ev.index("verifier_locked"), max(i for i, e in enumerate(ev) if e == "verdict_saved"))

    def test_intermediate_artifacts(self):
        d = self.res["honest"]["run_dir"]
        for p in ("derived/survey.json", "derived/mail_events.json", "derived/unsub_observation.json",
                  "verdict/verify.json", "verdict/report.md"):
            self.assertTrue(os.path.exists(os.path.join(d, p)), p)

    def test_report_contents(self):
        for mode, r in self.res.items():
            with open(r["report"], encoding="utf-8") as f:
                md = f.read()
            with self.subTest(mode=mode):
                self.assertIn("## 約束と現実の差分", md)
                self.assertIn("## 検証の条件と限界", md)
                self.assertIn("実行時間（実測）", md)
                self.assertIn("完全なブラインド検証ではない", md)
                self.assertNotIn("yourbot+", md)
                self.assertNotRegex(md, r"/(home|workspace|tmp)/")
        with open(self.res["evil"]["report"], encoding="utf-8") as f:
            self.assertIn("| 破った |", f.read())

    def test_abort_when_site_down(self):
        r = self.P.run_pipeline("http://127.0.0.1:9/", "x.test", self.mp, self.out, use_browser=False, observe_seconds=0)
        self.assertEqual(r["state"], "ABORTED")
        self.assertTrue(r["error"])


if __name__ == "__main__":
    unittest.main()


class TestTargetCheck(unittest.TestCase):
    """--target の受け付け判定（通信しない）。実在の外部 URL は渡さず、判定だけを確かめる。"""

    def test_rejects_outside_allowlist(self):
        from e2e import pipeline
        for u in ("https://example.com/", "http://example.com:8781/", "http://localhost:8781/", "http://[::1]:8781/",
                  "https://127.0.0.1:8781/", "http://127.0.0.1/", "http://127.0.0.2:8781/", "file:///etc/passwd"):
            with self.subTest(u=u):
                self.assertFalse(pipeline.check_target(u)[0])
        self.assertFalse(pipeline.check_target("http://127.0.0.1:8783/", [8781, 8782])[0])
        self.assertTrue(pipeline.check_target("http://127.0.0.1:8781/", [8781, 8782])[0])

    def test_cli_rejects_before_any_network(self):
        import contextlib, io
        from e2e import pipeline
        err = io.StringIO()
        with contextlib.redirect_stderr(err), contextlib.redirect_stdout(io.StringIO()):
            rc = pipeline.main(["--target", "https://example.com/", "--mailpit-bin", "/nonexistent", "--no-browser"])
        self.assertEqual(rc, 4)
        self.assertIn("拒否", err.getvalue())
        with contextlib.redirect_stderr(io.StringIO()), contextlib.redirect_stdout(io.StringIO()):
            rc = pipeline.main(["--target", "http://127.0.0.1:8783/", "--allowed-ports", "8781,8782",
                                "--mailpit-bin", "/nonexistent", "--no-browser"])
        self.assertEqual(rc, 4)

    def test_run_pipeline_rejects_external(self):
        from e2e import pipeline
        with self.assertRaises(ValueError):
            pipeline.run_pipeline("https://example.com/", "x.test", None, tempfile.mkdtemp())


@unittest.skipUnless(BIN, "Mailpit のバイナリが無い（MAILPIT_BIN / PATH / /tmp/mailpit）")
class TestTargetCLI(unittest.TestCase):
    """起動済みのテストサイト（別プロセス）に URL を1回渡すだけで最後まで動く（honest / evil）。"""

    def test_target_honest_and_evil(self):
        import contextlib, io, socket, subprocess, time
        from e2e import pipeline
        mp = E.MailpitProcess(BIN)
        procs, ports = [], {}
        try:
            for mode in ("honest", "evil"):
                ports[mode] = E.free_port()
                procs.append(subprocess.Popen([sys.executable, os.path.join(_helpers.ROOT, "testsite_mail", "server_mail.py"),
                                               "--mode", mode, "--port", str(ports[mode]), "--smtp-port", str(mp.smtp_port)],
                                              stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL))
            for p in ports.values():
                for _ in range(50):
                    try:
                        socket.create_connection(("127.0.0.1", p), 0.2).close()
                        break
                    except OSError:
                        time.sleep(0.1)
            out = tempfile.mkdtemp()
            for mode, want in (("honest", "effective"), ("evil", "ineffective")):
                import server_mail
                buf = io.StringIO()
                with contextlib.redirect_stdout(buf), contextlib.redirect_stderr(io.StringIO()):
                    rc = pipeline.main(["--target", f"http://127.0.0.1:{ports[mode]}/",
                                        "--sender-domain", server_mail.SITES[mode]["domain"],
                                        "--mailpit-api", mp.api, "--mailpit-smtp-port", str(mp.smtp_port),
                                        "--allowed-ports", ",".join(str(x) for x in ports.values()),
                                        "--expect", want, "--out", out, "--no-browser", "--observe-seconds", "1"])
                r = json.loads(buf.getvalue())[0]
                with self.subTest(mode=mode):
                    self.assertEqual(rc, 0, r)
                    self.assertEqual((r["state"], r["status"], r["agreement"]), ("REPORTED", want, "一致"))
        finally:
            for p in procs:
                p.terminate(); p.wait(5)
            mp.stop()
