"""SYNTHETIC TEST ONLY. No row, mark, or return below is historical evidence."""
import copy
import json
from dataclasses import replace
from datetime import date, timedelta
import pytest
from backtest.engine import Config, DataError, load_dataset, run, validate
from backtest.import_sample import sha, write_csv


def fixture_data(mode="STRICT", n=3, expiry="2024-02-02", strike=95):
    dates = [(date(2024, 1, 2) + timedelta(days=i)).isoformat() for i in range(n)]
    contracts = [{"contract_id": "TEST_ONLY_PUT", "symbol": "SPY", "right": "put", "strike": strike,
                  "expiry": expiry, "expiry_at": expiry + "T16:00:00-05:00", "currency": "USD", "multiplier": 100,
                  "settlement": "physical", "exercise_style": "American", "deliverable": "100_SHARES",
                  "metadata_basis": "VERIFIED", "metadata_source": "SYNTHETIC TEST ONLY, no historical data"}]
    sessions = [{"session_date": d, "decision_at": d + "T16:15:00-05:00"} for d in dates]
    quotes = [{"session_date": d, "contract_id": "TEST_ONLY_PUT", "observed_at": d + "T16:00:00-05:00",
               "available_at": d + "T16:05:00-05:00", "bid": 2, "ask": 2.2, "bid_size": 10, "ask_size": 10} for d in dates if d <= expiry]
    und = [{"session_date": d, "symbol": "SPY", "price": 100, "bid": 99.99, "ask": 100.01,
            "observed_at": d + "T16:00:00-05:00", "available_at": d + "T16:05:00-05:00", "expiry_settlement_eligible": True, "settlement_source": "SYNTHETIC_TEST_ONLY"} for d in dates]
    return {"manifest": {"source": "SYNTHETIC_TEST_ONLY", "dataset_kind": "SYNTHETIC_TEST_ONLY",
                         "provenance": "UNIT TEST ONLY; invented, NOT earnings evidence", "expected_sessions": dates,
                         "eod_reference_assumptions_acknowledged": mode != "STRICT"},
            "contracts": contracts, "sessions": sessions, "quotes": quotes, "underlying": und}


def test_repeatable_fingerprint_and_parameter_change():
    d = fixture_data()
    a = run(d)
    assert a == run(copy.deepcopy(d))
    assert a["run_fingerprint"] != run(d, Config(option_slippage=0.02))["run_fingerprint"]
    assert a["metrics"] is None and a["status"] == "INCOMPLETE"
    assert a["dataset_kind"] == "SYNTHETIC_TEST_ONLY"


def test_next_session_fill_and_liability_collateral_not_double_counted():
    r = run(fixture_data())
    assert r["events"][0]["event"] == "QUEUE_OPEN"
    entry = next(e for e in r["events"] if e["event"] == "SELL_TO_OPEN")
    assert entry["session_date"] == "2024-01-03" and entry["fill"] == 1.99
    row = r["ledger"][1]
    assert row["cash"] == pytest.approx(100198.35)
    assert row["collateral"] == 9500 and row["free_cash"] == pytest.approx(90698.35)
    assert row["option_liability"] == pytest.approx(220)
    assert row["equity"] == pytest.approx(99978.35)


def test_close_decision_next_day_not_same_day():
    d = fixture_data()
    # Lower min DTE required to demonstrate close at7 after entry; test config not default.
    cfg = Config(target_dte=8, min_dte=8, max_dte=10, close_dte=7, end_policy="CLOSE_IF_QUOTED")
    d = fixture_data(n=4, expiry="2024-01-10")
    r = run(d, cfg)
    close = next(e for e in r["events"] if e["event"] == "BUY_TO_CLOSE")
    assert close["session_date"] == "2024-01-04"
    assert close["fill"] == pytest.approx(2.21)


def test_insufficient_cash_excludes_without_using_premium():
    r = run(fixture_data(), Config(capital=9499.99))
    assert not any(e["event"] == "SELL_TO_OPEN" for e in r["events"])
    assert r["metrics"]["ending_equity"] == 9499.99


