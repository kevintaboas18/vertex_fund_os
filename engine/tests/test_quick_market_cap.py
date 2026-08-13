"""El P/FCF del quick no se calculaba NUNCA.

`_valuation_category` promete en su docstring "P/E and P/FCF", y el segundo
necesita `market_cap`. El packet del quick lo leia como `mktCap`, que es el
nombre del endpoint v3 de FMP: `/stable` lo renombro a `marketCap`, asi que
la clave venia None SIEMPRE y la valuacion quedaba colgando de un solo
multiplo.

`packet/builder.py` --el del analisis profundo-- ya leia los dos nombres, con
su comentario explicandolo y su test. El del quick se quedo atras.

Medido tras el arreglo: APH 1,6 -> 2,4 · KO 4,9 -> 3,0 · MSFT 5,0 -> 3,5.
PLTR se queda en 0,0 y es CORRECTO: P/E 271x y P/FCF 188x -- caro de verdad,
no un fallo de lectura.
"""
import wbj.cli as cli


class _FMPFalso:
    available = True

    def __init__(self, perfil):
        self._perfil = perfil

    def profile(self, _t):
        return [self._perfil]

    def ohlcv_daily(self, *a, **k):
        return []

    def analyst_estimates(self, *a, **k):
        return []

    def earnings_calendar(self, *a, **k):
        return []

    def insider_trades(self, *a, **k):
        return []


class _EdgarFalso:
    def cik_for(self, _t):
        return 1

    def companyfacts(self, _c):
        return {"entityName": "Prueba", "facts": {"us-gaap": {}}}


def _packet(monkeypatch, perfil):
    monkeypatch.setattr(cli, "_providers",
                        lambda: (object(), _EdgarFalso(), _FMPFalso(perfil)))
    return cli._build_packet("TEST")


def test_lee_el_nombre_nuevo_de_FMP(monkeypatch):
    """`/stable` devuelve `marketCap`. Este es el caso real de hoy."""
    p = _packet(monkeypatch, {"price": 100.0, "marketCap": 206_770_746_000})
    assert p["market_data"]["market_cap"] == 206_770_746_000


def test_y_sigue_leyendo_el_viejo(monkeypatch):
    """Un perfil cacheado de la v3 no puede dejar de funcionar."""
    p = _packet(monkeypatch, {"price": 100.0, "mktCap": 123.0})
    assert p["market_data"]["market_cap"] == 123.0


def test_el_nuevo_gana_cuando_estan_los_dos(monkeypatch):
    p = _packet(monkeypatch, {"price": 100.0, "marketCap": 999.0, "mktCap": 1.0})
    assert p["market_data"]["market_cap"] == 999.0


def test_sin_ninguno_queda_None_y_el_P_FCF_no_se_inventa(monkeypatch):
    p = _packet(monkeypatch, {"price": 100.0})
    assert p["market_data"]["market_cap"] is None


# ============================================================================
# El mismo fallo, buscado a proposito en TODO el mapa de campos
# ============================================================================


def test_el_mapa_de_campos_cubre_los_nombres_de_stable():
    """`mktCap` se encontro por accidente. Este cruce lo busca a proposito.

    FMP renombro campos al pasar de v3 a `/stable`, y el mapa falla en
    SILENCIO: si la clave no aparece en la respuesta, el campo simplemente no
    existe en el packet y su metrica queda MISSING sin que nada avise.

    Comparando el mapa contra las respuestas reales aparecieron dos mas:

      dividendsPaid  -> netDividendsPaid   (alimenta FIN-CF-016, los
                                            dividendos contaban como CERO
                                            y subestimaban los usos de caja)
      debtRepayment  -> netDebtIssuance    (mismo signo y misma convencion
                                            neta que `financial.py` ya
                                            documentaba al leerlo)

    Los nombres viejos se conservan: un payload cacheado de la v3 no puede
    dejar de funcionar de golpe.
    """
    from wbj.packet.builder import CANONICAL_FIELD_MAP as M

    for nuevo, viejo, destino in (
        ("netDividendsPaid", "dividendsPaid", "dividends_paid"),
        ("netDebtIssuance", "debtRepayment", "debt_repayment"),
    ):
        assert M.get(nuevo) == destino, f"{nuevo} sin mapear"
        assert M.get(viejo) == destino, f"{viejo} se perdio y romperia la cache"


