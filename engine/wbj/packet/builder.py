"""Packet builder: assembles the pydantic `Packet` handed to the six
Cerebro specialists from raw provider payloads.

Implements the first three phases of `Cerebro/00_main_agent/ORCHESTRATION.md`:

- Phase 0 (freeze the analysis clock): `knowledge_timestamp` is always the
  caller-supplied `now` — this module never calls `datetime.now()` itself,
  so packet construction is deterministic and testable.
- Phase 1 (validate common data + common facts table): hard-rejects packets
  missing timestamps, currency, any diluted-share source, or a full
  252-session daily OHLCV history; reconciles FMP vs EDGAR for
  revenue/diluted_shares/cash/total_debt via `wbj.packet.reconcile`.
- Phase 3 (freeze packets): `packet_hash` is a sha256 of the canonical
  (sorted-key, compact-separator) JSON of the packet, excluding the hash
  field itself, so a rebuild from identical inputs reproduces the same
  hash and any single changed input changes it.

`Providers` is a plain container of the four provider objects
(`wbj.providers.{fmp,edgar,finnhub,fred}` or their fakes in tests) that
`build_packet` reads from — it doesn't otherwise depend on their concrete
classes, only on the method surface documented on each fake in
`engine/tests/fixtures/packet/make_packet_fixture.py`.
"""

from __future__ import annotations

import hashlib
import json
import logging
from dataclasses import dataclass
from datetime import date, datetime, timezone
from typing import Any

from wbj.core.nullstates import EvidenceClass, NullState, Value
from wbj.packet.reconcile import reconcile
from wbj.packet.staleness import staleness_state
from wbj.schemas.packet import AnalysisMeta, MarketData, OHLCVRow, Packet, Security

logger = logging.getLogger(__name__)

# Minimum daily OHLCV sessions required to accept a packet (task-10 brief).
_MIN_DAILY_SESSIONS = 252

# Benchmark/sector index ticker used for relative-strength and beta/
# correlation metrics (technical.py's TECH-RS-011/RSS-012, risk.py's
# RSK-BETA-003/DBETA-004/CORR-005). SPY (S&P 500 ETF) is used for both the
# broad-market benchmark and, absent a per-sector ETF mapping, as the
# sector proxy too -- a per-GICS-sector ETF map (XLK, XLF, XLE, ...) is a
# reasonable future enhancement but not required for these metrics to
# score: technical.py/risk.py only need *a* liquid, long-history index
# series aligned to the stock's trading calendar, not a sector-precise one.
_BENCHMARK_TICKER = "SPY"

# FMP GICS-style sector name -> SPDR sector ETF, used as the sector series for
# market.py's sector-relative-strength (MKT-RSG-024). Completes the per-sector
# ETF map the original builder left as a TODO. Unknown/unmapped sectors fall
# back to the broad benchmark so the metric degrades gracefully.
_SECTOR_ETF = {
    "Technology": "XLK",
    "Financial Services": "XLF", "Financials": "XLF",
    "Energy": "XLE",
    "Healthcare": "XLV", "Health Care": "XLV",
    "Consumer Cyclical": "XLY",
    "Consumer Defensive": "XLP",
    "Industrials": "XLI",
    "Basic Materials": "XLB", "Materials": "XLB",
    "Communication Services": "XLC",
    "Utilities": "XLU",
    "Real Estate": "XLRE",
}


#: Annual history depth. DATASET.md: "5 years; preferred 10".
_ANNUAL_HISTORY_YEARS = 11


def _sector_etf_for(profile: dict) -> str:
    """SPDR sector ETF for the company's GICS sector, or SPY if unmapped."""
    return _SECTOR_ETF.get((profile.get("sector") or "").strip(), _BENCHMARK_TICKER)


class PacketRejected(Exception):
    """Raised when a packet fails one of the Phase 0/1 hard-reject checks:
    missing timestamps, missing currency, no diluted share count from any
    source, or fewer than 252 daily sessions."""


@dataclass
class Providers:
    """The four data sources `build_packet` reads from.

    Duck-typed against `wbj.providers.{fmp,edgar,finnhub,fred}`'s public
    method surface (see the Fake* classes in
    `tests/fixtures/packet/make_packet_fixture.py` for the exact contract
    this module relies on).
    """

    fmp: Any
    edgar: Any
    finnhub: Any
    fred: Any


# --- canonical field mapping -------------------------------------------
#
# Raw FMP statement keys -> Cerebro/shared/DATA_DICTIONARY.md canonical
# names. Unmapped keys (date, symbol, period, calendarYear, acceptedDate,
# eps, ...) pass through unchanged since the data dictionary doesn't fix a
# canonical name for them. Mapped keys are *replaced*, not duplicated, so
# no raw FMP key survives into `packet.fundamentals`.
CANONICAL_FIELD_MAP: dict[str, str] = {
    "revenue": "revenue",
    "costOfRevenue": "cogs",
    "grossProfit": "gross_profit",
    "operatingIncome": "ebit",
    "incomeBeforeTax": "income_before_tax",
    "incomeTaxExpense": "income_tax_expense",
    "netIncome": "net_income",
    "weightedAverageShsOutDil": "diluted_shares",
    "weightedAverageShsOut": "basic_shares",
    "cashAndCashEquivalents": "cash",
    # VAL-NWC-006 has to strip this from operating current assets:
    # DATA_DICTIONARY.md defines invested capital against "debt plus equity
    # minus excess cash", and marketable securities are excess cash parked in
    # a current account, not working capital. AAPL FY2025 carries 18.8B of it.
    "shortTermInvestments": "short_term_investments",
    "netReceivables": "net_receivables",
    "inventory": "inventory",
    "totalCurrentAssets": "total_current_assets",
    "totalCurrentLiabilities": "total_current_liabilities",
    "shortTermDebt": "short_term_debt",
    "longTermDebt": "long_term_debt",
    "totalDebt": "total_debt",
    "totalAssets": "total_assets",
    "totalLiabilities": "total_liabilities",
    "totalStockholdersEquity": "total_equity",
    "netCashProvidedByOperatingActivities": "operating_cash_flow",
    "capitalExpenditure": "capex",
    "freeCashFlow": "fcf",
    "acquisitionsNet": "acquisitions_net",
    # Los nombres de `/stable` van PRIMERO; los de la v3 se conservan para no
    # romper un payload cacheado. FMP renombro los dos, y como el mapa fallaba
    # en silencio --la clave no aparece, el campo queda ausente-- nadie se
    # entero: se descubrio comparando el mapa contra la respuesta real.
    #
    # `netDebtIssuance` es el nombre nuevo de `debtRepayment` Y comparte su
    # convencion: negativo = deuda neta repagada, positivo = deuda nueva. Que
    # es exactamente lo que `financial.py` documenta al leerlo, asi que la
    # semantica no cambia. NO se usa `longTermNetDebtIssuance` ni
    # `shortTermNetDebtIssuance`: son los dos tramos por separado, no el neto.
    "netDebtIssuance": "debt_repayment",
    "debtRepayment": "debt_repayment",
    "commonStockRepurchased": "common_stock_repurchased",
    # `netDividendsPaid` incluye los preferentes; `commonDividendsPaid` solo
    # los comunes. FIN-CF-016 suma "usos de caja", y un dividendo preferente
    # es caja que sale igual, asi que va el neto.
    "netDividendsPaid": "dividends_paid",
    "dividendsPaid": "dividends_paid",
    "stockBasedCompensation": "stock_based_compensation",
    # --- additional statement fields (task: fuller FMP statement mapping) --
    # These are genuine FMP statement data (not judgment/qualitative
    # inputs) that several specialist formulas are documented to want from
    # `Packet.fundamentals` (see e.g. risk.py's RSK-ICOV-011/AQI-023/
    # DEPI-025/SGAI-026/ALT-030 and financial.py's FIN-BS-020/DX-028/
    # DX-031 module comments: "not part of Packet.fundamentals"). As of
    # this change the current specialist code for those specific metrics
    # reads its inputs from an `overlay` dict (a separate judgment-layer
    # input, Task 20) rather than `Packet.fundamentals`, or is hardcoded
    # to MISSING pending a future code change -- see the packet-builder
    # commit message / task report for the exact list. Mapping the raw
    # data in here regardless: (a) is what those docstrings ask for, (b)
    # is immediately useful to any other row that already reads
    # `packet.fundamentals.annual/quarterly` directly, and (c) is the
    # natural source for a future overlay-from-packet default.
    "interestExpense": "interest_expense",
    "ebitda": "ebitda",
    "depreciationAndAmortization": "depreciation_and_amortization",
    "sellingGeneralAndAdministrativeExpenses": "sga",
    "researchAndDevelopmentExpenses": "rnd_expense",
    "accountPayables": "accounts_payable",
    "retainedEarnings": "retained_earnings",
    # FMP's /stable/ balance-sheet statement only exposes *net* PP&E, not
    # gross -- classic Beneish AQI/DEPI use gross PP&E, so this is a
    # documented, honest proxy rather than a true substitute; named
    # `ppe_net` (not `ppe`) so it's never silently mistaken for the gross
    # figure those formulas actually call for.
    "propertyPlantEquipmentNet": "ppe_net",
}