def test_cash_check_again_at_execution():
    d = fixture_data()
    # Price changed cannot retroactively select a different strike on fill day.
    d["underlying"][1]["price"] = 70
    r = run(d)
    assert next(e for e in r["events"] if e["event"] == "SELL_TO_OPEN")["contract_id"] == "TEST_ONLY_PUT"


def test_unknown_timestamps_strict_reject_and_reference_label():
    d = fixture_data(mode="OBSERVED_EOD_REFERENCE")
    for key in ("quotes", "underlying"):
        for r in d[key]:
            r["observed_at"] = r["available_at"] = ""
    for s in d["sessions"]:
        s["decision_at"] = ""
    d["contracts"][0]["metadata_basis"] = "USER_ASSUMPTION"
    d["contracts"][0]["expiry_at"] = ""
    with pytest.raises(DataError):
        run(d)
    r = run(d, Config(mode="OBSERVED_EOD_REFERENCE"))
    assert r["mode"] == "OBSERVED_EOD_REFERENCE" and any("clocks unknown" in x for x in r["disclosures"])


@pytest.mark.parametrize("field,value", [("bid", "nan"), ("ask", "inf"), ("bid", -1), ("ask", 0), ("bid_size", -1)])
def test_bad_numeric_quotes(field, value):
    d = fixture_data()
    d["quotes"][0][field] = value
    with pytest.raises(DataError):
        run(d)


def test_duplicates_and_crossed():
    for conflict in (False, True):
        d = fixture_data()
        q = dict(d["quotes"][0])
        if conflict:
            q["bid"] = 1
        d["quotes"].append(q)
        with pytest.raises(DataError, match="duplicate"):
            run(d)
    d = fixture_data()
    d["quotes"][0]["ask"] = 1
    with pytest.raises(DataError, match="crossed"):
        run(d)


def test_calendar_gap_not_inferred_from_available_quotes():
    d = fixture_data()
    d["sessions"].pop(1)
    with pytest.raises(DataError, match="calendar gap"):
        run(d)


def test_future_observation_rejected_but_late_availability_not_looked_ahead():
    d = fixture_data()
    d["quotes"][0]["observed_at"] = "2024-01-03T16:00:00-05:00"
    d["quotes"][0]["available_at"] = "2024-01-03T16:05:00-05:00"
    with pytest.raises(DataError, match="future"):
        run(d)
    d = fixture_data()
    d["quotes"][0]["available_at"] = "2024-01-03T17:00:00-05:00"
    r = run(d)
    assert next(e for e in r["events"] if e["event"] == "QUEUE_OPEN")["session_date"] == "2024-01-03"
    assert next(e for e in r["events"] if e["event"] == "SELL_TO_OPEN")["session_date"] == "2024-01-04"


def test_no_retroactive_fill_or_old_mark():
    d = fixture_data()
    d["quotes"][1]["observed_at"] = "2024-01-02T16:00:00-05:00"
    d["quotes"][1]["available_at"] = "2024-01-03T16:00:00-05:00"
    r = run(d)
    assert not any(e["event"] == "SELL_TO_OPEN" for e in r["events"])


def test_mark_gap_stops_not_forward_filled():
    d = fixture_data(n=4)
    d["quotes"] = [q for q in d["quotes"] if q["session_date"] != "2024-01-04"]
    r = run(d)
    assert r["status"] == "INCOMPLETE" and r["metrics"] is None
    assert len(r["ledger"]) == 2 and r["gaps"][0]["reason"] == "MISSING_DAILY_OPTION_MARK"


def test_missing_underlying_while_holding():
    d = fixture_data()
    d["underlying"].pop()
    r = run(d)
    assert r["gaps"][0]["reason"] == "MISSING_OR_NOT_YET_AVAILABLE_UNDERLYING"


def test_missing_option_on_open_does_not_fallback_or_fill_later():
    d = fixture_data(n=4)
    d["quotes"] = [q for q in d["quotes"] if q["session_date"] != "2024-01-03"]
    r = run(d)
    opens = [e for e in r["events"] if e["event"] == "SELL_TO_OPEN"]
    assert opens[0]["session_date"] == "2024-01-05"
    assert any(e["event"] == "ORDER_CANCELLED" for e in r["events"])