def test_no_se_confunde_el_neto_con_un_solo_tramo():
    """`longTermNetDebtIssuance` y `shortTermNetDebtIssuance` son los dos
    tramos por separado. Mapear uno como si fuera el neto contaria media
    deuda."""
    from wbj.packet.builder import CANONICAL_FIELD_MAP as M

    assert "longTermNetDebtIssuance" not in M
    assert "shortTermNetDebtIssuance" not in M


def test_el_precio_viene_ajustado_por_SPLITS_pero_no_por_dividendos():
    """`adj_close` cae siempre al cierre crudo, y hay que saber que cubre.

    `historical-price-eod/full` no devuelve `adjClose` -- verificado contra
    la respuesta real. El crudo de FMP SI viene ajustado por splits (NVDA el
    2024-06-07, tres dias antes de su split 10:1, cotiza a 120,89 y no a los
    ~1.208 que valia), asi que no hay roturas de serie.

    Lo que no cubre son los dividendos: KO a un ano da 70,46 crudo contra
    68,53 ajustado, un 2,7%. Este test fija que la limitacion este ESCRITA,
    para que nadie lea `adj_close` creyendo que incluye el dividendo.
    """
    from pathlib import Path

    src = (Path(__file__).parent.parent.parent / "engine" / "wbj" / "packet"
           / "builder.py").read_text(encoding="utf-8")
    i = src.index('adj_close=bar.get("adjClose"')
    contexto = src[max(0, i - 1600):i]
    assert "SPLITS" in contexto, "hay que decir que los splits SI estan cubiertos"
    assert "DIVIDENDOS" in contexto, "y que los dividendos NO"


# ============================================================================
# El quick puntuaba a un banco con las formulas de un industrial
# ============================================================================


def _packet_riesgo(adapter):
    """Un packet minimo con los datos que la categoria de riesgo mira."""
    def serie(v):
        return [{"end": "2025-12-31", "val": v}]
    return {
        "ticker": "TEST", "annual": {
            "revenue": serie(100.0), "net_income": serie(10.0),
            "operating_cash_flow": serie(20.0), "capex": serie(5.0),
            "long_term_debt": serie(900.0), "equity": serie(100.0),
            "operating_income": serie(12.0), "gross_profit": serie(40.0),
            "interest_expense": serie(30.0), "diluted_shares": serie(10.0),
        },
        "industry_adapter": adapter,
    }


def _fila(packet, key="risk"):
    from wbj.quick import quick_scorecard
    return next(r for r in quick_scorecard(packet)["categories"] if r["key"] == key)


def test_a_un_banco_se_le_retira_la_COBERTURA_pero_NO_el_apalancamiento():
    """La primera version retiro las DOS y era pasarse.

    Medido: la deuda/patrimonio de JPM es 0,74 y puntua 7,04/10 -- MEJOR que
    Coca-Cola (1,11 -> 5,67) y que Apple (1,06 -> 5,82). El apalancamiento de
    un banco no vive en `long_term_debt`, vive en los DEPOSITOS, y EDGAR no
    los mete en esa etiqueta: ese ratio mide deuda corporativa corriente y
    significa lo de siempre. El cero de JPM salia entero de la cobertura.

    Retirar una metrica que funciona es el mismo error que puntuar una que no
    aplica, solo que en la otra direccion.
    """
    from wbj.quick import quick_scorecard

    pk = _packet_riesgo("banks")
    filas = {r["key"]: r for r in quick_scorecard(pk)["categories"]}
    assert filas["risk"]["status"] == "scored"
    # El apalancamiento sigue puntuando: con D/E 9x este packet da bajo, pero
    # da -- no queda fuera.
    assert filas["risk"]["score10"] is not None


