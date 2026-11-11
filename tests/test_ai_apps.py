"""Offline guards for curated research: schema, evidence and both site surfaces."""
import json
from datetime import date
from pathlib import Path
from urllib.parse import urlsplit

ROOT = Path(__file__).resolve().parents[1]


def research():
    return json.loads((ROOT / 'data/ai_apps.json').read_text(encoding='utf-8'))


def test_ai_apps_schema_and_sources():
    data = research()
    assert data['schema_version'] == 1
    assert data['timezone'] == 'Asia/Tokyo'
    as_of = date.fromisoformat(data['as_of'])
    assert data['summary'] and data['methodology'] and data['limitations']
    assert '不构成投资建议' in data['disclaimer']
    sources = {s['id']: s for s in data['sources']}
    assert len(sources) == len(data['sources'])
    for s in sources.values():
        assert urlsplit(s['url']).scheme == 'https'
        assert urlsplit(s['url']).netloc
        assert s['title'] and s['kind']
        assert date.fromisoformat(s['published_at']) <= as_of
        assert date.fromisoformat(s['accessed_at']) <= as_of
    for row in [data['policy'], *data['segments'], *data['companies']]:
        assert row['source_ids']
        assert set(row['source_ids']) <= sources.keys()


def test_ai_apps_ranking_and_company_disclosures():
    data = research()
    assert [s['rank'] for s in data['segments']] == list(range(1, len(data['segments']) + 1))
    ids = {s['id'] for s in data['segments']}
    assert len(ids) == len(data['segments'])
    assert len({c['ticker'] for c in data['companies']}) == len(data['companies'])
    assert {c['market'] for c in data['companies']} == {'A股', '美股', '港股'}
    assert {c['tier'] for c in data['companies']} == {'领先', '观察', '高风险'}
    for s in data['segments']:
        assert all(s[k] for k in ['name', 'monetization', 'rationale', 'monitor', 'risks'])
    for c in data['companies']:
        assert c['segment_id'] in ids
        assert all(c[k] for k in ['name', 'ticker', 'thesis', 'evidence', 'evidence_kind',
                                  'evidence_period', 'ai_revenue', 'risks', 'next_check', 'policy_context'])
    assert '无数据' in next(c for c in data['companies'] if c['ticker'] == 'MSFT')['ai_revenue']
    assert '目标' in next(c for c in data['companies'] if c['ticker'] == 'NOW')['evidence']


def test_ai_apps_snapshot_mirror():
    assert research() == json.loads((ROOT / 'docs/api/ai_apps.json').read_text())
    assert (ROOT / 'static/ai_apps_view.js').read_bytes() == (ROOT / 'docs/static/ai_apps_view.js').read_bytes()
    for path in ['templates/index.html', 'docs/index.html']:
        html = (ROOT / path).read_text()
        assert 'data-view="ai"' in html and 'id="view-ai"' in html
        assert 'data-view="cnidx"' in html
        assert 'ai_apps_view.js?v=' in html
        assert html.index('ai_apps_view.js?v=') < html.index('/app.js?v=')
    js = (ROOT / 'static/app.js').read_text()
    assert "'/api/ai-apps': './api/ai_apps.json'" in js
    assert 'name === "ai" && window.AIAppsView' in js


def test_ai_apps_endpoint_reads_local_snapshot_without_refresh(monkeypatch):
    import app as web

    def no_network(*args, **kwargs):
        raise AssertionError('Research must not fetch or refresh market data')

    for name in ['get_quotes', 'build_full_analysis', 'compute_futures_movers', 'warmup']:
        monkeypatch.setattr(web, name, no_network)
    monkeypatch.setattr(web, '_spawn_bg_refresh', no_network)
    with web.app.test_client() as client:
        response = client.get('/api/ai-apps')
        assert response.status_code == 200
        assert response.is_json and response.get_json() == research()
        assert 'no-store' in response.headers['Cache-Control']
        html = client.get('/').get_data(as_text=True)
        assert 'data-view="ai"' in html and '20261111ng3' in html


def test_export_html_only_copies_curated_data(tmp_path, monkeypatch):
    import export_static_pages as export
    monkeypatch.setattr(export, 'DOCS', tmp_path)
    monkeypatch.setattr(export, 'API', tmp_path / 'api')
    # dump() normally logs a ROOT-relative path; use a sandbox writer here.
    def local_dump(path, obj):
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(obj, ensure_ascii=False))
    monkeypatch.setattr(export, 'dump', local_dump)
    export.export_html()
    assert json.loads((tmp_path / 'api/ai_apps.json').read_text()) == research()
    assert (tmp_path / 'static/ai_apps_view.js').exists()


def test_market_cap_groups_and_sourced_bands():
    data = research()
    sources = {s['id']: s for s in data['sources']}
    assert all(data['cap_methodology'].values())
    assert '未逐一复核' in data['cap_methodology']['group_note']
    small = [c for c in data['companies'] if c['cap_group'] == '中小盘']
    assert len(small) == 12
    assert sum(c['market'] == '美股' for c in small) == 6
    assert sum(c['market'] != '美股' for c in small) == 6
    for c in data['companies']:
        assert c['cap_group'] in {'大盘', '中小盘'}
        assert c['cap_band'] in {'小盘', '中盘', '大盘', '无数据'}
        assert date.fromisoformat(c['cap_as_of']) <= date.fromisoformat(data['as_of'])
        assert c['cap_basis']
        if c['cap_group'] == '中小盘':
            assert c['cap_band'] in {'小盘', '中盘'}
            assert c['cap_source_ids']
            for sid in c['cap_source_ids']:
                assert sources[sid]['url'].startswith('https://')
                assert sources[sid]['date_type'] == 'snapshot'
        else:
            assert c['cap_band'] == '无数据'  # Legacy grouping is not a certified market-cap size.
            assert '未复核' in c['cap_basis']
    for path in ['templates/index.html', 'docs/index.html']:
        html = (ROOT / path).read_text()
        assert 'id="ai-cap"' in html
        assert '<option value="中小盘">中小盘</option>' in html
