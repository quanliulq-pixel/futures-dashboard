"""Offline guards for presentation-only polish and the Pages mirror."""
from html.parser import HTMLParser
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


class Elements(HTMLParser):
    def __init__(self):
        super().__init__()
        self.elements = []

    def handle_starttag(self, tag, attrs):
        self.elements.append((tag, dict(attrs)))


def test_assets_are_identical_in_pages_mirror():
    for path in (ROOT / "static").iterdir():
        if path.is_file():
            assert path.read_bytes() == (ROOT / "docs/static" / path.name).read_bytes()


def test_pages_template_is_exact_static_transform():
    template = (ROOT / "templates/index.html").read_text()
    expected = template.replace(
        "<head>\n",
        '<head>\n  <base href="/futures-dashboard/" />\n'
        '  <script>window.STATIC_MODE = true;</script>\n', 1,
    ).replace('"/static/', '"./static/').replace(
        "{{ asset_v }}", "20261112idx3"
    ).replace("refreshSec: {{ refresh_sec }}", "refreshSec: 0")
    assert (ROOT / "docs/index.html").read_text() == expected
    assert '{{ asset_v }}' not in expected
    assert './static/cn_index_fund_view.js?v=20261112idx3' in expected


def test_asset_versions_agree():
    for path in ["app.py", "export_static_pages.py", "docs/index.html"]:
        assert '20261112idx3' in (ROOT / path).read_text()


def test_keyboard_scroll_regions_and_protected_tabs():
    html = (ROOT / "templates/index.html").read_text()
    parser = Elements()
    parser.feed(html)
    wrappers = [a for t, a in parser.elements if 'table-wrap' in a.get('class', '').split()]
    assert len(wrappers) == 14
    assert all(a.get('tabindex') == '0' and a.get('aria-label') for a in wrappers)
    views = [a.get('data-view') for t, a in parser.elements if t == 'button' and 'data-view' in a]
    assert views == ['watch', 'extremes', 'supply', 'movers', 'put', 'call', 'cnidx', 'ng', 'ai']
    assert '通缩' not in html
    # No restoration of the removed domestic market toggles in option screens.
    for side in ('put', 'call'):
        section = html.split(f'id="view-{side}"', 1)[1].split('</section>', 1)[0]
        assert 'data-market=' not in section


def test_direction_colors_and_accessibility_guards():
    css = (ROOT / "static/style.css").read_text()
    assert '--up: #ef5350;' in css and '--down: #26a69a;' in css
    assert 'prefers-reduced-motion: reduce' in css
    assert ':focus-visible' in css
    assert 'tabular-nums' in css
    assert 'overscroll-behavior-x: contain' in css


def test_badge_attribute_uses_escaped_existing_bias():
    js = (ROOT / 'static/app.js').read_text()
    assert 'data-option-bias="${esc(bias)}">${esc(bias)}</strong>' in js
    assert '不能下单' in (ROOT / 'static/cn_option_candidates_view.js').read_text()
    assert '无数据' in (ROOT / 'static/fund_score_view.js').read_text()


def test_method_disclosures_default_closed_and_keep_content_ids():
    parser = Elements()
    html = (ROOT / 'templates/index.html').read_text()
    parser.feed(html)
    disclosures = [a for t, a in parser.elements if t == 'details' and a.get('class') == 'method-details']
    assert len(disclosures) == 2 and all('open' not in a for a in disclosures)
    assert 'id="put-method"' in html and 'id="call-method"' in html

def test_cnidx_tab_drops_option_chain_ui_but_keeps_advice():
    html = (ROOT / "templates/index.html").read_text()
    section = html.split('id="view-cnidx"', 1)[1].split('</section>', 1)[0]
    assert '近月期权链' not in section
    assert '资金进出' in section and 'id="cnidx-flow-panel"' in section
    js = (ROOT / "static/app.js").read_text()
    assert '_cnOptSnippet' not in js
    assert 'cnidx-opt-table' not in js
    assert '/api/cn-index-options' not in js
    assert 'option_bias_cn' in js
    assert 'data-option-bias="${esc(bias)}">${esc(bias)}</strong>' in js
    app = (ROOT / "app.py").read_text(encoding="utf-8")
    assert '@app.route("/api/cn-index-options")' not in app
    assert '@app.route("/api/cn-index-advice")' in app
    assert (ROOT / "cffex_options.py").is_file()
