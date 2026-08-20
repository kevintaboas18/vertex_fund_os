"""El panel abierto en un navegador de VERDAD, con los CDN caídos.

Los demás tests miran el HTML como texto, o ejecutan su JavaScript en Node con
`document` simulado. Ninguna de las dos cosas ve lo que este archivo ve:

  · El smoke de Node **define** `window.Chart` y `window.lucide`, así que un
    `Chart.register(...)` sin guarda le pasa por delante sin despeinarse.
  · `node --check` valida la SINTAXIS. Un `ReferenceError` en tiempo de
    ejecución es sintaxis perfecta.

Y lo que había debajo era grave: `Chart.register(targetLineLabelsPlugin)` a
pelo, a nivel de módulo. Con el CDN de Chart.js caído eso lanza y **aborta el
bloque de script entero** — con él se van `_vcEsc`, las 38 funciones de render
del tab, `VC_SENALES` y todo lo declarado más abajo. El resultado no es «el
panel sin gráficas»: es el panel MUERTO, con las tablas en blanco y sin un solo
mensaje que lo explique. Los CDN se caen: una red de hotel, un bloqueador, un
DNS lento.

Este contenedor no tiene salida a los CDN, así que la condición de fallo es la
de por defecto — que es justo lo que hace útil el test aquí.

    python -m pytest tests_vertex/test_navegador.py -q
"""

from __future__ import annotations

import os
import re
import socket
import subprocess
import sys
import time
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
#: Las familias de sectores del MOTOR. El payload de mentira las copia de aquí
#: para que no puedan quedarse atrás el día que se toquen en `sectores.py`.
from engine.wbj.sectores import CATEGORIAS as _CATEGORIAS  # noqa: E402

pytest.importorskip("playwright", reason="hace falta playwright")
from playwright.sync_api import sync_playwright  # noqa: E402

#: Chromium viene preinstalado en la imagen; `PLAYWRIGHT_BROWSERS_PATH` lo dice.
_CHROME = next(
    (str(p) for p in Path(os.environ.get("PLAYWRIGHT_BROWSERS_PATH", "/opt/pw-browsers"))
     .glob("chromium-*/chrome-linux/chrome")), "")
if not _CHROME:
    pytest.skip("hace falta chromium", allow_module_level=True)


def _puerto_libre() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


@pytest.fixture(scope="module")
def servidor():
    """El panel servido por la app real, como en Render."""
    puerto = _puerto_libre()
    env = {**os.environ, "EDGAR_USER_AGENT": "Vertex test@ejemplo.com"}
    p = subprocess.Popen(
        [sys.executable, "-m", "uvicorn", "vertex_api:app",
         "--host", "127.0.0.1", "--port", str(puerto)],
        cwd=str(ROOT), env=env, stdout=subprocess.DEVNULL, stderr=subprocess.STDOUT)
    url = f"http://127.0.0.1:{puerto}/"
    try:
        import urllib.request
        for _ in range(60):
            time.sleep(1)
            if p.poll() is not None:
                pytest.fail("el servidor murió al arrancar")
            try:
                urllib.request.urlopen(url, timeout=3).read()
                break
            except Exception:                          # noqa: BLE001, PERF203
                continue
        else:
            pytest.fail("el servidor no respondió en 60 s")
        yield url
    finally:
        p.terminate()
        try:
            p.wait(timeout=10)
        except subprocess.TimeoutExpired:
            p.kill()


@pytest.fixture(scope="module")
def navegador():
    with sync_playwright() as pw:
        b = pw.chromium.launch(executable_path=_CHROME)
        yield b
        b.close()


def _abre(navegador, url, tema="dark"):
    pg = navegador.new_page(viewport={"width": 1280, "height": 1000})
    errores: list[str] = []
    pg.on("pageerror", lambda e: errores.append(str(e)))
    pg.add_init_script(
        f"try{{localStorage.setItem('vertex_theme','{tema}')}}catch(e){{}}")
    pg.goto(url, wait_until="load")
    pg.wait_for_timeout(2500)
    return pg, errores


class TestElPanelSobreviveASusCDN:
    """Tres `<script src>` externos: Tailwind, Lucide y Chart.js."""

    def test_ni_un_error_de_javascript_con_los_CDN_caidos(self, navegador, servidor):
        pg, errores = _abre(navegador, servidor)
        try:
            assert not errores, (
                "la página lanza al cargar sin CDN, y lo que venga después de "
                f"esa línea no se declara: {errores}")
        finally:
            pg.close()

    def test_todo_lo_del_tab_queda_declarado(self, navegador, servidor):
        """La prueba de que el bloque llegó hasta el final. Si algo lanzó por
        el camino, estas quedan en la zona muerta y `typeof` LANZA (no devuelve
        «undefined»), que es como se descubrió el fallo."""
        pg, _ = _abre(navegador, servidor)
        try:
            hay = pg.evaluate("""() => ({
                _vcEsc: typeof _vcEsc,
                VC_SENALES: typeof VC_SENALES,
                VC_TABS: typeof VC_TABS,
                renderProjTape: typeof renderProjTape,
                renderProjWheel: typeof renderProjWheel,
                renderProjIdeas: typeof renderProjIdeas,
                renderVictorTargets: typeof renderVictorTargets,
                renderProjSalud: typeof renderProjSalud,
                vcIconos: typeof vcIconos,
            })""")
            faltan = [k for k, v in hay.items() if v == "undefined"]
            assert not faltan, f"no llegaron a declararse: {faltan}"
        finally:
            pg.close()

    def test_el_html_del_panel_se_pinta_igual(self, navegador, servidor):
        """Sin Chart.js no hay gráficas; las tablas y el texto tienen que
        seguir estando. Eso es degradarse, no morirse."""
        pg, _ = _abre(navegador, servidor)
        try:
            filas = pg.evaluate("""(d) => {
                document.getElementById('projTape').innerHTML = '';
                renderProjTape(d);
                return document.querySelectorAll('#projTape tbody tr').length;
            }""", _TAPE)
            assert filas == len(_TAPE["trades"]), "la cinta no pintó sus filas"
        finally:
            pg.close()


#: Una cinta mínima, con un trade inusual y otro que no.
_TAPE = {
    "ok": True, "ticker": "WULF", "notable": 2, "total": 9, "period": "1d",
    "pages": 1, "min_premium": 100000,
    "aggression": {"score": 8, "ratio": 0.81, "premium_ask": 3.3e6,
                   "premium_bid": 0.8e6, "premium_mid": 4e5, "n": 2},
    "trades": [
        {"id": 1, "underlying": "WULF", "symbol": "O:W", "type": "call",
         "strike": 20, "expiration": "2027-01-15", "dte": 160, "price": 4.15,
         "size": 500, "premium": 2075000, "aggression": "ask", "side": "buy",
         "bid": 4.10, "ask": 4.20, "delta": 0.62, "iv": 0.55, "unusual": True,
         "unusual_score": 21,
         "unusual_parts": {"volume": 9, "timing": 5, "repetition": 7},
         "repeated": True, "multileg": False, "above_ask": True,
         "below_bid": False, "exceeded_oi": True, "big": True,
         "conv_delta": True, "leap": True, "simultaneous": False,
         "condition_code": None, "timestamp": "2026-08-05T14:00:00Z"},
        {"id": 2, "underlying": "WULF", "symbol": "O:W2", "type": "put",
         "strike": 15, "expiration": "2026-12-18", "dte": 132, "price": 1.02,
         "size": 200, "premium": 120400, "aggression": "bid", "side": "sell",
         "bid": 1.00, "ask": 1.05, "delta": -0.21, "iv": 0.61, "unusual": False,
         "unusual_score": 4,
         "unusual_parts": {"volume": 2, "timing": 1, "repetition": 1},
         "repeated": False, "multileg": True, "above_ask": False,
         "below_bid": True, "exceeded_oi": False, "big": False,
         "conv_delta": False, "leap": False, "simultaneous": True,
         "condition_code": "A", "timestamp": "2026-08-06T16:12:00Z"},
    ],
}


class TestLosDosTemasSonLosQueTocan:
    """En oscuro, los TONOS de Víctor sobre el fondo de Vertex. En claro, su
    diseño entero: sus mismos hexadecimales.

    Se miden con `getComputedStyle`, o sea el color que el navegador pinta de
    verdad — no la clase que se escribió esperando que lo pintara.
    """

    @staticmethod
    def _estilos(pg):
        return pg.evaluate("""(d) => {
            document.getElementById('projTape').innerHTML = '';
            renderProjTape(d);
            const g = s => { const e = document.querySelector(s);
                             return e ? getComputedStyle(e) : null; };
            const tabla = g('#projTape table.vc-t');
            const th = g('#projTape thead th');
            const pill = g('#projTape .vc-pill-call');
            const tr = document.querySelector('#projTape tbody tr.vc-unusual');
            return {
              fondo: getComputedStyle(document.getElementById('projectionsView')).backgroundColor,
              tabular: tabla ? tabla.fontVariantNumeric : null,
              th_bg: th ? th.backgroundColor : null,
              unusual: tr ? getComputedStyle(tr.querySelector('td')).backgroundColor : null,
              pill_bg: pill ? pill.backgroundColor : null,
              pill_color: pill ? pill.color : null,
              pill_caja: pill ? pill.textTransform : null,
            };
        }""", _TAPE)

    def test_en_claro_son_sus_hexadecimales_exactos(self, navegador, servidor):
        pg, _ = _abre(navegador, servidor, tema="light")
        try:
            e = self._estilos(pg)
            assert e["fondo"] == "rgb(243, 244, 246)", "su --bg es #f3f4f6"
            assert e["th_bg"] == "rgb(248, 249, 251)", "su --panel-2 es #f8f9fb"
            assert e["unusual"] == "rgb(255, 243, 201)", "su tr.unusual es #fff3c9"
            assert e["pill_bg"] == "rgb(231, 248, 240)", "su --green-bg es #e7f8f0"
            # Su `--green-dark` (#0e9f5f) sobre su propio relleno (#e7f8f0) da
            # 3,11:1: su fondo teñido es tan claro que el texto encima necesita
            # el mismo tono oscurecido. #0b7e4b = 4,65:1 en la pastilla y en la
            # página. El FONDO sigue siendo el suyo, exacto.
            assert e["pill_color"] == "rgb(11, 126, 75)", "el verde legible de su --green-dark"
        finally:
            pg.close()

    def test_en_oscuro_sigue_siendo_oscuro(self, navegador, servidor):
        """Que la capa clara no se cuele en el tema por defecto."""
        pg, _ = _abre(navegador, servidor, tema="dark")
        try:
            e = self._estilos(pg)
            assert e["fondo"] != "rgb(243, 244, 246)", "el fondo claro se coló en oscuro"
            assert e["th_bg"] == "rgb(11, 14, 20)", "el encabezado no es el oscuro"
        finally:
            pg.close()

    @pytest.mark.parametrize("tema", ["dark", "light"])
    def test_sus_reglas_de_tabla_valen_en_los_dos(self, navegador, servidor, tema):
        """`tabular-nums` y las MAYÚSCULAS de sus pills no dependen del tema:
        son estructura, no color."""
        pg, _ = _abre(navegador, servidor, tema=tema)
        try:
            e = self._estilos(pg)
            assert "tabular-nums" in (e["tabular"] or ""), \
                "sin tabular-nums los dígitos no alinean entre filas"
            assert e["pill_caja"] == "uppercase", "sus pills van en mayúsculas"
        finally:
            pg.close()


def _lum(c):
    c = [x / 255 for x in c]
    c = [x / 12.92 if x <= 0.03928 else ((x + 0.055) / 1.055) ** 2.4 for x in c]
    return 0.2126 * c[0] + 0.7152 * c[1] + 0.0722 * c[2]


def _contraste(a, b):
    l1, l2 = sorted([_lum(a), _lum(b)], reverse=True)
    return (l1 + 0.05) / (l2 + 0.05)


def _rgb(s):
    import re
    return tuple(int(x) for x in re.findall(r"\d+", s)[:3])


class TestEnClaroTodoSeLee:
    """El contraste MEDIDO, no mirado.

    En el móvil de Kevin había cifras que directamente no aparecían: el valor
    de cada tarjeta de estadística usa `text-gray-100`, que ninguna capa clara
    cubría — se quedaba en `#f3f4f6` sobre un fondo `#f3f4f6`, o sea **1,10:1**.
    «1.64%», «15%», «12», el precio de la acción y los empleados eran texto
    blanco sobre blanco.

    Y tres niveles de gris caían en su `--faint` (2,58:1), que es lo que hacía
    ilegible la letra pequeña.

    Sus colores están hechos para RELLENOS —una pastilla, una barra, una
    celda—, donde el contraste lo da el área. Como texto sobre claro no
    llegaban: `--green` 2,62:1, `--amber` 2,35:1, `--put` 2,79:1. Lo que se
    pinta ahora son sus mismos matices bajados de luminosidad hasta 4,6:1
    contra SU PROPIO fondo (`#f3f4f6`, no blanco puro: medir contra blanco daba
    4,2:1 en pantalla, que es lo que pasa por bueno y luego no se ve).
    """

    #: Todas las clases de texto que el panel usa de verdad.
    CLASES = ["text-white", "text-gray-100", "text-gray-200", "text-gray-300",
              "text-gray-400", "text-gray-500", "text-gray-600", "text-gray-700",
              "text-emerald-400", "text-emerald-300", "text-red-400",
              "text-red-300", "text-amber-400", "text-amber-300",
              "text-blue-400", "text-blue-500", "text-blue-300"]

    def test_ninguna_clase_de_texto_baja_de_4_5_a_1(self, navegador, servidor):
        pg, _ = _abre(navegador, servidor, tema="light")
        try:
            d = pg.evaluate("""(clases) => {
                const m = document.getElementById('projectionsView');
                m.classList.remove('hidden');
                const o = {};
                for (const c of clases) {
                    const e = document.createElement('span');
                    e.className = c; e.textContent = 'x';
                    m.appendChild(e); o[c] = getComputedStyle(e).color; e.remove();
                }
                o.__fondo = getComputedStyle(m).backgroundColor;
                return o;
            }""", self.CLASES)
            fondo = _rgb(d.pop("__fondo"))
            flojas = {k: round(_contraste(_rgb(v), fondo), 2)
                      for k, v in d.items() if _contraste(_rgb(v), fondo) < 4.5}
            assert not flojas, (
                f"sobre el fondo claro rgb{fondo} no se leen: {flojas} "
                "(WCAG pide 4.5:1 para texto normal)")
        finally:
            pg.close()

    def test_en_oscuro_tambien_se_lee(self, navegador, servidor):
        """Arreglar el claro no puede estropear el oscuro."""
        pg, _ = _abre(navegador, servidor, tema="dark")
        try:
            d = pg.evaluate("""(clases) => {
                const m = document.getElementById('projectionsView');
                m.classList.remove('hidden');
                const o = {};
                for (const c of clases) {
                    const e = document.createElement('span');
                    e.className = c; e.textContent = 'x';
                    m.appendChild(e); o[c] = getComputedStyle(e).color; e.remove();
                }
                return o;
            }""", self.CLASES)
            # El fondo oscuro del panel de Vertex.
            fondo = (11, 14, 20)
            flojas = {k: round(_contraste(_rgb(v), fondo), 2)
                      for k, v in d.items() if _contraste(_rgb(v), fondo) < 3.0}
            assert not flojas, f"en oscuro no se leen: {flojas}"
        finally:
            pg.close()


class TestElCuestionarioNoEligePorTi:
    """«Si presiono Personalizado me salen ya opciones elegidas.»

    El formulario leía `pfValor`, que cae al valor EFECTIVO — el de Kevin — así
    que las once preguntas aparecían con la respuesta de otra persona ya
    marcada, el capital de otra persona ya escrito y el rango 20–30 ya puesto.
    La insignia decía «valor heredado» al lado, pero lo que se ve manda sobre lo
    que se lee: parecía que lo habías elegido tú.

    Peor era el gemelo: el manejador de las preguntas de opción múltiple también
    partía del valor heredado, así que el PRIMER clic **quitaba** una opción de
    Kevin en vez de añadir la tuya. Pulsabas «crecimiento» y se quedaban
    marcadas «timing» e «ingresos».
    """

    def _perfil(self, navegador, servidor):
        pg, errores = _abre(navegador, servidor)
        pg.evaluate("""async () => {
            await fetch('/api/auth/registro', {method:'POST',
                headers:{'Content-Type':'application/json'},
                body: JSON.stringify({email:'cuestionario@ejemplo.com',
                                      nombre:'Prueba', password:'ClaveLarga123!'})});
        }""")
        pg.reload(wait_until="load")
        pg.wait_for_timeout(2500)
        pg.evaluate("switchView('perfilView'); pfCargar();")
        pg.wait_for_timeout(1800)
        pg.evaluate("pfTab('perfil'); pfModoSel='personalizado'; pfPintaModo();")
        pg.wait_for_timeout(900)
        return pg, errores

    def test_al_entrar_no_hay_nada_elegido(self, navegador, servidor):
        pg, errores = self._perfil(navegador, servidor)
        try:
            d = pg.evaluate("""() => ({
                preguntas: document.querySelectorAll('#pfPreguntas [data-pregunta]').length,
                marcadas: [...document.querySelectorAll('#pfPreguntas .pf-opt.activa')]
                            .map(b => b.dataset.preg + '=' + b.dataset.opt),
                llenas: [...document.querySelectorAll('#pfPreguntas .pf-in')]
                            .filter(i => String(i.value).trim() !== '')
                            .map(i => i.dataset.preg + '=' + i.value),
            })""")
            assert d["preguntas"] >= 10, f"el cuestionario no se pintó: {d}"
            assert not d["marcadas"], f"salen opciones ya elegidas: {d['marcadas']}"
            assert not d["llenas"], f"salen casillas ya rellenas: {d['llenas']}"
            assert not errores, errores[:3]
        finally:
            pg.close()

    def test_el_primer_clic_AÑADE_lo_que_pulsas(self, navegador, servidor):
        pg, errores = self._perfil(navegador, servidor)
        try:
            d = pg.evaluate("""() => {
                const b = document.querySelector('#pfPreguntas .pf-opt');
                const pulsado = b.dataset.preg + '=' + b.dataset.opt;
                b.click();
                return {pulsado,
                        marcadas: [...document.querySelectorAll('#pfPreguntas .pf-opt.activa')]
                                    .map(x => x.dataset.preg + '=' + x.dataset.opt)};
            }""")
            assert d["marcadas"] == [d["pulsado"]], (
                f"pulsaste {d['pulsado']} y quedó marcado {d['marcadas']}: el clic "
                "está operando sobre la lista heredada")
            assert not errores, errores[:3]
        finally:
            pg.close()

    def test_borrar_una_casilla_no_la_deja_contestada_con_un_cero(self, navegador, servidor):
        pg, errores = self._perfil(navegador, servidor)
        try:
            d = pg.evaluate("""() => {
                const i = document.querySelector('#pfPreguntas input[type=number]');
                const pon = v => { i.value = v;
                                   i.dispatchEvent(new Event('input', {bubbles:true})); };
                pon('5000');
                const escrito = pfRespuestas[i.dataset.preg];
                pon('');
                return {escrito, tras_borrar: pfRespuestas[i.dataset.preg] ?? null};
            }""")
            assert d["escrito"] == 5000
            assert d["tras_borrar"] is None, (
                "vaciar la casilla se guardó como una respuesta: el capital quedaría "
                f"en {d['tras_borrar']}")
            assert not errores, errores[:3]
        finally:
            pg.close()


