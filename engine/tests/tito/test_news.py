"""Port de `web/lib/news.test.ts` (27 casos)."""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

import pytest

from wbj.tito.news import (
    MACRO_FEEDS,
    NewsItem,
    build_news_report,
    company_aliases,
    contradiction_flag,
    decode_entities,
    flow_bias,
    mentions_company,
    news_bias,
    parse_feed_date,
    parse_rss,
    recency_weight,
)

NOW = datetime(2026, 7, 24, 12, 0, 0, tzinfo=timezone.utc)
NOW_ISO = "2026-07-24T12:00:00Z"


def item(**p: Any) -> NewsItem:
    base: dict[str, Any] = dict(
        id="x", title="t", url="u", publisher="p", published_utc=NOW_ISO,
        description=None, sentiment=None, reasoning=None, layer="company",
    )
    base.update(p)
    return NewsItem(**base)


CNBC = (
    "<rss><channel><item>      <link>https://cnbc.com/a.html</link>"
    '<guid isPermaLink="false">108339599</guid>'
    "<title>Trump&apos;s tariff draws rebukes</title>"
    "<description><![CDATA[Partners rejected the rationale.]]></description>"
    "<pubDate>Fri, 24 Jul 2026 03:15:46 GMT</pubDate>    </item>"
    "<item><link>https://cnbc.com/b.html</link><title>Intel jumps</title>"
    "<pubDate>Fri, 24 Jul 2026 01:00:00 GMT</pubDate></item></channel></rss>"
)


class TestParseRss:
    def test_extrae_los_items_aunque_vengan_en_una_sola_linea(self):
        rows = parse_rss(CNBC, "CNBC")
        assert len(rows) == 2
        assert rows[0].title == "Trump's tariff draws rebukes"
        assert rows[0].description == "Partners rejected the rationale."
        assert rows[0].url == "https://cnbc.com/a.html"
        # `toISOString()` SIEMPRE escribe los milisegundos.
        assert rows[0].published_utc == "2026-07-24T03:15:46.000Z"
        assert rows[0].layer == "macro"

    def test_descarta_items_sin_titulo_o_sin_link(self):
        assert parse_rss("<item><title>solo título</title></item>", "X") == []

    def test_no_revienta_con_xml_basura(self):
        assert parse_rss("no soy xml", "X") == []


class TestParseFeedDate:
    def test_acepta_rfc822_de_cnbc(self):
        # `new Date(raw).toISOString()`: los milisegundos van siempre.
        assert parse_feed_date("Fri, 24 Jul 2026 03:15:46 GMT") == "2026-07-24T03:15:46.000Z"

    def test_asume_utc_en_el_formato_sin_zona_de_investing(self):
        assert parse_feed_date("2026-07-24 02:54:27") == "2026-07-24T02:54:27.000Z"

    def test_acepta_tambien_el_iso_que_ya_trae_zona(self):
        # Su `new Date(raw)` lee el formato del estándar antes que el RFC-822;
        # el port solo intentaba el segundo y descartaba el feed entero.
        assert parse_feed_date("2026-07-24T02:54:27Z") == "2026-07-24T02:54:27.000Z"

    def test_devuelve_none_si_no_se_puede_parsear(self):
        assert parse_feed_date("ayer") is None
        assert parse_feed_date(None) is None


class TestDecodeEntities:
    def test_decodifica_las_entidades_de_los_feeds(self):
        assert decode_entities("Tesla&apos;s &amp; Intel&#39;s") == "Tesla's & Intel's"


class TestCompanyAliases:
    def test_limpia_los_sufijos_societarios(self):
        assert company_aliases("TSLA", "Tesla, Inc. Common Stock") == ["TSLA", "Tesla"]

    def test_conserva_el_ticker_y_el_nombre_de_nvidia(self):
        assert company_aliases("NVDA", "NVIDIA Corporation") == ["NVDA", "NVIDIA"]

    def test_descarta_tickers_de_una_o_dos_letras(self):
        assert "F" not in company_aliases("F", "Ford Motor Company")


ALIASES = company_aliases("TSLA", "Tesla, Inc. Common Stock")


class TestMentionsCompany:
    def test_encuentra_por_nombre_sin_importar_mayusculas(self):
        assert mentions_company("Why tesla stock crashed today", ALIASES) == "Tesla"

    def test_encuentra_por_ticker(self):
        assert mentions_company("TSLA earnings miss", ALIASES) == "TSLA"

    def test_exige_mayusculas_para_el_ticker(self):
        assert mentions_company("the tsla in lowercase", ["TSLA"]) is None

    def test_respeta_limites_de_palabra(self):
        assert mentions_company("Teslas Roadster", ["Tesla"]) is None

    def test_no_marca_titulares_ajenos(self):
        assert mentions_company("Fed holds rates steady", ALIASES) is None


class TestRecencyWeight:
    def test_pesa_completo_lo_de_hoy_y_menos_lo_viejo(self):
        assert recency_weight("2026-07-24T06:00:00Z", NOW) == 1
        assert recency_weight("2026-07-22T12:00:00Z", NOW) == 0.6
        assert recency_weight("2026-07-20T12:00:00Z", NOW) == 0.3
        assert recency_weight("2026-06-01T12:00:00Z", NOW) == 0.1


