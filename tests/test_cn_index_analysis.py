# -*- coding: utf-8 -*-
"""Timezone join regression for A-share index analysis seed."""
from __future__ import annotations

import json
from pathlib import Path

import pandas as pd
import pytest

import cn_index_analysis as A

ROOT = Path(__file__).resolve().parents[1]


def test_naive_day_index_strips_tz():
    naive = pd.Series([1.0, 2.0], index=pd.to_datetime(["2026-01-01", "2026-01-02"]))
    aware = pd.Series([3.0, 4.0], index=pd.to_datetime(["2026-01-01", "2026-01-02"], utc=True))
    ny = pd.Series(
        [5.0, 6.0],
        index=pd.to_datetime(["2026-01-01", "2026-01-02"]).tz_localize("America/New_York"),
    )
    a = A._naive_day_index(naive)
    b = A._naive_day_index(aware)
    c = A._naive_day_index(ny)
    assert a.index.tz is None and b.index.tz is None and c.index.tz is None
    assert list(a.index) == list(b.index) == list(c.index)


def test_download_close_joins_naive_and_tz_aware(monkeypatch):
    """This is the join that used to raise and wipe docs/api/cn_indices.json."""
    idx = pd.date_range("2026-01-01", periods=60, freq="D")
    naive = pd.Series(4000.0 + pd.RangeIndex(60).astype(float), index=idx)
    aware = pd.Series(30.0 + pd.RangeIndex(60).astype(float), index=idx.tz_localize("America/New_York"))

    monkeypatch.setattr(A, "_series_from_ak", lambda symbol: naive)
    monkeypatch.setenv("SKIP_AKSHARE", "0")
    monkeypatch.delenv("RENDER", raising=False)

    def fake_download(*_a, **_k):
        close = pd.concat({"FXI": aware, "KWEB": aware}, axis=1)
        close.columns = pd.MultiIndex.from_product([close.columns, ["Close"]])
        # columns are (FXI, Close) after from_product? Wait we need ticker on level 0
        df = pd.DataFrame({("FXI", "Close"): aware, ("KWEB", "Close"): aware})
        df.columns = pd.MultiIndex.from_tuples(df.columns)
        return df

    monkeypatch.setattr("net_limits.yf_download", fake_download)

    tickers = list(A.AK_INDEX_MAP.keys()) + list(A.SATELLITES.keys())
    # The previous implementation raised TypeError here.
    df = A._download_close(tickers)
    assert not df.empty
    assert getattr(df.index, "tz", None) is None
    assert "000300.SS" in df.columns
    assert "FXI" in df.columns and "KWEB" in df.columns
    rows = [A._row_from_series("000300.SS", df["000300.SS"], A.CN_INDICES["000300.SS"])]
    assert rows[0] is not None and rows[0]["id"] == "csi300"


def test_mixed_tz_dataframe_would_have_raised_without_helper():
    naive = pd.Series([1.0, 2.0], index=pd.to_datetime(["2026-01-01", "2026-01-02"]))
    aware = pd.Series([3.0, 4.0], index=pd.to_datetime(["2026-01-01", "2026-01-02"], utc=True))
    with pytest.raises(TypeError, match="tz-naive with tz-aware"):
        pd.DataFrame({"a": naive, "b": aware}).sort_index()
    fixed = pd.DataFrame({"a": A._naive_day_index(naive), "b": A._naive_day_index(aware)}).sort_index()
    assert len(fixed) == 2 and fixed.index.tz is None


def test_committed_cn_indices_seed_has_real_indices():
    path = ROOT / "docs" / "api" / "cn_indices.json"
    data = json.loads(path.read_text(encoding="utf-8"))
    err = str(data.get("error") or "")
    assert "tz-naive" not in err and "tz-aware" not in err
    idxs = data.get("indices") or []
    assert isinstance(idxs, list) and len(idxs) >= 3, f"seed indices empty or short: {len(idxs)} error={err!r}"
    ids = {row.get("id") for row in idxs if isinstance(row, dict)}
    assert {"csi300", "sse50", "csi1000"} <= ids