class TestNoSeArrastraElTickerViejo:
    """Un precio de hace media hora no se distingue de uno de hace un segundo.

    Dos sitios dejaban dato viejo en pantalla:

    · **Al volver al tab.** Analizabas WULF, te ibas al Dashboard y al regresar
      seguía todo puesto: cabecera, escenarios, niveles y cinta de un momento
      que ya pasó.
    · **Al pedir otro ticker.** La cabecera de arriba a la derecha vive FUERA de
      `projContent`, así que esconder el contenido nunca la tocaba: mientras
      cargaba NVDA se leían el nombre y el precio de WULF, como si el cargador
      estuviera trabajando sobre esa.
    """

    #: Lo mínimo que `vcSyncCabecera` necesita para pintar la cabecera.
    @staticmethod
    def _payload(tk, nombre, precio):
        return {"ok": True, "ticker": tk, "spot": precio,
                "company": {"ticker": tk, "name": nombre, "price": precio,
                            "change_percent": 1.9},
                "gex": {"regime": "positive"}}

    @staticmethod
    def _pinta_analisis(pg, payload):
        pg.evaluate("""(p) => {
            document.getElementById('projTicker').value = p.ticker;
            projData = p;
            document.getElementById('projEmpty').classList.add('hidden');
            document.getElementById('projContent').classList.remove('hidden');
            document.getElementById('projTape').innerHTML = '<b>cinta</b>';
            vcTabCargada.tape = true;
            vcSyncCabecera(p);
        }""", payload)

    @staticmethod
    def _estado(pg):
        return pg.evaluate("""() => ({
            entrada: (document.getElementById('projTicker')||{}).value,
            cabecera: (document.getElementById('projHbRight')||{}).innerText.trim(),
            vacio_visible: !document.getElementById('projEmpty').classList.contains('hidden'),
            contenido_oculto: document.getElementById('projContent').classList.contains('hidden'),
            cinta: (document.getElementById('projTape')||{}).innerHTML.length,
            datos: !!projData,
        })""")

    def test_salir_del_tab_borra_el_analisis(self, navegador, servidor):
        pg, errores = _abre(navegador, servidor)
        try:
            pg.evaluate("switchView('projectionsView')")
            pg.wait_for_timeout(500)
            self._pinta_analisis(pg, self._payload("WULF", "TeraWulf Inc.", 12.34))
            pg.wait_for_timeout(300)
            antes = self._estado(pg)
            assert "TeraWulf" in antes["cabecera"], antes

            pg.evaluate("switchView('homeView')")
            pg.wait_for_timeout(300)
            pg.evaluate("switchView('projectionsView')")
            pg.wait_for_timeout(500)
            d = self._estado(pg)
            assert d["entrada"] == "", f"el ticker sigue escrito: {d}"
            assert d["cabecera"] == "", f"la cabecera sigue con lo viejo: {d}"
            assert d["vacio_visible"] and d["contenido_oculto"], d
            assert d["cinta"] == 0, "la cinta del ticker viejo sigue puesta"
            assert not d["datos"], "`projData` no se soltó"
            assert not errores, errores[:3]
        finally:
            pg.close()

    def test_al_pedir_otro_ticker_la_cabecera_no_se_queda_con_el_anterior(
            self, navegador, servidor):
        pg, errores = _abre(navegador, servidor)
        try:
            pg.evaluate("switchView('projectionsView')")
            pg.wait_for_timeout(500)
            self._pinta_analisis(pg, self._payload("WULF", "TeraWulf Inc.", 12.34))
            pg.wait_for_timeout(300)
            assert "TeraWulf" in self._estado(pg)["cabecera"]

            # Se mira CON LA PETICIÓN EN VUELO, que es cuando se veía el fallo.
            pg.evaluate("loadProjections('NVDA')")
            pg.wait_for_timeout(250)
            d = self._estado(pg)
            assert d["cabecera"] == "", (
                f"mientras carga NVDA se sigue leyendo lo de antes: {d['cabecera']!r}")
            assert d["entrada"] == "NVDA", f"la entrada no siguió al nuevo: {d}"
            assert d["contenido_oculto"], d
            assert not d["datos"], "`projData` viejo seguiría alimentando los paneles"
            assert not errores, errores[:3]
        finally:
            pg.close()


class TestElRangoPorPosicionSeEntiende:
    """«Salen (_-_), o sea en blanco - en blanco.»

    Los dos extremos no son decorativos y el motor los usa distinto: el MÁXIMO
    es el tope —pasarse cuenta como infracción (`within_position_cap`)— y el
    MÍNIMO es el suelo de despliegue —quedarse por debajo se reporta como
    información, no como falta (`below_intended_sizing`)—. Dos casillas vacías
    con una raya en medio no dicen nada de eso.
    """

    def _abre_perfil(self, navegador, servidor, correo):
        pg, errores = _abre(navegador, servidor)
        pg.evaluate("""async (c) => {
            await fetch('/api/auth/registro', {method:'POST',
                headers:{'Content-Type':'application/json'},
                body: JSON.stringify({email:c, nombre:'R', password:'ClaveLarga123!'})});
        }""", correo)
        pg.reload(wait_until="load")
        pg.wait_for_timeout(2500)
        pg.evaluate("switchView('perfilView'); pfCargar();")
        pg.wait_for_timeout(1800)
        pg.evaluate("pfTab('perfil'); pfModoSel='personalizado'; pfPintaModo();")
        pg.wait_for_timeout(800)
        return pg, errores

    def test_cada_casilla_dice_cual_es_y_enseña_un_ejemplo(self, navegador, servidor):
        pg, errores = self._abre_perfil(navegador, servidor, "rango1@ejemplo.com")
        try:
            d = pg.evaluate("""() => {
                const n = document.querySelector('[data-pregunta="max_posicion_pct"]');
                const ins = [...n.querySelectorAll('input')];
                return {rotulos: [...n.querySelectorAll('label span')].map(s => s.innerText.trim()),
                        ejemplos: ins.map(i => i.placeholder),
                        valores: ins.map(i => i.value),
                        nota: n.querySelector('.pf-rango').innerText};
            }""")
            assert d["rotulos"] == ["mín", "máx"], d
            assert all(d["ejemplos"]), "sin ejemplo, la casilla vacía no sugiere nada"
            assert d["valores"] == ["", ""], "no puede salir premarcado"
            assert "mínimo" in d["nota"] and "máximo" in d["nota"], d["nota"]
            assert not errores, errores[:3]
        finally:
            pg.close()

    def test_el_porcentaje_se_traduce_a_dolares_de_TU_capital(self, navegador, servidor):
        """«30 – 70» no es una decisión hasta que se lee «$300 – $700»."""
        pg, errores = self._abre_perfil(navegador, servidor, "rango2@ejemplo.com")
        try:
            nota = pg.evaluate("""() => {
                const cap = document.querySelector('[data-pregunta="capital"] input');
                cap.value = '1000'; cap.dispatchEvent(new Event('input', {bubbles:true}));
                const ins = document.querySelectorAll('[data-pregunta="max_posicion_pct"] input');
                ins[0].value = '30'; ins[0].dispatchEvent(new Event('input', {bubbles:true}));
                ins[1].value = '70'; ins[1].dispatchEvent(new Event('input', {bubbles:true}));
                return document.querySelector(
                    '[data-pregunta="max_posicion_pct"] .pf-rango').innerText;
            }""")
            assert "30%" in nota and "70%" in nota, nota
            assert "$300" in nota and "$700" in nota, (
                f"el rango no se tradujo a dinero: {nota!r}")
            assert not errores, errores[:3]
        finally:
            pg.close()


class TestNoSeAnalizaSinElegirEmpresa:
    """«No quiero que me aparezca correr análisis sin yo elegir una acción.»

    Tecleando "AAPL" salían DOS cosas a la vez: la lista de empresas arriba y,
    500 ms después, la ficha con el precio y el botón «Correr el análisis»
    debajo. Mientras leías la lista para elegir, ya tenías bajo el cursor un
    botón que analizaba lo que hubiera escrito —una empresa que nunca elegiste—.

    El orden que se pidió es el de un buscador: escribes, eliges, y SOLO
    entonces aparece la acción con su precio y la opción de analizarla.
    """

    _SUG = {"q": "AAPL", "resultados": [
        {"ticker": "AAPL", "nombre": "Apple Inc.", "bolsa": "NASDAQ"},
        {"ticker": "AAP", "nombre": "Advance Auto Parts", "bolsa": "NYSE"}]}
    _QUOTE = {"ticker": "AAPL", "nombre_completo": "Apple Inc.",
              "precio": "302.25", "cambio_pct": -0.87, "volumen": "38.82M",
              "vwap": "302.83", "high": "305.66", "low": "300.57",
              "fuente": "fmp", "logo_url": ""}

    def _abre(self, navegador, servidor):
        pg, errores = _abre(navegador, servidor)
        pg.route("**/api/search*", lambda r: r.fulfill(status=200, json=self._SUG))
        pg.route("**/api/quote*", lambda r: r.fulfill(status=200, json=self._QUOTE))
        pg.route("**/api/tito-logo*", lambda r: r.fulfill(
            status=200, content_type="image/svg+xml",
            body='<svg xmlns="http://www.w3.org/2000/svg" width="8" height="8">'
                 '<circle cx="4" cy="4" r="3" fill="#3b82f6"/></svg>'))
        pg.evaluate("switchView('homeView')")
        return pg, errores

    @staticmethod
    def _estado(pg):
        return pg.evaluate("""() => ({
            sugerencias: !document.getElementById('tickerSuggest')
                            .classList.contains('hidden'),
            n_sug: document.querySelectorAll('#tickerSuggest .sugg').length,
            ficha: !document.getElementById('previewCard').classList.contains('hidden'),
            ticker: (document.getElementById('previewTicker') || {}).innerText,
            precio: (document.getElementById('previewPrice') || {}).innerText,
            logo: (document.getElementById('previewLogo') || {}).innerHTML,
        })""")

    def test_tecleando_salen_las_empresas_pero_no_el_boton_de_analizar(
            self, navegador, servidor):
        pg, errores = self._abre(navegador, servidor)
        try:
            pg.fill("#tickerInput", "AAPL")
            pg.dispatch_event("#tickerInput", "keyup")
            pg.wait_for_timeout(1500)          # más que los 500 ms de antes
            d = self._estado(pg)
            assert d["sugerencias"] and d["n_sug"] == 2, d
            assert not d["ficha"], (
                "la ficha con el botón «Correr el análisis» salió sin elegir")
            assert not errores, errores[:3]
        finally:
            pg.close()

    def test_al_elegir_sale_la_accion_con_su_precio_y_su_logo(
            self, navegador, servidor):
        pg, errores = self._abre(navegador, servidor)
        try:
            pg.fill("#tickerInput", "AAPL")
            pg.dispatch_event("#tickerInput", "keyup")
            pg.wait_for_timeout(900)
            pg.evaluate("pickSuggest(0)")
            pg.wait_for_timeout(1200)
            d = self._estado(pg)
            assert d["ficha"], "elegir una empresa no enseñó su ficha"
            assert "AAPL" in (d["ticker"] or ""), d
            assert "302.25" in (d["precio"] or ""), d
            assert not d["sugerencias"], "la lista se quedó abierta"
            assert "tito-logo" in (d["logo"] or ""), (
                "el logo no sale de nuestro servidor: " + (d["logo"] or "")[:120])
            assert not errores, errores[:3]
        finally:
            pg.close()

    def test_enter_elige_la_primera_en_vez_de_lanzar_el_analisis(
            self, navegador, servidor):
        """Enter con la lista abierta era «analiza ya»; ahora es «elijo esta»."""
        pg, errores = self._abre(navegador, servidor)
        lanzados = []
        pg.route("**/api/analyze*", lambda r: (lanzados.append(r.request.url),
                                               r.fulfill(status=200, json={})))
        try:
            pg.fill("#tickerInput", "AAPL")
            pg.dispatch_event("#tickerInput", "keyup")
            pg.wait_for_timeout(900)
            pg.press("#tickerInput", "Enter")
            pg.wait_for_timeout(1200)
            d = self._estado(pg)
            assert d["ficha"] and "AAPL" in (d["ticker"] or ""), d
            assert not lanzados, f"Enter lanzó el análisis solo: {lanzados}"
            assert not errores, errores[:3]
        finally:
            pg.close()


class TestElLogoSaleEnLasTresAreas:
    """El logo roto de la captura: Analyze pedía a `logo.clearbit.com`.

    Ese tercero cerró su API gratuita, así que el `<img>` daba 404 y quedaba el
    recuadro vacío. Proyecciones no se enteró porque siempre pidió a
    `/api/tito-logo`, que lo sirve nuestro propio servidor. Ahora las tres áreas
    —Analyze, Proyecciones y los reportes— pasan por la misma función.
    """

    def test_ninguna_area_pide_el_logo_a_un_tercero(self):
        html = (ROOT / "vertex_fund_os_platform.html").read_text(encoding="utf-8")
        vivos = [ln for ln in html.splitlines()
                 if "clearbit" in ln and not ln.lstrip().startswith("*")]
        assert not vivos, f"queda una petición a un tercero: {vivos[:2]}"

    def test_hay_iniciales_de_repuesto_cuando_no_existe_el_logo(self, navegador,
                                                               servidor):
        """Un ticker sin logo devuelve 404: sin repuesto quedaría un hueco."""
        pg, errores = _abre(navegador, servidor)
        try:
            d = pg.evaluate("""() => {
                const c = document.createElement('div');
                c.innerHTML = vxLogoHTML('WULF');
                const img = c.querySelector('img');
                img.dispatchEvent(new Event('error'));
                return {html: c.innerHTML.trim(), texto: c.innerText.trim()};
            }""")
            assert "<img" not in d["html"], d
            assert d["texto"] == "WU", d
            assert not errores, errores[:3]
        finally:
            pg.close()


class TestSectoresSeAlcanzaDesdeElMOVIL:
    """«Le di deploy y no me sale nada.»

    La pestaña nueva se añadió a la barra de links del escritorio, que vive
    dentro de un `hidden lg:flex`. En un teléfono esa barra no existe: el único
    camino a las secciones es el menú de la marca, y ese seguía mandando
    «Dashboard» a la pantalla vieja. La pantalla estaba entera, desplegada y
    funcionando, y no había forma de llegar a ella con el dedo.

    Por eso este caso mide con el ancho de un teléfono y no con el de un
    portátil: el fallo solo existe por debajo del punto de corte.
    """

    #: 390×844 — un teléfono normal, por debajo del `lg` de Tailwind.
    MOVIL = {"width": 390, "height": 844}

    _FILA = {"sma200": 95.0, "sma200_dist": 5.26,
             "cambios": {"1D": 0.5, "7D": 1.0, "1M": 2.0,
                         "3M": 3.0, "6M": 4.0, "1A": 5.0}}

    @classmethod
    def _parrilla(cls):
        secs = ["XLK", "XLF", "XLV", "XLY", "XLC", "XLI",
                "XLP", "XLE", "XLU", "XLRE", "XLB"]
        fila = lambda t: {"ticker": t, "nombre": t, "precio": 100.0,          # noqa: E731
                          "cambio_pct": 0.5, "rsi": 50.0, **cls._FILA}
        return {"ok": True, "etf": "", "cacheado": False,
                "referencias": ["SPY", "RSP", "QQQ"], "sectores": secs,
                "filas": [fila(t) for t in ["SPY", "RSP", "QQQ"] + secs],
                "rotacion": {"disponible": False, "motivo": ""}}

    def _abre(self, navegador, servidor, ruta=None):
        pg = navegador.new_page(viewport=self.MOVIL)
        errores: list[str] = []
        pg.on("pageerror", lambda e: errores.append(str(e)))
        pg.route("**/api/sectores*", ruta or (lambda r: r.fulfill(
            status=200, json=self._parrilla())))
        pg.goto(servidor, wait_until="load")
        pg.wait_for_timeout(2500)
        return pg, errores

    def test_el_menu_del_movil_lleva_a_sectores_y_a_analyze(self, navegador,
                                                            servidor):
        pg, errores = self._abre(navegador, servidor)
        try:
            vistas = pg.evaluate(
                "() => [...document.querySelectorAll('#mobileNav [data-view]')]"
                ".map(b => b.getAttribute('data-view'))")
            assert "sectorsView" in vistas, (
                "el menú del móvil no llega a Sectores: en un teléfono la "
                f"pantalla es inalcanzable. Hay {vistas}")
            assert "homeView" in vistas, "y Analyze tiene que seguir alcanzable"
            assert not errores, errores[:3]
        finally:
            pg.close()

    def test_y_la_parrilla_se_pinta_entera_en_un_telefono(self, navegador,
                                                          servidor):
        pg, errores = self._abre(navegador, servidor)
        try:
            pg.evaluate("mobileGo('sectorsView')")
            pg.wait_for_timeout(1500)
            d = pg.evaluate("""() => ({
                visible: !document.getElementById('sectorsView').classList.contains('hidden'),
                pulsables: document.querySelectorAll(
                    '#sectoresParrilla button[onclick^="abreSector"]').length,
                ventanas: [...document.querySelectorAll('#sectoresParrilla button')]
                    .slice(0, 5).map(b => b.innerText.trim()),
                texto: document.getElementById('sectoresParrilla').innerText,
            })""")
            assert d["visible"], "Sectores no se abrió desde el menú del móvil"
            assert d["pulsables"] == 11, (
                f"{d['pulsables']} sectores pulsables, deberían ser 11")
            assert d["ventanas"][:5] == ["7D", "1M", "3M", "6M", "1A"], d["ventanas"]
            assert "SPY" in d["texto"] and "SMA200" in d["texto"], d["texto"][:200]
            assert not errores, errores[:3]
        finally:
            pg.close()