def test_end_policy_reports_position_not_fake_completion():
    a = run(fixture_data())
    assert a["status"] == "INCOMPLETE" and a["metrics"] is None
    b = run(fixture_data(), Config(end_policy="CLOSE_IF_QUOTED"))
    assert b["status"] == "COMPLETE" and b["open_position"] is None
    assert b["metrics"]["ending_equity"] == pytest.approx(99976.70)
    assert b["events"][-1]["event"] == "FORCED_END_CLOSE"


def test_assignment_and_next_session_share_liquidation():
    cfg = Config(min_dte=1, target_dte=2, max_dte=4, close_dte=0)
    d = fixture_data(n=4, expiry="2024-01-04")
    d["underlying"][2].update(price=90, bid=89.99, ask=90.01)
    d["underlying"][3].update(price=92, bid=91.99, ask=92.01)
    r = run(d, cfg)
    assert r["status"] == "COMPLETE"
    assigned = r["ledger"][2]
    assert assigned["shares"] == 100 and assigned["collateral"] == 0 and assigned["option_liability"] == 0
    assert assigned["cash"] == pytest.approx(90698.35)
    assert assigned["equity"] == pytest.approx(99698.35)
    assert r["ledger"][3]["equity"] == pytest.approx(99896.35)
    assert [e["event"] for e in r["events"]].count("ASSIGN_PUT") == 1


def test_no_premature_assignment_and_post_expiry_quotes_rejected():
    d = fixture_data(n=4, expiry="2024-01-04")
    cfg = Config(min_dte=1, target_dte=2, max_dte=4, close_dte=0)
    d["underlying"][1]["price"] = 80
    r = run(d, cfg)
    assert r["ledger"][1]["shares"] == 0
    d["quotes"].append(dict(d["quotes"][-1], session_date="2024-01-05", observed_at="2024-01-05T16:00:00-05:00", available_at="2024-01-05T16:05:00-05:00"))
    with pytest.raises(DataError, match="expiry"):
        run(d, cfg)


def test_worthless_expiry_no_shares_and_missing_settlement_stops():
    cfg = Config(min_dte=1, target_dte=2, max_dte=4, close_dte=0)
    d = fixture_data(n=3, expiry="2024-01-04")
    r = run(d, cfg)
    assert r["status"] == "COMPLETE" and r["shares"] == 0
    assert r["events"][-1]["event"] == "EXPIRE_WORTHLESS"
    d["underlying"][-1]["expiry_settlement_eligible"] = False
    r = run(d, cfg)
    assert r["status"] == "INCOMPLETE" and "SETTLEMENT" in r["gaps"][0]["reason"]


def test_assignment_at_end_is_incomplete():
    d = fixture_data(n=3, expiry="2024-01-04")
    d["underlying"][-1]["price"] = 80
    r = run(d, Config(min_dte=1, target_dte=2, max_dte=4, close_dte=0))
    assert r["shares"] == 100 and r["status"] == "INCOMPLETE" and r["metrics"] is None


def test_strict_cannot_assume_standard_deliverable():
    d = fixture_data()
    d["contracts"][0]["metadata_basis"] = "USER_ASSUMPTION"
    with pytest.raises(DataError, match="metadata"):
        run(d)
    d["contracts"][0]["metadata_basis"] = "VERIFIED"
    d["contracts"][0]["deliverable"] = "UNKNOWN"
    with pytest.raises(DataError, match="deliverable"):
        run(d)


def test_economic_identity_duplicate_rejected():
    d = fixture_data()
    d["contracts"].append(dict(d["contracts"][0], contract_id="ALIAS"))
    with pytest.raises(DataError, match="economic"):
        run(d)


