"""Glass is decorative progressive enhancement: assets are served, only marked surfaces use it,
and no form, approval or payment control is ever wrapped."""
import re

from fastapi.testclient import TestClient

from trust_mw.api import create_app
from trust_mw.workspaces import DemoWorkspaces, make_review_workspace_factory


def client():
    ws = DemoWorkspaces(make_review_workspace_factory())
    app = create_app(None, {"demo": ("user_1", "pw")}, csrf_secret="s", admin_key="a", workspaces=ws)
    return TestClient(app, follow_redirects=True)


def test_vendored_assets_are_served_without_auth():
    c = client()
    for f in ("glass-init.js", "container.js", "html2canvas.min.js", "glass.css",
              "LICENSE-liquid-glass-js.txt", "LICENSE-html2canvas.txt"):
        assert c.get(f"/static/glass/{f}").status_code == 200, f


def test_login_page_marks_only_the_decorative_panel():
    html = client().get("/login").text
    assert "/static/glass/glass-init.js" in html
    marked = re.findall(r'<[^>]*data-glass=[^>]*>', html)
    assert len(marked) == 1 and "flow-visual" in marked[0]
    assert not re.search(r'<(form|input|button)[^>]*data-glass', html)


def test_console_marks_only_the_environment_box_and_keeps_labels_as_real_text():
    c = client()
    c.post("/login", data={"username": "demo", "password": "pw"})
    html = c.get("/console").text
    marked = re.findall(r'<[^>]*data-glass=[^>]*>', html)
    assert len(marked) == 1 and "paypal-mode" in marked[0]
    assert "Simulated payments" in html  # label is DOM text, not drawn into a canvas


def test_approval_detail_and_audit_pages_never_use_glass():
    c = client()
    c.post("/login", data={"username": "demo", "password": "pw"})
    held = next(i for i in c.get("/api/intents").json() if i["state"] == "HELD_FOR_APPROVAL")["intent_id"]
    for path in (f"/intents/{held}", f"/intents/{held}/detail", "/intents"):
        r = c.get(path)
        assert r.status_code == 200, path
        assert not re.search(r'<(form|input|button)[^>]*data-glass', r.text), path
    assert 'data-glass' not in c.get("/audit").text.split('<aside')[-1].split('</aside>')[-1]


def test_kill_switch_and_accessibility_guards_exist():
    js = client().get("/static/glass/glass-init.js").text
    for needle in ("glass=off", "prefers-reduced-motion", "prefers-reduced-transparency", "webglOk"):
        assert needle in js


def test_policy_panel_formats_every_amount_with_cents():
    c = client()
    c.post("/login", data={"username": "demo", "password": "pw"})
    html = c.get("/console").text
    for label, amount in (("Auto-approve up to", "$250.00"), ("Max single purchase", "$500.00"),
                          ("Total budget", "$1,000.00")):
        assert re.search(rf"<dt>{label}</dt><dd>\{amount}</dd>", html), label