class TestLaParrillaNuncaSeQuedaMUDA:
    """La otra forma de «no me sale nada», y la peor.

    Panel nuevo contra servidor viejo —el hueco entre que el navegador recarga
    y el backend termina de desplegar— devuelve un 404 con cuerpo
    `{"detail": "Not Found"}`: JSON válido, sin `ok` y sin `filas`. El código
    solo miraba `ok === false`, así que esa respuesta se colaba y se pintaba una
    parrilla VACÍA sin un mensaje. Parece que el mercado no existe.

    O hay parrilla, o hay motivo. En blanco, nunca.
    """

    def _texto_con(self, navegador, servidor, ruta):
        pg = navegador.new_page(viewport=TestSectoresSeAlcanzaDesdeElMOVIL.MOVIL)
        errores: list[str] = []
        pg.on("pageerror", lambda e: errores.append(str(e)))
        pg.route("**/api/sectores*", ruta)
        pg.goto(servidor, wait_until="load")
        pg.wait_for_timeout(2200)
        pg.evaluate("switchView('sectorsView')")
        pg.wait_for_timeout(1500)
        txt = pg.evaluate(
            "() => document.getElementById('sectoresParrilla').innerText.trim()")
        pg.close()
        return txt, errores

    def test_un_404_del_servidor_lo_DICE(self, navegador, servidor):
        txt, errores = self._texto_con(navegador, servidor, lambda r: r.fulfill(
            status=404, json={"detail": "Not Found"}))
        assert txt, "parrilla vacía y sin mensaje: el fallo mudo"
        assert "404" in txt or "desplegar" in txt, txt
        assert not errores, errores[:3]

    def test_una_respuesta_sin_filas_tambien(self, navegador, servidor):
        txt, errores = self._texto_con(navegador, servidor, lambda r: r.fulfill(
            status=200, json={"ok": True, "filas": []}))
        assert txt, "parrilla vacía y sin mensaje"
        assert not errores, errores[:3]

    def test_y_la_degradacion_declarada_enseña_SU_motivo(self, navegador,
                                                         servidor):
        """Sin clave de FMP no hay precios, y eso se explica con su frase."""
        txt, errores = self._texto_con(navegador, servidor, lambda r: r.fulfill(
            status=200, json={"ok": False, "filas": [],
                              "error": "Falta FMP_API_KEY: sin ella no hay "
                                       "precio ni RSI de los sectores."}))
        assert "FMP_API_KEY" in txt, txt
        assert not errores, errores[:3]


class TestTodoLoDelEscritorioSeAlcanzaEnElMOVIL:
    """La regla, convertida en guardián: **lo que se sube vale para todos los
    dispositivos**.

    La barra de links vive en un `hidden lg:flex`, así que en un teléfono no
    existe. Añadir una pestaña ahí y creer que ya está es exactamente cómo se
    desplegó una pantalla entera —Sectores— a la que en el móvil no había
    manera de llegar con el dedo: funcionando, servida y muda.

    Esto no mira una pantalla concreta: recorre TODAS las de la barra y exige
    que cada una esté también en el menú del móvil. Añadir una pestaña nueva y
    olvidar el móvil hace fallar este caso, no al usuario.
    """

    def test_ninguna_seccion_se_queda_solo_en_el_escritorio(self, navegador,
                                                            servidor):
        pg, errores = _abre(navegador, servidor)
        try:
            d = pg.evaluate("""() => {
                const barra = document.querySelector('nav .hidden.lg\\\\:flex');
                const sacaVista = s => (String(s || '').match(
                    /switchView\\('([A-Za-z]+View)'\\)/) || [])[1];
                const escritorio = [...(barra ? barra.querySelectorAll('button') : [])]
                    .map(b => sacaVista(b.getAttribute('onclick')))
                    .filter(Boolean);
                const movil = [...document.querySelectorAll('#mobileNav [data-view]')]
                    .map(b => b.getAttribute('data-view'));
                return {escritorio, movil};
            }""")
            assert len(d["escritorio"]) >= 4, (
                f"el escaneo no encontró la barra: {d}")
            faltan = [v for v in d["escritorio"] if v not in d["movil"]]
            assert not faltan, (
                f"{faltan} está en la barra del escritorio y NO en el menú del "
                f"móvil: en un teléfono esas pantallas son inalcanzables. "
                f"El menú tiene {d['movil']}")
            assert not errores, errores[:3]
        finally:
            pg.close()

    def test_y_el_menu_del_movil_no_apunta_a_pantallas_que_no_existen(
            self, navegador, servidor):
        """La otra cara: una entrada que lleva a una vista borrada abre la nada."""
        pg, errores = _abre(navegador, servidor)
        try:
            huerfanas = pg.evaluate("""() =>
                [...document.querySelectorAll('#mobileNav [data-view]')]
                    .map(b => b.getAttribute('data-view'))
                    .filter(v => !document.getElementById(v))""")
            assert not huerfanas, f"el menú del móvil lleva a la nada: {huerfanas}"
            assert not errores, errores[:3]
        finally:
            pg.close()


class TestElSectorSeAbreComoSECCION:
    """«Que si presiono XLK ya esté escrito todo y solo extraiga los números.»

    Dos cosas en una. La primera es de navegación: XLK abre su propia sección
    con `Dashboard › XLK` arriba, no un despliegue debajo de la parrilla.

    La segunda es la que se nota: los tickers y los nombres de las industrias
    NO se piden. Están escritos en el panel, porque no cambian nunca, así que
    la sección aparece entera al instante y solo los números viajan. Antes la
    lista empezaba con «Leyendo las industrias de…» y se quedaba un segundo
    largo en blanco esperando a saber algo que ya sabíamos.
    """

    _CAMBIOS = {"1D": 0.5, "7D": 1.0, "1M": 2.0, "3M": 3.0, "6M": 4.0, "1A": 5.0}

    @classmethod
    def _fila(cls, t, n="—"):
        return {"ticker": t, "nombre": n, "precio": 100.0, "cambio_pct": 0.5,
                "rsi": 50.0, "sma200": 95.0, "sma200_dist": 5.26,
                "cambios": dict(cls._CAMBIOS)}

    @classmethod
    def _parrilla(cls):
        secs = ["XLK", "XLF", "XLV", "XLY", "XLC", "XLI",
                "XLP", "XLE", "XLU", "XLRE", "XLB"]
        return {"ok": True, "etf": "", "cacheado": False,
                "referencias": ["SPY", "RSP", "QQQ"], "sectores": secs,
                "filas": [cls._fila(t, t) for t in ["SPY", "RSP", "QQQ"] + secs],
                "rotacion": {"disponible": False, "motivo": ""}}

    def _abre(self, navegador, servidor, matar_industrias=False):
        pg = navegador.new_page(viewport={"width": 390, "height": 844})
        errores: list[str] = []
        pg.on("pageerror", lambda e: errores.append(str(e)))

        def enruta(r):
            # El orden de `route` no decide: se mira la URL dentro. Playwright
            # da prioridad a la ÚLTIMA registrada, y con dos rutas la genérica
            # se comía la del sector.
            if "tickers=" in r.request.url:
                if matar_industrias:
                    r.abort()
                else:
                    r.fulfill(status=200, json={
                        "ok": True, "tickers": ["SMH", "IGV"],
                        "filas": [self._fila("SMH", "Semiconductores"),
                                  self._fila("IGV", "Software")]})
            else:
                r.fulfill(status=200, json=self._parrilla())

        pg.route("**/api/sectores*", enruta)
        pg.goto(servidor, wait_until="load")
        pg.wait_for_timeout(2400)
        pg.evaluate("switchView('sectorsView')")
        pg.wait_for_timeout(1200)
        return pg, errores

    def test_XLK_abre_su_propia_seccion_y_esconde_la_parrilla(self, navegador,
                                                              servidor):
        pg, errores = self._abre(navegador, servidor)
        try:
            pg.evaluate("abreSector('XLK')")
            pg.wait_for_timeout(900)
            d = pg.evaluate("""() => ({
                seccion: !document.getElementById('sectorView').classList.contains('hidden'),
                parrilla: !document.getElementById('sectorsView').classList.contains('hidden'),
                miga: document.querySelector('#sectorView nav').innerText,
                cabecera: document.getElementById('sectorCabecera').innerText,
            })""")
            assert d["seccion"], "XLK no abrió su sección"
            assert not d["parrilla"], "la parrilla tiene que quedarse detrás"
            assert "XLK" in d["miga"], d["miga"]
            assert "XLK" in d["cabecera"], d["cabecera"]
            assert not errores, errores[:3]
        finally:
            pg.close()

    def test_la_miga_devuelve_al_Dashboard(self, navegador, servidor):
        pg, errores = self._abre(navegador, servidor)
        try:
            pg.evaluate("abreSector('XLE')")
            pg.wait_for_timeout(800)
            pg.click("#sectorView nav button")
            pg.wait_for_timeout(800)
            d = pg.evaluate("""() => ({
                parrilla: !document.getElementById('sectorsView').classList.contains('hidden'),
                seccion: !document.getElementById('sectorView').classList.contains('hidden'),
            })""")
            assert d["parrilla"] and not d["seccion"], d
            assert not errores, errores[:3]
        finally:
            pg.close()

    def test_las_industrias_salen_AUNQUE_el_servidor_no_conteste(self, navegador,
                                                                 servidor):
        """La prueba de que no se espera a nadie: se TUMBA la petición.

        Una pausa no serviría — el driver síncrono la cuenta antes de devolver
        el control y el test se engañaría solo. Con la petición muerta, si los
        nombres están, es que nunca dependieron de ella.
        """
        pg, errores = self._abre(navegador, servidor, matar_industrias=True)
        try:
            pg.evaluate("abreSector('XLK')")
            pg.wait_for_timeout(1200)
            filas = pg.evaluate(
                "() => [...document.querySelectorAll('#sectorIndustrias [data-ind]')]"
                ".map(x => x.innerText.replace(/\\n/g, ' '))")
            # Los ETF esperados se leen de la TABLA, no se copian aquí. Copiarlos
            # ya salió caro una vez: la lista llevaba XSD, se cambió por AIQ al
            # completar las industrias, y este caso se puso rojo por estar
            # desactualizado —no por un fallo del panel—.
            esperados = pg.evaluate("() => VX_INDUSTRIAS['XLK'].map(x => x[0])")
            assert len(filas) == len(esperados), (
                f"XLK tiene {len(esperados)} industrias, salieron {len(filas)}")
            texto = " ".join(filas)
            for esperado in esperados:
                assert esperado in texto, f"falta {esperado}: {texto}"
            assert "Semiconductores" in texto and "Software" in texto
            assert "···" in texto, (
                "los números tendrían que quedarse en puntos suspensivos")
            assert not errores, errores[:3]
        finally:
            pg.close()

    def test_LOS_ONCE_sectores_tienen_sus_industrias_escritas(self, navegador,
                                                              servidor):
        """«Cuando entro al sector me salen algunas industrias.»

        Este caso medía lo contrario: abría XLU, que era el que no tenía NI UNA,
        y comprobaba que lo dijera. Decirlo estaba bien; que once sectores
        tuvieran uno vacío, no. Ahora los once traen las suyas, y lo que se mide
        es eso — que ninguno se quede en la frase de consuelo.
        """
        pg, errores = self._abre(navegador, servidor)
        try:
            sectores = pg.evaluate("() => VX_SECTORES.sectores.map(x => x[0])")
            for tk in sectores:
                pg.evaluate(f"abreSector('{tk}')")
                pg.wait_for_timeout(250)
                txt = pg.evaluate(
                    "() => document.getElementById('sectorIndustrias').innerText")
                assert txt.strip(), f"{tk}: ni lista ni motivo, la sección se queda muda"
                assert "desglose" not in txt, (
                    f"{tk} sigue sin industrias escritas: {txt[:120]}")
            assert not errores, errores[:3]
        finally:
            pg.close()

    def test_pero_un_ETF_sin_desglose_lo_SIGUE_diciendo(self, navegador,
                                                        servidor):
        """La rama sigue viva: si mañana entra un ETF sin industrias escritas,
        la sección lo cuenta en vez de quedarse en blanco."""
        pg, errores = self._abre(navegador, servidor)
        try:
            pg.evaluate("""() => {
                VX_INDUSTRIAS['XLU_PRUEBA'] = [];
                abreSector('XLU_PRUEBA');
            }""")
            pg.wait_for_timeout(400)
            txt = pg.evaluate(
                "() => document.getElementById('sectorIndustrias').innerText")
            assert "desglose" in txt, txt
            assert not errores, errores[:3]
        finally:
            pg.close()


class TestElSelectorDeVentanaFUNCIONAEnLosTresPisos:
    """«Desde el Dashboard sí me deja cambiar la fecha; dentro de un sector no.»

    Y era cierto: `vxPonVentana` repintaba mirando `window._sectorAbierto`, una
    variable que dejó de existir al partir la pantalla en tres pisos. Dentro de
    un sector o de una industria el clic guardaba la elección y no repintaba
    nada, así que los botones parecían no hacer caso.

    Aquí se mide donde fallaba: dentro.
    """

    _CAMBIOS = {"7D": 1.0, "1M": 2.0, "3M": 3.0, "6M": 4.0, "1A": 5.0}

    @classmethod
    def _fila(cls, t, n=None, mult=1.0):
        return {"ticker": t, "nombre": n or t, "precio": 100.0,
                "cambio_pct": 0.5, "rsi": 60.0, "sma200": 95.0,
                "sma200_dist": 5.0,
                "cambios": {k: v * mult for k, v in cls._CAMBIOS.items()}}

    @classmethod
    def _amp(cls):
        return {v: {"n": 2, "fuertes": ["SMH"], "debiles": [], "neutrales": [],
                    "confianza": "alta", "frase": f"reparto de {v}",
                    "empujan": [], "frenan": []}
                for v in cls._CAMBIOS}

    def _abre(self, navegador, servidor):
        pg = navegador.new_page(viewport={"width": 390, "height": 900})
        errores: list[str] = []
        pg.on("pageerror", lambda e: errores.append(str(e)))
        secs = ["XLK", "XLF", "XLV", "XLY", "XLC", "XLI",
                "XLP", "XLE", "XLU", "XLRE", "XLB"]

        # Los nombres bonitos de los dos que se miran; el resto se queda con su
        # ticker, que para este caso da igual.
        nombres = {"SMH": "Semiconductores", "NVDA": "NVIDIA"}

        def enruta(r):
            u = r.request.url
            if "tickers=" in u:
                # Se DEVUELVE lo que se pidió. Antes contestaba siempre con SMH,
                # así que al entrar en una industria las empresas no recibían
                # número nunca y el selector parecía no repintar: el caso medía
                # el arnés, no el panel.
                from urllib.parse import parse_qs, unquote, urlparse

                pedidos = [t for t in unquote(
                    parse_qs(urlparse(u).query).get("tickers", [""])[0]
                ).split(",") if t]
                r.fulfill(status=200, json={
                    "ok": True, "tickers": pedidos,
                    "filas": [self._fila(t, nombres.get(t), 2.0)
                              for t in pedidos],
                    "amplitud": self._amp()})
            else:
                r.fulfill(status=200, json={
                    "ok": True, "referencias": ["SPY", "RSP", "QQQ"],
                    "sectores": secs,
                    "filas": [self._fila(t) for t in ["SPY", "RSP", "QQQ"] + secs],
                    "amplitud": self._amp(),
                    "rotacion": {"disponible": False, "motivo": ""}})

        import re as _re
        pg.route(_re.compile(r"/api/sectores"), enruta)
        pg.goto(servidor, wait_until="load")
        pg.wait_for_timeout(2400)
        pg.evaluate("switchView('sectorsView')")
        pg.wait_for_timeout(1200)
        return pg, errores

    def test_1D_ya_no_esta_entre_las_opciones(self, navegador, servidor):
        pg, errores = self._abre(navegador, servidor)
        try:
            botones = pg.evaluate(
                "() => [...document.querySelectorAll('#sectoresParrilla button')]"
                ".slice(0, 5).map(b => b.innerText.trim())")
            assert botones == ["7D", "1M", "3M", "6M", "1A"], botones
            assert pg.evaluate("() => vxVentana()") == "7D", "el nuevo por defecto"
            assert not errores, errores[:3]
        finally:
            pg.close()

    def test_un_1D_guardado_de_antes_no_deja_la_pantalla_sin_ventana(
            self, navegador, servidor):
        """Quien lo tuviera guardado se quedaría mirando una ventana que ya no
        existe y sin ningún botón marcado."""
        pg, errores = self._abre(navegador, servidor)
        try:
            v = pg.evaluate("""() => {
                localStorage.setItem('vertex_ventana', '1D');
                return vxVentana();
            }""")
            assert v == "7D", v
            assert not errores, errores[:3]
        finally:
            pg.close()

    def test_DENTRO_de_un_sector_el_selector_repinta(self, navegador, servidor):
        pg, errores = self._abre(navegador, servidor)
        try:
            pg.evaluate("abreSector('XLK')")
            pg.wait_for_timeout(1100)
            lee = """() => ({
                fila: document.querySelector('#sectorIndustrias [data-ind]').innerText.replace(/\\n/g, ' '),
                amp: document.getElementById('sectorAmplitud').innerText,
                miga: document.getElementById('sectorMigas').innerText.replace(/\\n/g, ''),
            })"""
            pg.evaluate("vxPonVentana('7D')")
            pg.wait_for_timeout(700)
            a = pg.evaluate(lee)
            pg.evaluate("vxPonVentana('1A')")
            pg.wait_for_timeout(700)
            b = pg.evaluate(lee)
            assert a["fila"] != b["fila"], (
                f"el porcentaje no cambió al mover la ventana: {a['fila']}")
            # 7D vale 1,0 × 2 = 2,00% y 1A vale 5,0 × 2 = 10,00%.
            assert "+2.00%" in a["fila"] and "+10.00%" in b["fila"], (a, b)
            assert "reparto de 7D" in a["amp"] and "reparto de 1A" in b["amp"], (
                "la confianza tiene que seguir a la ventana, o la pantalla se "
                "contradice: +50% en las filas y «2 de 5 al alza» de hoy debajo")
            assert a["miga"] == b["miga"], "cambiar la ventana no debe navegar"
            assert not errores, errores[:3]
        finally:
            pg.close()

    def test_y_DENTRO_de_una_industria_tambien(self, navegador, servidor):
        pg, errores = self._abre(navegador, servidor)
        try:
            pg.evaluate("abreSector('XLK')")
            pg.wait_for_timeout(1000)
            pg.evaluate("abreIndustria('SMH')")
            pg.wait_for_timeout(1200)
            lee = ("() => document.querySelector('#sectorIndustrias [data-ind]')"
                   ".innerText.replace(/\\n/g, ' ')")
            pg.evaluate("vxPonVentana('7D')")
            pg.wait_for_timeout(700)
            a = pg.evaluate(lee)
            pg.evaluate("vxPonVentana('1A')")
            pg.wait_for_timeout(700)
            b = pg.evaluate(lee)
            assert a != b, f"el selector no repinta en el tercer piso: {a}"
            miga = pg.evaluate(
                "() => document.getElementById('sectorMigas').innerText.replace(/\\n/g, '')")
            assert "SMH" in miga, f"se salió del piso al cambiar la ventana: {miga}"
            assert not errores, errores[:3]
        finally:
            pg.close()