# INDUSTRY_ADAPTERS.md opens with "Select an adapter before scoring", and
# every specialist already reads `analysis.industry_adapter` — business
# and financial warn and cut model-fit confidence on a non-default one,
# valuation refuses outright rather than run a FCFF DCF on a bank. The
# consumers were all wired; the producer was hardcoded to
# "default_nonfinancial", so a bank, a REIT, a biotech and a SaaS
# company were every one of them scored as a generic operating company.
#
# Matching is on FMP's `industry` first (specific) then `sector`. Where
# the call is genuinely ambiguous the choice leans toward *applying* an
# adapter: marking a subscription business as generic would let it skip
# the customer-economics dimension entirely, which flatters it, while
# the reverse only costs coverage on a dimension it should be answering.
#: Industrias que el substring de arriba capturaria por accidente.
#:
#: Se consultan ANTES que `_ADAPTER_BY_INDUSTRY` y ganan. Cada una es un caso
#: donde la industria ENTERA esta mal enrutada -- no una empresa dudosa:
#:
#: - "Insurance - Brokers" (MMC, AJG, BRO): un corredor no suscribe ningun
#:   riesgo. Cobra comision y lleva deuda corriente, asi que su ROIC y su
#:   cobertura de intereses significan exactamente lo que significan en
#:   cualquier empresa de servicios. INDUSTRY_ADAPTERS.md le pide a un
#:   asegurador "combined ratio, reserve development, solvency capital": un
#:   corredor no tiene ninguna de las tres.
#: - "Real Estate - Services/Development/General/Diversified" (CBRE, JLL,
#:   HHH, JOE): la regla por SECTOR mandaba todo "real estate" a `reits`, y
#:   con eso a una corredora inmobiliaria se le sustituia el EPS por FFO/AFFO
#:   que no reporta ni tiene por que reportar. En la taxonomia de FMP un REIT
#:   siempre se llama "REIT - X"; nada mas en ese sector lo es.
#:
#: Los dos casos mueven empresas DESDE un adaptador que reemplaza el modelo
#: HACIA el generico, que devuelve metricas en vez de suprimirlas.
_ADAPTER_EXCEPTIONS: tuple[tuple[str, str], ...] = (
    ("insurance - brokers", "default_nonfinancial"),
    ("real estate - services", "default_nonfinancial"),
    ("real estate - development", "default_nonfinancial"),
    ("real estate - general", "default_nonfinancial"),
    ("real estate - diversified", "default_nonfinancial"),
)

_ADAPTER_BY_INDUSTRY: tuple[tuple[str, str], ...] = (
    ("reit", "reits"),
    ("insurance", "insurers"),
    ("bank", "banks"),
    ("biotechnology", "biotech"),
    ("software", "saas_subscriptions"),
    ("information technology services", "saas_subscriptions"),
    ("oil & gas", "commodities_cyclicals"),
    ("metals", "commodities_cyclicals"),
    ("mining", "commodities_cyclicals"),
    ("coal", "commodities_cyclicals"),
    ("steel", "commodities_cyclicals"),
    ("chemicals", "commodities_cyclicals"),
)
# No blanket "financial services" -> banks fallback. That sector holds
# payment networks, exchanges, asset managers and brokers, none of which
# take deposits or carry a loan book: it mapped Visa to the bank adapter,
# which would have had valuation refuse to value it and cut business's
# model-fit confidence to 40. Only an industry that actually says "bank"
# gets the bank adapter.
_ADAPTER_BY_SECTOR: tuple[tuple[str, str], ...] = (
    ("real estate", "reits"),
    ("basic materials", "commodities_cyclicals"),
    ("energy", "commodities_cyclicals"),
)


#: El SIC que el emisor declara ante la SEC -> adaptador, para las familias
#: donde se verifico que el string comercial se equivoca.
#:
#: `SOURCE_HIERARCHY.md` pone en el rango 1 la "regulatory filing and filing
#: acceptance metadata"; una etiqueta de industria de un proveedor de datos es
#: rango 5. Cuando discrepan, el Cerebro ya dijo cual gana, asi que esto no es
#: un criterio nuevo: es aplicar el orden que ya estaba escrito.
#:
#: Medido contra la SEC, el SIC separa exactamente lo que el string no separaba:
#:
#:   COF  6021 National Commercial Banks   <- FMP decia "Credit Services"
#:   ALLY 6022 State Commercial Banks      <- FMP decia "Credit Services"
#:   V/MA 7389 Services-Business Services  <- correctamente fuera
#:   AJG  6411 Insurance Agents, Brokers   <- distinto de PGR 6331 (underwriter)
#:   CBRE 6500 Real Estate                 <- distinto de O 6798 (REIT)
#:
#: Los dos ultimos confirman con autoridad de rango 1 las excepciones que
#: `_ADAPTER_EXCEPTIONS` habia deducido: la propia SEC le da codigo distinto a
#: un corredor que a un asegurador, y a una inmobiliaria que a un REIT.
#:
#: Deliberadamente NO cubre biotech/software/commodities. Ahi el string de FMP
#: acierta y el SIC es mas turbio -- 8731 "Commercial Physical & Biological
#: Research" mezcla biotecnologicas con laboratorios de ensayo y consultoras de
#: ingenieria. Se cambia donde se midio el fallo, no donde se puede.
_ADAPTER_BY_SIC: tuple[tuple[range, str], ...] = (
    # 6021 nacional, 6022 estatal, 6035/6036 cajas de ahorro.
    #
    # Medido sobre las 290 acciones del sector financiero del mercado, y son
    # el unico grupo homogeneo: 6021 37/40 por debajo de 1,5x de cobertura,
    # 6022 44/48, 6035 6/6, 6036 2/2, con medianas de 0,73 / 0,82 / 0,44 /
    # 0,37. No es que esos bancos esten mal -- es que la formula no mide nada
    # en ellos. El rango cubre exactamente esos cuatro.
    (range(6020, 6037), "banks"),
    # 6211 "Security Brokers, Dealers & Flotation Companies": la casa de
    # bolsa. NO va con los bancos, y el dato es la razon -- 7 de 17 por
    # debajo de 1,5x, mediana 2,47: el codigo mezcla mesas de trading (GS
    # 0,33, MS 0,45) con corredores (IBKR 2,09, SCHW 3,05) y gestoras (BLK
    # 11,20). Tratarlo como un banco suprimiria una metrica que funciona en
    # la mayoria, que es el error que ya se cometio una vez con las
    # aseguradoras.
    #
    # Va a un adaptador que a proposito NO se registra en ninguno de los tres
    # tratamientos de INDUSTRY_ADAPTERS.md -- ver `broker_dealers` en
    # `core/adapters.py`.
    (range(6211, 6212), "broker_dealers"),
    # Aseguradoras que SUSCRIBEN: vida, salud, incendio/maritimo/danos,
    # fianzas, titulos. 6411 (agentes y corredores) queda fuera a proposito.
    (range(6310, 6400), "insurers"),
    (range(6798, 6799), "reits"),
)


