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
