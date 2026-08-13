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
