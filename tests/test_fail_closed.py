"""issue #2: メール帰属と配信停止判定の fail-closed。標準ライブラリ unittest のみ。"""
import contextlib, io, os, unittest
from tests import _helpers  # noqa: F401
import auth_results, common, ingest_email, report

ROOT = _helpers.ROOT
TRUSTED_ID = "mx.receiver.example"
EV = {"source": "test_adapter", "trusted_source": True, "trusted_authserv_ids": [TRUSTED_ID]}


def svc(**kw):
    s = {"service": "svc-a", "tag": "k7q2x", "signed_up_at": "2026-10-11T13:00:00+09:00",
         "emails": [], "expected_sender_domains": ["shop.example"],
         "observed_until": "2026-10-12T13:00:00+09:00", "unsubscribed_at": "2026-10-11T14:00:00+09:00"}
    s.update(kw)
    return s


def mail(t="2026-10-11T15:00:00+09:00", typ="marketing", attribution="trusted", **kw):
    e = {"received_at": t, "from": "news@shop.example", "subject": "s", "type": typ, "attribution": attribution}
    e.update(kw)
    return e


def msg(ar=None, frm="Shop <news@shop.example>", subject="今だけクーポン", t="2026-10-11T15:00:00+09:00", **kw):
    m = {"to": "yourbot+k7q2x@example.com", "from": frm, "subject": subject, "received_at": t}
    if ar is not None:
        m["headers"] = {"Authentication-Results": ar}
    m.update(kw)
    return m


def ingest(m, ledger, evidence=None):
    with contextlib.redirect_stderr(io.StringIO()):
        return ingest_email.ingest(m, ledger, evidence=evidence)


class TestAuthResultsParse(unittest.TestCase):
    def test_parse_with_comments_and_props(self):
        r = auth_results.parse('mx.receiver.example (comment; x=y); spf=pass (sender ok) smtp.mailfrom=bounce@mail.shop.example;'
                               ' dkim=pass header.d=shop.example header.s=s1; dmarc=pass (p=REJECT) header.from=shop.example')
        self.assertEqual(r["authserv_id"], "mx.receiver.example")
        self.assertEqual([(x["method"], x["result"]) for x in r["results"]],
                         [("spf", "pass"), ("dkim", "pass"), ("dmarc", "pass")])
        self.assertEqual(r["results"][1]["props"]["header.d"], "shop.example")

    def test_trusted_dmarc_pass(self):
        e = auth_results.evaluate([f"{TRUSTED_ID}; dmarc=pass header.from=shop.example"], "shop.example", [TRUSTED_ID], True)
        self.assertEqual((e["status"], e["trusted"]), ("pass", True))

    def test_dkim_must_align(self):
        e = auth_results.evaluate([f"{TRUSTED_ID}; dkim=pass header.d=evil.example"], "shop.example", [TRUSTED_ID], True)
        self.assertNotEqual(e["status"], "pass")
        e = auth_results.evaluate([f"{TRUSTED_ID}; spf=pass smtp.mailfrom=b@mail.shop.example"], "shop.example", [TRUSTED_ID], True)
        self.assertEqual(e["status"], "pass")

    def test_dmarc_fail(self):
        e = auth_results.evaluate([f"{TRUSTED_ID}; spf=fail; dkim=none; dmarc=fail header.from=shop.example"],
                                  "shop.example", [TRUSTED_ID], True)
        self.assertEqual(e["status"], "fail")


class TestUntrustedAuthHeaders(unittest.TestCase):
    """偽装 From 一致 / 信頼できない認証ヘッダで帰属 trusted にならない"""

    def test_spoofed_from_without_auth_is_domain_only(self):
        L = {"services": [svc()]}
        ingest(msg(), L)
        self.assertEqual(L["services"][0]["emails"][0]["attribution"], "domain_only")

    def test_input_json_auth_header_not_trusted(self):  # 任意入力 JSON の A-R は信用しない
        L = {"services": [svc()]}
        ingest(msg(ar=f"{TRUSTED_ID}; dmarc=pass header.from=shop.example"), L)
        e = L["services"][0]["emails"][0]
        self.assertEqual(e["attribution"], "domain_only")
        self.assertFalse(e["provenance"]["auth"]["trusted"])
        self.assertEqual(e["provenance"]["auth"]["dmarc"], "pass")  # 解釈して記録はする
        self.assertEqual(e["provenance"]["source"], "input_json")

    def test_unknown_authserv_id_not_trusted(self):
        L = {"services": [svc()]}
        ingest(msg(ar="mx.attacker.example; dmarc=pass header.from=shop.example"), L, evidence=EV)
        self.assertEqual(L["services"][0]["emails"][0]["attribution"], "domain_only")

    def test_forged_header_below_top_ignored(self):  # 送信者が下に書いた偽の pass は使わない
        L = {"services": [svc()]}
        ingest(msg(ar=[f"{TRUSTED_ID}; spf=fail smtp.mailfrom=x@evil.example; dmarc=fail header.from=shop.example",
                       f"{TRUSTED_ID}; dmarc=pass header.from=shop.example"]), L, evidence=EV)
        self.assertEqual(L["services"][0]["emails"][0]["attribution"], "unverified")

    def test_trusted_pass_from_adapter(self):
        L = {"services": [svc()]}
        ingest(msg(ar=f"{TRUSTED_ID}; dmarc=pass header.from=shop.example"), L, evidence=EV)
        e = L["services"][0]["emails"][0]
        self.assertEqual(e["attribution"], "trusted")
        self.assertEqual(e["provenance"]["source"], "test_adapter")
        self.assertIn("received_at_source", e["provenance"])

    def test_trusted_pass_but_domain_mismatch(self):
        L = {"services": [svc()]}
        ingest(msg(frm="x@evil.example", ar=f"{TRUSTED_ID}; dmarc=pass header.from=evil.example"), L, evidence=EV)
        self.assertEqual(L["services"][0]["emails"][0]["attribution"], "unverified")


