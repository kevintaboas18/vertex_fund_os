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


class TestElNumeroEnVezDeLaFormula:
    """«close < zone_low - 0.25*ATR14 with volume/median(50d) >= 1.5…» es
    correcto y no se puede comparar con la pantalla del broker sin sacar la
    calculadora. La tesis tiene que decir el precio."""

    def _api(self):
        import importlib

        raiz = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        if raiz not in sys.path:
            sys.path.insert(0, raiz)
        return importlib.import_module("vertex_api")

    def _nivel(self, lado="soporte", baja=200.0, alta=210.0, precio=220.0, atr=8.0):
        """Un nivel con las distancias que publica el motor, coherentes."""
        borde = alta if lado == "soporte" else baja
        return {"lado": lado, "zona_baja": baja, "zona_alta": alta,
                "distance_atr": (borde - precio) / atr,
                "distance_percent": (borde - precio) / precio * 100}

    def test_el_ATR_se_recupera_EXACTO_del_propio_nivel(self):
        """El motor no publica el ATR, pero publica dos distancias que salen
        de él. Con el borde y el cierre se despeja — así el número enseñado es
        el que usó el motor ese día, no una aproximación con barras de hoy."""
        V = self._api()
        assert V._atr_del_nivel(self._nivel(atr=8.0), 220.0) == pytest.approx(8.0)
        assert V._atr_del_nivel(self._nivel(atr=3.25), 220.0) == pytest.approx(3.25)

    def test_el_borde_de_un_SOPORTE_no_es_el_de_una_RESISTENCIA(self):
        """Para un soporte el borde cercano es su cota ALTA; para una
        resistencia, la BAJA. Confundirlos da un ATR con la magnitud cambiada
        y un precio de salida que no existe."""
        V = self._api()
        sop = self._nivel("soporte", 200.0, 210.0, precio=220.0, atr=8.0)
        res = self._nivel("resistencia", 230.0, 240.0, precio=220.0, atr=8.0)
        assert V._atr_del_nivel(sop, 220.0) == pytest.approx(8.0)
        assert V._atr_del_nivel(res, 220.0) == pytest.approx(8.0)

    def test_el_precio_sale_de_la_MISMA_regla(self):
        """soporte: zona_baja − 0,25·ATR · resistencia: zona_alta + 0,25·ATR"""
        V = self._api()
        assert V._invalidacion_en_dinero(
            self._nivel("soporte", 200.0, 210.0, atr=8.0), 220.0) == 198.0
        assert V._invalidacion_en_dinero(
            self._nivel("resistencia", 230.0, 240.0, atr=8.0), 220.0) == 242.0

    def test_la_frase_lleva_el_PRECIO_y_no_la_formula(self):
        V = self._api()
        f = V._invalidacion_en_palabras(self._nivel(atr=8.0), 220.0)
        assert "$198.00" in f
        for jerga in ("zone_low", "zone_high", "ATR14", "median(50d)", "Broken by"):
            assert jerga not in f, f"quedó jerga en la frase: {jerga}"
        assert "volumen alto" in f

    @pytest.mark.parametrize("roto", [
        {"distance_atr": None},                  # sin la distancia no se despeja
        {"distance_atr": 0},                     # división por cero
        {"distance_atr": True},                  # un bool no es un número
        {"distance_percent": 99.0},              # las dos distancias no cuadran
    ])
    def test_si_algo_NO_CUADRA_no_se_inventa_un_precio(self, roto):
        """Un ATR inventado se convierte en un precio de invalidación
        inventado, y eso es PEOR que la fórmula en crudo: la fórmula al menos
        se nota que no se entiende."""
        V = self._api()
        n = {**self._nivel(atr=8.0), **roto}
        assert V._atr_del_nivel(n, 220.0) is None
        assert V._invalidacion_en_dinero(n, 220.0) is None
        assert V._invalidacion_en_palabras(n, 220.0) is None

    def test_sin_precio_del_analisis_tampoco(self):
        V = self._api()
        for p in (None, 0, -5, True, "220"):
            assert V._atr_del_nivel(self._nivel(atr=8.0), p) is None

    def test_escribir_una_tesis_no_toca_la_MEMORIA_de_verdad(self, tmp_path, monkeypatch):
        """La ruta de `Memoria/` se calculaba con `__file__` en tres sitios, así
        que cualquier llamada desde un guion escribía en la memoria REAL y le
        corregía la tesis a un ticker de verdad. Pasó escribiendo esto: un
        guion de muestra tocó `Memoria/tesis/NVDA.md` y hubo que revertirlo a
        mano. Es el mismo accidente que `REPORTES_LOCAL` y `_PERFIL_DIR`.
        """
        from pathlib import Path

        V = self._api()
        real = Path(V.__file__).parent / "Memoria" / "tesis"
        antes = {p.name: p.read_text(encoding="utf-8") for p in real.glob("*.md")}

        monkeypatch.setattr(V, "MEMORIA_LOCAL", str(tmp_path / "Memoria"))
        V._wbj_write_thesis_md(
            "NVDA", 220.0, "Speculative", 51.9, 296.72,
            {"12m": {"bull": 341.22, "base": 296.72, "bear": 209.82}},
            "Tesis de prueba.",
            V._invalidacion_en_palabras(self._nivel(atr=8.0), 220.0),
            regla_tecnica="Broken by a confirmed close < zone_low - 0.25*ATR14")

        despues = {p.name: p.read_text(encoding="utf-8") for p in real.glob("*.md")}
        assert despues == antes, "le escribió encima a una tesis de verdad"
        escrita = (tmp_path / "Memoria" / "tesis" / "NVDA.md").read_text(encoding="utf-8")
        # Y lo que escribió lleva el PRECIO arriba y la fórmula debajo.
        assert "$198.00" in escrita
        assert "Regla del motor:" in escrita and "zone_low" in escrita
        assert escrita.index("$198.00") < escrita.index("Regla del motor:")

    def test_la_REGLA_del_motor_no_se_pierde(self, tmp_path, monkeypatch):
        """Va debajo y en letra pequeña, no fuera: es la que se audita y la que
        evalúa el vigilante. Lo que cambia es cuál se lee primero."""
        V = self._api()
        monkeypatch.setattr(V, "_MEMORIA_DIR", str(tmp_path), raising=False)
        regla = "Broken by a confirmed close < zone_low - 0.25*ATR14"
        llano = V._invalidacion_en_palabras(self._nivel(atr=8.0), 220.0)
        # La firma acepta las dos y las escribe en ese orden.
        import inspect

        firma = inspect.signature(V._wbj_write_thesis_md)
        assert "regla_tecnica" in firma.parameters
        assert firma.parameters["regla_tecnica"].default is None, (
            "tiene que ser opcional: sin ella la tesis se escribe igual")
        assert llano and regla not in llano