def _adapter_por_sic(sic: str | None) -> str | None:
    """El adaptador que manda el SIC, o None si no lo cubre."""
    if not sic:
        return None
    try:
        n = int(str(sic).strip())
    except (TypeError, ValueError):
        return None
    if n == 6411:            # agentes y corredores de seguros: no suscriben
        return "default_nonfinancial"
    for tramo, adapter in _ADAPTER_BY_SIC:
        if n in tramo:
            return adapter
    return None


def _industry_adapter_for(profile: dict, sic: str | None = None) -> str:
    """Victor's adapter for this security, from its reported classification.

    Returns `default_nonfinancial` when nothing matches, which is the
    documented default for "non-financial operating companies".

    `sic` es opcional a proposito: si EDGAR no contesta, no hay CIK, o el
    emisor es extranjero, se cae al string comercial de siempre en vez de
    romper el packet. Una clasificacion peor es mucho mejor que ninguna.
    """
    por_sic = _adapter_por_sic(sic)
    if por_sic is not None:
        return por_sic
    industry = (profile.get("industry") or "").lower()
    sector = (profile.get("sector") or "").lower()
    for needle, adapter in _ADAPTER_EXCEPTIONS:
        if needle in industry:
            return adapter
    for needle, adapter in _ADAPTER_BY_INDUSTRY:
        if needle in industry:
            return adapter
    for needle, adapter in _ADAPTER_BY_SECTOR:
        if needle in sector:
            return adapter
    return "default_nonfinancial"


def _map_statement_row(row: dict) -> dict:
    """Apply `CANONICAL_FIELD_MAP` to one raw FMP statement row."""
    return {CANONICAL_FIELD_MAP.get(k, k): v for k, v in row.items()}


#: FMP reuses two balance-sheet account names inside the *cash-flow*
#: statement, where they mean the period's CHANGE in that account rather than
#: its closing balance. Merging by dict unpacking let the change win on both,
#: so `inventory` reached every consumer as a working-capital movement wearing
#: the name of a balance -- for AAPL FY2025, 1,400M (the movement) in place of
#: 5,718M (the balance), which is what FIN-BS-018's quick ratio and
#: FIN-DX-031's cash-conversion cycle actually require.
#:
#: DATA_POLICY.md forbids exactly this: "Do not silently change sign
#: conventions, currency, tax treatment, or denominator definitions."
#: Renaming keeps both figures -- the balance under its own name, the movement
#: under an explicit one -- rather than picking a winner and losing the other.
_CASHFLOW_DELTA_RENAMES: dict[str, str] = {
    "inventory": "inventoryChange",
    "accountsReceivables": "accountsReceivablesChange",
}


def _unreported_interest_to_absent(row: dict) -> dict:
    """A zero interest expense on a debt-carrying company is absence, not zero.

    FMP reports `interestExpense: 0` for AAPL FY2025 and FY2024 -- along with
    every other interest field -- because Apple stopped disclosing the line
    separately after FY2023; EDGAR's `InterestExpense` series stops in the
    same year, so no source has it. The figure is unreported, not zero, and
    Apple carried $112B of debt in that year.

    MISSING_DATA_POLICY.md step 2 decides it: "Is the source expected to
    report it? If yes but absent, use `MISSING`." A company carrying debt is
    expected to report its interest cost, so an absent figure is MISSING. A
    genuinely debt-free company is not expected to report one, and its zero
    stands.

    Left as zero, the claim propagated as fact: FIN-BS-020, RSK-ICOV-011 and
    RSK-FCC-012 each returned NOT_MEANINGFUL citing
    "ZERO_INTEREST_EXPENSE" -- telling the reader Apple has no interest cost
    rather than that nobody reported one. Under CLAUDE.md's "sin evidencia,
    no hay numero", the absence has to propagate instead.
    """
    if row.get("interest_expense") != 0:
        return row
    debt = row.get("total_debt")
    if debt in (None, 0):
        return row
    return {**row, "interest_expense": None}


def _merge_statement_period(income_row: dict, balance_row: dict, cashflow_row: dict) -> dict:
    """Merge one fiscal period's income/balance/cashflow rows into a single
    canonical-name record.

    Cash-flow rows are renamed first (see `_CASHFLOW_DELTA_RENAMES`) so a
    period movement cannot occupy a balance's field name.
    """
    cashflow_row = {_CASHFLOW_DELTA_RENAMES.get(k, k): v
                    for k, v in (cashflow_row or {}).items()}
    merged_raw = {**income_row, **balance_row, **cashflow_row}
    return _unreported_interest_to_absent(_map_statement_row(merged_raw))


def _merge_statements(income: list[dict], balance: list[dict], cashflow: list[dict]) -> list[dict]:
    return [
        _merge_statement_period(inc, bal, cf)
        for inc, bal, cf in zip(income or [], balance or [], cashflow or [])
    ]


# --- FMP-side Value extraction -------------------------------------------


def _fmp_value(row: dict | None, key: str, unit: str) -> Value:
    if not row or row.get(key) is None:
        return Value.null(NullState.MISSING, unit=unit, source_name="FMP")
    return Value.of(
        row[key],
        unit=unit,
        source_name="FMP",
        period=row.get("date"),
        as_of=row.get("acceptedDate"),
        evidence_class=EvidenceClass.R,
    )


def _fmp_diluted_shares(income_annual: list[dict], income_quarterly: list[dict]) -> Value:
    for row in (income_annual[:1] or []) + (income_quarterly[:1] or []):
        val = _fmp_value(row, "weightedAverageShsOutDil", "shares")
        if val.is_valid:
            return val
    return Value.null(NullState.MISSING, unit="shares", source_name="FMP")


# --- EDGAR-side Value extraction -----------------------------------------


def _edgar_entries(companyfacts: dict, taxonomy: str, tag: str, unit: str) -> list[dict]:
    return (
        (companyfacts or {})
        .get("facts", {})
        .get(taxonomy, {})
        .get(tag, {})
        .get("units", {})
        .get(unit, [])
    )


def _restatement_rank(entry: dict) -> tuple[str, str]:
    """Sort key that puts the latest official restatement last.

    SOURCE_HIERARCHY.md conflict resolution step 2 prefers "a later official
    restatement over the original filing". `filed` is the acceptance date of
    the filing the fact came from; `accn` breaks a same-day tie
    deterministically so a rebuild from identical inputs picks the same fact.
    """
    return (entry.get("filed") or "", entry.get("accn") or "")


