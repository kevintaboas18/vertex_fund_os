"""SEC EDGAR provider: ticker->CIK lookup, XBRL company facts, filing metadata.

Tier-1 per Cerebro/shared/SOURCE_HIERARCHY.md ("Regulatory filing and filing
acceptance metadata" ranks first). No API key is required — `EdgarProvider`
is always `available`. SEC's fair-access policy requires a descriptive
`User-Agent` identifying the requester on every request
(https://www.sec.gov/os/webmaster-faq#developers); `EDGAR_USER_AGENT` is
sent on every call via `wbj.providers.base.Provider.get_json`'s `headers`
pass-through.

Endpoints:
- `https://www.sec.gov/files/company_tickers.json` — ticker -> CIK map,
  one global payload (not per-ticker), refreshed roughly monthly by SEC,
  so cached for up to 30 days under a fixed global cache entry.
- `https://data.sec.gov/api/xbrl/companyfacts/CIK{cik:010d}.json` — all
  XBRL (dei/us-gaap/...) facts reported by the company across filings.
  Cached per-CIK for up to 1 day.
- `https://data.sec.gov/submissions/CIK{cik:010d}.json` — filing history
  including `acceptanceDateTime`, used to determine filing recency.
  Cached per-CIK for up to 1 day.
"""

from __future__ import annotations

import json
import logging
import os
import re
from pathlib import Path
from typing import Any

from wbj.providers.base import Provider

logger = logging.getLogger(__name__)

TICKERS_URL = "https://www.sec.gov/files/company_tickers.json"
COMPANYFACTS_URL = "https://data.sec.gov/api/xbrl/companyfacts/CIK{cik:010d}.json"
SUBMISSIONS_URL = "https://data.sec.gov/submissions/CIK{cik:010d}.json"

#: Último recurso, sólo si nadie configuró `EDGAR_USER_AGENT`. Deliberadamente
#: genérico: la SEC pide un contacto real, y poner aquí el correo de otra
#: persona hace que sus límites de fair-access y los tuyos se compartan.
_EDGAR_UA_FALLBACK = "Vertex Fund OS research (configura EDGAR_USER_AGENT)"

#: Identidad resuelta al importar. `EdgarProvider` prefiere la de `Settings`
#: (que lee `API/.env`); esto cubre a quien importa `_EDGAR_HEADERS` suelto
#: — `screener.py`, `scripts/webapp.py` — y el caso Render, donde la variable
#: llega por entorno y no existe ningún `.env` en disco.
EDGAR_USER_AGENT = (os.environ.get("EDGAR_USER_AGENT") or "").strip() or _EDGAR_UA_FALLBACK
_EDGAR_HEADERS = {"User-Agent": EDGAR_USER_AGENT}


def edgar_headers(settings: Any = None) -> dict[str, str]:
    """Cabeceras para la SEC, con la identidad configurada.

    Orden: `Settings.edgar_user_agent` (de `API/.env`) → variable de entorno →
    fallback genérico. Antes esto era una constante con el correo de Victor
    codificado, así que `EDGAR_USER_AGENT` en `render.yaml` y en `API/.env`
    no hacía absolutamente nada.
    """
    ua = (getattr(settings, "edgar_user_agent", None) or "").strip() if settings else ""
    return {"User-Agent": ua or EDGAR_USER_AGENT}

#: Nombres de formulario de las Schedule 13D/G, en sus DOS familias.
#:
#: Al exigir el formato estructurado (dic-2024) EDGAR renombro el tipo: lo que
#: se presentaba como `SC 13G/A` pasa a indexarse como `SCHEDULE 13G/A`. Filtrar
#: solo por los viejos devolvia CERO resultados desde 2025 — para NVDA se
#: perdian las dos presentaciones de Vanguard de 2026-01-30 y 2026-03-26, y el
#: reporte mostraba en su lugar un 13G/A de 2024 como si fuera el vigente.
#: Se piden las dos familias porque el historico anterior a dic-2024 conserva
#: los nombres antiguos.
_SCHEDULE_13DG_FORMS = (
    "SC 13D,SC 13D/A,SC 13G,SC 13G/A,"
    "SCHEDULE 13D,SCHEDULE 13D/A,SCHEDULE 13G,SCHEDULE 13G/A"
)

