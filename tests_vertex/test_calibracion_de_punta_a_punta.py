"""La calibración: que se guarde, que sobreviva al redeploy y que MUEVA algo.

Hay tests unitarios de cada pieza —`review_predictions` mide, `predict_pro`
desplaza, el almacén respalda—. Lo que no había es la prueba de que las piezas
están ENCADENADAS: que la predicción de hoy acaba en un archivo que el respaldo
se lleva, que al volver de un redeploy sigue ahí, que al vencer produce un
sesgo, y que ese sesgo cambia el target del día siguiente.

Cada eslabón por separado puede estar verde y la cadena rota — es exactamente
lo que pasó con las series: el motor calculaba bien, el archivo se escribía
bien, y el redeploy restauraba una foto congelada de tres semanas antes.

Un agente que "se calibra" pero cuyo número no llega a mover ningún target no
se está calibrando: está llevando una contabilidad que nadie lee.

    python -m pytest tests_vertex/test_calibracion_de_punta_a_punta.py -q
"""

from __future__ import annotations

import shutil
import subprocess
import sys
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "engine"))

if not shutil.which("git"):                            # pragma: no cover
    pytest.skip("hace falta git", allow_module_level=True)

#: Un día de mercado cualquiera, fijo: la calibración depende de fechas y con
#: `now()` de verdad el test cambiaría de resultado según el día que se corra.
AHORA = datetime(2026, 8, 13, 15, 0, tzinfo=timezone.utc)


def _foto(fecha: str, spot: float, base: float, horizonte: int = 10):
    """Una predicción guardada, con el tipo que el store escribe."""
    from wbj.tito.stores import PredictionSnapshot

    return PredictionSnapshot(
        date=fecha, horizon_days=horizonte, spot=spot,
        bear=spot * 0.94, base=base, bull=spot * 1.08,
        direction="up" if base >= spot else "down")   # type: ignore[arg-type]


def _barras(desde: str, n: int, cierre: float):
    """`n` sesiones planas a `cierre` desde `desde`. Basta para vencer una foto."""
    from wbj.tito.levels import LvlBar

    d0 = date.fromisoformat(desde)
    return [LvlBar(time=(d0 + timedelta(days=i)).isoformat(),
                   high=cierre + 1, low=cierre - 1, close=cierre)
            for i in range(n)]


@pytest.fixture
def series(tmp_path, monkeypatch):
    """El disco de series apuntando a `tmp_path`, como lo pone `vertex_api`."""
    monkeypatch.setenv("WBJ_TITO_DATA", str(tmp_path / "Series" / "tito"))
    from wbj.tito import stores

    return stores


class TestSeGuardaDondeElRespaldoLaVeALCANZAR:
    """Guardar en el sitio equivocado es indistinguible de guardar bien…

    …hasta el redeploy siguiente. `WBJ_TITO_DATA` tiene que caer DENTRO del
    almacén; si cae en `./data/tito` el archivo existe, se lee, todo parece
    correcto — y desaparece entero en el próximo despliegue sin que nada avise.
    """

    def test_la_prediccion_aterriza_en_el_arbol_que_se_respalda(self, series,
                                                                tmp_path):
        series.save_prediction("DEMO", _foto("2026-08-03", 100, 106))
        f = tmp_path / "Series" / "tito" / "predictions" / "DEMO.json"
        assert f.is_file(), f"no se escribió en {f}"
        assert series.load_journal("DEMO"), "se escribió pero no se relee"

    def test_el_almacen_se_lleva_las_series(self, tmp_path):
        """Que el archivo exista no basta: el respaldo tiene que INCLUIRLO."""
        from vertex_almacen import DIR_SERIES, Almacen

        remoto = tmp_path / "remoto.git"
        subprocess.run(["git", "init", "-q", "--bare", str(remoto)], check=True)
        a = Almacen(raiz=tmp_path / "alm", remoto=f"file://{remoto}",
                    token="ficticio")
        a.restaura()
        destino = a.ruta(DIR_SERIES, "tito") / "predictions"
        destino.mkdir(parents=True, exist_ok=True)
        (destino / "DEMO.json").write_text('{"ticker":"DEMO","snapshots":[]}',
                                           encoding="utf-8")
        a.sincroniza(mensaje="series de prueba")

        # Un contenedor NUEVO: disco vacío, mismo remoto.
        b = Almacen(raiz=tmp_path / "alm2", remoto=f"file://{remoto}",
                    token="ficticio")
        b.restaura()
        vuelto = b.ruta(DIR_SERIES, "tito") / "predictions" / "DEMO.json"
        assert vuelto.is_file(), (
            "la predicción no volvió del respaldo: el redeploy se la come")