class TestLaLineaDeSaludEnElCorreo:
    """La cookie de MarketSnack es de SESIÓN y caduca sola. Cuando caduca,
    cinco de los seis sub-agentes se quedan sin dato y sólo sobrevive
    Estructura — y hasta ahora sólo te enterabas si abrías el panel. El agente
    con el que se opera podía llevar días corriendo a uno de seis."""

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

    def test_una_fuente_CAIDA_sale_con_su_arreglo_y_su_impacto(self):
        """No basta decir que algo falla: hay que decir qué se pierde y qué
        hacer. «marketsnack.tape: error» manda a leer logs; esto no."""
        from datetime import datetime

        pm = self._pm()
        salud = {"ok": False, "total": 8, "rotos": [
            {"check": "marketsnack.tape", "detalle": "cookie expirada",
             "arreglo": "sácala otra vez de DevTools y actualízala en Render",
             "impacto": "5 de 6 sub-agentes sin dato"}]}
        _, texto, htmlb = pm.build_email(
            datetime(2026, 8, 29, 7, 0), self._movers(), self._movers(),
            salud=salud)
        assert "1 de 8 fuentes con problema" in texto
        assert "5 de 6 sub-agentes sin dato" in texto
        assert "DevTools" in texto
        assert "marketsnack.tape" in htmlb

    def test_con_todo_sano_lo_dice_en_UNA_linea(self):
        from datetime import datetime

        pm = self._pm()
        _, texto, _ = pm.build_email(
            datetime(2026, 8, 29, 7, 0), self._movers(), self._movers(),
            salud={"ok": True, "total": 8, "rotos": []})
        assert "las 8 fuentes responden" in texto
        assert "⚠️" not in texto.split("LO MÁS")[0]

    def test_y_NO_PODER_comprobarlo_no_es_lo_mismo_que_estar_sano(self):
        """`ok=None` es «no sé». Pintarlo de verde sería la mentira más cara
        de las tres."""
        from datetime import datetime

        pm = self._pm()
        _, texto, _ = pm.build_email(
            datetime(2026, 8, 29, 7, 0), self._movers(), self._movers(),
            salud={"ok": None, "total": 0, "rotos": []})
        assert "no se pudo comprobar" in texto
        assert "responden" not in texto.split("LO MÁS")[0]

    def test_SIN_salud_el_correo_sale_como_siempre(self):
        """El parámetro es opcional: el correo de los movers no puede depender
        de que el diagnóstico funcione."""
        from datetime import datetime

        pm = self._pm()
        asunto, texto, htmlb = pm.build_email(
            datetime(2026, 8, 29, 7, 0), self._movers(), self._movers())
        assert asunto.startswith("📈 Pre-Market Movers")
        assert "SALUD" not in texto
        assert "{salud_html}" not in htmlb, "quedó un hueco sin rellenar"

    def test_el_resumen_del_servidor_NUNCA_lanza(self):
        """Un diagnóstico que tumba el correo del que cuelga es peor que no
        tener diagnóstico."""
        import vertex_api as V

        original = V.tito_health
        try:
            V.tito_health = lambda *a, **k: (_ for _ in ()).throw(RuntimeError("boom"))
            s = V._salud_para_el_correo()
        finally:
            V.tito_health = original
        assert s["ok"] is None and s["rotos"] == []