# The tickers map is one global, ticker-independent payload, so it is
# cached under a fixed pseudo-ticker rather than the caller's ticker —
# looking up a second ticker must reuse the same cache entry.
_GLOBAL_CACHE_TICKER = "_GLOBAL"

_MAX_AGE_TICKERS = 30
_MAX_AGE_COMPANYFACTS = 1
_MAX_AGE_SUBMISSIONS = 1
# A 10-K is immutable once filed; only a new one supersedes it.
_MAX_AGE_FILING = 90


def _cik_cache_key(cik: int) -> str:
    return f"CIK{cik:010d}"


#: Where a declared registrant-succession map lives, alongside the other
#: human-supplied inputs and under the same trust model.
_OVERRIDES_FILENAME = "cik_overrides.json"


class EdgarProvider(Provider):
    """SEC EDGAR data provider (no API key required)."""

    def __init__(self, *args: Any, **kwargs: Any) -> None:
        super().__init__(*args, **kwargs)
        #: Set by `cik_for` when a declared override was applied, so a caller
        #: can disclose the substitution instead of it happening silently.
        self.overrides_applied: dict[str, dict] = {}

    @property
    def available(self) -> bool:
        """Always True — EDGAR requires no API key, only a User-Agent header."""
        return True

    @property
    def _headers(self) -> dict[str, str]:
        """Cabeceras de esta instancia: prefiere la identidad de sus `Settings`."""
        return edgar_headers(self.settings)

    def _declared_overrides(self) -> dict:
        """The `Entradas/cik_overrides.json` map, or `{}`.

        Entries without a `source_locator` are dropped: DATA_POLICY.md requires
        a stable locator for any value, and a substituted CIK with no filing
        documenting the succession is an inference wearing a fact's clothes.
        """
        from pathlib import Path

        root = getattr(self.settings, "entradas_dir", None) or (
            Path(getattr(self.settings, "reports_dir", ".")).parent / "Entradas")
        path = Path(root) / _OVERRIDES_FILENAME
        if not path.is_file():
            return {}
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            logger.warning("could not read CIK overrides at %s", path, exc_info=True)
            return {}
        if not isinstance(data, dict):
            return {}
        out = {}
        for key, entry in data.items():
            if key.startswith("_comment") or not isinstance(entry, dict):
                continue
            if not entry.get("source_locator"):
                logger.warning(
                    "CIK override for %s dropped: no source_locator. A substituted "
                    "registrant needs the filing that documents the succession.", key)
                continue
            try:
                entry = {**entry, "cik": int(entry["cik"])}
            except (KeyError, TypeError, ValueError):
                continue
            out[key.upper()] = entry
        return out

    def _has_usgaap(self, cik: int) -> bool:
        facts = (self.companyfacts(cik) or {}).get("facts") or {}
        return bool(facts.get("us-gaap"))

    def cik_for(self, ticker: str) -> int | None:
        """Look up the CIK for `ticker` via SEC's company_tickers.json map.

        Returns None if the ticker isn't found or the payload is malformed.
        """
        payload = self.get_json(
            TICKERS_URL,
            {},
            "tickers",
            _GLOBAL_CACHE_TICKER,
            max_age_days=_MAX_AGE_TICKERS,
            headers=self._headers,
        )
        if not isinstance(payload, dict):
            return None

        ticker_upper = ticker.upper()
        mapped: int | None = None
        for entry in payload.values():
            if not isinstance(entry, dict):
                continue
            if str(entry.get("ticker", "")).upper() != ticker_upper:
                continue
            try:
                mapped = int(entry.get("cik_str"))
            except (TypeError, ValueError):
                mapped = None
            break

        # A declared registrant succession, applied only when it is the
        # difference between data and no data. Two guards, both narrow on
        # purpose: an override must never shadow a CIK that already works, and
        # it must point somewhere that does. Without them this becomes a way to
        # silently redirect any ticker anywhere.
        override = self._declared_overrides().get(ticker_upper)
        if override is not None and mapped is not None and mapped != override["cik"]:
            if not self._has_usgaap(mapped) and self._has_usgaap(override["cik"]):
                self.overrides_applied[ticker_upper] = {
                    **override, "replaced_cik": mapped,
                }
                logger.info(
                    "CIK override for %s: %s -> %s (%s)",
                    ticker_upper, mapped, override["cik"], override["source_locator"])
                return override["cik"]
            logger.warning(
                "CIK override for %s ignored: mapped CIK %s reports us-gaap facts, or "
                "the declared CIK %s does not. An override may not shadow a working "
                "registrant.", ticker_upper, mapped, override["cik"])
        return mapped

    def companyfacts(self, cik: int) -> dict | None:
        """Fetch all XBRL company facts (dei/us-gaap/...) for `cik`."""
        payload = self.get_json(
            COMPANYFACTS_URL.format(cik=cik),
            {},
            "companyfacts",
            _cik_cache_key(cik),
            max_age_days=_MAX_AGE_COMPANYFACTS,
            headers=self._headers,
        )
        return payload if isinstance(payload, dict) else None

    #: Ventanas de 3 meses con que la SEC nombra cada dataset trimestral de 13F.
    _F13_WINDOWS = (("dec", "feb"), ("mar", "may"), ("jun", "aug"), ("sep", "nov"))
    _F13_LAST_DAY = {"feb": 28, "may": 31, "aug": 31, "nov": 30}
    _F13_BASE = ("https://www.sec.gov/files/structureddata/data/"
                 "form-13f-data-sets/{name}")

    def _f13_candidates(self, today) -> list[str]:
        """Nombres de dataset del trimestre actual hacia atras, mas reciente primero."""
        out = []
        year = today.year
        for back in range(6):                      # ~18 meses de margen
            for (ini, fin) in reversed(self._F13_WINDOWS):
                y = year - (1 if (ini == "dec") else 0)
                out.append(f"01{ini}{y}-{self._F13_LAST_DAY[fin]}{fin}{year}_form13f.zip")
            year -= 1
        return out

    def holders_13f_dataset(self, cusip: str, top: int = 12) -> list[dict]:
        """Tenedores institucionales de `cusip`, ORDENADOS POR VALOR DE POSICION.

        Por que existe, cuando ya hay `institutional_holders_13f`: aquel busca
        el CUSIP en el indice de texto completo de EDGAR, y ese camino **no
        puede** devolver a los grandes. Medido sobre NVDA: 10.000+ filings
        mencionan el CUSIP y el indice sirve 300; los hits mas recientes de
        Vanguard, BlackRock, State Street y FMR son de 2012, 2009, 2013 y 2016
        -- sus tablas de posiciones son tan grandes que EDGAR deja de
        indexarlas a texto completo. Lo que quedaba eran gestoras pequeñas
        ordenadas por fecha de presentacion, y recencia no es reconocimiento.

        Este metodo usa el conjunto de datos estructurado que la SEC publica
        cada trimestre: `INFOTABLE.tsv` trae TODAS las posiciones con CUSIP,
        valor y numero de acciones, y `COVERPAGE.tsv` el nombre del gestor. Es
        completo por construccion -- 6.334 filings reportan NVDA frente a los
        160 del indice -- y da el ranking real por tamaño de posicion, que es
        lo que pide el item 4 de CLAUDE.md.

        Coste: un zip de ~100 MB por trimestre, cacheado y COMPARTIDO por todos
        los tickers (el dataset cubre el mercado entero); el filtrado son ~2 s
        sobre 3,8 millones de filas. Devuelve [] si no hay dataset disponible,
        y el llamador conserva su respaldo.
        """
        import csv
        import io
        import zipfile
        from datetime import date

        cusip = str(cusip).strip().upper()
        if not cusip:
            return []
        cache_dir = Path(getattr(self.settings, "cache_dir", ".")) / "form13f"
        try:
            cache_dir.mkdir(parents=True, exist_ok=True)
        except OSError:
            return []

        blob = period = None
        for name in self._f13_candidates(date.today()):
            local = cache_dir / name
            if local.is_file() and local.stat().st_size > 1_000_000:
                blob, period = local.read_bytes(), name
                break
            try:
                blob = self.get_bytes(self._F13_BASE.format(name=name), headers=self._headers)
            except Exception:
                blob = None
            if blob:
                try:
                    local.write_bytes(blob)
                except OSError:
                    pass
                period = name
                break
        if not blob:
            logger.info("no 13F structured dataset available for %s", cusip)
            return []

        try:
            zf = zipfile.ZipFile(io.BytesIO(blob))
            por_filing: dict[str, dict] = {}
            with zf.open("INFOTABLE.tsv") as fh:
                for row in csv.DictReader(io.TextIOWrapper(fh, "utf-8", "replace"), delimiter="\t"):
                    if (row.get("CUSIP") or "").strip().upper() != cusip:
                        continue
                    acc = row.get("ACCESSION_NUMBER") or ""
                    agg = por_filing.setdefault(acc, {"value": 0.0, "shares": 0.0})
                    try:                       # varias filas por filing (clases, opciones)
                        agg["value"] += float(row.get("VALUE") or 0)
                        agg["shares"] += float(row.get("SSHPRNAMT") or 0)
                    except (TypeError, ValueError):
                        continue
            if not por_filing:
                return []
            nombres: dict[str, str] = {}
            with zf.open("COVERPAGE.tsv") as fh:
                for row in csv.DictReader(io.TextIOWrapper(fh, "utf-8", "replace"), delimiter="\t"):
                    acc = row.get("ACCESSION_NUMBER")
                    if acc in por_filing:
                        nombres[acc] = (row.get("FILINGMANAGER_NAME") or "").strip()
        except Exception:
            logger.warning("13F dataset unreadable for %s", cusip, exc_info=True)
            return []

        # Un mismo gestor presenta varios filings (enmiendas, entidades del
        # grupo). Se queda el mayor por gestor: sumarlos duplicaria la posicion.
        por_gestor: dict[str, dict] = {}
        for acc, agg in por_filing.items():
            nombre = nombres.get(acc) or "(sin nombre en COVERPAGE)"
            clave = nombre.upper()
            prev = por_gestor.get(clave)
            if prev is None or agg["value"] > prev["value"]:
                por_gestor[clave] = {"name": nombre, "value": agg["value"],
                                     "shares": agg["shares"], "accession": acc}
        rank = sorted(por_gestor.values(), key=lambda r: -r["value"])[:max(1, top)]
        for r in rank:
            r["period"] = period
            r["source_locator"] = f"13F-HR accession {r['accession']} (SEC 13F data set {period})"
        return rank

    def institutional_holders_13f(self, cusip: str, since: str, until: str,
                                  pages: int = 3) -> list[dict]:
        """Institutional managers whose 13F-HR reports a position in `cusip`.

        `CLAUDE.md`'s mandatory report item 4 asks for recognised funds holding
        the company, sourced to 13F. The module that needed it recorded that
        "SEC EDGAR has no per-company holdings endpoint", and on the paths it
        had tried that was true: a 13F is filed by the INVESTOR, so a
        company-CIK filing history never lists one -- AAPL's own submissions
        carry zero 13F-HR.

        EDGAR full-text search inverts that. It indexes filing *contents*, and
        an information table names the CUSIP of every position, so searching
        13F-HR for a company's CUSIP returns its holders. That is the
        per-company path, and it is free and tier 1 where the FMP endpoint
        that was being used is tier 5 and returns 402 on this plan.

        Returns one row per manager, newest filing kept, each carrying the
        accession DATA_POLICY.md requires as a source locator. Share counts
        are NOT returned: they live inside the information-table document, and
        naming a holder is what the report item asks for -- fetching and
        parsing hundreds of tables to rank them would be a different feature
        with a different failure mode.
        """
        base = "https://efts.sec.gov/LATEST/search-index"
        by_cik: dict[str, dict] = {}
        for page in range(max(1, pages)):
            params = {"q": f'"{cusip}"', "forms": "13F-HR",
                      "startdt": since, "enddt": until}
            if page:
                params["from"] = page * 100
            payload = self.get_json(
                base, params, "efts_13f", f"{cusip}_{since}_{page}",
                max_age_days=_MAX_AGE_SUBMISSIONS, headers=self._headers,
            )
            hits = (((payload or {}).get("hits") or {}).get("hits")) or []
            if not hits:
                break
            for hit in hits:
                source = hit.get("_source") or {}
                names = source.get("display_names") or []
                if not names:
                    continue
                label = str(names[0])
                match = re.search(r"CIK (\d+)", label)
                if not match:
                    continue
                cik_str = match.group(1)
                filed = source.get("file_date") or ""
                prior = by_cik.get(cik_str)
                if prior is None or filed > prior["filing_date"]:
                    by_cik[cik_str] = {
                        "name": label.split("  (")[0].strip(),
                        "cik": cik_str,
                        "filing_date": filed,
                        "accession": source.get("adsh"),
                        "form": source.get("form") or "13F-HR",
                    }
        return sorted(by_cik.values(), key=lambda r: r["filing_date"], reverse=True)

    def major_holders_13d_g(self, cusip: str, company_cik: int, since: str,
                            until: str, pages: int = 2) -> list[dict]:
        """Beneficial owners above 5%, from Schedule 13D/13G.

        `CLAUDE.md`'s report item 4 asks for *recognised* holders, and a 13F
        list cannot answer that: the search index names the filer but not the
        position, so ordering it puts whoever filed most recently on top.
        Recency is not recognition.

        A Schedule 13D/13G is filed only on crossing 5% of a class, so the
        filer set IS the recognised holders -- Vanguard, BlackRock, Berkshire
        -- with no ranking step and nothing to parse. 13D is the activist
        filing and 13G the passive one; both are included, because a 5% holder
        is material either way.

        These hits name two parties, unlike a 13F: the subject company and the
        filer. The company's own CIK is excluded so the issuer does not appear
        as a holder of itself.
        """
        subject = f"{int(company_cik):010d}"
        base = "https://efts.sec.gov/LATEST/search-index"
        by_cik: dict[str, dict] = {}
        for page in range(max(1, pages)):
            params = {"q": f'"{cusip}"', "forms": _SCHEDULE_13DG_FORMS,
                      "startdt": since, "enddt": until}
            if page:
                params["from"] = page * 100
            payload = self.get_json(
                base, params, "efts_13dg", f"{cusip}_{since}_{page}",
                max_age_days=_MAX_AGE_SUBMISSIONS, headers=self._headers,
            )
            hits = (((payload or {}).get("hits") or {}).get("hits")) or []
            if not hits:
                break
            for hit in hits:
                source = hit.get("_source") or {}
                names = source.get("display_names") or []
                ciks = source.get("ciks") or []
                filed = source.get("file_date") or ""
                for label, holder_cik in zip(names, ciks):
                    if str(holder_cik).zfill(10) == subject:
                        continue  # the issuer is the subject, not a holder
                    key = str(holder_cik)
                    prior = by_cik.get(key)
                    if prior is None or filed > prior["filing_date"]:
                        by_cik[key] = {
                            "name": str(label).split("  (")[0].strip(),
                            "cik": key,
                            "filing_date": filed,
                            "accession": source.get("adsh"),
                            "form": source.get("form") or "SC 13G",
                        }
        return sorted(by_cik.values(), key=lambda r: r["filing_date"], reverse=True)

    def latest_reit_supplement(self, cik: int, limit: int = 12) -> dict | None:
        """Locate the newest quarterly supplement filed as an 8-K exhibit.

        REITs publish FFO, AFFO, NOI and acquisition/disposition cap rates in
        a quarterly supplement, and none of those is tagged in XBRL: FFO and
        AFFO are non-GAAP, and no filer tags a cap rate at all. The supplement
        is where the issuer states them under a definition it discloses, which
        is what makes a transcription evidence class R rather than a
        definition this code picked.

        This returns *where the document is*, never a number out of it. The
        supplement is a laid-out HTML rendering of a PDF -- Realty Income's
        disposition table reads `Occupied 19 49,283 59,537 7.2` with no
        structure separating the columns -- and a parser that mistook one
        column for another would put a wrong cap rate into a valuation, which
        is worse than not having one.

        On SOURCE_HIERARCHY.md this sits at tier 2 ("official issuer filing
        exhibit") or tier 4 ("official earnings release and investor
        presentation") depending on which description you read it under. It is
        NOT tier 1; that is the regulatory filing itself.

        Returns `{"accession", "filing_date", "candidates": [...]}` or None.
        Naming is not standard across filers -- Realty Income ships
        `realtyincomeq12026supple.htm`, Prologis ships `pld-ex99_1.htm` and
        `pld-ex99_2.htm` where one is the press release and the other the
        supplement -- so this returns every candidate on the filing with how
        it matched, and leaves the choice to the analyst. Guessing which of
        two EX-99s is the supplement would put the wrong document behind a
        transcribed figure's citation.
        """
        payload = self.get_json(
            SUBMISSIONS_URL.format(cik=cik), {}, "submissions",
            _cik_cache_key(cik), max_age_days=_MAX_AGE_SUBMISSIONS,
            headers=self._headers,
        )
        recent = (payload or {}).get("filings", {}).get("recent")
        if not isinstance(recent, dict):
            return None
        forms = recent.get("form") or []
        seen = 0
        fallback: dict | None = None
        for i, form in enumerate(forms):
            if form != "8-K" or seen >= limit:
                continue
            seen += 1
            accession = (recent.get("accessionNumber") or [None] * len(forms))[i]
            if not accession:
                continue
            bare = accession.replace("-", "")
            index = self.get_json(
                f"https://www.sec.gov/Archives/edgar/data/{cik}/{bare}/index.json",
                {}, "filing_index", f"{_cik_cache_key(cik)}_{bare}",
                max_age_days=_MAX_AGE_FILING, headers=self._headers,
            )
            items = ((index or {}).get("directory") or {}).get("item") or []
            candidates = []
            for it in items:
                name = str(it.get("name", ""))
                low = name.lower()
                if not low.endswith((".htm", ".html", ".pdf")):
                    continue
                match = ("supplement-name" if "supple" in low
                         else "ex99" if ("ex99" in low or "ex-99" in low) else None)
                if match:
                    candidates.append({
                        "name": name, "match": match,
                        "url": f"https://www.sec.gov/Archives/edgar/data/{cik}/{bare}/{name}",
                    })
            if candidates:
                candidates.sort(key=lambda c: c["match"] != "supplement-name")
                hit = {
                    "accession": accession,
                    "filing_date": (recent.get("filingDate") or [None] * len(forms))[i],
                    "candidates": candidates,
                }
                # A filename saying "supplement" settles it. An EX-99 alone
                # does not: every issuer files press releases as EX-99, so
                # taking the newest one would return a press release and call
                # it a supplement. Those are kept only as a fallback, and the
                # scan continues looking for the stronger signal.
                if candidates[0]["match"] == "supplement-name":
                    return hit
                fallback = fallback or hit
        return fallback

    def latest_10k_text(self, cik: int) -> dict | None:
        """The newest 10-K's primary document, as plain text.

        SOURCE_HIERARCHY.md ranks the regulatory filing first — above the
        market-data feeds the rest of the packet leans on. Several
        figures (customer concentration above all) are disclosed only in
        this narrative and exist in no structured endpoint at any price.

        Returns `{"text", "url", "filing_date", "accession"}`, or None
        when no 10-K is on file.
        """
        import html
        import re

        payload = self.get_json(
            SUBMISSIONS_URL.format(cik=cik), {}, "submissions",
            _cik_cache_key(cik), max_age_days=_MAX_AGE_SUBMISSIONS,
            headers=self._headers,
        )
        recent = (payload or {}).get("filings", {}).get("recent")
        if not isinstance(recent, dict):
            return None

        return self._filing_text(cik, recent, "10-K", 0)

    def recent_filings_text(self, cik: int, form: str = "10-Q",
                            limit: int = 8) -> list[dict]:
        """The most recent `limit` filings of `form`, newest first.

        A single 10-K gives one observation of a disclosure; a series
        needs the quarterlies. DATASET.md asks for eight quarters of
        backlog/RPO history, and the 10-Q's revenue note carries the
        same figure.
        """
        payload = self.get_json(
            SUBMISSIONS_URL.format(cik=cik), {}, "submissions",
            _cik_cache_key(cik), max_age_days=_MAX_AGE_SUBMISSIONS,
            headers=self._headers,
        )
        recent = (payload or {}).get("filings", {}).get("recent")
        if not isinstance(recent, dict):
            return []
        idx = [i for i, f in enumerate(recent.get("form", [])) if f == form][:limit]
        out = []
        for n, i in enumerate(idx):
            doc = self._filing_text(cik, recent, form, n)
            if doc:
                out.append(doc)
        return out

    def _filing_text(self, cik: int, recent: dict, form: str,
                     nth: int) -> dict | None:
        """The `nth` most recent `form` filing, as plain text."""
        import html
        import re

        idx = [i for i, f in enumerate(recent.get("form", [])) if f == form]
        if nth >= len(idx):
            return None
        i = idx[nth]

        accession = recent["accessionNumber"][i]
        url = (f"https://www.sec.gov/Archives/edgar/data/{cik}/"
               f"{accession.replace('-', '')}/{recent['primaryDocument'][i]}")
        # Cache per accession: a filing is immutable, and the 10-Q series
        # must not overwrite one another under a single key.
        raw = self.get_text(url, {}, f"filing_{accession}", _cik_cache_key(cik),
                            max_age_days=_MAX_AGE_FILING, headers=self._headers)
        if not raw:
            return None

        # Strip markup to plain prose. Quote verification later compares
        # against this same normalised text, so both sides must agree.
        text = re.sub(r"<[^>]+>", " ", raw)
        # Decode *every* entity, not a hand-picked few. Filings are full
        # of &#160; inside dates and figures ("January&#160;25"); leaving
        # them encoded made quoted sentences fail verification against
        # the very document they were copied from.
        text = html.unescape(text)
        text = re.sub(r"\s+", " ", text).strip()
        return {"text": text, "url": url, "accession": accession,
                "form": form,
                "period": (recent.get("reportDate") or [None] * (i + 1))[i],
                "filing_date": recent.get("filingDate", [None] * (i + 1))[i]}

    def filing_acceptance_times(self, cik: int) -> list[dict] | None:
        """Return recent filings' form/acceptanceDateTime/accessionNumber.

        Derived from `https://data.sec.gov/submissions/CIK{cik}.json`'s
        `filings.recent` arrays. Returns None if the payload is malformed
        or lacks the expected `filings.recent` structure.
        """
        payload = self.get_json(
            SUBMISSIONS_URL.format(cik=cik),
            {},
            "submissions",
            _cik_cache_key(cik),
            max_age_days=_MAX_AGE_SUBMISSIONS,
            headers=self._headers,
        )
        if not isinstance(payload, dict):
            return None

        recent = payload.get("filings", {}).get("recent")
        if not isinstance(recent, dict):
            return None

        forms = recent.get("form", [])
        accept_times = recent.get("acceptanceDateTime", [])
        accession_numbers = recent.get("accessionNumber", [])

        return [
            {
                "form": form,
                "acceptanceDateTime": accepted,
                "accessionNumber": accession,
            }
            for form, accepted, accession in zip(
                forms, accept_times, accession_numbers, strict=False
            )
        ]