class TestElAgenteABREEnElDashboard:
    """«Lo primero que me salga al entrar es el Dashboard.»

    El marcado decide qué vista arranca visible: la única sin `hidden`. Era
    `homeView` (el buscador), y cambiarla no basta — al cargar la página nadie
    llama a `switchView`, que es quien pide la parrilla, así que el mapa se
    quedaba en «Leyendo el mercado…» hasta que tocaras otra pestaña y volvieras.
    Las dos mitades, o no funciona.
    """

    @staticmethod
    def _payload():
        secs = ["XLK", "XLF", "XLV", "XLY", "XLC", "XLI",
                "XLP", "XLE", "XLU", "XLRE", "XLB"]
        fila = lambda t: {"ticker": t, "nombre": t, "precio": 100.0,      # noqa: E731
                          "cambio_pct": 0.5, "rsi": 55.0, "sma200": 95.0,
                          "sma200_dist": 5.0,
                          "cambios": {"7D": 1.0, "1M": 2.0, "3M": 3.0,
                                      "6M": 4.0, "1A": 5.0}}
        return {"ok": True, "referencias": ["SPY", "RSP", "QQQ"],
                "sectores": secs,
                "filas": [fila(t) for t in ["SPY", "RSP", "QQQ"] + secs],
                "amplitud": {v: {"n": 11, "fuertes": secs, "debiles": [],
                                 "neutrales": [], "confianza": "alta",
                                 "frase": "todos", "empujan": [], "frenan": []}
                             for v in ("7D", "1M", "3M", "6M", "1A")},
                "rotacion": {"disponible": False, "motivo": ""}}

    def _abre(self, navegador, servidor, ancho=1440):
        import re as _re

        pg = navegador.new_page(viewport={"width": ancho, "height": 1000})
        errores: list[str] = []
        pg.on("pageerror", lambda e: errores.append(str(e)))
        pg.route(_re.compile(r"/api/sectores"),
                 lambda r: r.fulfill(status=200, json=self._payload()))
        pg.goto(servidor, wait_until="load")
        pg.wait_for_timeout(3000)
        return pg, errores

    def test_la_vista_de_arranque_es_el_dashboard(self, navegador, servidor):
        pg, errores = self._abre(navegador, servidor)
        try:
            visibles = pg.evaluate(
                "() => [...document.querySelectorAll('.view-section')]"
                ".filter(x => !x.classList.contains('hidden')).map(x => x.id)")
            assert visibles == ["sectorsView"], (
                f"al abrir se ve {visibles}, no el Dashboard")
            assert not errores, errores[:3]
        finally:
            pg.close()

    def test_y_la_parrilla_se_pide_SOLA_al_cargar(self, navegador, servidor):
        """La mitad que se olvida: cambiar qué vista arranca no dispara su
        carga, porque `switchView` es quien la pide y nadie lo llama."""
        pg, errores = self._abre(navegador, servidor)
        try:
            n = pg.evaluate("() => document.querySelectorAll("
                            "'#sectoresParrilla button[onclick^=\"abreSector\"]').length")
            assert n == 11, (
                f"{n} sectores al abrir: la parrilla no se pidió sola")
            assert not errores, errores[:3]
        finally:
            pg.close()

    def test_el_mapa_usa_la_pantalla_ENTERA_no_una_columna(self, navegador,
                                                           servidor):
        """Un mapa de mercado no es un texto: encerrarlo en una columna de
        lectura desperdicia media pantalla y obliga a bajar para ver once
        casillas que caben de un vistazo."""
        pg, errores = self._abre(navegador, servidor, ancho=1920)
        try:
            d = pg.evaluate("""() => ({
                usado: Math.round(document.getElementById('sectoresParrilla')
                    .getBoundingClientRect().width),
                ventana: window.innerWidth,
            })""")
            assert d["usado"] / d["ventana"] > 0.9, (
                f"solo usa {d['usado']} de {d['ventana']} px")
            assert not errores, errores[:3]
        finally:
            pg.close()


