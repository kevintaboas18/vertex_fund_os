"""El vigilante de las tesis: que la alarma escrita en el cajón suene.

La tesis siempre guardó qué la invalidaría —«Broken by a confirmed close <
zone_low - 0.25*ATR14 with volume/median(50d) >= 1.5…»—: un nivel exacto,
medible, con datos que el sistema se baja todos los días. **Nada lo comprobaba
nunca.** Estos casos miden que ahora sí, y que cuando no puede medir lo dice
en vez de callarse.
"""

from __future__ import annotations

import os
import sys

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(
    os.path.abspath(__file__))), "engine"))

pd = pytest.importorskip("pandas")

import vertex_vigilante as VG  # noqa: E402


def _barras(cierres, volumenes=None):
    """Barras diarias con la forma que come el motor."""
    n = len(cierres)
    return pd.DataFrame({
        "date": pd.date_range("2026-01-01", periods=n).strftime("%Y-%m-%d"),
        "open": cierres,
        "high": [c * 1.01 for c in cierres],
        "low": [c * 0.99 for c in cierres],
        "close": cierres,
        "volume": volumenes or [1_000_000] * n,
    })


def _pred(lado="soporte", baja=200.0, alta=210.0):
    return {
        "ticker": "NVDA", "date": "2026-08-05",
        "invalidacion": {"lado": lado, "zona_baja": baja, "zona_alta": alta,
                         "etiqueta": "S1 (z3)",
                         "regla": "close < zone_low - 0.25*ATR14"},
    }


class TestLaAlarmaSuena:

    def test_una_tesis_ROTA_se_detecta(self):
        """Pérdida del soporte con volumen y seguimiento: los tres requisitos
        de la regla del motor a la vez."""
        cierres = [220.0] * 65 + [190.0, 188.0, 186.0, 185.0, 184.0]
        vol = [1_000_000] * 65 + [5_000_000] * 5
        r = VG.revisa(_pred(), _barras(cierres, vol))
        assert r["estado"] == "roto" and r["roto"] is True
        assert r["cierre"] == 184.0

    def test_una_tesis_EN_PIE_no_da_falsa_alarma(self):
        r = VG.revisa(_pred(), _barras([220.0] * 70))
        assert r["estado"] == "en_pie" and r["roto"] is False

    def test_perder_el_nivel_SIN_VOLUMEN_no_es_romperla(self):
        """La regla exige `volume/median(50d) >= 1.5`. Sin eso es ruido, y
        avisar por ruido enseña a ignorar los avisos."""
        cierres = [220.0] * 65 + [190.0, 188.0, 186.0, 185.0, 184.0]
        r = VG.revisa(_pred(), _barras(cierres))          # volumen plano
        assert r["roto"] is False

    def test_un_solo_cierre_por_debajo_SIN_SEGUIMIENTO_tampoco(self):
        """«two consecutive closes beyond the buffer, or one close plus three
        sessions with no close back inside the zone». Un pinchazo y vuelta
        adentro no rompe nada."""
        cierres = [220.0] * 65 + [190.0, 221.0, 222.0, 223.0, 224.0]
        vol = [1_000_000] * 65 + [5_000_000] + [1_000_000] * 4
        r = VG.revisa(_pred(), _barras(cierres, vol))
        assert r["roto"] is False

    def test_una_RESISTENCIA_se_rompe_hacia_ARRIBA(self):
        """El lado sale de la regla, no de la etiqueta: es la regla la que
        dice hacia dónde se rompe."""
        cierres = [190.0] * 65 + [230.0, 232.0, 234.0, 235.0, 236.0]
        vol = [1_000_000] * 65 + [5_000_000] * 5
        r = VG.revisa(_pred(lado="resistencia"), _barras(cierres, vol))
        assert r["estado"] == "roto"

    def test_el_nivel_sale_EN_DINERO_y_no_en_formula(self):
        """«close < zone_low - 0.25*ATR14» es correcto y no le dice nada a
        nadie. El aviso tiene que llevar el precio que se mira en pantalla."""
        r = VG.revisa(_pred(), _barras([220.0] * 70))
        assert isinstance(r["nivel"], float)
        assert r["nivel"] < 200.0, "el nivel de un soporte va por debajo de la zona"
        assert "$" in VG.en_palabras(r)
        assert "ATR" not in VG.en_palabras(r) and "zone_low" not in VG.en_palabras(r)


