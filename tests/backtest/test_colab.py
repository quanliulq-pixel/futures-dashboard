"""Mocked clean Colab/public git and tiny byte downloads. No network or 238MB repeat."""
import importlib.util
import io
import json
from pathlib import Path
import types
import hashlib
import pytest

P = Path(__file__).resolve().parents[2] / 'examples' / 'colab_backtest.py'
spec = importlib.util.spec_from_file_location('colab_example', P)
m = importlib.util.module_from_spec(spec)
spec.loader.exec_module(m)  # import does NOT clone/download/run a backtest


def test_clean_bootstrap_from_content_like_cwd(tmp_path, monkeypatch):
    content = tmp_path / 'content'
    content.mkdir()
    monkeypatch.chdir(content)
    checkout = content / 'source'
    calls = []
    def fake_run(args, **kwargs):
        calls.append(args)
        if args[1:3] == ['clone', '--no-checkout']:
            checkout.mkdir()
            (checkout / '.git').mkdir()
            (checkout / 'backtest').mkdir()
            (checkout / 'backtest' / 'engine.py').write_text('# SYNTHETIC BOOTSTRAP TEST ONLY')
        out = 'f' * 40 if 'rev-parse' in args else ''
        return types.SimpleNamespace(stdout=out)
    monkeypatch.setattr(m.subprocess, 'run', fake_run)
    old_path = list(m.sys.path)
    # Mocked source contains no package; existing test runner imported one elsewhere.
    # Temporarily remove those module entries without pretending to install another.
    for name in list(m.sys.modules):
        if name == 'backtest' or name.startswith('backtest.'):
            monkeypatch.delitem(m.sys.modules, name)
    info = m.bootstrap(checkout, 'main')
    assert info['resolved_commit'] == 'f' * 40
    assert Path.cwd() == checkout and m.sys.path[0] == str(checkout)
    assert any(x[1:3] == ['clone', '--no-checkout'] for x in calls)
    assert any('checkout' in x and '--detach' in x for x in calls)
    m.sys.path[:] = old_path


def test_existing_checkout_never_checkout_or_reset(tmp_path, monkeypatch):
    src = tmp_path / 'existing'
    (src / '.git').mkdir(parents=True)
    (src / 'backtest').mkdir()
    (src / 'backtest' / 'engine.py').touch()
    calls = []
    def fake(args, **kw):
        calls.append(args)
        out = m.SOURCE_URL if 'get-url' in args else ('abc123' if 'rev-parse' in args else '')
        return types.SimpleNamespace(stdout=out)
    monkeypatch.setattr(m.subprocess, 'run', fake)
    assert m.ensure_source(src)['resolved_commit'] == 'abc123'
    assert not any('checkout' in a or 'reset' in a or 'clone' in a for a in calls)
    (tmp_path / 'notrepo').mkdir()
    with pytest.raises(RuntimeError, match='not a checkout'):
        m.ensure_source(tmp_path / 'notrepo')


def test_atomic_download_and_existing_digest_checked(tmp_path, monkeypatch):
    payload = b'SYNTHETIC TEST ONLY, not options data'
    monkeypatch.setattr(m, 'SAMPLE_SHA256', hashlib.sha256(payload).hexdigest())
    calls = []
    def fake_urlopen(url, **kw):
        calls.append(url)
        return io.BytesIO(payload)
    monkeypatch.setattr(m.urllib.request, 'urlopen', fake_urlopen)
    p = tmp_path / 'sample.zip'
    assert m.ensure_sample(p) == p and p.read_bytes() == payload
    assert not p.with_name('sample.zip.part').exists()
    m.ensure_sample(p)
    assert len(calls) == 1
    p.write_bytes(b'corrupted')
    with pytest.raises(RuntimeError, match='existing'):
        m.ensure_sample(p)
    assert p.read_bytes() == b'corrupted' and len(calls) == 1


def test_corrupt_download_never_renamed_and_no_part_overwrite(tmp_path, monkeypatch):
    p = tmp_path / 'sample.zip'
    monkeypatch.setattr(m.urllib.request, 'urlopen', lambda *a, **k: io.BytesIO(b'bad download'))
    with pytest.raises(RuntimeError, match='Download SHA256 mismatch'):
        m.ensure_sample(p)
    assert not p.exists() and p.with_name('sample.zip.part').read_bytes() == b'bad download'
    with pytest.raises(RuntimeError, match='already exists'):
        m.ensure_sample(p)


def test_notebook_has_bootstrap_before_import_and_cells_compile():
    nb = json.loads(P.with_suffix('.ipynb').read_text())
    text = '\n'.join(c['source'] for c in nb['cells'])
    assert 'SOURCE_REF' in text and 'source_checkout' in text and 'USER_ACK = False' in text
    assert text.index('source = bootstrap(') < text.index('from backtest.import_sample import convert')
    for i, cell in enumerate(nb['cells']):
        if cell['cell_type'] == 'code':
            compile(cell['source'], f'notebook-cell-{i}', 'exec')
