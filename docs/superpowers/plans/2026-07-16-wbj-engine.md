# `wbj` Compute Engine Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build the `wbj` Python package that implements every deterministic piece of the Cerebro v2.0.0 methodology — data fetching, packet validation, ~200 formulas, scoring/gates/overrides, valuation + levels engines, and report rendering — behind a staged CLI (`wbj fetch|packet|compute|aggregate|report|analyze`).

**Architecture:** Staged pipeline (Approach A of the spec). Each CLI stage writes a hashed JSON artifact; specialists run independently off a frozen packet; judgment-only metrics are `NOT_SCORABLE` until a Claude-agent overlay is merged. Spec: `docs/superpowers/specs/2026-07-16-wbj-engine-design.md`.

**Tech Stack:** Python 3.11+, typer, httpx, pandas, numpy, scipy, pydantic v2, python-dotenv, matplotlib, pytest.

## Global Constraints

- Package root: `engine/`, package name `wbj`, console script `wbj`. All paths below are relative to the repo root `/Users/victorgonzalez/Desktop/warren-buffett-jr/`.
- **Methodology is law:** formulas, thresholds, weights, and schemas come verbatim from `Cerebro/` files. Every task lists its authoritative Cerebro sources — the implementer MUST read them before coding. When this plan and a Cerebro file disagree, the Cerebro file wins; note the discrepancy in the commit message.
- Category weights fixed: Business 20, Financial 15, Market 20, Technical 20, Risk 15, Valuation 10 (sum 100).
- No bare `None` for metric values — always a `Value` (Task 2) carrying one of: number, `MISSING`, `NOT_APPLICABLE`, `NOT_MEANINGFUL`, `CONFLICTED`, `STALE`, `NOT_SCORABLE`.
- Score (0–10) and confidence (0–100) are separate; confidence never changes a score.
- Never impute (prohibited list, `Cerebro/shared/MISSING_DATA_POLICY.md`): customer concentration, organic growth, market share, debt maturities, earnings revisions, technical volume, beta, terminal growth, WACC, support/resistance touches.
- API keys load from `API/.env`; never print or log key values.
- All tests must run offline (fixtures under `engine/tests/fixtures/`); network calls only behind provider classes.
- Reports in **English**, saved to `Reportes/<TICKER>/<YYYY-MM-DD>/`.
- Every commit message follows `feat|test|chore(wbj): ...` style and ends with the Claude co-author trailer.
- Monte Carlo must be seeded (`numpy.random.default_rng(seed)`); `Date.now`-style nondeterminism prohibited in library code — timestamps come from the packet.

---

## Phase 1 — Foundations

### Task 1: Package scaffold, config, CLI skeleton

**Files:**
- Create: `engine/pyproject.toml`
- Create: `engine/wbj/__init__.py`
- Create: `engine/wbj/config.py`
- Create: `engine/wbj/cli.py`
- Test: `engine/tests/test_config.py`

**Interfaces:**
- Produces: `wbj.config.Settings` (pydantic-settings-style dataclass) with fields `fmp_api_key: str | None`, `finnhub_api_key: str | None`, `fred_api_key: str | None`, `repo_root: Path`, `cache_dir: Path`, `reports_dir: Path`; loader `load_settings(env_file: Path | None = None) -> Settings`.
- Produces: `wbj.cli.app` — typer app with subcommands `fetch`, `packet`, `compute`, `aggregate`, `report`, `analyze` (stubs that raise `typer.Exit(1)` with "not implemented" until wired in Task 24).

- [ ] **Step 1: Write the failing test**

```python
# engine/tests/test_config.py
from pathlib import Path
from wbj.config import load_settings

def test_loads_keys_from_env_file(tmp_path: Path):
    env = tmp_path / ".env"
    env.write_text("FMP_API_KEY=abc123\nFINNHUB_API_KEY=\n")
    s = load_settings(env_file=env)
    assert s.fmp_api_key == "abc123"
    assert s.finnhub_api_key is None  # empty string → None (key absent)

def test_missing_env_file_is_not_fatal(tmp_path: Path):
    s = load_settings(env_file=tmp_path / "nope.env")
    assert s.fmp_api_key is None

def test_settings_never_repr_keys(tmp_path: Path):
    env = tmp_path / ".env"
    env.write_text("FMP_API_KEY=SECRETVALUE\n")
    s = load_settings(env_file=env)
    assert "SECRETVALUE" not in repr(s)
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd engine && python -m pytest tests/test_config.py -v`
Expected: FAIL (`ModuleNotFoundError: No module named 'wbj'`)

- [ ] **Step 3: Implement**

`engine/pyproject.toml`:

```toml
[project]
name = "wbj"
version = "0.1.0"
description = "Warren Buffett Jr compute engine — Ruta 2030 v2.0.0"
requires-python = ">=3.11"
dependencies = [
  "typer>=0.12", "httpx>=0.27", "pandas>=2.2", "numpy>=1.26",
  "scipy>=1.13", "pydantic>=2.7", "python-dotenv>=1.0", "matplotlib>=3.8",
]
[project.optional-dependencies]
dev = ["pytest>=8"]
[project.scripts]
wbj = "wbj.cli:app"
[build-system]
requires = ["hatchling"]
build-backend = "hatchling.build"
```

`engine/wbj/config.py` — dataclass `Settings` with `repr=False` on key fields; `load_settings` uses `dotenv_values(env_file)` (default `<repo_root>/API/.env`, where `repo_root` = two parents up from `wbj/`), maps empty strings to `None`, sets `cache_dir = repo_root/"engine"/"cache"`, `reports_dir = repo_root/"Reportes"`.

`engine/wbj/cli.py` — `app = typer.Typer()`; six stub commands each taking `ticker: str`.

Install: `cd engine && pip install -e ".[dev]"`

- [ ] **Step 4: Run test to verify it passes**

Run: `cd engine && python -m pytest tests/test_config.py -v` → PASS (3 tests)

- [ ] **Step 5: Commit**

```bash
git add engine/ && git commit -m "feat(wbj): package scaffold, settings loader, CLI skeleton"
```

### Task 2: Null-state `Value` type and lineage

**Files:**
- Create: `engine/wbj/core/nullstates.py`
- Create: `engine/wbj/core/__init__.py`
- Test: `engine/tests/core/test_nullstates.py`

**Sources:** `Cerebro/shared/DATA_POLICY.md` (lineage fields, evidence classes R/C/E/A/Q), `Cerebro/shared/DATA_DICTIONARY.md` (allowed null states), `Cerebro/shared/MISSING_DATA_POLICY.md` (prohibited imputation list).

**Interfaces:**
- Produces: `NullState` (StrEnum: MISSING, NOT_APPLICABLE, NOT_MEANINGFUL, CONFLICTED, STALE, NOT_SCORABLE); `EvidenceClass` (StrEnum: R, C, E, A, Q); frozen pydantic model `Value` with fields `value: float | None`, `state: NullState | None`, `unit: str`, `period: str | None`, `as_of: str | None`, `source_name: str | None`, `source_locator: str | None`, `evidence_class: EvidenceClass | None`, `warnings: list[str]`; constructors `Value.of(x, unit, **lineage)` and `Value.null(state, unit="", **lineage)`; properties `is_valid` (numeric) / `is_null`; arithmetic helpers propagate null (`Value.map2(a, b, fn)` returns first null of a/b or `fn(a,b)`).
- Produces: `PROHIBITED_IMPUTATION: frozenset[str]` of metric_ids (verbatim list from MISSING_DATA_POLICY.md).

- [ ] **Step 1: Write the failing test**