class TestCuandoNoSePuedeMedirSeDICE:
    """Un vigilante que calla cuando le faltan datos se lee igual que uno que
    dice que todo va bien. No es lo mismo, y la diferencia es la que importa."""

    def test_con_POCAS_sesiones_dice_que_no_sabe(self):
        r = VG.revisa(_pred(), _barras([220.0] * 10))
        assert r["estado"] == "sin_datos"
        assert r["roto"] is None, "None es «no sé»; False sería «no se rompió»"
        assert "60" in r["detalle"]

    def test_sin_barras_tampoco_inventa(self):
        r = VG.revisa(_pred(), None)
        assert r["estado"] == "sin_datos" and r["roto"] is None

    def test_una_tesis_SIN_NIVEL_guardado_devuelve_None(self):
        """Las escritas antes de que esto existiera. No son un fallo ni un
        aviso: sencillamente no hay nada que comprobar."""
        assert VG.revisa({"ticker": "X", "date": "2026-08-05"},
                         _barras([220.0] * 70)) is None

    @pytest.mark.parametrize("inv", [
        None, {}, {"lado": "soporte"},
        {"lado": "soporte", "zona_baja": "200", "zona_alta": 210.0},
        {"lado": "otro", "zona_baja": 200.0, "zona_alta": 210.0},
    ])
    def test_un_bloque_a_medias_no_se_usa(self, inv):
        """Medio nivel es peor que ninguno: mediría contra un número que no
        significa lo que parece."""
        assert VG.niveles_guardados({"invalidacion": inv}) is None


class TestElResumenDeTodasLasTesis:

    class _Alm:
        """Un almacén de mentira: `lista` y `raiz`, que es lo que se usa."""

        def __init__(self, raiz):
            self.raiz = raiz

        def lista(self, sub, nombre):
            return sorted((self.raiz / sub).glob(f"*/*/{nombre}"))

    def _escribe(self, raiz, ticker, fecha, pred):
        import json

        d = raiz / "Reportes" / ticker / fecha
        d.mkdir(parents=True, exist_ok=True)
        (d / "prediccion.json").write_text(json.dumps(pred), encoding="utf-8")

    def test_separa_ROTAS_EN_PIE_y_SIN_DATOS(self, tmp_path):
        from datetime import date

        self._escribe(tmp_path, "ROTA", "2026-08-05", _pred())
        self._escribe(tmp_path, "PIE", "2026-08-05", _pred())
        self._escribe(tmp_path, "MUDA", "2026-08-05", _pred())
        self._escribe(tmp_path, "VIEJA", "2026-08-05",
                      {"ticker": "VIEJA", "date": "2026-08-05"})

        rotos = [220.0] * 65 + [190.0, 188.0, 186.0, 185.0, 184.0]
        vol = [1_000_000] * 65 + [5_000_000] * 5

        def barras_de(tk):
            if tk == "ROTA":
                return _barras(rotos, vol)
            if tk == "PIE":
                return _barras([220.0] * 70)
            return None                            # MUDA: el proveedor no contestó

        out = VG.revisa_todas(self._Alm(tmp_path), barras_de,
                              hoy=date(2026, 8, 28))
        assert [r["ticker"] for r in out["rotas"]] == ["ROTA"]
        assert [r["ticker"] for r in out["en_pie"]] == ["PIE"]
        assert [r["ticker"] for r in out["sin_datos"]] == ["MUDA"]
        assert out["sin_nivel"] == ["VIEJA"]
        assert out["revisadas"] == 3

    def test_de_cada_ticker_se_vigila_la_tesis_MAS_RECIENTE(self, tmp_path):
        """Avisar por la tesis de hace tres semanas cuando hay una de ayer
        sería avisar de algo que ya se corrigió solo."""
        from datetime import date

        self._escribe(tmp_path, "NVDA", "2026-08-05", _pred(baja=200.0))
        self._escribe(tmp_path, "NVDA", "2026-08-20", _pred(baja=150.0))
        abiertas = VG.predicciones_abiertas(self._Alm(tmp_path),
                                            hoy=date(2026, 8, 28))
        assert len(abiertas) == 1
        assert abiertas[0]["invalidacion"]["zona_baja"] == 150.0

    def test_manda_la_CARPETA_y_no_lo_que_diga_el_payload(self, tmp_path):
        """`Reportes/<TICKER>/<fecha>/` es la ruta canónica del archivo. Si se
        le cree al payload, dos cosas se rompen en silencio: con el ticker
        equivocado se piden las barras de OTRA empresa y se mide la tesis
        contra el precio de quien no es; con la fecha equivocada gana la tesis
        vieja y se vigila una que ya se corrigió sola."""
        from datetime import date

        # Archivada como AMD, pero el payload dice NVDA y una fecha vieja.
        mentiroso = {**_pred(), "ticker": "NVDA", "date": "2020-01-01"}
        self._escribe(tmp_path, "AMD", "2026-08-20", mentiroso)

        pedidos = []

        def barras_de(tk):
            pedidos.append(tk)
            return _barras([220.0] * 70)

        out = VG.revisa_todas(self._Alm(tmp_path), barras_de, hoy=date(2026, 8, 28))
        assert pedidos == ["AMD"], f"pidió las barras de otra empresa: {pedidos}"
        assert [r["ticker"] for r in out["en_pie"]] == ["AMD"]

    def test_una_tesis_VIEJISIMA_ya_no_se_vigila(self, tmp_path):
        from datetime import date

        self._escribe(tmp_path, "NVDA", "2024-01-05", _pred())
        assert VG.predicciones_abiertas(self._Alm(tmp_path),
                                        hoy=date(2026, 8, 28)) == []

    def test_un_ticker_que_REVIENTA_al_medirse_no_tumba_al_resto(self, tmp_path):
        """El vigilante recorre todo el libro. Un ticker con datos corruptos
        no puede llevarse por delante el aviso de los demás."""
        from datetime import date

        self._escribe(tmp_path, "BOOM", "2026-08-05", _pred())
        self._escribe(tmp_path, "PIE", "2026-08-05", _pred())

        def barras_de(tk):
            if tk == "BOOM":
                raise ValueError("datos corruptos")
            return _barras([220.0] * 70)

        out = VG.revisa_todas(self._Alm(tmp_path), barras_de, hoy=date(2026, 8, 28))
        assert [r["ticker"] for r in out["en_pie"]] == ["PIE"]
        assert [r["ticker"] for r in out["sin_datos"]] == ["BOOM"]