class TestLoNuevoDelDashboardSEVE_EnLosDosTamanos:
    """Franja, mapa de calor, RRG y volumen: en escritorio Y en teléfono.

    «Siempre que se mejore o se suba algo aplica para todos los dispositivos.»
    La forma cómoda de meter cuatro bloques nuevos en una pantalla estrecha es
    esconder alguno con un `hidden sm:…`, y entonces el teléfono enseña un
    Dashboard distinto del que se diseñó. Este caso pinta lo mismo a 1440 y a
    390 px y exige los cuatro bloques en los dos.
    """

    #: Los cuatro tamaños que pidió Kevin: «teléfonos, iPad, computadora,
    #: monitor y todo». Uno por punto de corte real de la hoja de estilos, que
    #: es donde el diseño cambia de forma y donde se rompe.
    MOVIL = {"width": 390, "height": 844}         # iPhone
    IPAD = {"width": 820, "height": 1180}         # iPad vertical
    ESCRITORIO = {"width": 1440, "height": 1000}  # portátil
    MONITOR = {"width": 2560, "height": 1440}     # monitor grande

    SECS = ["XLK", "XLF", "XLV", "XLY", "XLC", "XLI",
            "XLP", "XLE", "XLU", "XLRE", "XLB"]

    @classmethod
    def _payload(cls):
        vol = {v: {"medio": 12_400_000, "relativo": 1.45, "respalda": True}
               for v in ("7D", "1M", "3M", "6M", "1A")}
        fila = lambda t: {"ticker": t, "nombre": t, "precio": 100.0,      # noqa: E731
                          "cambio_pct": 0.5, "rsi": 55.0, "sma200": 95.0,
                          "sma200_dist": 5.0, "volumen": vol,
                          "cambios": {"7D": 1.0, "1M": 2.0, "3M": 3.0,
                                      "6M": 4.0, "1A": 5.0}}
        estela = [{"fuerza": 1.0 + i, "impulso": 0.5 - i * 0.2,
                   "cuadrante": "leading"} for i in range(5)]
        return {
            "ok": True, "referencias": ["SPY", "RSP", "QQQ"],
            "sectores": cls.SECS,
            "filas": ([fila(t) for t in ["SPY", "RSP", "QQQ"] + cls.SECS]
                      + [{**fila("^VIX"), "precio": 17.4, "cambio_pct": -3.2}]),
            "reloj": {"clave": "abierto", "abierto": True,
                      "frase": "Mercado abierto"},
            "generado": "2026-08-14T18:30:00+00:00",
            "interna": {"generado": "2026-08-14T09:00:00+00:00",
                        "sectores": {t: {"pct": 72.0, "n": 10} for t in cls.SECS}},
            "amplitud": {v: {"n": 11, "fuertes": cls.SECS, "debiles": [],
                             "neutrales": [], "confianza": "alta",
                             "frase": "todos", "empujan": [], "frenan": []}
                         for v in ("7D", "1M", "3M", "6M", "1A")},
            "rotacion": {"disponible": True,
                         "salud": {"clave": "amplia",
                                   "frase": "Amplitud sólida: la mayoría del "
                                            "mercado participa de la subida.",
                                   "pendiente": 0.4},
                         "matriz": {"leading": cls.SECS, "weakening": [],
                                    "lagging": [], "improving": []},
                         "cuadrantes": ["leading", "weakening", "lagging",
                                        "improving"],
                         # Las familias salen del MOTOR, no de una lista escrita
                         # a mano aquí: con `[]` el resumen de rotación se
                         # quedaba sin «Manda» ni «Se queda» y el caso pasaba
                         # midiendo una pantalla que en producción dice más.
                         "categorias": [{"clave": c, "nombre": n,
                                         "sectores": list(ts)}
                                        for c, n, ts in _CATEGORIAS],
                         "sectores": {t: {"fuerza": 2.0 - i * 0.4,
                                          "impulso": 0.5 if i % 2 else -0.5,
                                          "cuadrante": ["leading", "improving",
                                                        "weakening", "lagging"][i % 4]}
                                      for i, t in enumerate(cls.SECS)},
                         "entrando": [],
                         "saliendo": [], "dispersion": 0.8, "dia_rojo": [],
                         "diagnostico": [],
                         "estelas": {t: estela for t in cls.SECS}}}

    def _abre(self, navegador, servidor, viewport):
        import re as _re

        pg = navegador.new_page(viewport=viewport)
        errores: list[str] = []
        pg.on("pageerror", lambda e: errores.append(str(e)))
        pg.route(_re.compile(r"/api/sectores"),
                 lambda r: r.fulfill(status=200, json=self._payload()))
        pg.goto(servidor, wait_until="load")
        # Se espera a que el Dashboard esté PINTADO, no un número de segundos.
        # Con la batería entera corriendo, tres segundos fijos no siempre
        # alcanzaban y el caso fallaba por el arnés y no por el panel — que es
        # la peor clase de rojo: el que enseña a desconfiar de los tests.
        pg.wait_for_function(
            "() => document.querySelectorAll("
            "'#sectoresParrilla button[onclick^=\"abreSector\"]').length >= 11",
            timeout=20000)
        pg.wait_for_function(
            "() => document.querySelector('#sectoresRotacion h3') !== null",
            timeout=20000)
        return pg, errores

    def _mide(self, pg):
        return pg.evaluate("""() => {
            const v = id => {
                const n = id.startsWith('[') ? document.querySelector(id)
                                             : document.getElementById(id);
                if (!n) return null;
                const r = n.getBoundingClientRect();
                return { alto: r.height, arriba: r.top, derecha: r.right,
                         texto: (n.innerText || '').trim() };
            };
            const h1 = document.querySelector('#sectorsView h1');
            const reloj = document.querySelector('#sectoresFranja [data-vx="reloj"]');
            const rr = reloj ? reloj.getBoundingClientRect() : null;
            return {
                franja: v('sectoresFranja'),
                rotacion: v('sectoresRotacion'),
                parrilla: v('sectoresParrilla'),
                capas: document.querySelectorAll('#sectoresRotacion .relative.h-9').length,
                marcas: document.querySelectorAll('#sectoresRotacion button[onclick^="abreSector"]').length,
                frase: v('[data-vx="frase"]'),
                titulo: h1 ? h1.getBoundingClientRect().top : null,
                relojDerecha: rr ? rr.right : null,
                mapa: document.getElementById('sectoresMapa'),
            };
        }""")

    @pytest.mark.parametrize("nombre,viewport",
                             [("escritorio", ESCRITORIO), ("ipad", IPAD),
                              ("movil", MOVIL), ("monitor", MONITOR)])
    def test_los_cuatro_bloques_estan_y_tienen_alto(self, navegador, servidor,
                                                    nombre, viewport):
        pg, errores = self._abre(navegador, servidor, viewport)
        try:
            d = self._mide(pg)
            for clave in ("franja", "rotacion", "parrilla"):
                assert d[clave], f"[{nombre}] falta el bloque {clave}"
                assert d[clave]["alto"] > 20, (
                    f"[{nombre}] {clave} está en pantalla con alto "
                    f"{d[clave]['alto']}: se pintó vacío")
            assert d["mapa"] is None, (
                f"[{nombre}] el mapa de calor volvió: Kevin pidió quitarlo")
            assert d["frase"], f"[{nombre}] no hay frase en la franja"
            assert not errores, errores[:3]
        finally:
            pg.close()

    @pytest.mark.parametrize("nombre,viewport",
                             [("escritorio", ESCRITORIO), ("ipad", IPAD),
                              ("movil", MOVIL), ("monitor", MONITOR)])
    def test_la_franja_va_ENCIMA_del_titulo(self, navegador, servidor,
                                            nombre, viewport):
        """El estado del mercado se lee ANTES que nada.

        Todo lo de abajo depende de él: un sector que sube un 2% no significa lo
        mismo con el índice en verde que cayendo. Si la franja quedara debajo
        del título, la primera casilla se leería sin su referencia.
        """
        pg, errores = self._abre(navegador, servidor, viewport)
        try:
            d = self._mide(pg)
            assert d["titulo"] is not None, f"[{nombre}] no hay título"
            assert d["franja"]["arriba"] < d["titulo"], (
                f"[{nombre}] la franja ({d['franja']['arriba']}px) está por "
                f"debajo del título ({d['titulo']}px)")
            assert not errores, errores[:3]
        finally:
            pg.close()

    @pytest.mark.parametrize("nombre,viewport",
                             [("escritorio", ESCRITORIO), ("ipad", IPAD),
                              ("movil", MOVIL), ("monitor", MONITOR)])
    def test_el_reloj_va_pegado_a_la_ESQUINA_DERECHA(self, navegador, servidor,
                                                     nombre, viewport):
        """En los cuatro tamaños, y no «al final de la fila».

        Es el dato que se consulta sin buscarlo —«¿esto está vivo o es la foto
        de ayer?»—, así que tiene un sitio fijo. Si se colocara según lo que
        haya al lado, cambiaría de sitio con cada mensaje distinto.
        """
        pg, errores = self._abre(navegador, servidor, viewport)
        try:
            d = self._mide(pg)
            assert d["relojDerecha"] is not None, f"[{nombre}] no se encontró el reloj"
            margen = d["franja"]["derecha"] - d["relojDerecha"]
            assert 0 <= margen <= 40, (
                f"[{nombre}] el reloj termina a {margen}px del borde derecho de "
                "la franja: no está en la esquina")
            assert not errores, errores[:3]
        finally:
            pg.close()

    @pytest.mark.parametrize("nombre,viewport",
                             [("escritorio", ESCRITORIO), ("ipad", IPAD),
                              ("movil", MOVIL), ("monitor", MONITOR)])
    def test_el_reloj_el_vix_y_el_volumen_se_LEEN(self, navegador, servidor,
                                                  nombre, viewport):
        pg, errores = self._abre(navegador, servidor, viewport)
        try:
            d = self._mide(pg)
            franja = d["franja"]["texto"]
            assert "Mercado abierto" in franja, f"[{nombre}] sin reloj: {franja}"
            assert "VIX" in franja, f"[{nombre}] sin VIX: {franja}"
            assert "18:30" in franja or "6:30" in franja, (
                f"[{nombre}] sin la hora del dato: {franja}")
            parrilla = d["parrilla"]["texto"]
            assert "1.45x" in parrilla, (
                f"[{nombre}] el volumen no llegó a la parrilla")
            assert "72%" in parrilla, (
                f"[{nombre}] la amplitud interna no llegó a la parrilla")
            assert not errores, errores[:3]
        finally:
            pg.close()

    def test_el_VIX_no_pinta_casilla_en_la_parrilla(self, navegador, servidor):
        """Se baja para la franja; en el mapa sería un sector más, y sube
        cuando el mercado empeora."""
        pg, errores = self._abre(navegador, servidor, self.ESCRITORIO)
        try:
            n = pg.evaluate(
                "() => document.querySelectorAll('#sectoresParrilla button[onclick]')"
                ".length")
            # 11 sectores pulsables + 5 botones de ventana. El VIX no está.
            txt = pg.evaluate(
                "() => document.getElementById('sectoresParrilla').innerText")
            assert "VIX" not in txt, "el VIX se coló en la parrilla"
            assert n == 16, f"{n} botones: se coló o falta una casilla"
            assert not errores, errores[:3]
        finally:
            pg.close()

    @pytest.mark.parametrize("nombre,viewport",
                             [("escritorio", ESCRITORIO), ("ipad", IPAD),
                              ("movil", MOVIL), ("monitor", MONITOR)])
    def test_la_rotacion_se_PLIEGA_y_se_despliega(self, navegador, servidor,
                                                  nombre, viewport):
        """Plegada por defecto: la rotación contesta una pregunta que uno se
        hace DESPUÉS de mirar las casillas, no antes. Y lo que se elija se
        recuerda, o cambiar de ventana la volvería a plegar cada vez."""
        pg, errores = self._abre(navegador, servidor, viewport)
        try:
            # Plegado significa NO SE VE. El marcado sigue en el DOM —así el
            # estado se conserva sin volver a pedir nada—, de modo que se
            # cuenta lo visible, que es lo que le pasa a quien mira.
            cuenta = ("() => [...document.querySelectorAll("
                      "'#sectoresRotacion .relative.h-9')]"
                      ".filter(n => n.offsetParent !== null).length")
            # El estado se guarda en `localStorage`, que las páginas de un mismo
            # navegador COMPARTEN: sin limpiarlo, lo que dejó abierto el caso
            # anterior se cuela aquí y el fallo parece del panel.
            pg.evaluate("""() => {
                localStorage.removeItem('vertex_rotacion');
                pintaRotacion((window._sectoresData || {}).rotacion);
            }""")
            pg.wait_for_timeout(200)
            assert pg.evaluate(cuenta) == 0, (
                f"[{nombre}] la rotación arranca desplegada")
            pg.evaluate("vxRotacionAlterna()")
            pg.wait_for_timeout(200)
            assert pg.evaluate(cuenta) == 4, (
                f"[{nombre}] al abrir no salieron las cuatro capas")
            assert pg.evaluate(
                "() => [...document.querySelectorAll("
                "'#sectoresRotacion button[onclick^=\"abreSector\"]')]"
                ".filter(n => n.offsetParent !== null).length") == 11
            pg.evaluate("vxRotacionAlterna()")
            pg.wait_for_timeout(200)
            assert pg.evaluate(cuenta) == 0, f"[{nombre}] no se volvió a plegar"
            assert not errores, errores[:3]
        finally:
            pg.close()

    @pytest.mark.parametrize("nombre,viewport",
                             [("escritorio", ESCRITORIO), ("ipad", IPAD),
                              ("movil", MOVIL), ("monitor", MONITOR)])
    def test_las_capas_dicen_el_estado_CON_PALABRAS(self, navegador, servidor,
                                                    nombre, viewport):
        """El gráfico anterior eran once colas cruzándose sobre cuatro
        cuadrantes sin nombre: había que deducir el estado de dónde caía el
        punto. Ahora la capa lo dice, y en el orden que significa algo —arriba
        lo que tira, abajo lo que pesa—."""
        pg, errores = self._abre(navegador, servidor, viewport)
        try:
            # Se ABRE de forma explícita, no alternando: alternar depende de
            # cómo lo dejara el caso anterior, que comparte `localStorage`.
            pg.evaluate("""() => {
                localStorage.setItem('vertex_rotacion', '1');
                pintaRotacion((window._sectoresData || {}).rotacion);
            }""")
            pg.wait_for_timeout(200)
            t = pg.evaluate(
                "() => document.getElementById('sectoresRotacion').innerText")
            for palabra in ("Liderando", "Cogiendo fuerza", "Agotándose",
                            "Rezagados"):
                assert palabra in t, f"[{nombre}] falta la capa «{palabra}»: {t[:200]}"
            # Y en ese orden, de mejor a peor.
            pos = [t.index(p) for p in ("Liderando", "Cogiendo fuerza",
                                        "Agotándose", "Rezagados")]
            assert pos == sorted(pos), (
                f"[{nombre}] las capas no van de mejor a peor: {pos}")
            assert "S&P 500" in t, f"[{nombre}] falta la referencia del centro"
            assert not errores, errores[:3]
        finally:
            pg.close()

    @pytest.mark.parametrize("nombre,viewport",
                             [("escritorio", ESCRITORIO), ("ipad", IPAD),
                              ("movil", MOVIL), ("monitor", MONITOR)])
    def test_la_rotacion_se_CUENTA_en_frases_no_solo_en_tickers(
            self, navegador, servidor, nombre, viewport):
        """«Que explique en términos simples lo que ha estado pasando y a dónde
        está rotando el dinero y por qué.»

        Las cuatro capas dicen DÓNDE cae cada sector; no dicen qué familia
        manda, cuál se queda, dónde entra dinero de verdad ni si esto es
        rotación o el índice moviéndose en bloque. Eso es el resumen, y va en
        frases con su número al lado.
        """
        pg, errores = self._abre(navegador, servidor, viewport)
        try:
            pg.evaluate("""() => {
                localStorage.setItem('vertex_rotacion', '1');
                pintaRotacion((window._sectoresData || {}).rotacion);
            }""")
            pg.wait_for_timeout(200)
            t = pg.evaluate(
                "() => document.getElementById('sectoresRotacion').innerText")
            for etq in ("Qué está rotando, en corto", "Manda:", "Se queda:",
                        "Entra dinero:", "Sale dinero:", "Dispersión:"):
                assert etq in t, f"[{nombre}] falta «{etq}»: {t[:300]}"
            # Entrar y salir se dicen SOLO con volumen detrás: es la definición
            # del motor, y sin decirlo el lector lo lee como «lo que más sube».
            assert "con volumen" in t, (
                f"[{nombre}] no se dice que la entrada y la salida piden volumen")
            # Y el resumen va ANTES de las capas: primero la frase, después el
            # mapa que la sostiene.
            assert t.index("Qué está rotando, en corto") < t.index("Liderando"), (
                f"[{nombre}] el resumen quedó debajo del mapa de capas")
            assert not errores, errores[:3]
        finally:
            pg.close()

    def test_ya_NO_hay_una_maraña_de_lineas(self, navegador, servidor):
        """«Veo un revolu de líneas y no se entiende.» Once polilíneas cruzadas
        sobre el mismo plano no se pueden leer, por correctas que sean."""
        pg, errores = self._abre(navegador, servidor, self.ESCRITORIO)
        try:
            pg.evaluate("""() => {
                localStorage.setItem('vertex_rotacion', '1');
                pintaRotacion((window._sectoresData || {}).rotacion);
            }""")
            pg.wait_for_timeout(200)
            n = pg.evaluate(
                "() => document.querySelectorAll('#sectoresRotacion polyline').length")
            assert n == 0, f"volvieron las {n} estelas cruzadas"
            assert not errores, errores[:3]
        finally:
            pg.close()

    @pytest.mark.parametrize("nombre,viewport",
                             [("escritorio", ESCRITORIO), ("ipad", IPAD),
                              ("movil", MOVIL), ("monitor", MONITOR)])
    def test_la_frase_se_lee_y_dice_QUIEN_la_dijo(self, navegador, servidor,
                                                  nombre, viewport):
        """Una cita sin autor es un eslogan. Con autor es una opinión con
        alguien detrás que se jugó dinero sosteniéndola."""
        pg, errores = self._abre(navegador, servidor, viewport)
        try:
            f = pg.evaluate("""() => {
                const n = document.querySelector('#sectoresFranja [data-vx="frase"]');
                if (!n) return null;
                const ps = n.querySelectorAll('p');
                const r = n.getBoundingClientRect();
                return { texto: ps[0] ? ps[0].innerText : '',
                         autor: ps[1] ? ps[1].innerText : '',
                         ancho: r.width, alto: r.height };
            }""")
            assert f, f"[{nombre}] no hay frase en la franja"
            assert len(f["texto"]) > 15, f"[{nombre}] frase vacía: {f['texto']!r}"
            assert f["autor"].strip().startswith("—"), (
                f"[{nombre}] la frase no dice quién la dijo: {f['autor']!r}")
            assert len(f["autor"]) > 3
            assert f["ancho"] >= 180, (
                f"[{nombre}] la frase tiene {f['ancho']}px: sale en columna")
            assert not errores, errores[:3]
        finally:
            pg.close()

    def test_las_frases_CAMBIAN_solas(self, navegador, servidor):
        """«Que solo se vea una frase a la vez y vaya cambiando activamente.»"""
        pg, errores = self._abre(navegador, servidor, self.ESCRITORIO)
        try:
            leer = ("() => (document.querySelector("
                    "'#sectoresFranja [data-vx=\"frase\"] p') || {}).innerText")
            antes = pg.evaluate(leer)
            # Se adelanta el relevo en vez de esperar doce segundos: lo que se
            # mide es que el mecanismo cambia el texto y deja UNA sola frase.
            pg.evaluate("""() => {
                VX_FRASE_I = (VX_FRASE_I + 1) % VX_FRASES.length;
                document.querySelectorAll('[data-vx="frase"]').forEach(
                    n => { n.outerHTML = vxFraseHTML(); });
            }""")
            despues = pg.evaluate(leer)
            assert antes and despues and antes != despues, (
                f"la frase no cambió: {antes!r}")
            assert pg.evaluate(
                "() => document.querySelectorAll("
                "'#sectoresFranja [data-vx=\"frase\"]').length") == 1, (
                "hay más de una frase a la vez")
            assert not errores, errores[:3]
        finally:
            pg.close()

    def test_el_SPY_y_la_amplitud_salieron_de_la_franja(self, navegador,
                                                        servidor):
        """Kevin los quitó de arriba, y no se pierde nada: SPY, RSP y QQQ
        tienen su casilla en la parrilla, y la salud RSP-contra-SPY abre el
        bloque de rotación. Repetirlos arriba solo hacía la franja más alta."""
        pg, errores = self._abre(navegador, servidor, self.ESCRITORIO)
        try:
            t = pg.evaluate(
                "() => document.getElementById('sectoresFranja').innerText")
            assert "S&P 500" not in t, f"el S&P sigue en la franja: {t[:160]}"
            assert "RSP" not in t, f"la amplitud sigue en la franja: {t[:160]}"
            # Y siguen estando donde tienen que estar.
            p = pg.evaluate(
                "() => document.getElementById('sectoresParrilla').innerText")
            for x in ("SPY", "RSP", "QQQ"):
                assert x in p, f"{x} desapareció también de la parrilla"
            assert not errores, errores[:3]
        finally:
            pg.close()

    def test_la_parrilla_va_JUSTO_debajo_del_titulo(self, navegador, servidor):
        """«Abajo de Sectores, rápido, ahí estén todos los sectores.»"""
        pg, errores = self._abre(navegador, servidor, self.ESCRITORIO)
        try:
            d = pg.evaluate("""() => {
                const h1 = document.querySelector('#sectorsView h1');
                const p = document.getElementById('sectoresParrilla');
                const r = document.getElementById('sectoresRotacion');
                return { titulo: h1.getBoundingClientRect().top,
                         parrilla: p.getBoundingClientRect().top,
                         rotacion: r.getBoundingClientRect().top };
            }""")
            assert d["titulo"] < d["parrilla"] < d["rotacion"], (
                f"el orden no es título → parrilla → rotación: {d}")
            assert not errores, errores[:3]
        finally:
            pg.close()


class TestElPrecioSeMUEVESolo:
    """«Quiero que los precios cambien en tiempo real.»

    Se mide en un navegador de verdad porque es donde puede fallar: que el
    número cambie sin repintar la pantalla, que el destello diga la dirección,
    y que el latido se calle cuando no hay nada que mirar.
    """

    ESCRITORIO = {"width": 1440, "height": 900}
    MOVIL = {"width": 390, "height": 844}
    SECS = ["XLK", "XLF", "XLV", "XLY", "XLC", "XLI",
            "XLP", "XLE", "XLU", "XLRE", "XLB"]

    def _abre(self, navegador, servidor, viewport=None, precio=100.0):
        import re as _re

        pg = navegador.new_page(viewport=viewport or self.ESCRITORIO)
        errores: list[str] = []
        pg.on("pageerror", lambda e: errores.append(str(e)))
        estado = {"precio": precio, "sondeos": 0}

        vol = {v: {"medio": 1, "relativo": 1.0, "respalda": True}
               for v in ("7D", "1M", "3M", "6M", "1A")}
        fila = lambda t: {"ticker": t, "nombre": t, "precio": estado["precio"],
                          "cambio_pct": 0.5, "rsi": 55.0, "sma200": 95.0,
                          "sma200_dist": 5.0, "volumen": vol,
                          "cambios": {"7D": 1.0, "1M": 2.0, "3M": 3.0,
                                      "6M": 4.0, "1A": 5.0}}

        def enruta(r):
            u = r.request.url
            if "/vivo" in u:
                estado["sondeos"] += 1
                pedidos = [t for t in u.split("tickers=")[-1].split("&")[0]
                           .replace("%2C", ",").split(",") if t]
                r.fulfill(status=200, json={
                    "ok": True, "precios": {t: estado["precio"] for t in pedidos},
                    "reloj": {"clave": "abierto", "abierto": True,
                              "frase": "Mercado abierto"},
                    "generado": "2026-08-15T18:00:00+00:00"})
            else:
                r.fulfill(status=200, json={
                    "ok": True, "referencias": ["SPY", "RSP", "QQQ"],
                    "sectores": self.SECS,
                    "filas": [fila(t) for t in ["SPY", "RSP", "QQQ"] + self.SECS],
                    "reloj": {"clave": "abierto", "abierto": True,
                              "frase": "Mercado abierto"},
                    "amplitud": {}, "interna": {"sectores": {}},
                    "rotacion": {"disponible": False, "motivo": ""},
                    "generado": "2026-08-15T18:00:00+00:00"})

        pg.route(_re.compile(r"/api/sectores"), enruta)
        # El calendario TAMBIÉN se simula. Sin esto la página, al arrancar, se
        # queda esperando a FMP —que aquí está bloqueado— hasta que vence el
        # timeout, y con la batería entera corriendo eso movía el momento en
        # que llegaba el precio. El caso salía rojo por el arnés, no por el
        # panel: es el mismo error que ya se pagó con `_abre` y los tres
        # segundos fijos.
        pg.route(_re.compile(r"/api/dashboard/calendario"),
                 lambda r: r.fulfill(status=200, json={
                     "ok": True,
                     "resultados": {"filas": [], "motivo": "sin datos"},
                     "macro": {"publicados": [], "proximos": [],
                               "filas": [], "motivo": "sin datos"},
                     "generado": "2026-08-16T12:00:00+00:00"}))
        pg.goto(servidor, wait_until="load")
        # Se espera el PRECIO, no el nodo. La parrilla se pinta entera al
        # instante con «···» y los números entran después: esperar a que
        # existan las casillas dejaba correr el caso con los puntos
        # suspensivos todavía puestos, y el primer `assert` salía rojo por
        # llegar antes de tiempo. Se notó más al simular el calendario,
        # porque la página empezó a cargar antes.
        pg.wait_for_function(
            "() => { const n = document.querySelector('[data-vx-precio=\"XLK\"]');"
            " return n && n.textContent.includes('$'); }", timeout=25000)
        return pg, errores, estado

    def test_el_precio_CAMBIA_sin_repintar_la_pantalla(self, navegador, servidor):
        pg, errores, estado = self._abre(navegador, servidor)
        try:
            leo = ("() => document.querySelector('[data-vx-precio=\"XLK\"]')"
                   ".textContent")
            assert "100" in pg.evaluate(leo), pg.evaluate(leo)
            # Se marca la casilla: si el latido repintara, la marca se perdería.
            pg.evaluate("""() => {
                document.querySelector('[data-vx-precio="XLK"]')
                    .setAttribute('data-testigo', 'sigo-aqui');
            }""")
            estado["precio"] = 123.45
            pg.evaluate("vxVivoLatido()")
            pg.wait_for_function(
                "() => document.querySelector('[data-vx-precio=\"XLK\"]')"
                ".textContent.includes('123')", timeout=8000)
            assert pg.evaluate(
                "() => document.querySelector('[data-vx-precio=\"XLK\"]')"
                ".getAttribute('data-testigo')") == "sigo-aqui", (
                "se repintó la parrilla para cambiar un número")
            assert not errores, errores[:3]
        finally:
            pg.close()

    def test_el_destello_dice_la_DIRECCION(self, navegador, servidor):
        """Un parpadeo neutro obliga a acordarse del número anterior para
        saber si subió o bajó."""
        pg, errores, estado = self._abre(navegador, servidor)
        try:
            # Un primer latido para FIJAR la referencia. No es un apaño del
            # test: en el primer sondeo no hay con qué comparar, así que no
            # destella — y eso es lo correcto. El destello dice «subió» o
            # «bajó», y sin número anterior ninguna de las dos es cierta.
            pg.evaluate("vxVivoLatido()")
            pg.wait_for_timeout(300)
            estado["precio"] = 150.0
            pg.evaluate("vxVivoLatido()")
            pg.wait_for_function(
                "() => document.querySelector('[data-vx-precio=\"XLK\"]')"
                ".classList.contains('vx-sube')", timeout=8000)
            estado["precio"] = 90.0
            pg.evaluate("vxVivoLatido()")
            pg.wait_for_function(
                "() => document.querySelector('[data-vx-precio=\"XLK\"]')"
                ".classList.contains('vx-baja')", timeout=8000)
            assert not errores, errores[:3]
        finally:
            pg.close()

    def test_con_la_BOLSA_CERRADA_no_se_sondea(self, navegador, servidor):
        """El precio es el mismo y sondearlo es tirar cuota."""
        pg, errores, estado = self._abre(navegador, servidor)
        try:
            # Primero se deja QUIETO: el latido de arranque va en vuelo y su
            # respuesta reescribe el reloj con el del servidor, así que medir
            # sin esperar mide la carrera y no el freno.
            pg.evaluate("vxVivoPara()")
            pg.wait_for_timeout(600)
            pg.evaluate("""() => {
                window._sectoresData = window._sectoresData || {};
                window._sectoresData.reloj = {clave: 'cerrado', abierto: false};
            }""")
            antes = estado["sondeos"]
            pg.evaluate("vxVivoLatido()")
            pg.wait_for_timeout(600)
            assert estado["sondeos"] == antes, "sondeó con la bolsa cerrada"
            assert not errores, errores[:3]
        finally:
            pg.close()

    def test_solo_se_preguntan_los_tickers_QUE_SE_VEN(self, navegador,
                                                      servidor):
        """En el Dashboard son las catorce casillas. Preguntar por las 351
        empresas escritas en el panel sería gastar cuota en lo que nadie mira.
        """
        pg, errores, _ = self._abre(navegador, servidor)
        try:
            vistos = pg.evaluate("() => vxVivoTickers()")
            assert "XLK" in vistos and "SPY" in vistos
            assert len(vistos) <= 15, f"se piden de más: {len(vistos)}"
            assert "NVDA" not in vistos, (
                "se piden empresas que no están en pantalla")
            assert not errores, errores[:3]
        finally:
            pg.close()

    def test_tambien_en_el_MOVIL(self, navegador, servidor):
        pg, errores, estado = self._abre(navegador, servidor, self.MOVIL)
        try:
            estado["precio"] = 321.0
            pg.evaluate("vxVivoLatido()")
            pg.wait_for_function(
                "() => document.querySelector('[data-vx-precio=\"XLK\"]')"
                ".textContent.includes('321')", timeout=8000)
            assert not errores, errores[:3]
        finally:
            pg.close()

    def test_el_latido_ARRANCA_SOLO_al_entrar(self, navegador, servidor):
        """Lo que de verdad importa: que Kevin no tenga que hacer nada. Si el
        latido solo se enciende navegando fuera y volviendo, para él la
        función no existe."""
        pg, errores, estado = self._abre(navegador, servidor)
        try:
            assert pg.evaluate("() => VX_VIVO_TIMER !== null"), (
                "el latido no se encendió al abrir el Dashboard")
            # Y sondeó de verdad, sin esperar los quince segundos.
            assert estado["sondeos"] >= 1, "no preguntó el precio al entrar"
            assert not errores, errores[:3]
        finally:
            pg.close()

    def test_y_se_APAGA_al_irse_a_otra_pantalla(self, navegador, servidor):
        pg, errores, _ = self._abre(navegador, servidor)
        try:
            pg.evaluate("switchView('perfilView')")
            pg.wait_for_timeout(300)
            assert pg.evaluate("() => VX_VIVO_TIMER === null"), (
                "el latido sigue gastando cuota fuera del panel")
            assert not errores, errores[:3]
        finally:
            pg.close()


