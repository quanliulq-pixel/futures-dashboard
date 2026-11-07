# Offline cash-secured Put backtest — research MVP

No UI, public endpoint, scheduled task, broker link, trading, calls or changes to
existing screens. Uses Python 3.10+ standard library only. Tests use pytest.

**Every fill is hypothetical, even with real quotes. US puts are American but
this engine does NOT model early exercise/assignment. Expiry-only assignment,
no dividends/interest/taxes/corporate actions: these are material limitations.**
The engine is not a claim about executable historical returns. It rejects
unsupported rights, non-USD, unverified STRICT metadata and nonstandard delivery.
All synthetic tests say **SYNTHETIC_TEST_ONLY**, never performance evidence.

## Configurable defaults

SPY, $100,000 initial cash, exactly one CSP, target 30 calendar DTE (25–40 range),
nearest 5% OTM put, close decision at <=7 calendar DTE, execution on the NEXT
supplied trading session. Rank by |DTE−30| then |OTM−5%|, expiry, strike, id;
no delta/IV signal. Prior session's chain fixes the contract; no same-day reselection.
Unknown liquidity never silently becomes known: STRICT entry requires bid size;
EOD reference allows missing size with a disclosed hypothetical-size assumption.
Sell at bid−$0.01/share, close at ask+$0.01/share, $0.65/contract/side; assigned
stock sell at bid−$0.01/share, no additional stock commission modeled.
Full K×100 cash plus entry fee must exist BEFORE premium; premium can't enable
entry. Collateral is a restriction on cash, not a second equity deduction.
Expiry ITM>= $0.01/share triggers assumed assignment: cash−K×100, shares+100;
otherwise zero liability. Discretionary/pin/after-hours exercise is unsupported.
Assigned stock is sold next session; missing bid in STRICT stops the run.
EOD reference explicitly substitutes underlying EOD close−slippage for stock sale.

Daily equity = cash + marked stock − option ask×100. This is a conservative
observed-quote mark, not a promise of liquidation. Close slippage/fees only book
on actual simulated close. No IV/Black-Scholes premiums are synthesized.

## Two modes (never confuse them)

- `STRICT`: timezone-aware observed_at, available_at and session decision_at;
  observed_at <= available_at <= decision_at; current-session observation only.
  Entry signal on T can fill only on T+1 on a NEW quote later than T decision.
  Metadata must be explicitly VERIFIED with source. Caller supplies/attests
  these metadata: the engine validates consistency, not provider honesty.
  Expiry needs actual expiry_at and an attested settlement-price observation
  at that time, available by that session decision. No timestamps are invented.
- `OBSERVED_EOD_REFERENCE`: actual historical DAILY observations, with possibly
  unknown quote update/availability clocks. Requires explicit manifest ack.
  Daily-file observations are treated as known after the labelled session solely
  to produce lagged decisions; hypothetical fills use the next session's quote.
  Not timestamp-accurate execution replay. Permits explicitly documented
  USER_ASSUMPTION standard delivery metadata; never labels it verified.
  The 2022 sample adapter is ONLY this mode. A file date does not prove that its
  bid/ask updated on that date. Asynchronous/stale quotes remain a limitation.

`available_at` after decision is valid input but unavailable until then, never
looked ahead. Missing observations are not estimated from future prices. Current
session's observed bid/ask is needed even for marking an old position; no carry.
No arbitrary premium/no-arbitrage bound filter: asynchronous EODs are not clean
synchronized pricing observations. Finite nonnegative bid, positive ask and
noncrossed spread required; zero bid can't open. Input NaN/Inf/duplicate rows
(even identical), conflicting ids, post-expiry quotes and unknown flags reject.

## Gap / endpoint policy

Calendar comes from explicit `expected_sessions`, NOT the dates with usable
quotes. Dataset/session mismatch rejects. Calendar source must be reviewed by
caller; the sample adapter cites vendor manifest rather than pretending it
independently validated the exchange calendar.