class TestLaTuberiaLLEVALoTuyo:
    """El correo pre-market existía y mandaba movers genéricos del mercado.
    La tubería estaba montada y viajaba vacía de lo de Kevin."""

    def _pm(self):
        import importlib

        ruta = os.path.join(os.path.dirname(os.path.dirname(
            os.path.abspath(__file__))), "scripts")
        if ruta not in sys.path:
            sys.path.insert(0, ruta)
        return importlib.import_module("premarket_email")

    def _movers(self):
        return [{"ticker": "AAA", "name": "A Corp", "pct": 5.1,
                 "price": 10.0, "mcap": 2e10}]

    def test_una_tesis_rota_sale_en_el_ASUNTO(self):
        """En el asunto y no sólo en el cuerpo: es lo único que se lee en la
        pantalla bloqueada del teléfono."""
        from datetime import datetime

        pm = self._pm()
        avisos = {"rotas": [{"ticker": "NVDA", "lado": "soporte",
                             "nivel": 198.58, "cierre": 184.0}],
                  "en_pie": [], "sin_datos": [], "sin_nivel": [], "revisadas": 1}
        asunto, texto, htmlb = pm.build_email(
            datetime(2026, 8, 28, 7, 0), self._movers(), self._movers(),
            avisos=avisos)
        assert "tesis se rompió" in asunto
        assert "NVDA" in texto and "198.58" in texto
        assert "NVDA" in htmlb
        assert "no una orden de compra o venta" in texto

    def test_sin_ninguna_rota_el_asunto_NO_alarma(self):
        from datetime import datetime

        pm = self._pm()
        avisos = {"rotas": [], "en_pie": [{"ticker": "AAPL"}], "sin_datos": [],
                  "sin_nivel": [], "revisadas": 1}
        asunto, texto, _ = pm.build_email(
            datetime(2026, 8, 28, 7, 0), self._movers(), self._movers(),
            avisos=avisos)
        assert "⚠️" not in asunto
        assert "Ninguna tesis rota" in texto

    def test_lo_que_no_se_pudo_medir_SE_DICE_en_el_correo(self):
        from datetime import datetime

        pm = self._pm()
        avisos = {"rotas": [], "en_pie": [], "revisadas": 1, "sin_nivel": [],
                  "sin_datos": [{"ticker": "MU"}]}
        _, texto, _ = pm.build_email(
            datetime(2026, 8, 28, 7, 0), self._movers(), self._movers(),
            avisos=avisos)
        assert "sin poder medir" in texto and "MU" in texto

    def test_SIN_vigilante_el_correo_sale_como_siempre(self):
        """La firma nueva es opcional a propósito: el correo de los movers no
        puede depender de que el vigilante funcione."""
        from datetime import datetime

        pm = self._pm()
        asunto, texto, htmlb = pm.build_email(
            datetime(2026, 8, 28, 7, 0), self._movers(), self._movers())
        assert asunto.startswith("📈 Pre-Market Movers")
        assert "TUS TESIS" not in texto
        assert "{tesis_html}" not in htmlb, "quedó un hueco sin rellenar en el HTML"