def test_csv_checksum_and_repeatability(tmp_path):
    d = fixture_data()
    for k in ("sessions", "contracts", "quotes", "underlying"):
        write_csv(tmp_path / (k + ".csv"), list(d[k][0]), d[k])
    # CSV boolean convention is lower-case true/false
    p = tmp_path / 'underlying.csv'
    p.write_text(p.read_text().replace('True', 'true'))
    d["manifest"]["file_sha256"] = {p.name: sha(p) for p in tmp_path.glob('*.csv')}
    (tmp_path / 'manifest.json').write_text(json.dumps(d["manifest"]))
    a = load_dataset(tmp_path, Config())
    assert run(a) == run(load_dataset(tmp_path, Config()))
    with (tmp_path / 'quotes.csv').open('a') as f:
        f.write('\n')
    with pytest.raises(DataError, match="checksum"):
        load_dataset(tmp_path, Config())


@pytest.mark.parametrize('cfg', [Config(capital=float('nan')), Config(option_slippage=-1), Config(close_dte=25), Config(contracts=2), Config(min_dte=25.5)])
def test_invalid_config(cfg):
    with pytest.raises(DataError):
        run(fixture_data(), cfg)


def test_unknown_manifest_flags_not_truthy_strings():
    d = fixture_data(mode="OBSERVED_EOD_REFERENCE")
    d["manifest"]["eod_reference_assumptions_acknowledged"] = "false"
    with pytest.raises(DataError, match="acknowledgment"):
        run(d, Config(mode="OBSERVED_EOD_REFERENCE"))
    d = fixture_data()
    d["underlying"][0]["expiry_settlement_eligible"] = "unknown"
    with pytest.raises(DataError, match="true/false"):
        run(d)


def test_available_late_mark_is_gap_not_forward_lookup():
    d = fixture_data()
    d["quotes"][2]["available_at"] = "2024-01-05T16:05:00-05:00"
    r = run(d)
    assert r["status"] == "INCOMPLETE" and r["gaps"][0]["reason"] == "MISSING_DAILY_OPTION_MARK"


def test_contract_mismatch_does_not_use_other_strike():
    d = fixture_data()
    other = dict(d["contracts"][0], contract_id="OTHER_TEST_ONLY", strike=90)
    d["contracts"].append(other)
    # On last day only an entirely different strike is quoted.
    d["quotes"][-1]["contract_id"] = other["contract_id"]
    r = run(d)
    assert r["status"] == "INCOMPLETE" and r["gaps"][0]["contract_id"] == "TEST_ONLY_PUT"


def test_assignment_threshold_not_any_subcent_itm():
    d = fixture_data(n=3, expiry="2024-01-04")
    d["underlying"][-1]["price"] = 94.999
    r = run(d, Config(min_dte=1, target_dte=2, max_dte=4, close_dte=0))
    assert r["status"] == "COMPLETE" and r["shares"] == 0


def test_cli_writes_incomplete_result_and_exit2(tmp_path):
    import subprocess, sys
    d = fixture_data()
    for k in ("sessions", "contracts", "quotes", "underlying"):
        write_csv(tmp_path / (k + ".csv"), list(d[k][0]), d[k])
    p = tmp_path / 'underlying.csv'
    p.write_text(p.read_text().replace('True', 'true'))
    d["manifest"]["file_sha256"] = {p.name: sha(p) for p in tmp_path.glob('*.csv')}
    (tmp_path / 'manifest.json').write_text(json.dumps(d["manifest"]))
    out = tmp_path / 'run.json'
    p = subprocess.run([sys.executable, '-m', 'backtest.cli', '--dataset', str(tmp_path), '--output', str(out)], capture_output=True)
    assert p.returncode == 2 and json.loads(out.read_text())["metrics"] is None
    assert out.with_name('run_ledger.csv').exists()


def test_missing_expiry_session_not_silently_settled_late():
    cfg = Config(min_dte=1, target_dte=2, max_dte=4, close_dte=0)
    d = fixture_data(n=4, expiry="2024-01-04")
    # Supplied calendar itself omits expiry: engine still refuses late settlement.
    for key in ("sessions", "quotes", "underlying"):
        d[key] = [r for r in d[key] if r["session_date"] != "2024-01-04"]
    d["manifest"]["expected_sessions"].remove("2024-01-04")
    r = run(d, cfg)
    assert r["status"] == "INCOMPLETE" and r["gaps"][0]["reason"] == "MISSING_EXPIRY_SESSION"