class TestUnaPrediccionVencidaProduceNUMEROS:
    """Sin esto la memoria es una lista de fotos que nadie evalúa."""

    def test_los_cuatro_numeros_de_su_MemoriaCard_salen(self, series):
        # Predijo 106 y la acción cerró en 100: se equivocó por arriba un 6%.
        foto = _foto("2026-07-20", 100, 106)
        r = series.review_predictions([foto.__dict__],
                                      _barras("2026-07-21", 20, 100), AHORA)
        assert r["matured_count"] == 1, r
        assert r["mean_abs_error_pct"] is not None, "sin error medio no hay calibración"
        assert r["bias_pct"] is not None, "sin sesgo no hay corrección"
        assert r["direction_hit_rate"] is not None
        assert r["base_touch_rate"] is not None
        # El error es del ~6% y el sesgo NEGATIVO: apuntó alto.
        assert 5 < r["mean_abs_error_pct"] < 7, r["mean_abs_error_pct"]
        assert r["bias_pct"] < 0, "predijo 106, cerró 100: apuntó alto"

    def test_una_foto_que_aun_no_vence_no_inventa_calibracion(self, series):
        """El horizonte no ha pasado: medirla sería medir un partido en curso."""
        ayer = (AHORA - timedelta(days=1)).strftime("%Y-%m-%d")
        r = series.review_predictions([_foto(ayer, 100, 106).__dict__],
                                      _barras(ayer, 2, 101), AHORA)
        assert r["matured_count"] == 0, r
        assert r["mean_abs_error_pct"] is None
        assert series.calibration_from_review(r)["samples"] == 0