def _edgar_value_at(
    companyfacts: dict, taxonomy: str, tag: str, unit: str, target_date: str | None,
    *, require_period: bool = False,
) -> Value:
    """Latest EDGAR XBRL fact for `tag`, preferring the entry whose `end`
    matches `target_date` (the FMP statement period it's being reconciled
    against) and falling back to the most recent entry otherwise."""
    entries = _edgar_entries(companyfacts, taxonomy, tag, unit)
    if not entries:
        return Value.null(NullState.MISSING, unit=unit, source_name="EDGAR")

    # A flow fact carries `start` as well as `end`, and several periods
    # can share one end date: Salesforce files a half-year cumulative and
    # a standalone quarter both ending 2025-07-31. Matching on `end`
    # alone therefore picks whichever came first in the payload, which is
    # a coin flip between an annual figure and a quarterly one — the kind
    # of mismatch `reconcile` then reports as a 395% source conflict.
    #
    # `fp`/`form` say plainly which is which, so an annual reconciliation
    # takes the annual fact. Balance-sheet facts have no `start` and are
    # unaffected.
    candidates = [e for e in entries if e.get("end") == target_date]
    annual = [e for e in candidates
              if e.get("fp") == "FY" or (e.get("form") or "").startswith("10-K")]
    # SOURCE_HIERARCHY.md conflict resolution step 2: "Prefer a later
    # official restatement over the original filing" (QA_CHECKLIST.md:
    # "Historical statements use latest valid restatement for current
    # analysis"). One period appears in companyfacts once per filing that
    # reports it -- the 10-K that first stated it, then every later filing
    # that repeats or restates it in a comparative column -- listed in
    # ascending `filed` order. Taking `[0]` therefore handed back the
    # superseded original whenever a figure had been restated. Take the most
    # recently filed entry instead.
    pool = annual or candidates
    entry = max(pool, key=_restatement_rank) if pool else None
    if entry is None:
        if require_period:
            # The caller is choosing between tags and needs to know this
            # one does not cover the period, rather than being handed the
            # newest fact the tag happens to hold. Reading a "some value
            # is better than none" fallback as a match is how NVIDIA's
            # reconciliation went from exact agreement to 702% apart: the
            # ASC 606 tag it does not use for the year returned a stale
            # quarterly figure instead of deferring to the tag it does.
            return Value.null(NullState.MISSING, unit=unit, source_name="EDGAR")
        # Same restatement preference for the no-period-match fallback: the
        # newest period, and within it the most recently filed statement of it.
        entry = max(entries, key=lambda e: (e.get("end", ""), *_restatement_rank(e)))
    return Value.of(
        entry["val"],
        unit=unit,
        source_name="EDGAR",
        source_locator=entry.get("accn"),
        period=entry.get("end"),
        as_of=entry.get("filed"),
        evidence_class=EvidenceClass.R,
    )


#: Lease-liability tags summed into the EDGAR debt figure so it measures
#: the same concept the provider's `totalDebt` does. Both kinds are
#: reported: NVIDIA carries its $2.944B under the *operating* tags, and
#: under ASC 842 an operating lease liability sits on the balance sheet
#: like any other obligation — which is what DATA_DICTIONARY.md means by
#: "debt-like obligations". Current and noncurrent halves are listed
#: separately because a company may report either alone.
_EDGAR_LEASE_LIABILITY_TAGS = (
    "FinanceLeaseLiabilityNoncurrent", "FinanceLeaseLiabilityCurrent",
    "OperatingLeaseLiabilityNoncurrent", "OperatingLeaseLiabilityCurrent",
    "CapitalLeaseObligationsNoncurrent", "CapitalLeaseObligationsCurrent",
)


#: Revenue tags in preference order. ASC 606 filers report under
#: `RevenueFromContractWithCustomerExcludingAssessedTax`; `Revenues` is
#: the older element and, where a filer tags both, often carries a
#: narrower figure. Reading `Revenues` alone gave Salesforce $8.39B
#: against the $41.525B it actually reports for the same fiscal year.
_EDGAR_REVENUE_TAGS = (
    "RevenueFromContractWithCustomerExcludingAssessedTax",
    "RevenueFromContractWithCustomerIncludingAssessedTax",
    "Revenues",
    "SalesRevenueNet",
)


def _edgar_first_available(
    companyfacts: dict, taxonomy: str, tags: tuple[str, ...], unit: str,
    target_date: str | None, *, require_period: bool = False,
) -> Value:
    """The first tag in `tags` that reports a value for the period.

    Filers do not agree on which element to use, so a single hardcoded
    tag silently mismatches whole industries rather than failing loudly.

    `require_period=True` drops the permissive second pass. Callers that
    reconcile against another source can absorb an off-period figure -- that is
    what `reconcile` is for -- but a caller feeding a metric directly cannot:
    the fallback returns the newest fact the tag happens to hold, which for a
    tag the filer stopped using is a figure from years ago wearing today's date.
    Measured on live data: Walmart's `Depreciation` newest entry is 2013-01-31
    and NVIDIA's `ProceedsFromIssuanceOfCommonStock` is 2016-07-31.
    """
    # First pass: a tag that actually covers the period being reconciled.
    for tag in tags:
        value = _edgar_value_at(companyfacts, taxonomy, tag, unit, target_date,
                                require_period=True)
        if not value.is_null:
            return value
    if require_period:
        return Value.null(NullState.MISSING, unit=unit, source_name="EDGAR")
    # Second pass: no tag covers it, so take the best available and let
    # `reconcile` judge the result rather than silently reporting nothing.
    for tag in tags:
        value = _edgar_value_at(companyfacts, taxonomy, tag, unit, target_date)
        if not value.is_null:
            return value
    return Value.null(NullState.MISSING, unit=unit, source_name="EDGAR")


def _edgar_total_debt(companyfacts: dict, target_date: str | None) -> Value:
    """EDGAR has no single "total debt" tag; sum the noncurrent + current
    debt tags for the target period. If either half is unavailable, the
    sum isn't safe to report — return MISSING rather than silently
    treating a missing half as zero.

    Finance leases are added because the comparison target includes them
    and DATA_DICTIONARY.md defines the concept as "Interest-bearing debt
    plus debt-like obligations". Omitting them made this an
    apples-to-oranges difference that `reconcile` then reported as a
    source conflict: NVIDIA's borrowings tie out to the cent
    ($8.468B on both sides), and the entire 34.8% "disagreement" was its
    $2.944B of capitalised leases, counted by the provider and not by
    these two tags. A conflict flag on that would withhold invested
    capital, ROIC, spread and EVA over a definitional gap rather than a
    factual one — which is what SOURCE_HIERARCHY.md's first
    conflict-resolution step ("Verify period, currency, scale ... and
    continuing-operations treatment") exists to prevent.

    Leases are treated as optional: a company reporting none is not a
    company whose debt is unknown.
    """
    # `require_period=True` on every component. `reconcile`'s contract is
    # explicit -- "Inputs must already be period/unit/currency-aligned by the
    # caller ... `reconcile` only adjudicates numeric disagreement between
    # aligned values" -- and this caller was not honouring it: the permissive
    # fallback returns the newest fact a tag holds, so Exxon's
    # `LongTermDebtNoncurrent` came back from 2017-12-31 and Coca-Cola's from a
    # 2024 quarter, then got summed with current-period components.
    #
    # Being strict here does not lose the field: with EDGAR MISSING, `reconcile`
    # takes FMP's value and warns that the tier-1 source was unavailable, which
    # is the documented path and a labelled tier-5 current figure rather than an
    # unlabelled tier-1 stale one.
    long_term = _edgar_value_at(companyfacts, "us-gaap", "LongTermDebtNoncurrent",
                                "USD", target_date, require_period=True)
    current = _edgar_value_at(companyfacts, "us-gaap", "DebtCurrent",
                              "USD", target_date, require_period=True)
    if long_term.is_null or current.is_null:
        return Value.null(NullState.MISSING, unit="USD", source_name="EDGAR")
    leases = 0.0
    for tag in _EDGAR_LEASE_LIABILITY_TAGS:
        part = _edgar_value_at(companyfacts, "us-gaap", tag, "USD", target_date,
                               require_period=True)
        if not part.is_null:
            leases += part.value
    return Value.of(
        long_term.value + current.value + leases,
        unit="USD",
        source_name="EDGAR",
        period=target_date,
        evidence_class=EvidenceClass.C,
    )


def _edgar_diluted_shares(companyfacts: dict, target_date: str | None) -> Value:
    """Weighted-average diluted shares tag first; falls back to EDGAR's
    basic `dei:EntityCommonStockSharesOutstanding` tag (most recent filing)
    when the weighted-diluted tag isn't reported.

    The weighted tag is period-bound and read strictly: Exxon's newest entry for
    it is 2013-12-31, and a 2013 share count passed `reconcile`'s 5% check on
    pure coincidence -- buybacks took the count down and the Pioneer deal put it
    back, so twelve-year-old shares looked current. The `dei` fallback keeps
    `target_date=None` on purpose: it is a point-in-time cover-page count, where
    "most recent filing" IS the right answer.
    """
    weighted = _edgar_value_at(
        companyfacts, "us-gaap", "WeightedAverageNumberOfDilutedSharesOutstanding",
        "shares", target_date, require_period=True,
    )
    if weighted.is_valid:
        return weighted
    basic = _edgar_value_at(companyfacts, "dei", "EntityCommonStockSharesOutstanding", "shares", None)
    if basic.is_valid:
        return basic
    return Value.null(NullState.MISSING, unit="shares", source_name="EDGAR")


