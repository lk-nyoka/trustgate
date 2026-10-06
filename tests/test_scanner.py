from tests.conftest import ATTACK_FEE, ATTACK_UPGRADE, BENIGN, Env
from trust_mw.scanner import scan_page


def test_detects_hidden_json_ld_and_offscreen_css():
    sources = {f.source for f in scan_page(ATTACK_FEE)}
    assert {"json_ld", "css_hidden"} <= sources


def test_detects_display_none_agent_instruction():
    assert any(f.flag == "HIDDEN_PAYMENT_INSTRUCTION" for f in scan_page(ATTACK_UPGRADE))


def test_benign_pages_are_not_flagged():
    benign = [
        BENIGN,
        "<html><body><p>You will pay $180 at checkout. Payment is processed securely.</p></body></html>",
        "<html><body><p>Baggage fee applies for a second bag.</p><div style='display:none'>menu</div></body></html>",
        '<html><head><script type="application/ld+json">{"@type":"Offer","price":"320"}</script></head><body>ok</body></html>',
    ]
    assert [scan_page(h) for h in benign] == [[]] * len(benign)


def test_injection_inside_allowed_merchant_is_tightened_to_approval():
    """In policy and under the threshold, so hard rules say ALLOW, but the page escalates it."""
    e = Env()
    r = e.propose("cpt-jnb-economy-180", url="https://demo-airlines.test/injected-upgrade")
    assert r["decision"] == "APPROVAL_REQUIRED"
    assert "CONTEXT_HIDDEN_PAYMENT_INSTRUCTION" in r["reason_codes"]
    assert e.payments.calls == []


def test_benign_page_does_not_add_friction(env):
    r = env.propose("cpt-jnb-economy-180", url="https://demo-airlines.test/checkout")
    assert r["decision"] == "ALLOW"


def test_off_domain_source_url_is_flagged_and_not_fetched(env):
    r = env.propose("cpt-jnb-economy-180", url="https://evil.test/checkout")
    assert r["decision"] == "APPROVAL_REQUIRED"
    assert "CONTEXT_SOURCE_URL_OFF_DOMAIN" in r["reason_codes"]


def test_attack_scene_surfaces_hidden_instruction_evidence(env):
    """Agent reads the (injected) airline page, then proposes paying a different, unlisted merchant."""
    r = env.propose("activation-fee-3", merchant="merchant_activation_services",
                    url="https://demo-airlines.test/injected-fee")
    assert r["decision"] == "BLOCK"
    assert r["reason_codes"][0] == "MERCHANT_NOT_IN_POLICY"  # primary reason is deterministic
    assert "CONTEXT_HIDDEN_PAYMENT_INSTRUCTION" in r["reason_codes"]
    assert "CONTEXT_PAYEE_DIFFERS_FROM_PAGE" in r["reason_codes"]
    evidence = [e for e in env.svc.get_audit(r["intent_id"]) if e["event"] == "CONTEXT_SCANNED"][0]["data"]
    assert any(f["source"] == "json_ld" for f in evidence)
    assert env.payments.calls == []


def test_payment_to_the_page_merchant_adds_no_payee_flag(env):
    r = env.propose("cpt-jnb-economy-180", url="https://demo-airlines.test/checkout")
    assert "CONTEXT_PAYEE_DIFFERS_FROM_PAGE" not in r["reason_codes"]