class TestElSesgoDEVERDADMueveElTarget:
    """El eslabón que cierra el lazo — y el único que se puede romper en silencio.

    Todo lo demás puede estar bien y este no: `review_predictions` calcularía su
    sesgo, la pantalla lo enseñaría, y los targets saldrían exactamente iguales
    que sin memoria. Un agente que "aprende" y predice lo mismo no aprende.
    """

    @staticmethod
    def _predice(calibration):
        """La misma llamada con y sin memoria: lo único que cambia es esto."""
        from wbj.tito.expected_move import LevelInput
        from wbj.tito.prediction import SubScores, predict_pro

        return predict_pro(
            spot=100.0, iv=0.45, horizon_days=10,
            nodes=[LevelInput(strike=110, concentration=1.0, side="call",
                              net_gex=5e6),
                   LevelInput(strike=92, concentration=0.7, side="put",
                              net_gex=-3e6)],
            scores=SubScores(aggression=5, conviction=6, unusuality=5,
                             structure=8, iv_context=4, validation=6),
            regime="positive", callvpct=70, hit_rate=55,
            calibration=calibration)

    def test_con_sesgo_el_target_se_corrige_y_sin_el_no(self):
        from wbj.tito.prediction import calibration_shift_pct

        sin = self._predice(None)
        con = self._predice({"bias_pct": -6.0, "samples": 12})
        assert sin.calibration["applied"] is False, sin.calibration
        assert con.calibration["applied"] is True, con.calibration
        assert con.calibration["shift_pct"] != 0
        assert con.base.target != sin.base.target, (
            "el sesgo no movió el target: el lazo de calibración está abierto")
        # El desplazamiento que se aplicó es el que su fórmula dice.
        assert con.calibration["shift_pct"] == calibration_shift_pct(-6.0, 12)

    def test_pocas_muestras_no_corrigen_nada(self):
        """Dos aciertos no son un patrón. Corregir con eso es sobreajustar."""
        con_dos = self._predice({"bias_pct": -6.0, "samples": 2})
        assert con_dos.calibration["applied"] is False, con_dos.calibration
        assert con_dos.base.target == self._predice(None).base.target

    def test_lo_que_SALE_del_disco_es_lo_que_la_revision_sabe_leer(self, series):
        """La costura donde el lazo estaba abierto, y nadie lo veía.

        El diario se escribe con SU formato —`horizonDays`, camelCase— para que
        el archivo sea intercambiable con el de su app. La revisión, portada,
        leía `horizon_days`. Las dos decisiones son correctas por separado; en
        cadena, `load_journal` devolvía `horizonDays`, la lectura daba `None`,
        el vencimiento se calculaba contra una fecha inválida y **ninguna
        predicción vencía jamás**: `matured_count` en 0 con meses de historial,
        sesgo `None`, calibración apagada para siempre.

        Este caso pasa por `load_journal` —lo que hace la app— en vez de armar
        las fotos a mano. Los unitarios las arman en snake_case y el
        comparador del diferencial TRADUCE la clave antes de llamar: la
        traducción vivía en el banco de pruebas y no en producción, que es cómo
        182/182 casos estaban verdes con el lazo abierto.
        """
        series.save_prediction("DEMO", _foto("2026-07-20", 100, 106))
        delDisco = series.load_journal("DEMO")
        assert "horizonDays" in delDisco[0], (
            "el archivo dejó de llevar SU formato; este test ya no mide la costura")
        r = series.review_predictions(delDisco, _barras("2026-07-21", 20, 100),
                                      AHORA)
        assert r["matured_count"] == 1, (
            "una predicción de hace tres semanas sigue sin vencer: "
            f"la revisión no entiende lo que el disco guarda — {r['evals'][0]}")
        assert r["bias_pct"] is not None
        assert r["evals"][0]["horizon_days"] == 10, r["evals"][0]

    def test_el_lazo_ENTERO_con_una_prediccion_de_verdad(self, series):
        """De la foto guardada al target corregido, sin saltarse un paso."""
        series.save_prediction("DEMO", _foto("2026-07-20", 100, 106))
        for i, f in enumerate(("2026-07-21", "2026-07-22", "2026-07-23",
                               "2026-07-24", "2026-07-27")):
            series.save_prediction("DEMO", _foto(f, 100, 105 + i * 0.5))
        guardadas = series.load_journal("DEMO")
        assert len(guardadas) == 6, guardadas
        # Tal cual salen del disco: es lo que la app le pasa a la revisión.

        review = series.review_predictions(guardadas,
                                           _barras("2026-07-21", 25, 100), AHORA)
        cal = series.calibration_from_review(review)
        assert cal["samples"] >= 5, cal
        assert cal["bias_pct"] is not None

        sin = TestElSesgoDEVERDADMueveElTarget._predice(None)
        con = TestElSesgoDEVERDADMueveElTarget._predice(cal)
        assert con.base.target != sin.base.target, (
            "seis predicciones vencidas y el target sale idéntico: no calibra")


class TestLosNUMEROSLleganALaPantalla:
    """Calcularlos y no enseñarlos es la otra forma de no tenerlos.

    El error medio se calculaba desde el principio y la ruta lo tiraba, así que
    el panel enseñaba el sesgo —hacia dónde falla— sin el cuánto. Y no son lo
    mismo: +0,2% de sesgo con ±14% de error medio es un agente descalibrado que
    parece calibrado, porque los fallos hacia arriba y hacia abajo se cancelan
    al promediar con signo.
    """

    def test_la_ruta_reenvia_las_cuatro_cifras(self):
        fuente = (ROOT / "vertex_api.py").read_text(encoding="utf-8")
        for clave, viene_de in (("error_medio_pct", "mean_abs_error_pct"),
                                ("sesgo_pct", "bias_pct"),
                                ("dir_hit_rate", "direction_hit_rate"),
                                ("base_touch_rate", "base_touch_rate")):
            assert f'"{clave}": review.get("{viene_de}")' in fuente, (
                f"la ruta ya no reenvía {clave} desde {viene_de}")

    def test_el_panel_pinta_el_error_medio(self):
        html = (ROOT / "vertex_fund_os_platform.html").read_text(encoding="utf-8")
        assert "m.error_medio_pct" in html, (
            "el error medio llega al navegador y no se pinta")
        assert "Error medio del target" in html

    def test_review_predictions_sigue_dando_esas_claves(self, series):
        """Si el motor las renombra, los dos tests de arriba mienten."""
        r = series.review_predictions([_foto("2026-07-20", 100, 106).__dict__],
                                      _barras("2026-07-21", 20, 100), AHORA)
        for k in ("mean_abs_error_pct", "bias_pct", "direction_hit_rate",
                  "base_touch_rate", "matured_count"):
            assert k in r, f"el motor ya no devuelve {k}: la ruta reenviaría None"