# --- analyst inputs that XBRL already reports ------------------------------
#
# `Entradas/<TICKER>.json` exists for figures no feed serves. But several of its
# keys ARE reported, as structured us-gaap tags, in the companyfacts payload
# this builder already downloads -- so asking a person to retype them was work
# for nothing, and worse: two of them have a documented proxy that fires when
# the key is absent, permanently lowering model-fit confidence.
#
# Only tags whose DEFINITION matches the key are mapped. Three that look
# mappable are deliberately absent, because filling them would silently change
# a definition -- which DATA_POLICY.md prohibits outright:
#
#   - `debt_schedule` wants the debt BALANCE per forecast year; XBRL publishes
#     the maturity ladder, which is REPAYMENTS. Different quantity. Victor's own
#     remedy says no path is inferred from one balance sheet.
#   - `options_outstanding` wants `[{name, count, strike}]` per grant; XBRL
#     carries only an aggregate count and a weighted-average strike.
#   - `share_history` is the company's MARKET SHARE over time (it feeds
#     `market_share_trend`), not its share count. No filing tag reports it.
#
_XBRL_SCALARS: tuple[tuple[str, tuple[str, ...], str], ...] = (
    ("equity_issuance", ("ProceedsFromIssuanceOfCommonStock",), "USD"),
    ("lease_charge", ("OperatingLeaseCost", "OperatingLeaseExpense"), "USD"),
)

#: Two-year pairs. RSK-AQI-023 and RSK-DEPI-025 are RATIOS of this year over
#: last, so a gross figure this year against a net one last year would be a
#: fabricated jump. Either both years come from the true tag or neither does,
#: leaving risk.py's own net/D&A fallback -- which is consistent across years
#: and now says so via its proxy warning.
_XBRL_PAIRS: tuple[tuple[str, str, tuple[str, ...], str], ...] = (
    # Gross PP&E, not net: what Beneish AQI is defined on.
    ("ppe", "ppe_prior", ("PropertyPlantAndEquipmentGross",), "USD"),
    # Depreciation alone, not D&A: what Beneish DEPI is defined on.
    ("depreciation", "depreciation_prior", ("Depreciation",), "USD"),
)

#: VAL-LEASE-004 wants "payment per future year" -- exactly what this ladder is.
_XBRL_LEASE_LADDER: tuple[str, ...] = (
    "LesseeOperatingLeaseLiabilityPaymentsDueNextTwelveMonths",
    "LesseeOperatingLeaseLiabilityPaymentsDueYearTwo",
    "LesseeOperatingLeaseLiabilityPaymentsDueYearThree",
    "LesseeOperatingLeaseLiabilityPaymentsDueYearFour",
    "LesseeOperatingLeaseLiabilityPaymentsDueYearFive",
)


def _xbrl_analyst_inputs(companyfacts: dict, target_date: str | None,
                         prior_date: str | None = None) -> dict[str, Any]:
    """Analyst-input keys that companyfacts reports outright.

    Returns plain values (the overlay holds plain Python, not `Value`), keyed
    exactly as `Entradas/<TICKER>.json` names them. A tag the company does not
    report is simply absent: a REIT files no gross PP&E and a bank no
    standalone `Depreciation`, and per MISSING_DATA_POLICY.md that is
    NOT_APPLICABLE, not a hole to paper over. Absent keys leave the existing
    proxy or MISSING path untouched.
    """
    out: dict[str, Any] = {}
    # `require_period=True` on every read here. These values go straight into a
    # metric with nothing to reconcile them against, so the permissive fallback
    # would hand over the newest fact a tag happens to hold: Walmart's
    # `Depreciation` stops at 2013-01-31 and NVIDIA's
    # `ProceedsFromIssuanceOfCommonStock` at 2016-07-31, and both were arriving
    # as if they described the current year.
    for key, tags, unit in _XBRL_SCALARS:
        v = _edgar_first_available(companyfacts, "us-gaap", tags, unit, target_date,
                                  require_period=True)
        if v.is_valid and v.value is not None:
            out[key] = float(v.value)

    for key, key_prior, tags, unit in _XBRL_PAIRS:
        ahora = _edgar_first_available(companyfacts, "us-gaap", tags, unit, target_date,
                                       require_period=True)
        antes = _edgar_first_available(companyfacts, "us-gaap", tags, unit, prior_date,
                                       require_period=True)
        if (ahora.is_valid and ahora.value is not None
                and antes.is_valid and antes.value is not None
                and prior_date and float(ahora.value) != float(antes.value)):
            # Equal values across two different period ends mean the lookup fell
            # back to the same entry for both, so there is no real prior year.
            out[key] = float(ahora.value)
            out[key_prior] = float(antes.value)

    # The ladder is only usable as a contiguous run from year one: a gap would
    # shift every later payment a year closer and overstate the lease debt.
    escalera: list[float] = []
    for tag in _XBRL_LEASE_LADDER:
        v = _edgar_value_at(companyfacts, "us-gaap", tag, "USD", target_date,
                            require_period=True)
        if not (v.is_valid and v.value is not None):
            break
        escalera.append(float(v.value))
    if len(escalera) >= 2:
        out["lease_commitments"] = escalera

    # RSK-RUN-015 is "(Cash + committed undrawn liquidity) / Average monthly cash
    # burn" and RSK-MAT-016 adds the same term; 05_risk_analysis/DATASET.md
    # sources it from filings as "available facilities". Read as a nested key
    # because risk.py reads `overlay["cash_burn"]["committed_liquidity"]`, and it
    # defaulted to 0.0 -- conservative, but it understated every runway.
    #
    # `require_period=True` is not optional here. This tag goes stale silently:
    # Tesla's newest entry is 2016 and American Airlines' is 2023, and the
    # no-period-match fallback would hand back a nine-year-old facility balance
    # as current liquidity. The period must cover the annual close or the term
    # stays absent.
    #
    # companyfacts returns the consolidated figure, not one row per facility
    # (verified on KO, TSLA and AAL: no period carries two different values), so
    # this is total undrawn capacity rather than an arbitrary single revolver.
    facilidad = _edgar_value_at(
        companyfacts, "us-gaap", "LineOfCreditFacilityRemainingBorrowingCapacity",
        "USD", target_date, require_period=True)
    if facilidad.is_valid and facilidad.value is not None and float(facilidad.value) > 0:
        out["cash_burn"] = {"committed_liquidity": float(facilidad.value)}
    return out


# --- staleness age derivation ---------------------------------------------


def _age_days(now: datetime, date_str: str | None) -> float:
    if not date_str:
        return float("inf")
    return (now.date() - date.fromisoformat(date_str[:10])).days


def _max_date(dates: list[str | None]) -> str | None:
    clean = [d for d in dates if d]
    return max(clean) if clean else None


def _ohlcv_row(bar: dict) -> OHLCVRow:
    """One raw FMP `/historical-price-eod` bar -> `OHLCVRow`, shared by the
    stock and benchmark/sector series so both use the same adjusted-close
    convention (`adj_close` falls back to `close` when FMP omits it)."""
    return OHLCVRow(
        date=bar["date"],
        open=bar["open"],
        high=bar["high"],
        low=bar["low"],
        close=bar["close"],
        adj_close=bar.get("adjClose", bar["close"]),
        volume=bar["volume"],
    )


#: When a US regular session is settled, in UTC. The builder already stamped
#: `market_timestamp` as `<date>T21:00:00+00:00`, so this is that same
#: convention named once instead of assumed twice.
_SESSION_CLOSE_UTC_HOUR = 21