class TestNewsBias:
    def test_sin_sentimiento_es_neutral(self):
        assert news_bias([item(), item()], NOW).bias == "neutral"

    def test_varias_negativas_frescas_es_bearish(self):
        b = news_bias([item(sentiment="negative"), item(sentiment="negative")], NOW)
        assert b.bias == "bearish"
        assert b.score == -1
        assert b.negative == 2

    def test_positivas_es_bullish(self):
        assert news_bias([item(sentiment="positive")], NOW).bias == "bullish"

    def test_una_de_cada_y_empate_es_mixed(self):
        b = news_bias([item(sentiment="positive"), item(sentiment="negative")], NOW)
        assert b.bias == "mixed"
        assert b.score == 0

    def test_la_noticia_fresca_pesa_mas_que_la_vieja(self):
        b = news_bias(
            [
                item(sentiment="negative", published_utc="2026-07-24T10:00:00Z"),
                item(sentiment="positive", published_utc="2026-06-01T10:00:00Z"),
            ],
            NOW,
        )
        assert b.bias == "bearish"


class TestFlowBias:
    def test_mapea_el_pct_de_calls_a_direccion(self):
        assert flow_bias(93) == "bullish"
        assert flow_bias(7) == "bearish"
        assert flow_bias(50) == "neutral"
        assert flow_bias(60) == "bullish"
        assert flow_bias(40) == "bearish"


BEAR_NEWS = news_bias([item(sentiment="negative")], NOW)
BULL_NEWS = news_bias([item(sentiment="positive")], NOW)
NO_NEWS = news_bias([], NOW)


class TestContradictionFlag:
    def test_mismo_lado_es_confirmacion(self):
        f = contradiction_flag("bearish", BEAR_NEWS)
        assert f.kind == "confirm"
        assert "bajista" in f.title

    def test_flujo_alcista_contra_noticia_negativa_es_conflicto(self):
        f = contradiction_flag("bullish", BEAR_NEWS)
        assert f.kind == "conflict"
        assert "contra el pánico" in f.detail

    def test_flujo_bajista_contra_noticia_positiva_es_conflicto(self):
        assert contradiction_flag("bearish", BULL_NEWS).kind == "conflict"

    def test_sin_noticias_con_direccion_no_hay_bandera(self):
        assert contradiction_flag("bullish", NO_NEWS).kind == "none"

    def test_flujo_repartido_no_hay_bandera(self):
        assert contradiction_flag("neutral", BEAR_NEWS).kind == "none"


class TestBuildNewsReport:
    """El puente entre las dos capas.

    La firma es la de Víctor —`(ticker, company_name, now)`— así que las capas
    se sustituyen a nivel de módulo en vez de inyectarse por argumento: la API
    pública no debe llevar parámetros que solo existen para probar.
    """

    @pytest.fixture
    def capas(self, monkeypatch):
        def _set(company=(), macro=()):
            monkeypatch.setattr("wbj.tito.news.fetch_ticker_news",
                                lambda *a, **k: list(company))
            monkeypatch.setattr("wbj.tito.news.fetch_macro_feeds",
                                lambda *a, **k: list(macro))
        return _set

    def test_promueve_el_titular_macro_que_nombra_a_la_empresa(self, capas):
        capas(macro=[
            item(id="1", title="Why Tesla stock crashed", url="a", layer="macro"),
            item(id="2", title="Fed holds rates steady", url="b", layer="macro"),
        ])
        r = build_news_report("TSLA", "Tesla, Inc.", NOW)
        assert len(r.promoted) == 1
        assert r.promoted[0].matched_by == "Tesla"
        assert [m.title for m in r.macro] == ["Fed holds rates steady"]

    def test_el_sesgo_sale_solo_de_la_capa_de_empresa(self, capas):
        # Un titular macro promovido NO lleva sentimiento por ticker, así que no
        # puede mover el sesgo: solo Massive da sentimiento por subyacente.
        capas(company=[item(id="9", sentiment="negative")],
              macro=[item(id="1", title="Tesla soars", url="a", layer="macro",
                          sentiment="positive")])
        assert build_news_report("TSLA", "Tesla, Inc.", NOW).bias.bias == "bearish"

    def test_recorta_las_dos_capas(self, capas):
        capas(macro=[item(id=str(i), title=f"Macro {i}", url=f"u{i}", layer="macro")
                     for i in range(20)])
        r = build_news_report("TSLA", "Tesla, Inc.", NOW)
        assert len(r.macro) == 6
        assert r.feeds_total == len(MACRO_FEEDS) == 4

    def test_sin_noticias_no_revienta(self, capas):
        capas()
        r = build_news_report("TSLA", None, NOW)
        assert r.bias.bias == "neutral"
        assert r.company == [] and r.macro == [] and r.promoted == []

    def test_una_capa_caida_no_tumba_la_otra(self, monkeypatch):
        def boom(*a, **k):
            raise RuntimeError("Massive caído")
        monkeypatch.setattr("wbj.tito.news.fetch_ticker_news", boom)
        monkeypatch.setattr("wbj.tito.news.fetch_macro_feeds",
                            lambda *a, **k: [item(id="1", title="Fed holds", url="a",
                                                  layer="macro")])
        r = build_news_report("TSLA", "Tesla, Inc.", NOW)
        assert r.company == []
        assert len(r.macro) == 1