- Flat, missing mark/underlying: skip or cancel order, log event; known cash
  remains valueable. No new decision on a flat session with missing underlying.
- Holding option/shares, required quote/underlying missing/not yet available:
  STOP with `status=INCOMPLETE`, explicit gap and last fully valued ledger row.
  No fabricated whole-period metrics (`metrics=null`).
- A missing opening quote cancels that queued order. It doesn't fill later or
  substitute a different contract. A new later decision is a NEW order.
- Default end policy `MARK_INCOMPLETE`: open option/shares at file end means
  INCOMPLETE, not an assumed expiration at zero. Final fully valued row retained.
- Optional PREDECLARED `CLOSE_IF_QUOTED`: force-close option at final session ask
  plus costs, log FORCED_END_CLOSE. This changes the experiment; final shares
  still make run incomplete. Can't invent a missing final quote.
- No automatic universe selection based on what contracts happen to survive
  future files. Every selection reads only that decision session's eligible rows.

## Local files (CSV UTF-8, blank means unknown)

Directory: manifest.json plus exactly these four CSV inputs. No arbitrary web
paths or server endpoints. Dataset rows are never downloaded by the engine.

`contracts.csv`:
contract_id,symbol,right,strike,expiry,expiry_at,currency,multiplier,settlement,
exercise_style,deliverable,metadata_basis,metadata_source

Only right=put,currency=USD,multiplier=100,settlement=physical,
exercise_style=American,deliverable=100_SHARES. expiry is YYYY-MM-DD;
expiry_at is aware ISO8601. Identity is immutable across sessions and is not just
symbol (expiry/strike/right/delivery matter). metadata_basis VERIFIED or
USER_ASSUMPTION; only the latter may be used under explicit reference ack.

`sessions.csv`: session_date,decision_at (ascending explicit trading calendar).
`quotes.csv`: session_date,contract_id,observed_at,available_at,bid,ask,bid_size,ask_size
(one row/contract/session in this daily MVP; normalize intraday data separately).
`underlying.csv`: session_date,symbol,price,observed_at,available_at,bid,ask,
expiry_settlement_eligible,settlement_source
Price must be **unadjusted**; availability is when information is usable by this
simulation. Settlement eligibility is a literal true/false attestation, not a
claim verified just by a field label. STRICT true also requires source.

`manifest.json`: dataset_kind HISTORICAL_OBSERVED or SYNTHETIC_TEST_ONLY,
source,provenance,expected_sessions (ordered date list),file_sha256 (all four
CSV basenames to SHA256),disclosures (list), and for reference mode explicit
boolean eod_reference_assumptions_acknowledged=true. Keep unknown clocks blank;
never substitute a guessed close timestamp. Caller must separately provide
trusted session calendar, unadjusted price/settlement series and corporate-
action exclusions for a strict real-data study.

## Real free-sample acquisition / importer (local only)

Source https://historicaldata.net/options.html ; download URL below. Sample
license permits free local evaluation but restricts redistribution. Read ZIP's
LICENSE.txt yourself; DO NOT execute supplied verify.py blindly. Adapter reads
ZIP CSV and manifest directly, checks vendor CSV hashes; never extracts paths or
runs third-party code. No third-party rows are included in this repository.

