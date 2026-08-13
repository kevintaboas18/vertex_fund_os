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