class TestVerdictFailClosed(unittest.TestCase):
    def test_unchecked_promo_cannot_confirm_failure(self):
        v = common.unsub_verdict(svc(emails=[mail(attribution="unchecked")]))
        self.assertEqual((v["status"], v["after"]), ("pending", None))

    def test_domain_only_promo_cannot_confirm_failure(self):
        v = common.unsub_verdict(svc(emails=[mail(attribution="domain_only")]))
        self.assertEqual(v["status"], "pending")

    def test_trusted_promo_after_unsub_detected(self):  # 正当な認証済み販促メールが停止後に届いた
        v = common.unsub_verdict(svc(emails=[mail(attribution="domain_only"), mail("2026-10-11T16:00:00+09:00")]))
        self.assertEqual((v["status"], v["after"]), ("ineffective", 1))
        self.assertIn("除外", v["reason"])

    def test_trusted_via_ingest_end_to_end(self):
        L = {"services": [svc()]}
        ingest(msg(ar=f"{TRUSTED_ID}; dkim=pass header.d=shop.example"), L, evidence=EV)
        self.assertEqual(common.unsub_verdict(L["services"][0])["status"], "ineffective")

    def test_unsub_notice_tz_missing_unobserved(self):
        self.assertEqual(common.unsub_verdict(svc(emails=[mail(typ="transactional", subject="配信停止を受け付けました")],
                                                  evidence_policy=None))["status"], "effective")
        for bad in ("2026-10-11T15:00:00", None, "Sun, 11 Oct 2026 06:00:00 -0000"):
            with self.subTest(bad=bad):
                self.assertEqual(common.unsub_verdict(svc(emails=[mail(bad)]))["status"], "pending")
        self.assertEqual(common.unsub_verdict(svc(observed_until=None))["status"], "pending")
        self.assertEqual(common.unsub_verdict(svc(unsubscribed_at=None))["status"], "unobserved")

    def test_unclassified_after_unsub_is_pending(self):
        v = common.unsub_verdict(svc(emails=[mail(typ="other")]))
        self.assertEqual(v["status"], "pending")

    def test_zero_wording_is_not_permanent_success(self):
        v = common.unsub_verdict(svc())
        self.assertEqual(v["status"], "effective")
        self.assertIn("検出せず", v["reason"])
        m = report.metrics(svc())
        self.assertIn("検出せず", m["verdict"])
        self.assertNotIn("効いた", m["verdict"])

    def test_strict_needs_receiver_checks(self):
        s = svc(evidence_policy="strict")
        self.assertEqual(common.unsub_verdict(s)["status"], "pending")
        s["receiver_checks"] = [{"at": "2026-10-11T13:59:00+09:00", "kind": "canary", "ok": True}]
        self.assertEqual(common.unsub_verdict(s)["status"], "pending")  # 観測期限後の確認が無い
        s["receiver_checks"].append({"at": "2026-10-12T13:00:05+09:00", "kind": "canary", "ok": True})
        self.assertEqual(common.unsub_verdict(s)["status"], "effective")
        s["receiver_checks"].append({"at": "2026-10-12T13:00:06+09:00", "kind": "api", "ok": False})
        self.assertEqual(common.unsub_verdict(s)["status"], "pending")

    def test_ingest_sets_strict_policy(self):
        L = {"services": [svc()]}
        ingest(msg(subject="配信停止を受け付けました"), L)
        s = L["services"][0]
        self.assertEqual(s["evidence_policy"], "strict")
        self.assertEqual(common.unsub_verdict(s)["status"], "pending")

    def test_report_shows_attribution_breakdown(self):
        s = svc(emails=[mail(), mail("2026-10-11T16:00:00+09:00", attribution="domain_only")])
        md = report.render_md(s, report.metrics(s), False, "G")
        self.assertIn("認証済み1", md)
        self.assertIn("ドメイン一致のみ（認証なし）1", md)
        self.assertIn("Authentication-Results", md)


class TestReadmeLimits(unittest.TestCase):
    def test_readme_states_header_interpretation_only(self):
        with open(os.path.join(ROOT, "README.md"), encoding="utf-8") as f:
            r = f.read()
        self.assertIn("Authentication-Results", r)
        self.assertIn("ヘッダの解釈だけ", r)
        self.assertIn("実際の受信メールでは未検証", r)


if __name__ == "__main__":
    unittest.main()