```python
# engine/tests/core/test_nullstates.py
from wbj.core.nullstates import Value, NullState, PROHIBITED_IMPUTATION

def test_valid_value():
    v = Value.of(0.21, unit="ratio", source_name="10-K")
    assert v.is_valid and v.value == 0.21 and not v.is_null

def test_null_value_has_no_number():
    v = Value.null(NullState.NOT_SCORABLE)
    assert v.is_null and v.value is None and v.state == NullState.NOT_SCORABLE

def test_exactly_one_of_value_or_state():
    import pytest
    with pytest.raises(ValueError):
        Value(value=1.0, state=NullState.MISSING, unit="x", warnings=[])

def test_null_propagates_through_map2():
    a, b = Value.of(10, unit="usd"), Value.null(NullState.MISSING)
    out = Value.map2(a, b, lambda x, y: x / y)
    assert out.is_null and out.state == NullState.MISSING

def test_prohibited_list_contains_wacc_and_beta():
    assert "wacc" in PROHIBITED_IMPUTATION and "beta" in PROHIBITED_IMPUTATION
```

- [ ] **Step 2: Run** → FAIL (module missing)
- [ ] **Step 3: Implement** `nullstates.py` per the interface above (model_validator enforces XOR of value/state).
- [ ] **Step 4: Run** → PASS (5 tests)
- [ ] **Step 5: Commit** `feat(wbj): Value null-state type with lineage and prohibited-imputation list`

### Task 3: Formula registry

**Files:**
- Create: `engine/wbj/core/formulas.py`
- Test: `engine/tests/core/test_formulas.py`

**Sources:** `Cerebro/shared/FORMULA_REGISTRY.md` (result object shape, registration requirements), `Cerebro/shared/CALCULATION_CONVENTIONS.md`.

**Interfaces:**
- Produces: `@register_formula(id: str, version: str, unit: str, inputs: list[str], min_history: int = 0, missing_behavior: NullState = NullState.MISSING)` decorator storing into `REGISTRY: dict[str, Formula]`; `run_formula(formula_id, data: dict[str, Value | list | float]) -> FormulaResult` where `FormulaResult` = pydantic model `{formula_id, formula_version, inputs: dict, result: Value, status: Literal["OK","NULL","ERROR"], warnings: list[str]}`.
- Produces: convention helpers in same module: `yoy(cur, prior)`, `cagr(end, begin, years)` (returns `Value.null(NOT_MEANINGFUL)` + warning on sign change / non-positive base), `avg_balance(begin, end)` (falls back to end value with `END_BALANCE_PROXY` warning when begin missing), `annualize_vol(daily_std)` (×√252), `winsorize(series, 0.05, 0.95)`.
- Later tasks register their formulas into this REGISTRY with Cerebro IDs.

- [ ] **Step 1: Write the failing test**

```python
# engine/tests/core/test_formulas.py
from wbj.core.formulas import register_formula, run_formula, REGISTRY, cagr, yoy
from wbj.core.nullstates import Value, NullState

def test_register_and_run():
    @register_formula(id="TST-001", version="2.0.0", unit="pct", inputs=["a", "b"])
    def growth(a: float, b: float) -> float:
        return a / b - 1
    r = run_formula("TST-001", {"a": 110.0, "b": 100.0})
    assert r.status == "OK" and abs(r.result.value - 0.10) < 1e-12
    assert r.formula_id == "TST-001" and r.formula_version == "2.0.0"

def test_missing_input_returns_null_not_crash():
    r = run_formula("TST-001", {"a": 110.0, "b": Value.null(NullState.MISSING)})
    assert r.status == "NULL" and r.result.state == NullState.MISSING

def test_cagr_rejects_sign_change():
    r = cagr(end=50.0, begin=-10.0, years=3)
    assert r.is_null and r.state == NullState.NOT_MEANINGFUL

def test_yoy():
    assert abs(yoy(120.0, 100.0).value - 0.20) < 1e-12
```

- [ ] **Step 2: Run** → FAIL
- [ ] **Step 3: Implement** — decorator unwraps `Value` inputs (any null input → NULL result with that state), catches exceptions → status ERROR + warning, wraps numeric returns in `Value.of` with `evidence_class=C`.
- [ ] **Step 4: Run** → PASS
- [ ] **Step 5: Commit** `feat(wbj): formula registry with Cerebro result object and calculation conventions`

### Task 4: Scoring engine

**Files:**
- Create: `engine/wbj/core/scoring.py`
- Test: `engine/tests/core/test_scoring.py`

**Sources:** `Cerebro/shared/SCORING_ENGINE.md` (read fully — anchors, peer scoring, hybrid, reweighting), `Cerebro/00_main_agent/SCORING_AND_GATES.md`.

**Interfaces:**
- Produces:
  - `anchor_score(x: float, anchors: list[tuple[float, float]]) -> float` — piecewise-linear interpolation, clamped [0, 10].
  - `peer_score(x: float, peers: list[float], higher_is_better: bool = True) -> Value` — `10*percentile_rank`; `< 8` valid peers → `Value.null(NOT_SCORABLE)` with warning.
  - `hybrid_score(abs_s: float, peer_s: float, w_abs: float, w_peer: float) -> float` (asserts weights sum to 1).
  - `Dimension` model `{name, max_points, metric_scores: list[tuple[weight, Value]]}` with `.score10()` — weighted mean over valid metrics; if valid weight < 0.70 of total → `NOT_SCORABLE`.
  - `Category` model `{name, max_points, dimensions: list[Dimension]}` with `.points()` (Σ `dim.max * score/10`), `.score10()`, `.coverage()` (valid metric weight / applicable weight), `.complete` (coverage ≥ 0.70).
- Constants: `CATEGORY_WEIGHTS = {"business": 20, "financial": 15, "market": 20, "technical": 20, "risk": 15, "valuation": 10}`; `COVERAGE_COMPLETE = 0.85`, `COVERAGE_USABLE = 0.70`.

- [ ] **Step 1: Write the failing test** (anchor set straight from SCORING_ENGINE.md example)

```python
# engine/tests/core/test_scoring.py
from wbj.core.scoring import anchor_score, peer_score, Dimension, Category
from wbj.core.nullstates import Value, NullState

ANCHORS = [(0.0, 0), (0.05, 3), (0.10, 5), (0.15, 7), (0.25, 10)]

def test_anchor_interpolation():
    assert anchor_score(0.10, ANCHORS) == 5
    assert abs(anchor_score(0.125, ANCHORS) - 6.0) < 1e-9   # halfway 5→7
    assert anchor_score(-0.50, ANCHORS) == 0                # clamped
    assert anchor_score(0.90, ANCHORS) == 10                # clamped

def test_peer_score_needs_8_peers():
    assert peer_score(5.0, [1, 2, 3]).is_null

def test_dimension_reweights_only_above_70pct_valid():
    d = Dimension(name="x", max_points=5, metric_scores=[
        (0.5, Value.of(8, unit="score")),
        (0.3, Value.of(6, unit="score")),
        (0.2, Value.null(NullState.MISSING)),
    ])  # 80% valid weight → reweight
    assert abs(d.score10() - (0.5*8 + 0.3*6) / 0.8) < 1e-9

def test_dimension_not_scorable_below_70pct():
    d = Dimension(name="x", max_points=5, metric_scores=[
        (0.5, Value.null(NullState.MISSING)),
        (0.5, Value.of(9, unit="score")),
    ])
    assert d.score10_value().is_null  # 50% < 70%

def test_category_points_math():
    # MAIN-002 spirit: dimension points = max * score/10
    d1 = Dimension(name="a", max_points=5, metric_scores=[(1.0, Value.of(8, unit="score"))])
    d2 = Dimension(name="b", max_points=4, metric_scores=[(1.0, Value.of(5, unit="score"))])
    c = Category(name="business", max_points=20, dimensions=[d1, d2])
    assert abs(c.points() - (5*0.8 + 4*0.5)) < 1e-9  # 4.0 + 2.0 = 6.0
```