def _settled_sessions(ohlcv_newest_first: list[dict], now: datetime) -> list[dict]:
    """`ohlcv_newest_first` without the session that has not closed yet.

    FMP serves the CURRENT session as a bar whose `close` is just the last
    print, and it keeps moving until the bell. Taking it as a close made two
    things wrong at once:

    - `market_timestamp` is built as `<newest bar date>T21:00:00+00:00`, so a
      packet built at 09:41 ET claimed a 16:00 ET close that had not happened.
    - Every technical level, ATR and the "current price" marker moved between
      runs on the same day. The audit trail records `packet_hashes` to make a
      report reproducible; an in-progress bar quietly broke that.

    A bar dated today counts only once `now` is at or past the close. Anything
    dated after `now` is not ours to use at all.
    """
    cutoff = now.date()
    settled_today = now.hour >= _SESSION_CLOSE_UTC_HOUR
    out = []
    for bar in ohlcv_newest_first:
        try:
            bar_date = date.fromisoformat(str(bar["date"])[:10])
        except (ValueError, KeyError, TypeError):
            continue
        if bar_date > cutoff or (bar_date == cutoff and not settled_today):
            continue
        out.append(bar)
    return out


# --- build_packet -----------------------------------------------------------


def _resolve_gap_sessions(earnings_calendar: list[dict], ohlcv_raw: list[dict]) -> list[str]:
    """Each reported earnings event mapped to the session that carries its gap.

    TECH-GAP-020: "Use first regular session after after-hours release."
    A company reporting after the close gaps on the *next* session; one
    reporting before the open gaps on the announcement date itself. FMP's
    earnings calendar carries no before/after-market marker, and
    DATA_POLICY.md forbids converting an unverified assumption into a
    number -- so rather than assume a convention per ticker, the price
    evidence decides: of the two candidate sessions, take whichever opened
    further from its own prior close. That is the session the market
    actually repriced on, which is what the formula measures.

    Verified against live data: AAPL (after-hours) resolves to the following
    session (+2.77% vs +0.10% on the announcement date), JPM and XOM (before
    the open) resolve to the announcement date itself (-2.25% vs +0.86%).

    Ties and equal moves keep the announcement date, the conservative read:
    no evidence of a delayed reaction means no reason to shift the event.
    """
    if not earnings_calendar or not ohlcv_raw:
        return []
    # `ohlcv_raw` is newest-first; index ascending so "next session" is +1.
    bars = list(reversed(ohlcv_raw))
    index_of = {str(b.get("date")): i for i, b in enumerate(bars)}

    def _gap_size(i: int) -> float:
        """|open_i / close_(i-1) - 1|, or -1 when the pair is unusable."""
        if i <= 0 or i >= len(bars):
            return -1.0
        prior_close, open_ = bars[i - 1].get("close"), bars[i].get("open")
        if not isinstance(prior_close, (int, float)) or not isinstance(open_, (int, float)):
            return -1.0
        if prior_close == 0:
            return -1.0
        return abs(open_ / prior_close - 1.0)

    sessions: list[str] = []
    for row in earnings_calendar:
        # Only events that have actually reported: a scheduled future date
        # has no gap to measure.
        if row.get("epsActual") is None and row.get("eps") is None:
            continue
        announced = str(row.get("date") or "")
        i = index_of.get(announced)
        if i is None:
            continue
        same_day, next_day = _gap_size(i), _gap_size(i + 1)
        chosen = i + 1 if next_day > same_day else i
        if 0 <= chosen < len(bars):
            date = str(bars[chosen].get("date") or "")
            if date and date not in sessions:
                sessions.append(date)
    return sorted(sessions)


#: How many peers to pull fundamentals for. SCORING_ENGINE.md requires "a
#: minimum of 8 valid peers" for a peer percentile, and FMP returns 9-14 for
#: the tickers tested; the cap bounds the per-packet request count.
_MAX_PEER_PANEL = 12


def _peer_panel(peers: list, fmp: Any) -> list[dict]:
    """`peer_growth_margin_returns` (DATASET.md, financial + valuation).

    DATASET.md marks a peer panel of "Comparable peer growth, margins, and
    returns" required for the peer rules, and SCORING_ENGINE.md needs "a
    minimum of 8 valid peers" before a peer percentile may be used. The
    packet carried only the peer *symbol* list, so FIN-GR-003 was MISSING,
    VAL-REL-034 dropped out of the valuation ensemble, and the
    peer-sensitive margin bands fell back to defaults on every company.

    One row per peer, from its own annual income statement and profile:
    revenue growth (FIN-GR-003's input), gross/operating/net margin (the
    peer-sensitive band inputs), and EV/Sales (VAL-REL-034's multiple).
    A peer whose statements do not load is skipped rather than guessed --
    the panel reports who it could measure, and the consumers apply their
    own >=8 minimum to what arrives.
    """
    symbols: list[str] = []
    for entry in peers or []:
        symbol = entry.get("symbol") if isinstance(entry, dict) else entry
        # The retired /v3 endpoint wrapped the list; /stable returns objects.
        if isinstance(entry, dict) and "peersList" in entry:
            symbols.extend(str(s) for s in (entry.get("peersList") or []))
        elif symbol:
            symbols.append(str(symbol))
    panel: list[dict] = []
    for symbol in symbols[:_MAX_PEER_PANEL]:
        try:
            income = fmp.income_annual(symbol, limit=2) or []
        except Exception:  # one unreachable peer must not fail the packet
            logger.warning("peer panel: income statement unavailable for %s", symbol)
            continue
        if not income:
            continue
        latest = income[0]
        revenue = latest.get("revenue")
        if not isinstance(revenue, (int, float)) or revenue == 0:
            continue
        prior_revenue = income[1].get("revenue") if len(income) > 1 else None
        growth = (
            revenue / prior_revenue - 1.0
            if isinstance(prior_revenue, (int, float)) and prior_revenue else None
        )

        def _margin(key: str) -> float | None:
            v = latest.get(key)
            return v / revenue if isinstance(v, (int, float)) else None

        market_cap = None
        try:
            profile_rows = fmp.profile(symbol)
            profile_row = (profile_rows[0] if isinstance(profile_rows, list) and profile_rows
                           else profile_rows) or {}
            market_cap = profile_row.get("marketCap") or profile_row.get("mktCap")
        except Exception:
            logger.warning("peer panel: profile unavailable for %s", symbol)
        panel.append({
            "symbol": symbol,
            "period": latest.get("date"),
            "revenue": revenue,
            "revenue_growth": growth,
            "gross_margin": _margin("grossProfit"),
            "operating_margin": _margin("operatingIncome"),
            "net_margin": _margin("netIncome"),
            # EV/Sales needs enterprise value; market cap alone is the
            # equity-only read, so it is named for what it is.
            "market_cap": market_cap,
            "price_to_sales": (
                market_cap / revenue if isinstance(market_cap, (int, float)) else None
            ),
        })
    return panel