class TestLasDosCAJASDelCalendario:
    """«Dos cajas abajo de Rotación Sectorial, una a la izquierda y otra a la
    derecha en paralelo.»"""

    ESCRITORIO = {"width": 1440, "height": 900}
    IPAD = {"width": 820, "height": 1180}
    MOVIL = {"width": 390, "height": 844}
    MONITOR = {"width": 2560, "height": 1440}

    def _abre(self, navegador, servidor, viewport, cal=None):
        import re as _re

        pg = navegador.new_page(viewport=viewport)
        errores: list[str] = []
        pg.on("pageerror", lambda e: errores.append(str(e)))
        hoy = __import__("datetime").date.today().isoformat()
        por_defecto = {
            "ok": True,
            # Con la forma REAL que sirve el servidor: medida hecha, umbral y
            # capitalización por fila. Sin ellos la caja se pinta por el camino
            # de «no se pudo medir», y el navegador no llegaría a ver nunca el
            # camino bueno.
            "resultados": {"filas": [
                {"ticker": "NVDA", "fecha": hoy, "sector": "XLK",
                 "sector_nombre": "Tecnología", "cuando": "amc", "cap": 3.4e12},
                {"ticker": "JPM", "fecha": hoy, "sector": "XLF",
                 "sector_nombre": "Financiero", "cuando": "bmo", "cap": 7.1e11}],
                "motivo": "", "medida": True, "fuera": 37,
                "umbral": 10_000_000_000, "motivo_tamano": ""},
            "macro": {"filas": [
                {"serie": "UNRATE", "nombre": "Paro", "valor": 4.1,
                 "previo": 4.3, "fecha": "2026-07-01"},
                {"serie": "CPIAUCSL", "nombre": "Inflación (IPC interanual)",
                 "valor": 2.9, "previo": 3.1, "fecha": "2026-07-01"}], "motivo": ""},
            "generado": "2026-08-15T12:00:00+00:00"}
        pg.route(_re.compile(r"/api/dashboard/calendario"),
                 lambda r: r.fulfill(status=200, json=cal or por_defecto))
        pg.route(_re.compile(r"/api/sectores"),
                 lambda r: r.fulfill(status=200, json={
                     "ok": True, "referencias": [], "sectores": [], "filas": [],
                     "amplitud": {}, "interna": {"sectores": {}},
                     "rotacion": {"disponible": False, "motivo": ""},
                     "reloj": {"clave": "cerrado", "abierto": False, "frase": "x"},
                     "generado": "2026-08-15T12:00:00+00:00"}))
        pg.goto(servidor, wait_until="load")
        pg.wait_for_function(
            "() => document.getElementById('sectoresMacro')"
            "&& document.getElementById('sectoresMacro').innerText.trim().length > 0",
            timeout=25000)
        return pg, errores

    @pytest.mark.parametrize("nombre,viewport",
                             [("escritorio", ESCRITORIO), ("ipad", IPAD),
                              ("movil", MOVIL), ("monitor", MONITOR)])
    def test_las_dos_se_VEN_y_tienen_alto(self, navegador, servidor, nombre,
                                          viewport):
        pg, errores = self._abre(navegador, servidor, viewport)
        try:
            for ident in ("sectoresResultados", "sectoresMacro"):
                caja = pg.evaluate(
                    "(id) => { const n = document.getElementById(id);"
                    " return n ? {alto: n.getBoundingClientRect().height,"
                    " txt: n.innerText.trim().length} : null; }", ident)
                assert caja and caja["alto"] > 40 and caja["txt"] > 10, (
                    f"[{nombre}] {ident} no se ve: {caja}")
            assert not errores, errores[:3]
        finally:
            pg.close()

    def test_la_caja_PINTA_el_corte_por_tamano(self, navegador, servidor):
        """«Los más importantes, no todos.» Un recorte que no dice dónde corta
        no se distingue de un fallo que se comió filas: la primera vez que
        falte una empresa esperada, la caja tiene que contestar sola."""
        pg, errores = self._abre(navegador, servidor, self.ESCRITORIO)
        try:
            txt = pg.evaluate(
                "() => document.getElementById('sectoresResultados').innerText")
            assert "$10B" in txt, f"no se pinta el umbral: {txt[:300]}"
            assert "37" in txt, "no se dice cuántas quedaron fuera"
            assert not errores, errores[:3]
        finally:
            pg.close()

    def test_el_ORDEN_en_el_DOM_es_izquierda_y_luego_derecha(self, navegador,
                                                              servidor):
        """Que vayan EN PARALELO lo fija el marcado (`lg:grid-cols-2`), y eso
        lo mide `test_las_dos_cajas_van_EN_PARALELO_bajo_la_rotacion` sobre el
        HTML. Aquí NO se puede medir la geometría: Tailwind viene del CDN y ese
        CDN no carga en este navegador, así que ninguna clase de rejilla
        aplica y las dos cajas saldrían apiladas midan lo que midan.

        Lo que sí es medible aquí, y es lo que importa del orden: cuál va
        primera en el DOM, que es la que sale a la izquierda cuando la rejilla
        existe y arriba cuando no.
        """
        pg, errores = self._abre(navegador, servidor, self.ESCRITORIO)
        try:
            primero = pg.evaluate("""() => {
                const a = document.getElementById('sectoresResultados');
                const b = document.getElementById('sectoresMacro');
                return (a.compareDocumentPosition(b)
                        & Node.DOCUMENT_POSITION_FOLLOWING) !== 0;
            }""")
            assert primero, "macro quedó antes que resultados"
            assert not errores, errores[:3]
        finally:
            pg.close()

    def test_van_DEBAJO_de_la_rotacion(self, navegador, servidor):
        pg, errores = self._abre(navegador, servidor, self.ESCRITORIO)
        try:
            orden = pg.evaluate("""() => {
                const r = document.getElementById('sectoresRotacion')
                    .getBoundingClientRect();
                const c = document.getElementById('sectoresResultados')
                    .getBoundingClientRect();
                const l = document.getElementById('sectoresLectura')
                    .getBoundingClientRect();
                return {bajoRotacion: c.top >= r.bottom - 4,
                        sobreLectura: c.top <= l.top + 4};
            }""")
            assert orden["bajoRotacion"], "las cajas no van debajo de la rotación"
            assert orden["sobreLectura"], "quedaron por debajo de la lectura"
            assert not errores, errores[:3]
        finally:
            pg.close()

    def test_si_FALLA_una_la_otra_SIGUE(self, navegador, servidor):
        """Que FRED no conteste no puede dejar sin calendario de resultados."""
        hoy = __import__("datetime").date.today().isoformat()
        pg, errores = self._abre(navegador, servidor, self.ESCRITORIO, cal={
            "ok": True,
            "resultados": {"filas": [{"ticker": "NVDA", "fecha": hoy,
                                      "sector": "XLK", "sector_nombre": "Tec",
                                      "cuando": "amc"}], "motivo": ""},
            "macro": {"filas": [], "motivo": "Sin FRED_API_KEY no hay datos macro."},
            "generado": "2026-08-15T12:00:00+00:00"})
        try:
            txtR = pg.evaluate(
                "() => document.getElementById('sectoresResultados').innerText")
            txtM = pg.evaluate(
                "() => document.getElementById('sectoresMacro').innerText")
            assert "NVDA" in txtR, "el fallo de FRED se llevó los resultados"
            assert "FRED" in txtM, "la caja rota no dice por qué está vacía"
            assert not errores, errores[:3]
        finally:
            pg.close()


class TestLaVistaDeIDEASSePintaDeVerdad:
    """El agujero que dejó a Kevin mirando una rueda.

    `renderProjIdeas` y `vcRiesgoHTML` **no las tocaba ningún test de
    navegador**: los del servidor buscan cadenas dentro del HTML, y una cadena
    presente no dice nada sobre si la función lanza al ejecutarse.

    Y la forma de `loadProjIdeas` convierte cualquier excepción de render en
    una rueda eterna: la rueda se pinta lo PRIMERO, y si algo revienta después
    —un campo nuevo mal leído, un formateador con `undefined`— la función
    aborta y nadie sustituye ese HTML. No sale error, no sale tabla: sale
    «Escaneando…» para siempre, que es exactamente lo que se vio en el
    teléfono.
    """

    #: La forma REAL que sirve `/api/tito-ideas`, con los campos que se fueron
    #: añadiendo: el sizing, el perfil con sus dos presupuestos y el aviso de
    #: historial sin tiempo. Si el render se cae con alguno, este test lo dice.
    @staticmethod
    def _payload():
        return {
            "ok": True, "engine": "victor/tito", "scanned": 120, "pages": 1,
            "truncated": False, "min_premium": 100000, "moneyness_cap": 0.25,
            "tickers": 2, "with_history": 1, "history_skipped": 3,
            "unusual_cut": 24,
            "rejected": {"theta_alto": 4, "vencido": 2, "sin_theta": 1,
                         "no_inusual": 9, "lejano": 3},
            "perfil": {
                "capital": 1000.0, "tolerancia": "especulativo",
                "riesgo_pct": 30.0, "riesgo_por_trade": 300.0,
                "max_posicion_pct": [5, 80], "posicion_usd": [50.0, 800.0],
                "perdida_max": [15.0, 240.0],
                "theta_budget_pct": 5.0, "theta_budget": 240.0,
                "budget_premium": 800.0, "budget_theta": 240.0,
                "horizonte": "1-3 años", "horizonte_dias": 90, "caben": 1,
            },
            "ideas": [
                {"id": 1, "ticker": "BAC", "symbol": "BAC", "underlying": "BAC",
                 "type": "put", "strike": 60, "expiration": "2026-12-18",
                 "dte": 123, "price": 1.66, "premium": 8850000.0,
                 "asset_price": 60.0, "side": "ask", "aggression": "agresivo",
                 "theta_pct_daily": 0.42, "repeated": False,
                 "sizing": {"max_contracts": 4, "cost_per_contract": 166.0,
                            "total_cost": 664.0, "cost_pct_of_account": 66.4,
                            "binding": "prima", "blocked": None,
                            "blocked_reason": None, "burn_days": 90,
                            "theta_burn_per_contract": 63.0, "total_burn": 252.0,
                            "burn_pct_of_account": 25.2, "fully_decays": False},
                 "history": {"hit_rate": 61, "median_sessions": 8, "resolved": 12}},
                {"id": 2, "ticker": "SPX", "symbol": "SPX", "underlying": "SPX",
                 "type": "call", "strike": 7900, "expiration": "2027-03-19",
                 "dte": 214, "price": 387.59, "premium": 5810000.0,
                 "asset_price": 7800.0, "side": "bid", "aggression": "medio",
                 "theta_pct_daily": 0.3, "repeated": True,
                 "sizing": {"max_contracts": 0, "cost_per_contract": 38759.0,
                            "total_cost": 0.0, "cost_pct_of_account": 0.0,
                            "binding": None, "blocked": None,
                            "blocked_reason": None, "burn_days": 90,
                            "theta_burn_per_contract": 0.0, "total_burn": 0.0,
                            "burn_pct_of_account": 0.0, "fully_decays": False},
                 "history": None},
            ],
            "generated_at": "2026-08-17T21:00:00+00:00",
        }

    def _pinta(self, navegador, servidor, payload=None, **kw):
        import json as _json
        import re as _re

        pg = navegador.new_page(viewport={"width": 1280, "height": 1000})
        errores: list[str] = []
        pg.on("pageerror", lambda e: errores.append(str(e)))
        pg.route(_re.compile(r"/api/tito-ideas"),
                 lambda r: r.fulfill(status=200,
                                     json=payload or self._payload()))
        pg.goto(servidor, wait_until="load")
        pg.wait_for_timeout(1500)
        pg.evaluate("switchView('projectionsView')")
        pg.evaluate("loadProjIdeas()")
        pg.wait_for_timeout(2500)
        return pg, errores

    def test_la_tabla_se_pinta_y_la_rueda_DESAPARECE(self, navegador, servidor):
        pg, errores = self._pinta(navegador, servidor)
        try:
            txt = pg.evaluate(
                "() => document.getElementById('projIdeas').innerText")
            assert "Escaneando" not in txt, (
                "la rueda se quedó: algo lanzó al renderizar y nadie sustituyó "
                f"ese HTML. Errores de página: {errores}")
            assert "BAC" in txt, f"la tabla no llegó a pintarse: {txt[:300]}"
            assert not errores, f"el render lanza: {errores}"
        finally:
            pg.close()

    def test_la_TARJETA_DE_RIESGO_se_pinta_con_los_campos_nuevos(
            self, navegador, servidor):
        """`vcRiesgoHTML` se reescribió dos veces sin que ningún navegador la
        ejecutara. Aquí se comprueban los números que salen de ella."""
        pg, errores = self._pinta(navegador, servidor)
        try:
            txt = pg.evaluate(
                "() => (document.getElementById('projRiesgo')||{}).innerText || ''")
            assert "$15" in txt and "$240" in txt, (
                f"la pérdida máxima no sale como rango: {txt[:400]}")
            assert "$800" in txt, "no sale el techo de despliegue del motor"
            assert not errores, f"la tarjeta de riesgo lanza: {errores}"
        finally:
            pg.close()

    def test_si_el_RENDER_lanza_NO_se_queda_la_rueda(self, navegador, servidor):
        """La red de seguridad, medida rompiendo el render a propósito.

        Da igual QUÉ campo se lea mal el día de mañana: lo que no puede pasar
        es que el fallo se vea como una rueda girando. Un fallo que se dice se
        puede reintentar; una rueda solo se puede abandonar.
        """
        import re as _re

        pg = navegador.new_page(viewport={"width": 1280, "height": 1000})
        pg.on("pageerror", lambda e: None)
        pg.route(_re.compile(r"/api/tito-ideas"),
                 lambda r: r.fulfill(status=200, json=self._payload()))
        pg.goto(servidor, wait_until="load")
        pg.wait_for_timeout(1500)
        pg.evaluate("switchView('projectionsView')")
        # Se rompe el render a mano: es la forma de medir la red y no la suerte.
        pg.evaluate("() => { window.renderProjIdeas = "
                    "() => { throw new Error('roto a proposito'); }; }")
        # `.catch` porque el cargador RELANZA a propósito: la red pinta el
        # aviso y deja que el error siga saliendo en la consola, que es donde
        # se arregla. Sin esto lo que falla es el arnés, no el producto.
        pg.evaluate("() => { loadProjIdeas().catch(() => {}); }")
        pg.wait_for_timeout(2000)
        try:
            txt = pg.evaluate(
                "() => document.getElementById('projIdeas').innerText")
            assert "Escaneando" not in txt, (
                "un fallo de render sigue viéndose como una rueda eterna")
            assert txt.strip(), "no se pinta nada: la caja queda vacía"
        finally:
            pg.close()

    def test_las_DOS_VISTAS_de_Victor_existen_y_se_cambian(self, navegador, servidor):
        """Su pagina tiene Estudiante y Pro; aqui solo existia la Pro.

        La de Estudiante no es una version reducida: es la misma matematica
        contada en frases, que es como se entiende un techo de contratos sin
        leer una tabla de 16 columnas.
        """
        pg, errores = self._pinta(navegador, servidor)
        try:
            # Estudiante es la de por defecto, como en su app.
            pg.evaluate("vcIdeasCambiaVista('estudiante')")
            pg.wait_for_timeout(400)
            est = pg.evaluate("() => document.getElementById('projIdeas').innerText")
            assert "Máximo" in est or "no alcanza" in est or "Con $" in est, (
                f"la vista Estudiante no cuenta el veredicto en palabras: {est[:400]}")
            assert "BAC" in est
            assert pg.evaluate(
                "() => document.querySelectorAll('#projIdeas article').length") >= 1, (
                "no hay tarjetas en la vista Estudiante")

            pg.evaluate("vcIdeasCambiaVista('pro')")
            pg.wait_for_timeout(400)
            assert pg.evaluate(
                "() => document.querySelectorAll('#projIdeas table tbody tr').length") >= 1, (
                "la vista Pro no pinta la tabla")
            pro = pg.evaluate("() => document.getElementById('projIdeas').innerText")
            # Las dos columnas de su tabla que faltaban.
            assert "θ/día" in pro and "% cuenta" in pro, (
                f"faltan columnas suyas: {pro[:300]}")
            assert not errores, errores[:3]
        finally:
            pg.close()

    def test_el_boton_de_VOLVER_A_ESCANEAR_existe(self, navegador, servidor):
        """Sin el, la unica forma de repetir el escaneo era recargar la pagina."""
        pg, errores = self._pinta(navegador, servidor)
        try:
            hay = pg.evaluate(
                "() => [...document.querySelectorAll('#projIdeas button')]"
                ".some(b => /Volver a escanear/.test(b.textContent))")
            assert hay, "no hay boton de volver a escanear"
            assert not errores, errores[:3]
        finally:
            pg.close()

    def test_el_escaneo_SE_CUENTA_paso_a_paso_en_pantalla(self, navegador, servidor):
        """Lo que se veia congelado. Su pagina pinta una lista de pasos que
        crece; aqui la etiqueta del primero se quedaba clavada todo el escaneo.

        Se sirve un SSE de verdad, con pausa entre pasos, y se mira la pantalla
        ANTES de que llegue el `done`.
        """
        import re as _re

        cuerpo = (
            'data: {"type":"step","label":"Escaneando el flujo de todo el mercado…"}\n\n'
            'data: {"type":"step","label":"Página 1 — 120 operaciones grandes"}\n\n'
            'data: {"type":"step","label":"Clasificando 120 operaciones…"}\n\n'
        )
        pg = navegador.new_page(viewport={"width": 1280, "height": 1000})
        errores: list[str] = []
        pg.on("pageerror", lambda e: errores.append(str(e)))
        pg.route(_re.compile(r"/api/tito-ideas/stream"),
                 lambda r: r.fulfill(status=200, body=cuerpo,
                                     headers={"Content-Type": "text/event-stream"}))
        pg.goto(servidor, wait_until="load")
        pg.wait_for_timeout(1500)
        pg.evaluate("switchView('projectionsView')")
        # Se graba TODO lo que se pinta, no solo el estado final: los pasos son
        # por definición intermedios, y al cerrarse el stream el cargador cae a
        # la ruta JSON y los sustituye. Mirar solo el final no vería nunca lo
        # que esta prueba mide.
        pg.evaluate("""() => {
            window.__pintado = [];
            const n = document.getElementById('projIdeas');
            new MutationObserver(() => window.__pintado.push(n.innerText))
                .observe(n, { childList: true, subtree: true, characterData: true });
        }""")
        pg.evaluate("() => { loadProjIdeas().catch(() => {}); }")
        pg.wait_for_timeout(2000)
        try:
            visto = "\n".join(pg.evaluate("() => window.__pintado || []"))
            assert "Página 1" in visto, (
                f"los pasos del stream no llegan a la pantalla: {visto[:400]}")
            assert "Clasificando" in visto, f"solo se pinta el primer paso: {visto[:400]}"
            assert not errores, errores[:3]
        finally:
            pg.close()