- [ ] **Step 2: Run** → FAIL
- [ ] **Step 3: Implement** per SCORING_ENGINE.md exactly.
- [ ] **Step 4: Run** → PASS
- [ ] **Step 5: Commit** `feat(wbj): scoring engine — anchors, peer percentile, dimension/category math`

### Task 5: Confidence engine

**Files:**
- Create: `engine/wbj/core/confidence.py`
- Test: `engine/tests/core/test_confidence.py`

**Sources:** `Cerebro/shared/CONFIDENCE_ENGINE.md`.

**Interfaces:**
- Produces: `confidence(coverage, source_quality, freshness, consistency, model_fit) -> float` = `0.30*c + 0.25*sq + 0.20*f + 0.15*cons + 0.10*mf` (all inputs 0–100, output 0–100); `confidence_label(x) -> Literal["High","Medium","Low","Insufficient"]` (≥85 / 70–84 / 50–69 / <50); `total_confidence(cat_conf: dict[str, float]) -> float` = `Σ(weight_pts * conf)/100` using `CATEGORY_WEIGHTS`.

- [ ] **Step 1: Write the failing test**

```python
from wbj.core.confidence import confidence, confidence_label, total_confidence

def test_weighted_formula():
    assert confidence(100, 100, 100, 100, 100) == 100
    assert abs(confidence(80, 60, 100, 40, 50) -
               (0.30*80 + 0.25*60 + 0.20*100 + 0.15*40 + 0.10*50)) < 1e-9

def test_labels():
    assert confidence_label(85) == "High"
    assert confidence_label(70) == "Medium"
    assert confidence_label(50) == "Low"
    assert confidence_label(49.9) == "Insufficient"

def test_total_confidence_points_weighted():
    conf = dict(business=90, financial=80, market=70, technical=60, risk=50, valuation=40)
    expected = (20*90 + 15*80 + 20*70 + 20*60 + 15*50 + 10*40) / 100
    assert abs(total_confidence(conf) - expected) < 1e-9
```

- [ ] **Steps 2–5:** run-fail → implement → run-pass → commit `feat(wbj): confidence engine`

## Phase 2 — Data layer

### Task 6: Response cache + provider base

**Files:**
- Create: `engine/wbj/providers/__init__.py`
- Create: `engine/wbj/providers/cache.py`
- Create: `engine/wbj/providers/base.py`
- Test: `engine/tests/providers/test_cache.py`

**Interfaces:**
- Produces: `Cache(cache_dir)` with `get(ticker, key) -> dict | None`, `put(ticker, key, payload: dict)` — writes `engine/cache/<TICKER>/<key>.json` as `{"fetched_at": iso8601, "payload": ...}`; `age_days(ticker, key) -> float | None`.
- Produces: `Provider` base class: `__init__(settings, cache, client: httpx.Client | None)`, method `get_json(url, params, cache_key, ticker, max_age_days: float | None)` — cache-first, 3 retries exponential backoff (0.5s/1s/2s) on 5xx/timeouts, returns `None` on final failure (caller maps to `MISSING`), never raises for network errors, strips any `apikey` param from log/warning text.

- [ ] **Step 1: Write the failing test** — cache round-trip, `age_days`, and a `Provider.get_json` test using `httpx.MockTransport` that (a) serves from cache without hitting transport, (b) retries twice on 500 then succeeds, (c) returns `None` after 3 failures. Show the MockTransport wiring in the test file.

```python
# engine/tests/providers/test_cache.py  (representative core; implementer completes)
import httpx
from wbj.providers.cache import Cache
from wbj.providers.base import Provider

def test_cache_roundtrip(tmp_path):
    c = Cache(tmp_path)
    c.put("NVDA", "profile", {"name": "NVIDIA"})
    assert c.get("NVDA", "profile")["name"] == "NVIDIA"
    assert c.age_days("NVDA", "profile") < 1/24

def test_provider_returns_none_after_3_failures(tmp_path):
    calls = {"n": 0}
    def handler(request):
        calls["n"] += 1
        return httpx.Response(500)
    p = Provider(settings=None, cache=Cache(tmp_path),
                 client=httpx.Client(transport=httpx.MockTransport(handler)))
    assert p.get_json("https://x.test/a", {}, "k", "NVDA") is None
    assert calls["n"] == 3
```

- [ ] **Steps 2–5:** fail → implement (backoff sleeps monkeypatchable via `Provider._sleep`) → pass → commit `feat(wbj): response cache and resilient provider base`

### Task 7: FMP provider

**Files:**
- Create: `engine/wbj/providers/fmp.py`
- Test: `engine/tests/providers/test_fmp.py`
- Fixtures: `engine/tests/fixtures/fmp/*.json` (hand-write minimal realistic samples per endpoint; field names from FMP docs)

**Interfaces:**
- Produces: `FMPProvider(settings, cache)` with methods (all return parsed payload dicts/lists or `None`):
  `profile(t)`, `income_annual(t, limit=6)`, `income_quarterly(t, limit=21)`, `balance_annual(t, limit=6)`, `balance_quarterly(t, limit=21)`, `cashflow_annual(t, limit=6)`, `cashflow_quarterly(t, limit=21)`, `ohlcv_daily(t, years=3)` (split/dividend-adjusted series), `peers(t)`, `analyst_estimates(t)`, `insider_trades(t)` (Form 4 list), `institutional_holders(t)` (13F), `earnings_calendar(t)`.
- Base URL `https://financialmodelingprep.com/api/v3`; `apikey` from settings; provider disabled (`available == False`, all methods return `None`) when key missing.

- [ ] **Step 1: Write the failing test** — for each method: correct URL/params assembled (assert via MockTransport-captured request), fixture payload returned, and `FMPProvider(available=False).profile("NVDA") is None` when key absent.
- [ ] **Steps 2–5:** fail → implement → pass → commit `feat(wbj): FMP provider (statements, OHLCV, insiders, 13F, estimates)`

### Task 8: SEC EDGAR provider + source-hierarchy reconciliation

**Files:**
- Create: `engine/wbj/providers/edgar.py`
- Create: `engine/wbj/packet/reconcile.py`
- Test: `engine/tests/providers/test_edgar.py`, `engine/tests/packet/test_reconcile.py`
- Fixtures: `engine/tests/fixtures/edgar/companyfacts_sample.json`, `engine/tests/fixtures/edgar/tickers_sample.json`

**Sources:** `Cerebro/shared/SOURCE_HIERARCHY.md` (8 tiers, materiality: conflict is material if it moves category score ≥0.5/10, valuation midpoint ≥5%, S/R zone ≥0.5 ATR, or a gate/override — for the facts table use the 5% valuation-input proxy: shares/debt/cash differing >2% is escalated, >5% is material).

**Interfaces:**
- Produces: `EdgarProvider` with `cik_for(ticker)` (via `company_tickers.json`), `companyfacts(cik)` (XBRL facts), `filing_acceptance_times(cik)` — User-Agent header `"warren-buffett-jr victor@infusioninvestments.com"` (SEC requirement), no API key needed.
- Produces: `reconcile(field: str, fmp: Value, edgar: Value) -> Value` — if both valid and relative diff ≤2% → EDGAR value (tier-1), lineage notes both; 2–5% → EDGAR value + warning; >5% → `Value.null(CONFLICTED)` with both values in warnings. Only EDGAR valid → EDGAR; only FMP → FMP with `source_quality` note.

