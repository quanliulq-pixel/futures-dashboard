# %% [markdown]
# Offline CSP research — clean Colab bootstrap included.
# Run cells in order. No backtest package needs to be preinstalled.
# SOURCE_REF selects public source; commit is recorded. No secrets or paid service.
# Main must contain this patch; before merge use a published branch/commit containing it.
# Historical observations are NOT actual fills. USER_ACK gates DATA download/use.
# Synthetic tests are TEST ONLY, never historical earnings evidence.

# %%
from pathlib import Path
import hashlib
import csv
import importlib
import json
import os
import subprocess
import sys
import urllib.request

SOURCE_URL = 'https://github.com/quanliulq-pixel/futures-dashboard.git'
SAMPLE_URL = 'https://historicaldata.net/file/options_sample_2022H2.zip'
SAMPLE_SHA256 = '8517b069f4d41e44f950dc79a3cdab694359b0fae91664190011e39dd8e48b76'


def file_sha256(path):
    h = hashlib.sha256()
    with Path(path).open('rb') as f:
        for b in iter(lambda: f.read(1024 * 1024), b''):
            h.update(b)
    return h.hexdigest()


def ensure_source(directory, source_ref='main'):
    """Only a NEW checkout is changed. Never reset/checkout an existing directory."""
    directory = Path(directory).resolve()
    if not source_ref or source_ref.startswith('-'):
        raise RuntimeError('SOURCE_REF must be a branch, tag or commit, not an option')
    def git(*args):
        return subprocess.run(['git', '-C', str(directory), *args], check=True,
                              capture_output=True, text=True).stdout.strip()
    if directory.exists():
        if not (directory / '.git').exists():
            raise RuntimeError(f'{directory} exists but is not a checkout; choose another SOURCE_DIR. No files changed.')
        origin = git('remote', 'get-url', 'origin').rstrip('/')
        if origin not in (SOURCE_URL, SOURCE_URL.removesuffix('.git')):
            raise RuntimeError('Existing checkout has unexpected origin; choose a fresh SOURCE_DIR. No files changed.')
        if git('status', '--porcelain'):
            raise RuntimeError('Existing checkout is dirty; preserve your work and choose a fresh SOURCE_DIR.')
        head = git('rev-parse', 'HEAD')
        requested = git('rev-parse', '--verify', source_ref + '^{commit}')
        if head != requested:
            raise RuntimeError('Existing checkout differs from SOURCE_REF; choose fresh SOURCE_DIR. No reset performed.')
    else:
        directory.parent.mkdir(parents=True, exist_ok=True)
        subprocess.run(['git', 'clone', '--no-checkout', SOURCE_URL, str(directory)], check=True)
        # Safe only in the directory created immediately above, not an existing checkout.
        git('checkout', '--detach', source_ref)
        head = git('rev-parse', 'HEAD')
    if not (directory / 'backtest' / 'engine.py').is_file():
        raise RuntimeError('Selected source lacks backtest engine. Merge patch or select published SOURCE_REF containing it; no data downloaded.')
    return {'url': SOURCE_URL, 'requested_ref': source_ref, 'resolved_commit': head, 'directory': str(directory)}


def ensure_sample(path):
    """Pinned digest, including existing file. Download atomically; never delete mismatches."""
    path = Path(path)
    if path.exists():
        if file_sha256(path) != SAMPLE_SHA256:
            raise RuntimeError(f'SHA256 mismatch for existing {path}. Move/quarantine it manually, then retry; file left untouched.')
        return path
    path.parent.mkdir(parents=True, exist_ok=True)
    part = path.with_name(path.name + '.part')
    if part.exists():
        raise RuntimeError(f'{part} already exists. Inspect/move it manually before retry; no overwrite performed.')
    # Exclusive creation also guards against a concurrent downloader.
    with part.open('xb') as out:
        with urllib.request.urlopen(SAMPLE_URL, timeout=60) as response:
            for b in iter(lambda: response.read(1024 * 1024), b''):
                out.write(b)
    if file_sha256(part) != SAMPLE_SHA256:
        raise RuntimeError(f'Download SHA256 mismatch: {part}. Inspect/quarantine it manually; final ZIP was not created.')
    if path.exists():
        raise RuntimeError('Final ZIP appeared during download; .part left untouched. Inspect manually.')
    part.rename(path)
    return path


# %%
def bootstrap(source_dir, source_ref='main'):
    info = ensure_source(source_dir, source_ref)
    # Avoid mixing a previously imported backtest from another checkout in same kernel.
    for name, module in list(sys.modules.items()):
        if name == 'backtest' or name.startswith('backtest.'):
            origin = getattr(module, '__file__', None)
            if origin and not Path(origin).resolve().is_relative_to(Path(info['directory'])):
                raise RuntimeError('Another backtest package is already loaded. Restart Colab runtime before switching source.')
    sys.path.insert(0, info['directory'])
    os.chdir(info['directory'])
    importlib.invalidate_caches()
    print('SOURCE', json.dumps(info))
    return info


def export_result(result, output):
    """Own simulated ledger/events, not licensed input quote rows."""
    output = Path(output)
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(result, indent=2, allow_nan=False))
    for key in ('ledger', 'events', 'gaps'):
        rows = result[key]
        names = list(dict.fromkeys(k for row in rows for k in row)) or ['session_date']
        path = output.with_name(output.stem + '_' + key + '.csv')
        with path.open('w', newline='') as f:
            writer = csv.DictWriter(f, fieldnames=names)
            writer.writeheader()
            writer.writerows(rows)
    return output


# %%
def main():
    SOURCE_REF = 'main'  # Change to published branch/tag/commit; commit recorded below.
    USER_ACK = False  # Review backtest/README.md, sample license and assumptions, then set True.
    WORK = Path('/content/csp-research' if Path('/content').exists() else '/tmp/csp-research')
    SOURCE_DIR = WORK / 'source'  # Choose NEW directory for another ref; never hard-reset old work.
    WORK.mkdir(parents=True, exist_ok=True)
    source = bootstrap(SOURCE_DIR, SOURCE_REF)  # Works from clean /content before any backtest import.
    (WORK / 'source-provenance.json').write_text(json.dumps(source, indent=2))
    if not USER_ACK:
        raise RuntimeError('Source ready. Review assumptions, then set USER_ACK=True and rerun for data evaluation.')
    ZIP = ensure_sample(WORK / 'options_sample_2022H2.zip')
    from backtest.import_sample import convert
    from backtest.engine import Config, load_dataset, run
    DATA = WORK / 'spy-july-2022'
    if not DATA.exists():
        convert(ZIP, DATA, '2022-07-01', '2022-07-29', acknowledge=True)
    cfg = Config(mode='OBSERVED_EOD_REFERENCE')
    result = run(load_dataset(DATA, cfg), cfg)
    result['source_checkout'] = source  # audit metadata, not a manufactured dataset timestamp
    print(result['status'], result['gaps'])
    print(result['metrics'])  # None on incomplete; no fabricated whole-period return.
    export_result(result, WORK / 'run.json')
    return result


# %%
if __name__ == '__main__':
    main()