def test_underlying_future_or_late_availability_not_used():
    d = fixture_data()
    d["underlying"][0]["observed_at"] = "2024-01-03T16:00:00-05:00"
    d["underlying"][0]["available_at"] = "2024-01-03T16:05:00-05:00"
    with pytest.raises(DataError, match="future"):
        run(d)
    d = fixture_data()
    d["underlying"][2]["available_at"] = "2024-01-05T16:05:00-05:00"
    r = run(d)
    assert r["gaps"][0]["reason"] == "MISSING_OR_NOT_YET_AVAILABLE_UNDERLYING"


def test_expiry_before_actual_clock_not_simulated_at_earlier_session_close():
    d = fixture_data(n=3, expiry="2024-01-04")
    d["contracts"][0]["expiry_at"] = "2024-01-04T17:00:00-05:00"
    r = run(d, Config(min_dte=1, target_dte=2, max_dte=4, close_dte=0))
    assert r["status"] == "INCOMPLETE" and r["gaps"][0]["reason"] == "UNSUPPORTED_EXPIRY_TIMING"


@pytest.mark.parametrize('key,bad', [('right','call'), ('currency','CNY'), ('multiplier',150), ('exercise_style','European'), ('settlement','cash')])
def test_unsupported_contract_fields_rejected(key, bad):
    d = fixture_data()
    d["contracts"][0][key] = bad
    with pytest.raises(DataError):
        run(d)


def test_forced_policy_skips_open_for_last_session_two_sessions():
    d = fixture_data(n=2)
    r = run(d, Config(end_policy="CLOSE_IF_QUOTED"))
    assert r['status'] == 'COMPLETE' and r['metrics']['fees'] == 0
    assert [e['event'] for e in r['events']] == ['OPEN_SKIPPED_AT_DATA_END']
    assert r['ledger'][-1]['equity'] == 100000
    # Default deliberately still opens and honestly ends incomplete.
    normal = run(d)
    assert normal['status'] == 'INCOMPLETE'
    assert any(e['event'] == 'SELL_TO_OPEN' for e in normal['events'])


def test_expiry_settlement_first_cancels_pending_close_across_holiday():
    d = fixture_data(n=7, expiry='2024-01-08')
    # Explicit Fri -> Mon gap. Queue close Friday (3DTE); expiry session Monday.
    keep = {'2024-01-02','2024-01-03','2024-01-04','2024-01-05','2024-01-08'}
    for k in ('quotes','underlying','sessions'):
        d[k] = [r for r in d[k] if r['session_date'] in keep]
    d['manifest']['expected_sessions'] = [s['session_date'] for s in d['sessions']]
    r = run(d, Config(min_dte=4, target_dte=6, max_dte=8, close_dte=3))
    assert any(e['event']=='QUEUE_CLOSE' and e['session_date']=='2024-01-05' for e in r['events'])
    cancel = next(e for e in r['events'] if e['event']=='ORDER_CANCELLED')
    assert cancel['session_date']=='2024-01-08' and 'EXPIRY_SETTLEMENT_FIRST' in cancel['reason']
    assert not any(e['event']=='BUY_TO_CLOSE' for e in r['events'])
    assert r['events'][-1]['event']=='EXPIRE_WORTHLESS'


def test_close_dte1_expiry_day_buyback_is_cancelled_explicitly():
    d = fixture_data(n=4, expiry='2024-01-05')
    r = run(d, Config(min_dte=2, target_dte=3, max_dte=4, close_dte=1))
    assert any(e['event']=='QUEUE_CLOSE' and e['session_date']=='2024-01-04' for e in r['events'])
    assert any(e['event']=='ORDER_CANCELLED' and e['session_date']=='2024-01-05' and 'EXPIRY_SETTLEMENT_FIRST' in e['reason'] for e in r['events'])
    assert r['events'][-1]['event']=='EXPIRE_WORTHLESS'