- [ ] **Step 1: Write the failing tests** — companyfacts parsing pulls `dei:EntityCommonStockSharesOutstanding` and `us-gaap` revenue/cash/debt concepts from the fixture; reconcile matrix: agree→EDGAR, 3% diff→EDGAR+warning, 8% diff→CONFLICTED.
- [ ] **Steps 2–5:** fail → implement → pass → commit `feat(wbj): EDGAR provider and tier-1 reconciliation`

### Task 9: FinnHub + FRED providers

**Files:**
- Create: `engine/wbj/providers/finnhub.py`, `engine/wbj/providers/fred.py`
- Test: `engine/tests/providers/test_finnhub_fred.py`
- Fixtures: `engine/tests/fixtures/finnhub/*.json`, `engine/tests/fixtures/fred/*.json`

**Interfaces:**
- `FinnhubProvider`: `estimates(t)` (consensus EPS/revenue + history), `earnings_calendar(t)`, `quote(t)`.
- `FredProvider`: `series(series_id, limit)` — used for `DGS10` (10y treasury → risk-free rate). Helper `risk_free_rate() -> Value` (latest DGS10 / 100, unit "ratio").

- [ ] **Steps 1–5:** same TDD pattern as Task 7. Commit `feat(wbj): FinnHub and FRED providers`

### Task 10: Packet builder — validation, facts table, staleness, hashing

**Files:**
- Create: `engine/wbj/packet/__init__.py`, `engine/wbj/packet/builder.py`, `engine/wbj/packet/staleness.py`, `engine/wbj/schemas/packet.py`
- Test: `engine/tests/packet/test_builder.py`, `engine/tests/packet/test_staleness.py`
- Fixture: `engine/tests/fixtures/packet/NVDA_packet.json` (built once from the other fixtures; committed — this becomes THE fixture for all specialist tests)

**Sources:** `Cerebro/QUICK_START.md`, `Cerebro/examples/INPUT_PACKET_EXAMPLE.md` (exact field shape), `Cerebro/00_main_agent/ORCHESTRATION.md` (five clocks), `Cerebro/shared/DATA_POLICY.md` (staleness table), `Cerebro/shared/DATA_DICTIONARY.md` (canonical names: `revenue, cogs, ebit, nopat, operating_cash_flow, capex, fcf, net_debt, invested_capital, diluted_shares, adj_close, atr14, ...`).

**Interfaces:**
- Produces: pydantic `Packet` mirroring INPUT_PACKET_EXAMPLE.md: `security{ticker, exchange, security_type, reporting_currency, valuation_currency}`, `analysis{knowledge_timestamp, market_timestamp, industry_adapter}`, `fundamentals` (5y annual + quarterly statements as canonical-name records), `market_data{daily: list[OHLCVRow], benchmark: list, sector: list, adjusted: True}`, `estimates`, `capital_structure`, `insiders`, `institutional_holders`, `facts_table: dict[str, Value]` (reconciled shares/debt/cash/revenue/price), `staleness: dict[str, str]`, `packet_hash: str`.
- Produces: `build_packet(ticker, providers, now: datetime) -> Packet`; `packet_hash = sha256(canonical_json(packet_without_hash))`.
- Produces: `staleness_state(data_type, age) -> Literal["FRESH","STALE"]` with thresholds: daily market data 1 trading session (>3 calendar days proxy), consensus 7 days, quarterly fundamentals 120 days, market-size study 548 days, peer set 90 days.
- Hard-reject (raise `PacketRejected`) only when: no timestamps, no currency, no diluted share count source, or <252 daily sessions.

- [ ] **Step 1: Write the failing tests** — build from fixtures via fake providers; assert: canonical field mapping (FMP `netIncome` → `net_income` etc. — write the full mapping dict in `builder.py`), ≥252 sessions enforced, hash stable across rebuilds with same inputs, hash changes when one input changes, staleness table values, `PacketRejected` on missing currency.
- [ ] **Steps 2–5:** fail → implement → pass → commit `feat(wbj): packet builder with facts table, staleness, and hashing`

## Phase 3 — Math engines

### Task 11: Technical indicator library

**Files:**
- Create: `engine/wbj/engines/indicators.py`
- Test: `engine/tests/engines/test_indicators.py`

**Sources:** `Cerebro/04_technical_momentum/FORMULAS.md` (TECH-001…021, 034…040 definitions — read for exact parameters).

**Interfaces (all take pandas Series/DataFrame of adjusted OHLCV, return Series):**
- `sma(close, n)`, `ema(close, n)` (α=2/(n+1), init with SMA of first n), `true_range(df)`, `atr14(df)` (**Wilder**: `ATR_t = (13*ATR_{t-1} + TR_t)/14`, init = mean of first 14 TRs), `rsi14(close)` (**Wilder** smoothing; zero avg loss → 100), `macd(close)` (12/26/9 → dict of macd/signal/hist), `adx14(df)`, `roc(close, n)`, `relative_strength(close, bench, n)` (ratio of n-day returns), `composite_rs_percentile(rs_by_window: dict, universe: DataFrame) -> float` (**0.35·Pct(RS21) + 0.25·Pct(RS63) + 0.25·Pct(RS126) + 0.15·Pct(RS252)**), `realized_vol(close, n)` (std of log returns × √252), `volume_ratio(volume)` (vs median 50d), `up_down_volume_ratio(df, n=50)`, `obv(df)`, `cmf(df, n=20)`, `range_position_52w(df)`, `median_dollar_volume(df, n=63)`.

- [ ] **Step 1: Write the failing tests** — verify against hand-computed short series, e.g.:

```python
def test_wilder_atr_smoothing():
    # 15 bars with constant TR=2.0 → ATR14 stays 2.0
    df = constant_tr_frame(tr=2.0, bars=20)
    assert abs(atr14(df).iloc[-1] - 2.0) < 1e-9

def test_rsi_all_gains_is_100():
    close = pd.Series(np.arange(1.0, 40.0))
    assert rsi14(close).iloc[-1] == 100.0

def test_composite_rs_weights_sum():
    # equal percentile 50 in every window → composite 50
    ...
def test_ema_initialized_with_sma():
    ...
```

Include a golden test: run `sma/ema/rsi14/atr14/macd` on the NVDA fixture OHLCV and compare to values pre-computed once with pandas in the test itself (not hardcoded magic numbers — compute reference inline with straightforward pandas ops).

- [ ] **Steps 2–5:** fail → implement → pass → commit `feat(wbj): technical indicators (Wilder ATR/RSI, MACD, ADX, RS composite)`

### Task 12: Important-levels engine

**Files:**
- Create: `engine/wbj/engines/levels_engine.py`
- Create: `engine/wbj/schemas/levels.py`
- Test: `engine/tests/engines/test_levels.py`

**Sources:** `Cerebro/special_sauces/IMPORTANT_LEVELS_ENGINE.md` (read FULLY — every parameter below is defined there), `Cerebro/04_technical_momentum/FORMULAS.md` TECH-022…033.

**Interfaces:**
- Schema `Zone`: `{zone_id, type: support|resistance, lower, center, upper, timeframe: daily|weekly, status: candidate|confirmed|strong|broken|role_reversed, strength_0_100, touches: list[Touch], distance_percent, distance_atr, confirmation_rule: str, invalidation_rule: str}`; `Touch = {date, pivot_price, rejection_atr, volume_ratio}`.
- `find_pivots(df, k)` — symmetric pivots, k=3 daily / k=2 weekly; a pivot only confirmed after k future bars close.
- `zigzag_pivots(df, atr)` — reversal threshold 1.5·ATR14.
- `cluster_zones(pivots, atr, price)` — tolerance `max(0.50*atr_at_pivot, 0.0075*pivot_price)`; highs and lows clustered separately; center = recency-weighted median (weight `exp(-ln2*age_sessions/126)`).
- `count_touches(zone, df)` — independent if ≥5 sessions apart (daily) / ≥2 weeks (weekly); valid rejection ≥0.5 ATR within 3 sessions.
- `strength(zone) -> float` — exactly:
  `30*min(N_eff/4,1) + 20*min(median_reaction_ATR/2,1) + 15*min(median_volume_ratio/1.5,1) + 15*exp(-ln2*age_latest/126) + (10 if weekly else 5) + 10*min(confluence_count/3,1)`, capped 100, `N_eff = Σ exp(-ln2*age_i/126)`.