class TestNadaSeSaleDeLaPANTALLA:
    """«En el teléfono me sale así, y no se puede mover para el lado.»

    El panel salía encogido a media pantalla, con una franja muerta a la derecha
    y sin poder desplazarlo de lado. Ese es el cuadro exacto de un documento más
    ancho que el viewport: iOS lo ALEJA para que quepa —por eso todo sale
    pequeño— y el `overflow-x: hidden` del `body` impide llegar a lo que
    sobresale. Ni se ve, ni se alcanza, ni se entiende.

    La medida que lo decide es una sola y no admite interpretación:
    `documentElement.scrollWidth` no puede pasar de `clientWidth`. Un elemento
    ancho SÍ puede existir —una tabla de once columnas en un iPad— siempre que
    scrollee DENTRO de su caja en vez de empujar el documento.

    Se mide en los cuatro tamaños de la regla de la casa: teléfono, teléfono
    grande, iPad y escritorio.
    """

    TAMANOS = [
        ("iPhone SE", {"width": 375, "height": 667}),
        ("iPhone 14", {"width": 390, "height": 844}),
        ("iPad", {"width": 820, "height": 1180}),
        ("escritorio", {"width": 1280, "height": 900}),
    ]

    @pytest.mark.parametrize("nombre,viewport", TAMANOS)
    @pytest.mark.parametrize("vista", ["homeView", "projectionsView", "exploreView"])
    def test_el_documento_no_es_mas_ancho_que_la_pantalla(
            self, navegador, servidor, nombre, viewport, vista):
        pg = navegador.new_page(viewport=viewport, device_scale_factor=2,
                                is_mobile=viewport["width"] < 800, has_touch=True)
        try:
            pg.goto(servidor, wait_until="load")
            pg.wait_for_timeout(1800)
            try:
                pg.evaluate(f"switchView('{vista}')")
            except Exception:                     # una vista que no existe no es este test
                pytest.skip(f"{vista} no está en el panel")
            pg.wait_for_timeout(700)
            d = pg.evaluate("""() => {
                const de = document.documentElement;
                const fuera = [];
                document.querySelectorAll('*').forEach(el => {
                    const r = el.getBoundingClientRect();
                    if (r.right > de.clientWidth + 1 && r.width > de.clientWidth + 1) {
                        fuera.push(el.tagName.toLowerCase() + '#' + (el.id || '') +
                                   '.' + (el.className || '').toString().slice(0, 50) +
                                   ' (' + Math.round(r.width) + 'px)');
                    }
                });
                return { cw: de.clientWidth, sw: de.scrollWidth, fuera: fuera.slice(0, 5) };
            }""")
            assert d["sw"] <= d["cw"], (
                f"[{nombre} · {vista}] el documento mide {d['sw']}px en una pantalla "
                f"de {d['cw']}px: el teléfono se aleja para que quepa y todo sale "
                f"pequeño. Lo que se sale: {d['fuera']}")
        finally:
            pg.close()

    def test_la_barra_de_navegacion_SIGUE_pegada_arriba(self, navegador, servidor):
        """El precio de contener el desbordamiento mal pagado.

        `overflow-x: hidden` en `html` habría servido igual… y habría roto el
        `position: sticky` de la barra, porque crea un contenedor de scroll.
        Por eso se usa `clip`. Esto lo comprueba en vez de confiar.
        """
        pg = navegador.new_page(viewport={"width": 390, "height": 844},
                                is_mobile=True, has_touch=True)
        try:
            pg.goto(servidor, wait_until="load")
            pg.wait_for_timeout(1500)
            pegada = pg.evaluate("""() => {
                const n = document.querySelector('nav');
                return n ? getComputedStyle(n).position : null;
            }""")
            assert pegada == "sticky", (
                f"la barra dejó de estar pegada arriba (position: {pegada}) — "
                "es lo que rompe `overflow-x: hidden` en `html`")
        finally:
            pg.close()

    @pytest.mark.parametrize("nombre,viewport", [
        ("iPhone SE", {"width": 375, "height": 667}),
        ("iPhone 14", {"width": 390, "height": 844}),
        ("iPhone Max", {"width": 430, "height": 932}),
    ])
    @pytest.mark.parametrize("vista", ["estudiante", "pro"])
    def test_con_la_TABLA_ANCHA_pintada_tampoco_se_sale(
            self, navegador, servidor, nombre, viewport, vista):
        """El caso que de verdad lo reproduce, y que el de arriba no tocaba.

        La tabla de Ideas declara `min-w-[700px]`. En un teléfono la consulta de
        `@media (max-width: 639px)` la convierte en tarjetas… pero medido sin la
        red: el documento salía de **405px en una pantalla de 390** y de 444 en
        una de 430. Con eso iOS aleja la página para que quepa, y ahí está el
        panel a media pantalla con la franja muerta al lado.

        Y hay realimentación: al ensancharse el documento, `max-width: 639px`
        deja de aplicar, las tablas vuelven a ser tablas y el desbordamiento se
        sostiene solo. Por eso el arreglo va en el `<style>` propio y no depende
        del CDN.
        """
        import re as _re

        pg = navegador.new_page(viewport=viewport, device_scale_factor=2,
                                is_mobile=True, has_touch=True)
        pg.route(_re.compile(r"/api/tito-ideas"),
                 lambda r: r.fulfill(
                     status=200,
                     json=TestLaVistaDeIDEASSePintaDeVerdad._payload()))
        try:
            pg.goto(servidor, wait_until="load")
            pg.wait_for_timeout(1500)
            pg.evaluate("switchView('projectionsView')")
            pg.evaluate("() => { loadProjIdeas().catch(() => {}); }")
            pg.wait_for_timeout(2200)
            pg.evaluate(f"vcIdeasCambiaVista('{vista}')")
            pg.wait_for_timeout(600)
            d = pg.evaluate("""() => {
                const de = document.documentElement;
                const fuera = [];
                document.querySelectorAll('*').forEach(el => {
                    const r = el.getBoundingClientRect();
                    if (r.right > de.clientWidth + 1)
                        fuera.push(el.tagName.toLowerCase() + '.' +
                                   (el.className || '').toString().slice(0, 40) +
                                   ' (' + Math.round(r.width) + 'px)');
                });
                return { cw: de.clientWidth, sw: de.scrollWidth, fuera: fuera.slice(0, 4) };
            }""")
            assert d["sw"] <= d["cw"], (
                f"[{nombre} · ideas/{vista}] documento de {d['sw']}px en pantalla "
                f"de {d['cw']}px. Se sale: {d['fuera']}")
        finally:
            pg.close()


class TestLosDosDRIFTDicenLoMISMO:
    """«Se supone que ambos drift van de acuerdo.»

    Y no iban. Las tarjetas de arriba enseñaban SIEMPRE el bucket de ~30 DTE,
    así que al elegir «3 meses · Drift» arriba se leía el vencimiento de 29
    días y abajo el de 92. Dos vencimientos distintos, la misma pantalla, sin
    nada que dijera que hablaban de cosas diferentes.

    Y la gráfica, al elegir un plazo de Drift, decía «Sin datos».
    """

    @staticmethod
    def _payload():
        base = TestDriftSePintaJuntoAlDelAgente._payload()
        base["targets_drift"] = {
            "90": {"bear": {"target": 179.24, "change_pct": -17.3,
                            "probability": 0.37, "driver": "suelo de 1σ"},
                   "base": {"target": 380.0, "change_pct": 26.6,
                            "probability": 0.74, "driver": "imán"},
                   "bull": {"target": 420.0, "change_pct": 40.0,
                            "probability": 0.40, "driver": "muro"},
                   "confidence": 69.0, "direction": "up",
                   "summary": "Nivel imán: 48% del peso del mapa está en $380.00",
                   "caveat": None, "calibration": None,
                   "niveles_de": "drift", "vencimiento": "2026-11-20",
                   "dte_real": 92, "muro_puts": 260.0, "muro_calls": 380.0,
                   "iman": 380.0, "duplicado": False, "rango_estrecho": False},
        }
        base["chart_geometry"] = {"90": {
            "iv": 0.38, "em": {"sigma_pct": 18.9, "lower1": 243.0, "upper1": 357.0,
                               "lower2": 186.0, "upper2": 414.0},
            "cone": [{"t": 0.0, "upper1": 300.0, "lower1": 300.0,
                      "upper2": 300.0, "lower2": 300.0},
                     {"t": 1.0, "upper1": 357.0, "lower1": 243.0,
                      "upper2": 414.0, "lower2": 186.0}],
            "paths": {k: {"seed": 1.0, "target": v, "clamped": False,
                          "points": [{"t": 0.0, "price": 300.0},
                                     {"t": 1.0, "price": v}]}
                      for k, v in (("bear", 179.24), ("base", 380.0),
                                   ("bull", 420.0))}}}
        base["history"] = [{"time": f"2026-08-{d:02d}", "open": 300.0,
                            "high": 305.0, "low": 295.0, "close": 300.0}
                           for d in range(1, 21)]
        base["predictions"] = {"30": {
            "bear": {"target": 280.0, "change_pct": -6.7, "probability": 0.4,
                     "driver": "x"},
            "base": {"target": 305.0, "change_pct": 1.7, "probability": 0.6,
                     "driver": "x"},
            "bull": {"target": 320.0, "change_pct": 6.7, "probability": 0.3,
                     "driver": "x"},
            "confidence": 60.0, "direction": "up", "summary": "s",
            "caveat": None, "calibration": None}}
        base["score"] = 61
        base["verdict"] = "Oportunidad Moderada"
        base["levels_for_chart"] = []
        return base

    def _abre_en(self, navegador, servidor, horizonte):
        pg, errores = _abre(navegador, servidor)
        pg.evaluate("switchView('projectionsView')")
        pg.wait_for_timeout(300)
        pg.evaluate("""(p) => {
            projData = p; window._vcData = p; window._vcHz = null;
            renderProjections(p); renderVictorTargets(p);
        }""", self._payload())
        pg.wait_for_timeout(400)
        pg.evaluate(f"""() => {{
            window._vcHz = {horizonte};
            renderProjections(window._vcData);
            renderVictorTargets(window._vcData);
            renderVictorProjChart('projChart', window._vcData, {horizonte});
        }}""")
        pg.wait_for_timeout(600)
        return pg, errores

    def test_al_elegir_un_plazo_de_drift_las_TARJETAS_lo_siguen(
            self, navegador, servidor):
        pg, errores = self._abre_en(navegador, servidor, 90)
        try:
            cards = pg.evaluate(
                "() => document.getElementById('projCards').innerText")
            # El bucket de 90 DTE: muros 260/380, imán 380.
            assert "$380.00" in cards and "$260.00" in cards, cards[:400]
            # Y NO los del bucket de 30 DTE, que es lo que se quedaba pegado.
            assert "$400.00" not in cards, (
                "las tarjetas siguen enseñando el vencimiento de ~30 días "
                f"con el horizonte en 90: {cards[:400]}")
            assert "2026-11-20" in cards, cards[:400]
            assert not errores, errores[:3]
        finally:
            pg.close()

    def test_con_un_plazo_del_AGENTE_vuelven_al_de_30_dias(
            self, navegador, servidor):
        """El de ~30 DTE es el único que solapa con 10/20/30."""
        pg, errores = self._abre_en(navegador, servidor, 30)
        try:
            cards = pg.evaluate(
                "() => document.getElementById('projCards').innerText")
            assert "$400.00" in cards, cards[:400]
            assert "2026-09-18" in cards, cards[:400]
            assert not errores, errores[:3]
        finally:
            pg.close()

    def test_la_GRAFICA_se_dibuja_con_un_plazo_de_drift(
            self, navegador, servidor):
        """«Si elijo el drift me gustaría que se vea la gráfica.»"""
        pg, errores = self._abre_en(navegador, servidor, 90)
        try:
            d = pg.evaluate("""() => {
                const c = document.getElementById('projChart');
                return { txt: (c.innerText || '').trim(),
                         svgs: c.querySelectorAll('svg').length,
                         // La gráfica dibuja con `polyline`/`line`/`path`
                         // según el trazo; se cuentan los tres.
                         trazos: c.querySelectorAll(
                             'svg polyline, svg path, svg line').length };
            }""")
            assert "Sin datos" not in d["txt"], d
            assert d["svgs"] >= 1, f"no se dibujó ningún SVG: {d}"
            assert d["trazos"] >= 5, (
                f"la gráfica salió sin trazos: {d}")
            # Y que lo dibujado sea lo de ESTE plazo, no lo del agente.
            for esperado in ("$420.00", "$380.00", "$179.24", "90d", "±18.9%"):
                assert esperado in d["txt"], (
                    f"falta «{esperado}» en la gráfica: {d['txt'][:300]}")
            assert not errores, errores[:3]
        finally:
            pg.close()

    def test_el_TEXTO_del_resumen_y_la_linea_de_niveles_dicen_lo_mismo(
            self, navegador, servidor):
        """El imán de la línea azul y el que nombra el resumen: el mismo."""
        pg, errores = self._abre_en(navegador, servidor, 90)
        try:
            txt = pg.evaluate(
                "() => document.getElementById('projTargets').innerText")
            assert "$380.00" in txt, txt[:400]
            # El resumen nombra el imán; si dijera otro precio, se
            # contradiría con la línea de niveles de justo encima.
            assert "$380.00" in txt.split("Nivel imán")[-1][:60] or \
                   "380" in txt.split("imán")[-1][:60], txt[:600]
            assert not errores, errores[:3]
        finally:
            pg.close()