def build_packet(ticker: str, providers: Providers, now: datetime) -> Packet:
    """Build the analysis `Packet` for `ticker` from `providers`, framed at
    the analysis clock `now` (never the wall clock — callers own `now`).

    Raises `PacketRejected` for the Phase 0/1 hard-reject conditions: no
    currency, no derivable market timestamp, fewer than 252 daily
    sessions, or no diluted share count from any source.
    """
    fmp, edgar, finnhub = providers.fmp, providers.edgar, providers.finnhub

    profile_raw = fmp.profile(ticker)
    profile: dict = (profile_raw[0] if isinstance(profile_raw, list) and profile_raw else profile_raw) or {}

    # DATASET.md asks for "5 years; preferred 10" of margins and "5-10
    # years" of capital-allocation history. The provider default is six,
    # which clears the minimum and never reaches the preference — and six
    # years starting in 2020 puts the last recession on the boundary with
    # no prior year to compare against, so BUS-STAB-009's recession-year
    # drawdown was uncomputable for every company. Eleven years spans the
    # preferred decade with one year of run-up.
    income_annual = fmp.income_annual(ticker, limit=_ANNUAL_HISTORY_YEARS) or []
    income_quarterly = fmp.income_quarterly(ticker) or []
    balance_annual = fmp.balance_annual(ticker, limit=_ANNUAL_HISTORY_YEARS) or []
    balance_quarterly = fmp.balance_quarterly(ticker) or []
    cashflow_annual = fmp.cashflow_annual(ticker, limit=_ANNUAL_HISTORY_YEARS) or []
    cashflow_quarterly = fmp.cashflow_quarterly(ticker) or []
    # `today=now.date()`, like the benchmark fetch below. `ohlcv_daily`'s own
    # docstring says the caller must supply it "so this stays deterministic";
    # omitted, it fell back to `date.today()` — the wall clock — so the stock
    # series and the benchmark series could be anchored to different days.
    ohlcv_raw = _settled_sessions(fmp.ohlcv_daily(ticker, today=now.date()) or [], now)
    peers = fmp.peers(ticker) or []
    peer_panel = _peer_panel(peers, fmp)
    analyst_estimates = fmp.analyst_estimates(ticker) or []
    insider_trades = fmp.insider_trades(ticker) or []
    institutional_holders = fmp.institutional_holders(ticker) or []
    earnings_calendar = fmp.earnings_calendar(ticker) or []

    quote = finnhub.quote(ticker)
    finnhub_eps_estimate = finnhub.estimates(ticker)
    finnhub_revenue_estimate = finnhub.revenue_estimates(ticker)

    cik = edgar.cik_for(ticker)
    companyfacts = edgar.companyfacts(cik) if cik is not None else {}

    risk_free_rate = providers.fred.risk_free_rate()

    # --- Phase 0: freeze the analysis clock ---------------------------

    currency = profile.get("currency")
    if not currency:
        raise PacketRejected(f"packet rejected for {ticker}: missing currency")

    market_timestamp: str | None = None
    if ohlcv_raw:
        market_timestamp = f"{ohlcv_raw[0]['date']}T21:00:00+00:00"
    elif quote and quote.get("t") is not None:
        market_timestamp = datetime.fromtimestamp(quote["t"], tz=timezone.utc).isoformat()

    if market_timestamp is None:
        raise PacketRejected(
            f"packet rejected for {ticker}: missing timestamps (no OHLCV or quote data available)"
        )

    if len(ohlcv_raw) < _MIN_DAILY_SESSIONS:
        raise PacketRejected(
            f"packet rejected for {ticker}: fewer than {_MIN_DAILY_SESSIONS} daily sessions "
            f"({len(ohlcv_raw)} available)"
        )

    # --- Phase 1: common facts table -----------------------------------

    latest_annual_date = income_annual[0]["date"] if income_annual else None

    fmp_revenue = _fmp_value(income_annual[0] if income_annual else None, "revenue", "USD")
    # Strict on period for both: `reconcile` adjudicates VALUES, not periods, and
    # its docstring puts alignment on the caller. JPMorgan's
    # `CashAndCashEquivalentsAtCarryingValue` newest entry is 2018-12-31 -- that
    # one happened to be caught as a >5% conflict, but a stale figure that lands
    # within 5% is accepted silently, which is how Exxon's 2013 share count got
    # through. Period first, per SOURCE_HIERARCHY.md's own step 1 ("Verify
    # period, currency, scale ...").
    edgar_revenue = _edgar_first_available(
        companyfacts, "us-gaap", _EDGAR_REVENUE_TAGS, "USD", latest_annual_date,
        require_period=True)

    fmp_cash = _fmp_value(balance_annual[0] if balance_annual else None, "cashAndCashEquivalents", "USD")
    edgar_cash = _edgar_value_at(
        companyfacts, "us-gaap", "CashAndCashEquivalentsAtCarryingValue", "USD",
        latest_annual_date, require_period=True,
    )

    fmp_total_debt = _fmp_value(balance_annual[0] if balance_annual else None, "totalDebt", "USD")
    edgar_total_debt = _edgar_total_debt(companyfacts, latest_annual_date)

    fmp_diluted_shares = _fmp_diluted_shares(income_annual, income_quarterly)
    edgar_diluted_shares = _edgar_diluted_shares(companyfacts, latest_annual_date)

    if fmp_diluted_shares.is_null and edgar_diluted_shares.is_null:
        raise PacketRejected(
            f"packet rejected for {ticker}: no diluted share count available from any source"
        )

    # ONE price for the whole packet, and it is the same bar every other
    # number is framed against.
    #
    # This used to read `profile.price`, a live quote, while the technical
    # levels and the football field's "current" marker read the newest bar of
    # `market_data.daily`. The two disagreed by ~2% on NVDA (195.04 vs 198.70)
    # because early in a session FMP's profile price still carries the PRIOR
    # close. So margin of safety, earnings yield, FCF yield and the reverse-DCF
    # were computed against yesterday while the levels were drawn against
    # today, and the report presented both as "the price".
    #
    # The settled adjusted close wins: it is the same series, the same
    # adjustment convention (splits/dividends) and the same timestamp the
    # packet already declares. The live quote stays as a declared fallback for
    # when no series is available.
    price_value = Value.null(NullState.MISSING, unit="usd_per_share", source_name="FMP")
    if ohlcv_raw:
        _newest = ohlcv_raw[0]
        price_value = Value.of(
            _newest.get("adjClose", _newest["close"]),
            unit="usd_per_share",
            source_name="FMP",
            source_locator=f"fmp:historical-price-eod/{ticker.upper()} {_newest['date']} adjusted close",
            as_of=market_timestamp,
            evidence_class=EvidenceClass.R,
        )
    elif profile.get("price") is not None:
        price_value = Value.of(
            profile["price"],
            unit="usd_per_share",
            source_name="FMP",
            source_locator=f"fmp:profile/{ticker.upper()} live quote (no settled EOD series)",
            as_of=market_timestamp,
            evidence_class=EvidenceClass.R,
            warnings=["PRICE_FROM_LIVE_QUOTE_NOT_SETTLED_CLOSE"],
        )

    facts_table: dict[str, Value] = {
        "revenue": reconcile("revenue", fmp_revenue, edgar_revenue),
        "diluted_shares": reconcile("diluted_shares", fmp_diluted_shares, edgar_diluted_shares),
        "cash": reconcile("cash", fmp_cash, edgar_cash),
        "total_debt": reconcile("total_debt", fmp_total_debt, edgar_total_debt),
        "price": price_value,
    }
    # When a reconciled valuation input conflicts (>5% source gap, reconcile's
    # materiality proxy), keep both source values under `<field>:fmp` /
    # `<field>:edgar` so a specialist can measure SOURCE_HIERARCHY.md's own
    # score-based materiality — a >5% input gap can be a definitional
    # difference (e.g. leases summed into EDGAR debt) that never moves a
    # category score. These are alternatives, not facts: the ':' suffix keeps
    # them out of fact iteration (see business._source_reconciliation_checks).
    for _field, _fmp_v, _edgar_v in (("cash", fmp_cash, edgar_cash),
                                     ("total_debt", fmp_total_debt, edgar_total_debt)):
        _f = facts_table[_field]
        if _f.is_null and str(_f.state or "").endswith("CONFLICTED"):
            if _fmp_v.is_valid:
                facts_table[f"{_field}:fmp"] = _fmp_v
            if _edgar_v.is_valid:
                facts_table[f"{_field}:edgar"] = _edgar_v

    # --- fundamentals: canonical-name annual + quarterly records --------

    fundamentals = {
        "annual": _merge_statements(income_annual, balance_annual, cashflow_annual),
        "quarterly": _merge_statements(income_quarterly, balance_quarterly, cashflow_quarterly),
    }

    # --- market data ------------------------------------------------------

    daily_rows = [_ohlcv_row(bar) for bar in ohlcv_raw]

    # Benchmark (and, absent a per-sector ETF map, sector-proxy) series:
    # SPY aligned to the stock's own trading dates via an inner join, so
    # `close.tail(n)` position-alignment in technical.py/risk.py lines up
    # with the same calendar dates on both sides. `today=now.date()` keeps
    # this deterministic given `now` (never the wall clock).
    benchmark_raw = fmp.ohlcv_daily(_BENCHMARK_TICKER, today=now.date()) or []
    stock_dates = {bar["date"] for bar in ohlcv_raw}
    benchmark_aligned_raw = [bar for bar in benchmark_raw if bar["date"] in stock_dates]
    benchmark_rows = [_ohlcv_row(bar) for bar in benchmark_aligned_raw]

    # Sector series from the company's SPDR sector ETF (real per-sector data,
    # not the broad benchmark) so MKT-RSG-024 measures the actual sector vs the
    # market. Aligned to the benchmark's exact dates (forward-filling the odd
    # missing ETF day) so the position-wise sector-vs-benchmark comparison
    # never mismatches length. Falls back to the benchmark when the sector is
    # unmapped or its ETF has no overlapping history.
    sector_ticker = _sector_etf_for(profile)
    if sector_ticker == _BENCHMARK_TICKER:
        sector_rows = benchmark_rows
    else:
        sector_raw = fmp.ohlcv_daily(sector_ticker, today=now.date()) or []
        sector_by_date = {bar["date"]: bar for bar in sector_raw}
        if any(bar["date"] in sector_by_date for bar in benchmark_aligned_raw):
            sector_rows, prev = [], None
            for bar in benchmark_aligned_raw:
                prev = sector_by_date.get(bar["date"], prev)
                sector_rows.append(_ohlcv_row(prev if prev is not None else bar))
        else:
            sector_rows = benchmark_rows

    market_data = MarketData(daily=daily_rows, benchmark=benchmark_rows, sector=sector_rows, adjusted=True)

    # --- earnings gap sessions ----------------------------------------------
    # TECH-GAP-020 measures the gap on "the first regular session after the
    # [after-hours] release", and levels_engine.earnings_gaps expects dates
    # already resolved to that session. The builder was fetching the earnings
    # calendar for staleness only and throwing it away, so technical's whole
    # earnings-gap dimension (3 of its 20 points) was NOT_SCORABLE on every
    # company for want of the dates.
    gap_sessions = _resolve_gap_sessions(earnings_calendar, ohlcv_raw)

    # --- staleness ----------------------------------------------------------

    consensus_dates = [row.get("date") for row in earnings_calendar if row.get("eps") is not None]
    if not consensus_dates:
        consensus_dates = [row.get("date") for row in earnings_calendar]
    latest_consensus_date = _max_date(consensus_dates)

    latest_quarterly_date = income_quarterly[0]["date"] if income_quarterly else None
    # (The 13F holder date used to be read here and passed to `peer_set`
    # staleness. DATA_POLICY.md has no staleness row for holder data, and using
    # it for the peer set was the bug fixed below, so nothing reads it now.)

    staleness = {
        "daily_market": staleness_state("daily_market", _age_days(now, ohlcv_raw[0]["date"])),
        "quarterly_fundamentals": staleness_state(
            "quarterly_fundamentals", _age_days(now, latest_quarterly_date)
        ),
        "consensus": staleness_state("consensus", _age_days(now, latest_consensus_date)),
        # DATA_POLICY.md's row is "Peer set | 90 days or material corporate
        # event". This measured `latest_holder_date` -- the 13F institutional
        # holder filing date -- which is a different quantity, and on a plan
        # where the institutional endpoint returns 402 the list is empty, so the
        # date was None, the age infinite, and `peer_set` came back STALE on
        # EVERY ticker. That is not cosmetic: business.py folds `peer_set` into
        # its freshness component whenever peers are used, and freshness is 20%
        # of CONFIDENCE_ENGINE.md's score, so every peer-using analysis carried a
        # permanent confidence penalty for holder data it never had.
        #
        # The peer set's real age is when it was fetched. `FMPProvider.peers`
        # caches with `max_age_days=_MAX_AGE_REFERENCE` (7), so a peer set that
        # reached this packet is at most a week old -- inside the 90-day
        # threshold by construction. Absent peers stay STALE: there is no set to
        # call fresh.
        "peer_set": staleness_state("peer_set", 0.0 if peer_panel else float("inf")),
    }

    # --- remaining packet blocks --------------------------------------------

    estimates = {
        "fmp_analyst_estimates": analyst_estimates,
        "finnhub_eps_estimate": finnhub_eps_estimate,
        "finnhub_revenue_estimate": finnhub_revenue_estimate,
        "peers": peers,
        # DATASET.md `peer_growth_margin_returns`: growth, margins and
        # multiples per peer, required for the peer rules.
        "peer_panel": peer_panel,
        "risk_free_rate": risk_free_rate.value,
        # DATASET.md (technical) lists `earnings_event_dates` as required:
        # "Timestamped earnings release dates and session mapping". These are
        # already mapped to the gap session (see `_resolve_gap_sessions`).
        "earnings_dates": gap_sessions,
    }

    capital_structure = {
        "diluted_shares": facts_table["diluted_shares"].value,
        "total_debt": facts_table["total_debt"].value,
        "cash": facts_table["cash"].value,
        # Same renamed-field trap as `exchange`: FMP's stable endpoint
        # returns `marketCap`, not the legacy `mktCap`, so this read
        # produced None for every company — and market cap is the equity
        # weight in WACC, which business's ROIC-WACC spread rests on.
        "market_cap": profile.get("marketCap") or profile.get("mktCap"),
        "beta": profile.get("beta"),
    }

    security = Security(
        ticker=ticker,
        # FMP's stable endpoint returns `exchange` ("NASDAQ") and
        # `exchangeFullName` ("NASDAQ Global Select"). Reading only the
        # legacy `exchangeShortName` meant this key was never present, so
        # every packet carried an empty exchange — a field OUTPUT_SCHEMA.md
        # declares and QA_CHECKLIST.md's first line requires to be correct.
        exchange=(profile.get("exchange")
                  or profile.get("exchangeShortName")
                  or profile.get("exchangeFullName")
                  or ""),
        security_type="operating_company",
        reporting_currency=currency,
        valuation_currency=currency,
        sector=profile.get("sector") or "",
        industry=profile.get("industry") or "",
    )
    # El SIC va PRIMERO, y si no llega manda el string de FMP como siempre.
    # `sic_for` reusa el `submissions.json` que ya esta en cache para los
    # filings, asi que no cuesta una peticion extra a la SEC.
    _sic: str | None = None
    try:
        _cik_sic = edgar.cik_for(ticker) if edgar is not None else None
        if _cik_sic is not None:
            _par = edgar.sic_for(_cik_sic)
            _sic = _par[0] if _par else None
    except Exception:                                     # noqa: BLE001
        # Un emisor extranjero sin CIK, EDGAR caido o sin User-Agent no puede
        # costar el packet entero: se sigue con la clasificacion comercial.
        _sic = None

    analysis = AnalysisMeta(
        knowledge_timestamp=now.isoformat(),
        market_timestamp=market_timestamp,
        industry_adapter=_industry_adapter_for(profile, _sic),
    )

    packet = Packet(
        security=security,
        analysis=analysis,
        fundamentals=fundamentals,
        market_data=market_data,
        estimates=estimates,
        capital_structure=capital_structure,
        insiders=insider_trades,
        institutional_holders=institutional_holders,
        facts_table=facts_table,
        xbrl_inputs=_xbrl_analyst_inputs(
            companyfacts, latest_annual_date,
            income_annual[1]["date"] if len(income_annual) > 1 else None),
        staleness=staleness,
        packet_hash="",
    )

    return packet.model_copy(update={"packet_hash": _hash_packet(packet)})


def _hash_packet(packet: Packet) -> str:
    """sha256 of the canonical (sorted-key, compact-separator) JSON of
    `packet`, excluding `packet_hash` itself. Deterministic across rebuilds
    from identical inputs; changes if any packet content changes."""
    payload = packet.model_dump(mode="json")
    payload.pop("packet_hash", None)
    canonical = json.dumps(payload, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()