- `classify(zone)` — Candidate 1 touch; Confirmed ≥2; Strong ≥3 OR (2 touches AND median reaction ≥1 ATR AND any volume ratio ≥1.5); Broken; Role-reversed (break + retest + ≥0.5 ATR rejection).
- `breakout_confirmed(df, zone, atr)` — ALL of: close > `zone.upper + 0.25*atr`; volume/median(50d) ≥1.5; AND (2 consecutive closes above OR 1 close + 3 sessions with no close back inside). `failed_breakout` = close back inside within 3 sessions.
- `avwap(df, anchor_date)` — `Σ(TP·V)/ΣV`, TP=(H+L+C)/3.
- `volume_profile(df, atr)` — `bin_width = max(0.50*atr, 0.005*price)`; POC = max bin; HVN >p75; LVN <p25.
- `earnings_gaps(df, earnings_dates, atr)` — material when `|gap| ≥ max(1.0*atr, 0.03*prior_close)`; track day-1/5/20 hold ratios and fill status.
- `rank_levels(zones)` — `0.45*strength + 0.25*recency + 0.20*cross_lens_confluence + 0.10*liquidity_confidence`.
- `compute_levels(df_daily, earnings_dates, valuation_bands | None) -> LevelsOutput` (nearest 3 support + 3 resistance + MAs + AVWAPs + gaps).

- [ ] **Step 1: Write the failing tests** — synthetic OHLCV constructed so answers are known:

```python
def test_symmetric_pivot_k3_detects_local_max():
    # bar 10 is highest of bars 7..13 → pivot high at index 10, only after bar 13 closes
def test_zone_tolerance_formula():
    # atr=2, price=100 → tolerance = max(1.0, 0.75) = 1.0
def test_touches_5_sessions_apart():
def test_strength_formula_exact():
    # hand-computed: N_eff=2 fresh touches (age 0) → 30*min(2/4,1)=15; etc.
def test_breakout_requires_volume_and_close():
def test_gap_material_threshold():
    # atr=1, close=100: 2% gap not material; 3.1% gap material
```

- [ ] **Steps 2–5:** fail → implement → pass → commit `feat(wbj): important-levels engine (pivots, zones, strength, breakouts, AVWAP, gaps)`

### Task 13: Institutional valuation engine

**Files:**
- Create: `engine/wbj/engines/valuation_engine.py`
- Create: `engine/wbj/schemas/valuation.py`
- Test: `engine/tests/engines/test_valuation.py`

**Sources:** `Cerebro/special_sauces/INSTITUTIONAL_VALUATION_ENGINE.md` (read FULLY), `Cerebro/06_valuation_analysis/FORMULAS.md` (VAL-001…044).

**Interfaces (each returns `Value` or a small pydantic model; every function registered in the formula REGISTRY with its VAL id):**
- Normalization: `normalized_ebit(reported, unusual_gains, nonrecurring, misclassified)`; `rd_capitalize(rd_history, life)` → asset + amortization + adjusted EBIT; `lease_debt(commitments, pretax_kd)` (PV).
- ROIC block: `nopat(norm_ebit, tax_rate)`; `invested_capital(...)` financing view reconciled to operating view (warn if views differ >5%); `roic`, `spread(roic, wacc)`, `eva`, `incremental_roic(Δnopat, Δic)`, `fundamental_growth(reinvestment_rate, roic)`.
- Discount rate: `unlever_beta(b, tax, de)`, `relever_beta(bu, tax, target_de)`, `cost_of_equity(rf, beta, erp)`, `synthetic_kd(rf, interest_coverage)` (spread table from the engine doc), `wacc(e, d, ke, kd, tax)` + `wacc_sensitivity(w, bp=100)`.
- DCF: `fcff(ebit, tax, dna, capex, dnwc)`; `dcf_value(fcffs, wacc, terminal_growth)` → `{ev, pv_explicit, pv_terminal, terminal_share}` with warnings: `g >= wacc` → refuse (`NOT_MEANINGFUL`), `terminal_share > 0.75` → warning; `equity_bridge(ev, cash, nonop, debt, lease_debt, preferred, minority, pension)`; `per_share(equity, diluted)`.
- Cross-checks: `fcfe_value`, `economic_profit_value` (`EV = IC0 + PV(EP)`; assert reconciles to FCFF within 1%), `residual_income_value`, `justified_pe(g, roe, ke)`, `justified_ev_sales(margin, tax, g, roic, wacc)`, `hist_zscore(current, history)` (robust: `(x − median)/(1.4826*MAD)`).
- `reverse_dcf(price, shares, base_inputs)` — scipy `brentq`/`fsolve` for implied revenue CAGR at market price given margin, then implied margin given consensus CAGR (≥2 unknowns solved).
- `scenarios(bear, base, bull)` — each `{probability, growth, margin, wacc, tv_growth}` → values; probabilities must sum to 1.0; `weighted = Σ p_i·v_i`.
- `monte_carlo(base_inputs, n=2000, seed: int)` — rng = `default_rng(seed)`; triangular/bounded draws on growth, margin, wacc; returns `{p10, p25, median, p75, p90, seed, trials}`.
- `ensemble(model_values: list[tuple[value, reliability_weight]])` → weighted value + dispersion; `margin_of_safety(value, price)` = `(value − price)/value`.

- [ ] **Step 1: Write the failing tests** — closed-form cases:

```python
def test_gordon_terminal_math():
    # single FCFF=100 growing 2%, wacc 10% → TV = 100*1.02/0.08 = 1275
def test_g_greater_than_wacc_refused():
def test_terminal_share_warning_above_75pct():
def test_wacc():
    # E=800, D=200, Ke=10%, Kd=5%, tax=25% → 0.8*.10+0.2*.05*.75 = 8.75%
def test_equity_bridge_and_per_share():
def test_reverse_dcf_recovers_known_growth():
    # value a company with growth g*, then reverse from that price → recovers g* ±1e-4
def test_monte_carlo_deterministic_given_seed():
def test_scenario_probabilities_must_sum_to_1():
def test_economic_profit_reconciles_with_fcff():
```

- [ ] **Steps 2–5:** fail → implement → pass → commit `feat(wbj): institutional valuation engine (DCF, WACC, reverse DCF, scenarios, Monte Carlo)`

## Phase 4 — Specialists

Every specialist follows the same contract, so this pattern is stated once and **each task below must still be implemented fully and separately** (no shared shortcuts beyond `specialists/common.py`):

- Create `engine/wbj/specialists/common.py` in Task 14: `SpecialistOutput` pydantic envelope exactly per `Cerebro/shared/OUTPUT_CONTRACT.md` and the shared envelope in each OUTPUT_SCHEMA.md: `{agent_id, version: "2.0.0", status, security, knowledge_timestamp, category{max_points, awarded_points, score_10, confidence}, coverage, dimensions[], metrics[], mandatory_flags[], assumptions[], judgment_requests[], source_lineage[], validation_tests}` — plus agent-specific extension fields.
- Each specialist module exposes `run(packet: Packet, overlay: dict | None = None) -> <Agent>Output`.
- Judgment-only metrics: emit `Value.null(NOT_SCORABLE)` AND append a `JudgmentRequest{request_id, metric_id, question, schema_hint}`.
- Each task's Step 1 encodes that specialist's `VALIDATION_TESTS.md` rows as pytest cases and runs `run()` against the NVDA fixture packet asserting schema-validity + reproducible category math (`awarded_points == Σ dimension points` within 1e-6).