class TestUnaPantallaEnBlancoNoEsUnaOPCION:
    """«Me sale así» — y «así» era una pantalla vacía, sin un solo mensaje.

    Dos defectos encadenados la producían:

    · Los cuatro pintores iban seguidos, así que el primero que lanzaba se
      llevaba por delante a los tres de detrás. Un fallo en las tarjetas
      borraba los targets, la gráfica y la cabecera, que no tenían culpa.
    · Y el `catch` escribía el error en `projEmpty`… **dos líneas después de
      ocultarlo**. El mensaje existía, en un nodo invisible. Desde fuera:
      pantalla en blanco y muda.

    Una pantalla en blanco es el peor error posible, porque ni siquiera se
    puede contar lo que pasó.
    """

    def _rompe(self, navegador, servidor, cual):
        """Sabotea UN pintor y pide el análisis, como lo pide el panel."""
        pg, errores = _abre(navegador, servidor)
        pg.evaluate("switchView('projectionsView')")
        pg.wait_for_timeout(300)
        pg.route(re.compile(r"/api/projection-targets"),
                 lambda r: r.fulfill(
                     status=200,
                     json=TestDriftSePintaJuntoAlDelAgente._payload()))
        pg.evaluate(f"""() => {{
            window.{cual} = function () {{ throw new Error('sabotaje de {cual}'); }};
        }}""")
        pg.evaluate("loadProjections('NVDA')")
        pg.wait_for_timeout(3000)
        return pg, errores

    @pytest.mark.parametrize("cual,etiqueta", [
        ("renderProjections", "las tarjetas"),
        ("renderVictorTargets", "los targets"),
        ("vcSyncCabecera", "la cabecera"),
    ])
    def test_si_un_pintor_revienta_SE_DICE_en_pantalla(self, navegador, servidor,
                                                      cual, etiqueta):
        pg, _ = self._rompe(navegador, servidor, cual)
        try:
            visible = pg.evaluate("""() => {
                const c = document.getElementById('projRoto');
                if (!c) return null;
                return c.offsetParent !== null ? c.innerText : null;
            }""")
            assert visible, (
                f"«{cual}» reventó y la pantalla no dijo NADA. Eso es el "
                f"fallo entero: en blanco y sin mensaje.")
            assert etiqueta in visible, visible
            assert "sabotaje" in visible, visible
        finally:
            pg.close()

    @pytest.mark.parametrize("cual", ["renderProjections", "renderVictorTargets"])
    def test_y_LO_DEMAS_se_sigue_pintando(self, navegador, servidor, cual):
        """Un pintor roto no puede llevarse a los otros tres por delante."""
        pg, _ = self._rompe(navegador, servidor, cual)
        try:
            # El que NO se saboteó tiene que haber pintado algo.
            otro = ('projTargets' if cual == 'renderProjections' else 'projCards')
            txt = pg.evaluate(
                f"() => (document.getElementById('{otro}').innerText || '').trim()")
            # Lo que se mide es que el OTRO pintor CORRIÓ, no cuánto escribió.
            # Con este payload `renderVictorTargets` pinta «Sin escenarios.»
            # —no trae predicciones— y eso ya prueba lo que importa: no se lo
            # llevó por delante el que reventó. Un umbral de longitud aquí
            # mediría el payload, no la cadena de pintores.
            assert txt, (
                f"al romper «{cual}» se quedó vacío también #{otro}: los "
                f"pintores siguen encadenados")
        finally:
            pg.close()

    def test_si_falla_TODO_el_vacio_se_vuelve_a_ENSENAR(self, navegador, servidor):
        """El error se escribía en un nodo ya oculto. Ahora se enseña primero.

        Se rompe `vcFetchTargets`, que lanza ANTES de que exista payload: ese
        es el camino que pasa por el `catch` de fuera.
        """
        pg, _ = _abre(navegador, servidor)
        try:
            pg.evaluate("switchView('projectionsView')")
            pg.wait_for_timeout(300)
            pg.evaluate("""() => {
                window.vcFetchTargets = async function () {
                    throw new Error('la red se cayó');
                };
            }""")
            pg.evaluate("loadProjections('NVDA')")
            pg.wait_for_timeout(2500)
            d = pg.evaluate("""() => {
                const e = document.getElementById('projEmpty');
                return { visible: e.offsetParent !== null, txt: (e.innerText||'').trim(),
                         contenido: !document.getElementById('projContent')
                                        .classList.contains('hidden') };
            }""")
            assert d["visible"], "el mensaje se escribió en un nodo oculto otra vez"
            assert "la red se cayó" in d["txt"], d["txt"]
            assert not d["contenido"], "el contenido vacío se quedó a la vista"
        finally:
            pg.close()

    def test_el_aviso_del_ticker_VIEJO_no_se_queda_pegado(self, navegador, servidor):
        """Acusaría al siguiente símbolo de un fallo que no es suyo."""
        pg, _ = self._rompe(navegador, servidor, "renderVictorTargets")
        try:
            assert pg.evaluate("() => !!document.getElementById('projRoto')")
            pg.evaluate("vcLimpiaTicker({})")
            pg.wait_for_timeout(300)
            assert pg.evaluate("() => !document.getElementById('projRoto')"), (
                "el aviso del ticker anterior sigue puesto")
        finally:
            pg.close()


class TestDriftSePintaJuntoAlDelAgente:
    """«call wall/Drift → $215/$225.»

    Dos reglas, y las dos se miden aquí:

    · **A 10/20/30 días** —lo que el motor ya ve— se pintan los DOS números,
      el suyo y el de Drift, separados por una barra. Es el formato que pidió
      Kevin y el bucket que se usa es el de ~30 DTE, el único que solapa.
    · **A 90/120/320 días** el motor no llega: sus horizontes cortan en 30 y su
      GEX solo mira los strikes a ±20% del spot. Ahí el muro de calls, el de
      puts y el imán salen SOLO de Drift, y el resto del scorecard sigue
      siendo del motor.

    Se pinta llamando a `renderProjections` con el payload, que es exactamente
    lo que hace `loadProjections` cuando la ruta responde.
    """

    @staticmethod
    def _payload(drift=True):
        nodos = [
            {"strike": 310.0, "net_gex": 4.2e6, "call_gex": 4.2e6, "put_gex": 0,
             "trade_premium": 0, "trade_count": 0, "concentration": 0.9, "side": "call"},
            {"strike": 280.0, "net_gex": -3.1e6, "call_gex": 0, "put_gex": 3.1e6,
             "trade_premium": 0, "trade_count": 0, "concentration": 0.7, "side": "put"},
        ]
        d = {
            "ok": True, "ticker": "DEMO", "spot": 300.0,
            "gex": {"regime": "positive", "nodes": nodos, "flip_strike": 295.0,
                    "king_strike": 305.0, "total_net_gex": 1.1e6,
                    "direction": "up", "low_liquidity": False, "n": 24,
                    "iv": 0.38},
            "structure": {"put_pct": 45.0, "call_pct": 55.0},
            "unusual": [],
        }
        if drift:
            d["drift"] = {
                "spot": 300.0, "iv": 0.38,
                "iv_fuente": "volatilidad realizada del motor (no IV de la cadena)",
                "mensuales": 9, "motivo": "", "sin_datos": [],
                "buckets": [
                    {"etiqueta": "Largo ~320 DTE", "sentimiento": "Largo",
                     "dte_objetivo": 320, "vencimiento": "2027-07-16",
                     "dte_real": 330, "muro_calls": 500.0, "muro_calls_oi": 12000,
                     "muro_puts": 200.0, "muro_puts_oi": 8000,
                     "magneto": 500.0, "magneto_nocional": 6.0e8,
                     "sigma": 110.0, "total_oi": 90000, "nocional_neto": 4.0e8,
                     "deriva": "DENTRO DEL RANGO · atracción: el imán de nocional está en 500,00.",
                     "breakout": False, "duplicado": False, "solapa_motor": False},
                    {"etiqueta": "Largo ~120 DTE", "sentimiento": "Largo",
                     "dte_objetivo": 120, "vencimiento": "2026-12-18",
                     "dte_real": 120, "muro_calls": 420.0, "muro_calls_oi": 9000,
                     "muro_puts": 250.0, "muro_puts_oi": 7000,
                     "magneto": 420.0, "magneto_nocional": 3.0e8,
                     "sigma": 68.0, "total_oi": 70000, "nocional_neto": 2.0e8,
                     "deriva": "DENTRO DEL RANGO · atracción.",
                     "breakout": False, "duplicado": False, "solapa_motor": False},
                    {"etiqueta": "Corto ~90 DTE", "sentimiento": "Corto",
                     "dte_objetivo": 90, "vencimiento": "2026-11-20",
                     "dte_real": 92, "muro_calls": 380.0, "muro_calls_oi": 6000,
                     "muro_puts": 260.0, "muro_puts_oi": 5000,
                     "magneto": 380.0, "magneto_nocional": 2.0e8,
                     "sigma": 60.0, "total_oi": 50000, "nocional_neto": 1.0e8,
                     "deriva": "RUPTURA al alza: el precio está fuera del rango.",
                     "breakout": True, "duplicado": False, "solapa_motor": False},
                    {"etiqueta": "Corto ~30 DTE", "sentimiento": "Corto",
                     "dte_objetivo": 30, "vencimiento": "2026-09-18",
                     "dte_real": 29, "muro_calls": 400.0, "muro_calls_oi": 11000,
                     "muro_puts": 270.0, "muro_puts_oi": 9500,
                     "magneto": 400.0, "magneto_nocional": 4.4e8,
                     "sigma": 34.0, "total_oi": 60000, "nocional_neto": 3.0e8,
                     "deriva": "DENTRO DEL RANGO · atracción.",
                     "breakout": False, "duplicado": False, "solapa_motor": True},
                ],
            }
        return d

    def _pinta(self, navegador, servidor, drift=True):
        pg, errores = _abre(navegador, servidor)
        pg.evaluate("switchView('projectionsView')")
        pg.wait_for_timeout(400)
        pg.evaluate("(p) => { projData = p; renderProjections(p); }",
                    self._payload(drift))
        pg.wait_for_timeout(400)
        return pg, errores

    def test_las_tarjetas_llevan_los_DOS_numeros(self, navegador, servidor):
        pg, errores = self._pinta(navegador, servidor)
        try:
            txt = pg.evaluate("() => document.getElementById('projCards').innerText")
            assert "Muro de calls / Drift" in txt, txt[:400]
            assert "Muro de puts / Drift" in txt, txt[:400]
            assert "Nodo imán / Drift" in txt, txt[:400]
            # El del motor y el de Drift, en ese orden, separados por la barra.
            assert "$310.00 / $400.00" in txt, txt[:400]
            assert "$280.00 / $270.00" in txt, txt[:400]
            assert "$305.00 / $400.00" in txt, txt[:400]
            assert not errores, errores[:3]
        finally:
            pg.close()

    def test_el_numero_de_drift_es_el_del_bucket_de_30_no_el_de_320(
            self, navegador, servidor):
        """El que solapa, no el primero de la lista.

        En el payload el bucket de 320 va PRIMERO y su muro es $500. Si se
        cogiera el primero en vez del marcado, la tarjeta diría $500 — un
        número de dentro de un año pegado a un horizonte de tres semanas.
        """
        pg, errores = self._pinta(navegador, servidor)
        try:
            txt = pg.evaluate("() => document.getElementById('projCards').innerText")
            assert "$400.00" in txt and "$500.00" not in txt, txt[:400]
        finally:
            pg.close()

    def test_sin_drift_la_tarjeta_queda_COMO_ESTABA(self, navegador, servidor):
        """Sin dato no se inventa media barra: se pinta lo del motor y ya."""
        pg, errores = self._pinta(navegador, servidor, drift=False)
        try:
            txt = pg.evaluate("() => document.getElementById('projCards').innerText")
            assert "/ Drift" not in txt, txt[:400]
            assert "Muro de calls" in txt and "$310.00" in txt
            oculta = pg.evaluate(
                "() => document.getElementById('projDriftCard').classList.contains('hidden')")
            assert oculta, "la tarjeta de plazos largos se pintó sin datos"
            assert not errores, errores[:3]
        finally:
            pg.close()

    def test_los_TRES_motivos_de_no_tener_numero_se_distinguen(self, navegador, servidor):
        """«Sin número» no es una sola cosa, y decirlo como si lo fuera miente.

        · Drift **no corrió** (el servidor devolvió `null`: cadena que no llegó,
          o el módulo reventó) → los muros de arriba son solo los del motor.
        · Drift corrió y **ese plazo** no se pudo resolver → sale su motivo.
        · Un «no tiene número» a secas se lee como «no hay posiciones ahí»,
          que es justo lo contrario de lo que pasó.
        """
        pg, errores = _abre(navegador, servidor)
        try:
            pg.evaluate("switchView('projectionsView')")
            pg.wait_for_timeout(400)

            # (a) el servidor no sirvió drift
            p = self._payload(drift=False)
            pg.evaluate("(p) => { projData = p; renderProjections(p); }", p)
            pg.wait_for_timeout(300)
            txt = pg.evaluate("() => document.getElementById('projCards').innerText")
            assert "no pudo leer la cadena" in txt, txt[:400]

            # (b) corrió, pero el plazo de 30 se quedó sin resolver
            p = self._payload()
            p["drift"]["buckets"] = [b for b in p["drift"]["buckets"]
                                     if not b["solapa_motor"]]
            p["drift"]["sin_datos"] = [{
                "etiqueta": "Corto ~30 DTE", "dte_objetivo": 30,
                "motivo": "ese vencimiento no tiene calls y puts con interés abierto"}]
            pg.evaluate("(p) => { projData = p; renderProjections(p); }", p)
            pg.wait_for_timeout(300)
            txt = pg.evaluate("() => document.getElementById('projCards').innerText")
            assert "no tiene número para el plazo de ~30 días" in txt, txt[:400]
            assert "calls y puts con interés abierto" in txt, txt[:400]
            assert "no pudo leer la cadena" not in txt, txt[:400]
            assert not errores, errores[:3]
        finally:
            pg.close()

    def test_los_plazos_largos_tienen_su_propia_tarjeta(self, navegador, servidor):
        pg, errores = self._pinta(navegador, servidor)
        try:
            oculta = pg.evaluate(
                "() => document.getElementById('projDriftCard').classList.contains('hidden')")
            assert not oculta, "la tarjeta de plazos largos no se mostró"
            txt = pg.evaluate("() => document.getElementById('projDriftBody').innerText")
            for e in ("Largo ~320 DTE", "Largo ~120 DTE", "Corto ~90 DTE"):
                assert e in txt, f"falta {e}: {txt[:400]}"
            # El de 30 NO: ese ya va al lado del número del motor arriba.
            assert "Corto ~30 DTE" not in txt, txt[:400]
            assert not errores, errores[:3]
        finally:
            pg.close()

    def test_la_tarjeta_larga_dice_que_ahi_manda_DRIFT_y_no_el_motor(
            self, navegador, servidor):
        pg, errores = self._pinta(navegador, servidor)
        try:
            txt = pg.evaluate("() => document.getElementById('projDriftCard').innerText")
            assert "30 días" in txt, "no explica dónde corta el motor"
            assert "solo de Drift" in txt, txt[:400]
        finally:
            pg.close()

    def test_la_ruptura_se_marca(self, navegador, servidor):
        pg, errores = self._pinta(navegador, servidor)
        try:
            txt = pg.evaluate("() => document.getElementById('projDriftBody').innerText")
            assert "RUPTURA" in txt, txt[:400]
        finally:
            pg.close()

    def test_el_interes_abierto_NO_se_pinta_como_dinero(self, navegador, servidor):
        """`fmtAbbr` mete el signo de dólar y el OI se cuenta en CONTRATOS.

        "$11K" ahí sería una cifra de dinero que nadie midió.
        """
        pg, errores = self._pinta(navegador, servidor)
        try:
            txt = pg.evaluate("() => document.getElementById('projCards').innerText")
            assert "OI 11K" in txt, txt[:400]
            assert "OI $" not in txt, txt[:400]
        finally:
            pg.close()

    @pytest.mark.parametrize("nombre,viewport", [
        ("iPhone SE", {"width": 375, "height": 667}),
        ("iPhone 14", {"width": 390, "height": 844}),
        ("iPad", {"width": 820, "height": 1180}),
    ])
    def test_con_la_TABLA_DE_DRIFT_pintada_nada_se_sale(self, navegador, servidor,
                                                       nombre, viewport):
        """La regla de la casa: lo que se sube vale en los cuatro tamaños.

        La tabla de plazos largos declara `min-w-[720px]`. Si eso empujara el
        documento, el teléfono volvería a alejarse para que quepa — que es el
        fallo que se cerró en la ronda anterior.
        """
        pg = navegador.new_page(viewport=viewport, device_scale_factor=2,
                                is_mobile=viewport["width"] < 800, has_touch=True)
        try:
            pg.goto(servidor, wait_until="load")
            pg.wait_for_timeout(1800)
            pg.evaluate("switchView('projectionsView')")
            pg.wait_for_timeout(400)
            pg.evaluate("(p) => { projData = p; renderProjections(p); }",
                        self._payload())
            pg.wait_for_timeout(600)
            d = pg.evaluate("""() => ({ cw: document.documentElement.clientWidth,
                                        sw: document.documentElement.scrollWidth })""")
            assert d["sw"] <= d["cw"], (
                f"[{nombre}] con Drift pintado el documento mide {d['sw']}px "
                f"en una pantalla de {d['cw']}px")
        finally:
            pg.close()
