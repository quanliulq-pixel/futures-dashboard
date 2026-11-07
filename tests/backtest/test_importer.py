"""SYNTHETIC TEST ONLY: tiny vendor-shaped ZIP built in memory; no licensed rows."""
import csv
import hashlib
import io
import json
import zipfile
import pytest
from backtest.engine import Config, DataError, load_dataset, run
from backtest.import_sample import convert


def sample_zip(path, bad_checksum=False):
    # Entirely invented quote, TEST ONLY; not copied from vendor sample.
    b = io.StringIO()
    names = ["underlying", "type", "underlying_close", "quote_date", "contract", "strike", "expiration", "style", "settlement_time", "bid", "ask", "bid_size", "ask_size", "quote_time"]
    w = csv.DictWriter(b, fieldnames=names)
    w.writeheader()
    w.writerow(dict(zip(names, ["SPY", "put", "100", "2022-07-01", "SPY220801P00095000", "95", "2022-08-01", "A", "PM", "2", "2.2", "10", "10", ""])))
    text = b.getvalue().encode()
    with zipfile.ZipFile(path, 'w') as z:
        z.writestr('day_by_date/2022-07-01_options.csv', text)
        z.writestr('day_by_date/manifest.json', json.dumps({"files": [{"name": "2022-07-01_options.csv", "sha256": 'bad' if bad_checksum else hashlib.sha256(text).hexdigest()}], "trading_days_missing": []}))


def test_adapter_requires_ack_and_rejects_rewrite(tmp_path, monkeypatch):
    z = tmp_path / 'test-only.zip'
    sample_zip(z)
    monkeypatch.setattr("backtest.import_sample.EXPECTED_ZIP_SHA256", hashlib.sha256(z.read_bytes()).hexdigest())
    with pytest.raises(DataError, match="acknowledge"):
        convert(z, tmp_path / 'data', '2022-07-01', '2022-07-29')
    convert(z, tmp_path / 'data', '2022-07-01', '2022-07-29', acknowledge=True)
    cfg = Config(mode='OBSERVED_EOD_REFERENCE')
    d = load_dataset(tmp_path / 'data', cfg)
    # Test fixture masquerades as vendor shape: overwrite provenance for test result
    # classifier immediately so no synthetic dataset is reported as historical evidence.
    d['manifest']['dataset_kind'] = 'SYNTHETIC_TEST_ONLY'
    r = run(d, cfg)
    assert r['dataset_kind'] == 'SYNTHETIC_TEST_ONLY'
    assert d['quotes'][0]['observed_at'] == '' and d['sessions'][0]['decision_at'] == ''
    with pytest.raises(DataError):
        load_dataset(tmp_path / 'data', Config())
    with pytest.raises(DataError, match="overwrite"):
        convert(z, tmp_path / 'data', '2022-07-01', '2022-07-29', acknowledge=True)


def test_adapter_rejects_vendor_checksum(tmp_path, monkeypatch):
    z = tmp_path / 'test-only.zip'
    sample_zip(z, bad_checksum=True)
    monkeypatch.setattr("backtest.import_sample.EXPECTED_ZIP_SHA256", hashlib.sha256(z.read_bytes()).hexdigest())
    with pytest.raises(DataError, match="checksum"):
        convert(z, tmp_path / 'data', '2022-07-01', '2022-07-29', acknowledge=True)


def test_pinned_zip_sha_checked_before_parse(tmp_path):
    z = tmp_path / 'wrong.zip'
    z.write_bytes(b'SYNTHETIC_CORRUPT_TEST_ONLY')
    with pytest.raises(DataError, match="ZIP SHA256 mismatch"):
        convert(z, tmp_path / 'data', '2022-07-01', '2022-07-29', acknowledge=True)