### Task 14: Financial specialist (15 pts) + common envelope

**Files:**
- Create: `engine/wbj/specialists/__init__.py`, `engine/wbj/specialists/common.py`, `engine/wbj/specialists/financial.py`
- Test: `engine/tests/specialists/test_financial.py`

**Sources:** `Cerebro/02_financial_analysis/FORMULAS.md` (FIN-001…033 with BAD/GOOD/EXCELLENT bands), `SCORING.md`, `DECISION_RULES.md`, `OUTPUT_SCHEMA.md`, `VALIDATION_TESTS.md`.

**Key implementation points:**
- Register FIN-001…033. Bands (verbatim examples — full set in FORMULAS.md): YoY revenue <0 BAD / 0–10% GOOD / >10% EXCELLENT; gross margin <20/20–40/>40%; op margin <10/10–20/>20%; net margin <5/5–10/>10%; FCF margin <0/0–10/>10%; current ratio <1.0/1.0–1.5/1.5–3.0 (>3 → idle-capital note); quick <0.7/0.7–1.0/>1.0; D/E >2.0/1.0–2.0/<1.0; interest coverage <1.5/1.5–3/>3; ROE <8/8–15/>15%; ROIC <8/8–15/>15%; ROA <3/3–8/>8%.
- **Core-27 diagnostic:** each valid metric BAD=0/GOOD=1/EXCELLENT=2 → `percent = points/(2*valid)*100`, `score10 = percent/10`; must reconcile with dimension-weighted score within **1.5 points** or emit warning.
- Dimensions (3 pts each): revenue quality & growth; EPS & FCF; margins; balance & liquidity; cash conversion & capital efficiency.
- Flags: `SOLVENCY_WARNING` (coverage <1.5×), negative equity → D/E `NOT_MEANINGFUL`; loss + negative FCF + external dependence → flag for Override 1.
- Extension fields: `core_27_metrics{valid_count, points, maximum_valid_points, percent, rows}, profitability_and_cash, balance_and_maturities, return_on_capital, dilution_and_sbc, mandatory_overrides, strongest_metric, weakest_metric`.

- [ ] **Step 1: Write the failing tests** — band edges (`yoy=0.10 → GOOD not EXCELLENT` if band is exclusive — check FORMULAS.md for edge convention and encode it), core-27 percent math, reconciliation warning, NVDA-fixture run produces schema-valid output, every row of `02_financial_analysis/VALIDATION_TESTS.md`.
- [ ] **Steps 2–5:** fail → implement → pass → commit `feat(wbj): financial specialist with core-27 diagnostic`

### Task 15: Business specialist (20 pts)

**Files:** Create `engine/wbj/specialists/business.py`; Test `engine/tests/specialists/test_business.py`
**Sources:** `Cerebro/01_business_analysis/` (all 7 files).

**Key points:** BUS-001…030 (ROIC/spread/EVA reuse Task 13 functions via REGISTRY); margin stability (5y stdev; range ≤3pp = moat signal); cumulative FCF conversion (ΣFCF5y/ΣNI5y); guidance accuracy `1−|actual−guide|/max(|guide|,floor)` clipped [0,1]; dimensions Moat 5 / Competitive 4 / Management 4 / Durability 4 / Customer economics 3. Caps: no positive spread → moat score ≤6; largest customer >30% → durability ≤6 + `CONCENTRATION_RED_FLAG`; diluted share CAGR >5% → `DILUTION_RED_FLAG`; ROIC<WACC → `VALUE_DESTRUCTION`. Wide-moat gate: spread ≥5pp in ≥4/5 years AND 5y op-margin range ≤5pp AND ≥2 quantitative moat effects AND no unresolved concentration — the "≥2 quantitative moat effects" and moat classification are **judgment requests**. Extension fields per OUTPUT_SCHEMA.md (`business_in_one_sentence`, `moat{...}`, `roic_history`, `three_thesis_killers` → judgment requests).

- [ ] **Steps 1–5:** encode VALIDATION_TESTS.md rows + cap tests (e.g., spread negative → moat capped) → fail → implement → pass → commit `feat(wbj): business specialist`

### Task 16: Market & Growth specialist (20 pts)

**Files:** Create `engine/wbj/specialists/market.py`; Test `engine/tests/specialists/test_market.py`
**Sources:** `Cerebro/03_market_analysis/` (all 7 files).

**Key points:** MKT-001…025; runway years `ln(target/current)/ln(1+g)`; revision breadth needs ≥5 estimates (else `NOT_SCORABLE` — estimates are on the prohibited-imputation list); earnings surprise uses consensus **frozen before release** (`estimates.provider_snapshot_before_earnings`); catalyst impact `P × Impact × EvidenceQuality × TimeDecay`, `TimeDecay = exp(−ln2·months_to_event/12)` — P/Impact/EvidenceQuality are judgment requests, TimeDecay computed; TAM tier confidence T1=100/T2=85/T3=70/T4=45/T5=0 — tier assignment is a judgment request; narrative-only catalysts cap dimension at 3; TAM confidence <60 caps TAM dimension at 6. Dimensions: TAM 5 / Revisions 4 / Catalysts 4 / Runway & share 4 / Operating leverage 3.

- [ ] **Steps 1–5:** TDD as above; test TimeDecay math (`months=12 → 0.5`) and the ≥5-estimate rule. Commit `feat(wbj): market & growth specialist`

### Task 17: Technical specialist (20 pts)

**Files:** Create `engine/wbj/specialists/technical.py`; Test `engine/tests/specialists/test_technical.py`
**Sources:** `Cerebro/04_technical_momentum/` (all 7 files); engines from Tasks 11–12.

**Key points:** dimensions Primary trend 4 (needs ≥200 sessions; no valid SMA200 → capped 6) / Relative strength 4 (percentile <30 → score 0–3; 30–70 → 4–6; >70 → 7–10) / Volume demand 3 (missing volume → capped 5; U/D >1.2 and CMF>0.10 good, CMF<−0.10 bad) / Earnings-gap behavior 3 (needs ≥4 valid events; >70% day-5/20 hold → 7–10) / Breakout & base quality 3 / Sector breadth & volatility 3. **Trend anchors verbatim** from DECISION_RULES.md: Close<SMA50<SMA200 & SMA200 slope <−1 ATR/50 sessions → 0–2; below SMA200 mixed → 3; within ±1 ATR of SMA200 & slope ±0.25 ATR → 4–5; above SMA200 mixed SMA50 → 6; Close>SMA50>SMA200 both slopes positive → 8; +ADX≥25 & 52w-position ≥0.80 → 9–10. Extension fields: `market_state, indicators, important_levels (from levels engine), breakouts_and_failures`.

- [ ] **Steps 1–5:** synthetic OHLCV per trend-anchor case; VALIDATION_TESTS.md rows; NVDA fixture. Commit `feat(wbj): technical & momentum specialist`

### Task 18: Risk specialist (15 pts, higher = safer)

**Files:** Create `engine/wbj/specialists/risk.py`; Test `engine/tests/specialists/test_risk.py`
**Sources:** `Cerebro/05_risk_analysis/` (all 7 files), `Perfil Inversionista/Victor Gonzalez.md` (profile-fit inputs).