```bash
# Store OUTSIDE repo. No account, purchase, paid service or credentials.
curl -fL -o /tmp/options_sample_2022H2.zip \
  https://historicaldata.net/file/options_sample_2022H2.zip
# ~238 MB. Measured SHA256 Nov7 2026:
# 8517b069f4d41e44f950dc79a3cdab694359b0fae91664190011e39dd8e48b76
# Local user's explicit reference/delivery assumptions acknowledgment:
python -m backtest.import_sample --zip /tmp/options_sample_2022H2.zip \
  --out /tmp/spy-july --start 2022-07-01 --end 2022-07-29 \
  --acknowledge-reference-assumptions
python -m backtest.cli --dataset /tmp/spy-july --mode OBSERVED_EOD_REFERENCE \
  --output /tmp/july-default.json
# Exit2 INCOMPLETE is meaningful and still writes evidence.
# Alternative experiment: declare final-date liquidation BEFORE running:
python -m backtest.cli --dataset /tmp/spy-july --mode OBSERVED_EOD_REFERENCE \
  --end-policy CLOSE_IF_QUOTED --output /tmp/july-forced-close.json
# Dynamic configurable examples (same inputs, new fingerprint/result):
python -m backtest.cli --dataset /tmp/spy-july --mode OBSERVED_EOD_REFERENCE \
  --capital 50000 --target-dte 35 --min-dte 25 --max-dte 40 --otm 0.07 \
  --close-dte 10 --fee-per-contract 1 --option-slippage 0.02 \
  --output /tmp/alternative.json
python -m pytest -q tests/backtest
```

CLI outputs JSON plus `_ledger.csv`, `_events.csv`, `_gaps.csv`. Exit0 COMPLETE,
exit2 INCOMPLETE (metrics omitted), exit1 VALIDATION_FAILED. Run fingerprint
covers full validated data, all config and engine version; source files and
source ZIP hashes accompany output for audit. No stale look-through web cache.
CSV and JSON results are local research artifacts, not uploaded by this code.

First session is a real signal warm-up; first potential fill is NEXT session.
No optimization/tuning on the sample. July has 20 vendor sessions; importer
acknowledges 100-share standard unadjusted SPY, not independently verified delivery.
It drops unusable bid/ask records, reports counts, never fills missing records.
Do not use TSLA split-period sample with this unsupported-corporate-action MVP.

## Colab

Use `examples/colab_backtest.ipynb` or cell-formatted `colab_backtest.py`. Put
project folder on Colab filesystem, change working directory to repo root (so
`import backtest` works), and explicitly review/set USER_ACK. The notebook uses
same stdlib engine; no cloud trading, web endpoint or login. Do not publicly
share notebook outputs containing source data. Long datasets should run offline,
not on the Render page-open request path.

### Review clarifications

Date interval is currently chosen by importer `--start/--end` (including the first
signal warmup session), not by CLI slicing. CLI consumes the entire supplied
calendar. Separate directories for different date experiments avoid accidentally
changing the warmup or declaring a different data-end liquidation after the fact.

`CLOSE_IF_QUOTED` also skips any new entry that would execute on the final
liquidation session: event `OPEN_SKIPPED_AT_DATA_END`. No pointless entry and
buyback at the same final quote. Default MARK_INCOMPLETE still allows final-date
entries and reports the open position honestly.

**Expiry-settlement-first daily convention:** on expiry session the engine
settles the option before processing queued buybacks. A pending close (e.g.
close_dte=1, or a holiday gap) is cancelled with explicit ORDER_CANCELLED reason
EXPIRY_SETTLEMENT_FIRST. It does NOT imply an unavailable intraday expiry buyback.
This is an engine convention, not a claim that brokers execute in this order.

Colab example now bootstraps public source itself from
https://github.com/quanliulq-pixel/futures-dashboard.git, configurable SOURCE_REF
(default main). No package needs to exist in /content first. Main must contain
this patch; before merge choose a published ref that contains it. Resolved commit
is printed, saved to source-provenance.json and included as source_checkout in
run output. Existing checkout is verified and never reset/switched; choose a fresh
SOURCE_DIR to change refs or preserve dirty work. USER_ACK gates DATA download/use,
not the explicitly shown public-source bootstrap.

Sample ZIP digest is pinned and verified BEFORE parsing by importer and notebook,
including an already-existing ZIP. Notebook downloads exclusively to .part,
verifies digest, then atomically renames. Corrupt/partial existing files are left
untouched, with instructions to inspect/quarantine manually. Changed vendor
archives require explicit review, not silently updating the digest.