def test_al_banco_se_le_retira_el_FCF_porque_el_Cerebro_lo_dice():
    """INDUSTRY_ADAPTERS.md, Banks: "Do not use enterprise-value/EBITDA,
    net-debt/EBITDA, or conventional FCFF". Con todas las letras, y solo bajo
    Banks.

    No es una laguna de datos: JPM y BAC no etiquetan capex porque un banco no
    tiene ciclo de reinversion en activo fijo. Dejarlo MISSING lo mantenia en
    el denominador y hundia la categoria a la mitad.
    """
    banco = _fila(_packet_riesgo("banks"))
    ind = _fila(_packet_riesgo("default_nonfinancial"))
    assert banco["coverage"] == 1.0, (
        "lo aplicable esta cubierto: ni la cobertura ni el FCF cuentan como hueco")
    assert ind["coverage"] == 1.0


def test_a_una_casa_de_bolsa_NO_se_le_retira_el_apalancamiento():
    """Aunque su balance parezca de banco. El SIC 6211 va de SEIC 0,00 y
    RJF 0,10 a MS 3,06: no es una clase, igual que no lo era para la
    cobertura de intereses. Retirarselo a las nueve sanas para tapar a GS
    seria el error de las aseguradoras otra vez."""
    from wbj.core import adapters

    assert adapters.cost_of_funds_is_interest("broker_dealers") is False
    casa = _fila(_packet_riesgo("broker_dealers"))
    ind = _fila(_packet_riesgo("default_nonfinancial"))
    assert casa["score10"] == ind["score10"]


def test_el_cero_de_JPM_venia_de_la_COBERTURA_y_ya_no():
    """JPM sacaba 0,0 de 10 en riesgo. Ese cero salia entero de la cobertura
    de intereses (0,4x): su apalancamiento real es 0,74 y puntua 7,04.

    Con la cobertura y el FCF retirados y el apalancamiento intacto, un banco
    con deuda/patrimonio NORMAL --0,74, el de JPM-- puntua bien. Antes ese
    mismo banco sacaba cero.
    """
    from wbj.quick import quick_scorecard

    pk = _packet_riesgo("banks")
    # D/E 0,74 como JPM: patrimonio 100, deuda 74.
    pk["annual"]["long_term_debt"] = [{"end": "2025-12-31", "val": 74.0}]
    fila = next(r for r in quick_scorecard(pk)["categories"] if r["key"] == "risk")
    assert fila["score10"] > 6.0, (
        f"un banco con apalancamiento corriente no puede sacar {fila['score10']}")


def test_no_aplica_SALE_del_denominador_y_no_hunde_la_cobertura():
    """La diferencia entre NOT_APPLICABLE y un cero, medida.

    Un cero contaria como metrica valida y mala. NOT_APPLICABLE sale del
    denominador y la categoria REESCALA sobre lo que si aplica -- por eso el
    banco conserva cobertura plena sobre sus metricas aplicables en vez de
    caer a la mitad. Es lo mismo que hace el motor profundo.
    """
    banco = _fila(_packet_riesgo("banks"))
    assert banco["coverage"] == 1.0, (
        "lo aplicable esta todo cubierto: la solvencia no cuenta como hueco")
    assert banco["score10"] is not None


def test_una_aseguradora_NO_entra_aqui():
    """Mismo predicado que el motor profundo, `cost_of_funds_is_interest`, que
    es solo bancos: una aseguradora cobra primas y pide prestado como
    cualquiera (PGR da 51x de cobertura, UNH 4,7x)."""
    from wbj.core import adapters

    assert adapters.cost_of_funds_is_interest("insurers") is False
    aseg = _fila(_packet_riesgo("insurers"))
    ind = _fila(_packet_riesgo("default_nonfinancial"))
    assert aseg["score10"] == ind["score10"]


def test_una_fila_sin_puntaje_no_dice_que_esta_puntuada():
    """El `status` se decidia por si la categoria se habia CONSTRUIDO, no por
    si habia puntaje: salia "scored" con score10 None, que es justo lo que la
    interfaz no sabe pintar."""
    sin_nada = _packet_riesgo("banks")
    # Sin patrimonio no hay apalancamiento, y al banco ya no le aplican ni la
    # cobertura ni el FCF: no queda una sola metrica.
    sin_nada["annual"]["equity"] = []
    sin_nada["annual"]["long_term_debt"] = []
    fila = _fila(sin_nada)
    assert fila["score10"] is None
    assert fila["status"] == "not_scorable"
    assert fila.get("reason"), "y tiene que decir POR QUE"