**Key points:** RSK-001…035: annualized vol; downside deviation (MAR=0); beta 252/756d (needs benchmark; on prohibited list — never proxied); downside beta (min 30 down observations); max drawdown 1/3/5y + recovery time; historical VaR 95/99 (1d/10d) + CVaR (≥500 obs preferred, warning below); **Beneish M-score** `−4.84 + 0.920·DSRI + 0.528·GMI + 0.404·AQI + 0.892·SGI + 0.115·DEPI − 0.172·SGAI + 4.679·TATA − 0.327·LVGI` (flag > −1.78, screening only); **Altman Z″** `6.56·WC/TA + 3.26·RE/TA + 6.72·EBIT/TA + 1.05·BE/TL`; Piotroski F (0–9); cash runway `(cash + committed liquidity)/monthly burn`; maturity-wall coverage 12/24/36m. Dimensions: Financing 3 / Concentration 3 / Execution & earnings quality 3 / Regulatory-macro 2 / Valuation-compression 2 / Volatility & drawdown 2. Anchors: coverage <1.5/1.5–3/>3 (best >5); runway <12m/12–24m/>24m; 3y maxDD worse than −60% / −30–60% / better than −30%; downside beta >1.5/0.8–1.5/<0.8. Mandatory: `SOLVENCY_WARNING` text verbatim: "Operating earnings do not provide a comfortable interest buffer."; thesis killers (≥3) are judgment requests with computed early-warning metrics. Profile-fit section reads the Perfil file (position sizing vs 30–60% cap, horizon fit).

- [ ] **Steps 1–5:** Beneish/Altman closed-form tests with hand-built inputs; VaR on synthetic returns; VALIDATION_TESTS.md rows. Commit `feat(wbj): risk & resilience specialist`

### Task 19: Valuation specialist (10 pts)

**Files:** Create `engine/wbj/specialists/valuation.py`; Test `engine/tests/specialists/test_valuation.py`
**Sources:** `Cerebro/06_valuation_analysis/` (all 7 files); engine from Task 13.

**Key points:** model selection matrix (general non-financial → FCFF DCF + economic-profit cross-check; other adapters → `ADAPTER_UNSUPPORTED`); dimensions Growth-adjusted multiples 3 / Historical & peer comparison 2 (min 8 peers or history-only) / CF & earnings yield 2 / Fair value by scenarios 2 (requires WACC>g, diluted shares, scenario audit) / Margin of safety 1 (MOS 0–15% → 4–6; >15% → 7–10; low confidence caps 5). Scenario growth/margin defaults derived (hist CAGR, consensus, fundamental growth) — flagged as assumptions, agent-overridable. Extension fields: `model_selection, wacc{value, components, sensitivity}, scenarios[3], reverse_dcf{...}, model_cross_checks, fair_value_distribution{p10..p90}, reference_bands{bear, base, bull, mos_15, mos_25}`.

- [ ] **Steps 1–5:** TDD; verify scenarios sum to 1, reverse DCF present, VALIDATION_TESTS.md rows. Commit `feat(wbj): valuation specialist`

## Phase 5 — Assembly

### Task 20: Judgment overlay

**Files:**
- Create: `engine/wbj/overlay/__init__.py`, `engine/wbj/overlay/merge.py`, `engine/wbj/schemas/overlay.py`
- Test: `engine/tests/overlay/test_merge.py`

**Interfaces:**
- `JudgmentRequest{request_id, agent_id, metric_id, question, schema_hint}`; `collect_requests(outputs: list[SpecialistOutput]) -> list[JudgmentRequest]` → written as `judgment_requests.json`.
- `Judgment{request_id, answer: float | str | dict, evidence_class: EvidenceClass, source: str, rationale: str}`; `merge_overlay(outputs, judgments: list[Judgment]) -> list[SpecialistOutput]` — validates each judgment against its request's schema_hint, replaces the `NOT_SCORABLE` metric, **re-runs that specialist's scoring** (call `specialist.rescore(output)` — add this method to `common.py`), recomputes coverage/confidence, assigns new output hash. Unknown `request_id` → error; judgment without evidence_class/source → rejected.

- [ ] **Steps 1–5:** test round-trip: compute on fixture → collect requests → answer one (moat classification) → merge → coverage and category points increase, hash changes. Commit `feat(wbj): judgment overlay for agent-supplied qualitative scores`

### Task 21: Aggregation — overrides, gates, contradictions, price synthesis

**Files:**
- Create: `engine/wbj/aggregate/__init__.py`, `engine/wbj/aggregate/overrides.py`, `engine/wbj/aggregate/gates.py`, `engine/wbj/aggregate/contradiction.py`, `engine/wbj/aggregate/synthesis.py`, `engine/wbj/schemas/final_report.py`
- Test: `engine/tests/aggregate/test_gates.py`, `engine/tests/aggregate/test_synthesis.py`

**Sources:** `Cerebro/00_main_agent/SCORING_AND_GATES.md`, `CONTRADICTION_RESOLUTION.md`, `PRICE_LEVEL_SYNTHESIS.md`, `FINAL_REPORT_SCHEMA.md`, `VALIDATION_TESTS.md` (MAIN-001…), `Cerebro/shared/HANDOFF_CONTRACT.md`.

**Key implementation points:**
- `validate_handoff(output)` — reject if dimension points don't reproduce category points, scores lack formula IDs, missing knowledge_timestamp/confidence/coverage, levels lack touch dates/zone bounds.
- `apply_overrides(outputs) -> list[Override]` — all 7, exact conditions (capital dependence → cap Avoid/Speculative; ROIC<WACC → no Elite/Quality; coverage<1.5× → warning; Risk ≤4/15 → cap Speculative; Val ≤4 AND Tech ≤8 → Wait/Avoid; any category coverage <0.70 → gate-ineligible; unresolved facts-table conflict → suppress per-share).
- `apply_gates(raw_total, cats, confidences, overrides) -> ProfileResult` — table verbatim:
  - Momentum: raw ≥78 AND tech ≥17 AND market ≥16 AND (bus+fin) ≥28 AND risk ≥8 AND tech confidence ≥70
  - Quality: raw ≥80 AND bus ≥16 AND fin ≥11 AND risk ≥10 AND val ≥5 AND tech ≥12
  - Value: raw ≥75 AND val ≥8 AND bus ≥13 AND risk ≥10 AND tech ≥9
  - Conditional/Watch: raw ≥60, no major gate (record exact missing conditions)
  - Speculative: risk ≤4 OR total confidence <60 OR critical category incomplete OR runway <12m unfunded
  - Avoid/Wait: override demands it OR raw <50
- Descriptive bands: 90 Elite / 80 Strong / 70 Conditional / 60 Mixed / 50 Weak / <50 Avoid.
- `contradictions(cats)` — 6-row lookup (strong business + weak technical → "Quality watch/wait", etc.); never mutates scores.
- `synthesize_levels(technical_output, valuation_output, price, atr)` — 12 level classes; confluence when overlap within `max(0.50*atr, 0.0075*price)` and ≥1 technical; **never average technical with intrinsic**; `distance_percent`, `distance_atr` per side convention; language whitelist — the renderer may only use: reference, zone, confirmation, invalidation, scenario value.
- `FinalReport` pydantic model per FINAL_REPORT_SCHEMA.md (report_version 2.0.0, profile, category_scorecard, executive_thesis 7 sentences, important_levels, valuation_scenarios, reverse_dcf, thesis_killers, monitoring_triggers, missing_or_conflicted_data, audit{packet_hashes, formula_versions, validation_summary}).

- [ ] **Step 1: Write the failing tests** — the MAIN validation rows:

```python
def test_MAIN_002_raw_total():
    assert raw_total([16, 10.5, 18, 16, 9, 7]) == 76.5

def test_MAIN_003_risk_cap():
    # risk=4/15, total=90 → profile capped Speculative
def test_MAIN_007_low_coverage_blocks_gates():
    # any category coverage 0.65 → no gate passes
def test_MAIN_009_conflict_suppresses_per_share():
def test_momentum_gate_exact_thresholds():
    # raw 78, tech 17, market 16, bus+fin 28, risk 8, tech_conf 70 → passes
    # raw 77.9 → fails with reason "raw_total<78"
def test_synthesis_never_averages_technical_and_intrinsic():
def test_confluence_tolerance():
```

- [ ] **Steps 2–5:** fail → implement → pass → commit `feat(wbj): aggregation — overrides, gates, contradictions, price-level synthesis`

### Task 22: Charts

**Files:**
- Create: `engine/wbj/report/__init__.py`, `engine/wbj/report/charts.py`
- Test: `engine/tests/report/test_charts.py`

**Sources:** root `CLAUDE.md` §"Reglas de visualización" (4 rules), spec §9.

**Interfaces:** all functions take data + `out_path: Path`, save PNG (150 dpi, `Agg` backend), return the path:
- `price_levels_chart(df, levels, smas)` — price + zone bands (shaded rectangles, not single lines) + SMA50/200.
- `scenario_fan_chart(history, scenarios)` — history solid line; each scenario a **dotted** projected band; every scenario labeled on-chart with its growth/margin assumptions (rule 2); never a single projected line (rule 1).
- `scorecard_chart(category_points)` — horizontal bars vs max.
- `football_field_chart(reference_bands)` — valuation ranges per model/scenario + current price line.

- [ ] **Step 1: Write the failing tests** — files created and non-empty; scenario fan raises `ValueError("single-line projection prohibited")` if a scenario has no band width; assumption labels present (inspect figure texts for "growth=").
- [ ] **Steps 2–5:** fail → implement → pass → commit `feat(wbj): report charts per visualization rules`

### Task 23: Report renderer

**Files:**
- Create: `engine/wbj/report/render.py`
- Test: `engine/tests/report/test_render.py`

**Sources:** `Cerebro/00_main_agent/FINAL_REPORT_SCHEMA.md`, `Cerebro/examples/FINAL_REPORT_EXAMPLE.md`, root `CLAUDE.md` §"Contenido obligatorio del reporte final".

**Interfaces:** `render(final: FinalReport, charts: dict[str, Path], out_dir: Path) -> Path` — writes `report.json` (schema-validated) and `report.md` with required sections (English): Executive summary (7 sentences) · Research classification (invest/avoid; **if avoid → revisit date/event, required**) · Category scorecard · Price scenario ranges with assumptions (never single price) · Important levels table (Rank|Type|Lower/Center/Upper|Distance %|Distance ATR|Strength|Status|Confirmation|Invalidation) · Notable 13F holders & management track record · Insider activity (**only trades >$1,000,000 total flagged "significant"**) · Thesis killers & monitoring triggers · Profile fit ($25,000 capital; 30–60% max position; 3–5y horizon; US-only) · Missing/conflicted data · Audit appendix (packet hashes, formula versions, test summary). Insufficient data → the sentence "Insufficient data to reach an investment conclusion" and affected dimensions listed.

- [ ] **Step 1: Write the failing tests** — build a minimal `FinalReport` fixture; assert: all section headers present; avoid-classification without revisit date raises; insider filter ($999,999 not flagged, $1,000,001 flagged); forbidden words ("guaranteed target", "must hold", "certain floor") absent; report.json round-trips through the pydantic schema.
- [ ] **Steps 2–5:** fail → implement → pass → commit `feat(wbj): final report renderer (md + json)`

### Task 24: CLI wiring + end-to-end golden test

**Files:**
- Modify: `engine/wbj/cli.py`
- Create: `engine/wbj/pipeline.py`
- Test: `engine/tests/test_end_to_end.py`
- Fixture: `engine/tests/fixtures/golden/NVDA_report.json`

**Interfaces:**
- `pipeline.py`: `stage_fetch(ticker, settings)`, `stage_packet(ticker, settings) -> Packet`, `stage_compute(packet) -> list[SpecialistOutput]` (writes 6 output JSONs + `judgment_requests.json` to `engine/cache/<T>/artifacts/`), `stage_aggregate(outputs, overlay_path: Path | None) -> FinalReport`, `stage_report(final, out_dir)`, `run_all(ticker, settings, overlay_path=None)`.
- CLI: each stub from Task 1 now calls its stage; `aggregate` gains `--overlay path.json`; `analyze` runs all; `--offline` flag forces cache-only (used by tests).

- [ ] **Step 1: Write the failing test**

```python
def test_analyze_offline_end_to_end(tmp_path, fixture_cache):
    # fixture_cache = engine/tests/fixtures copied into a temp cache dir
    final = run_all("NVDA", settings_for(tmp_path, cache=fixture_cache), offline=True)
    assert final.report_version == "2.0.0"
    assert 0 <= final.profile.raw_score <= 100
    out = tmp_path / "Reportes" / "NVDA"
    day_dir = next(out.iterdir())
    assert (day_dir / "report.md").exists() and (day_dir / "report.json").exists()
    assert len(list((day_dir / "charts").iterdir())) >= 3

def test_golden_report_stable():
    final = run_all("NVDA", ..., offline=True)
    golden = json.loads(GOLDEN.read_text())
    assert normalize(final.model_dump()) == normalize(golden)  # normalize strips timestamps/hashes
```

- [ ] **Step 2: Run** → FAIL
- [ ] **Step 3: Implement** `pipeline.py` + CLI wiring; generate the golden file once (`python -m wbj.cli analyze NVDA --offline`), inspect it manually for sanity (scores reproduce, levels present), commit it as fixture.
- [ ] **Step 4: Run full suite** `cd engine && python -m pytest -v` → ALL PASS
- [ ] **Step 5: Commit** `feat(wbj): CLI pipeline wiring + offline end-to-end golden test`

### Task 25: Live smoke test + docs

**Files:**
- Create: `engine/README.md`
- Modify: root `README.md` (add "Engine" section)

- [ ] **Step 1:** With Victor's keys in `API/.env`, run `wbj analyze NVDA` live. Verify: report renders, no key material in any output file (`grep -r "$FMP_API_KEY" Reportes/ engine/cache/` → empty).
- [ ] **Step 2:** Fix any live-API field mismatches discovered (each fix gets its own test using a captured fixture).
- [ ] **Step 3:** Write `engine/README.md`: install, key setup, stage-by-stage usage, overlay workflow for the Claude sub-agents, offline mode.
- [ ] **Step 4:** `python -m pytest -v` → ALL PASS.
- [ ] **Step 5: Commit** `docs(wbj): engine README and live smoke-test fixes`

---

## Self-review notes

- **Spec coverage:** §2 stages → Tasks 1, 24; §4 data layer → 6–10; §5 core → 2–5; §6 engines/specialists → 11–19; §7 overlay → 20; §8 aggregation → 21; §9 report → 22–23; §10 error handling → Tasks 2, 6, 10; §11 testing → every task + 24; §12 exclusions honored (no Robinhood/real-options/adapters tasks).
- **Type consistency:** `Value`/`NullState` (Task 2) used by 3–21; `SpecialistOutput` (Task 14) consumed by 20–21; `FinalReport` (Task 21) consumed by 23–24; `Zone` (Task 12) consumed by 17 and 21.
- **Deliberate non-placeholder references:** formula bodies for the full BUS/FIN/MKT/TECH/RSK/VAL sets live in the Cerebro `FORMULAS.md` files — each task names its exact source files and the implementer transcribes them; the plan embeds every structural constant (weights, gates, caps, engine parameters) so drift is detectable in review.
