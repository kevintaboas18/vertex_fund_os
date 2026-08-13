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
                    .slice(0, 6).map(b => b.innerText.trim()),
                texto: document.getElementById('sectoresParrilla').innerText,
            })""")
            assert d["visible"], "Sectores no se abrió desde el menú del móvil"
            assert d["pulsables"] == 11, (
                f"{d['pulsables']} sectores pulsables, deberían ser 11")
            assert d["ventanas"] == ["1D", "7D", "1M", "3M", "6M", "1A"], d["ventanas"]
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
            assert len(filas) == 5, f"XLK tiene 5 industrias, salieron {len(filas)}"
            texto = " ".join(filas)
            for esperado in ("SMH", "IGV", "CIBR", "SKYY", "XSD"):
                assert esperado in texto, f"falta {esperado}: {texto}"
            assert "Semiconductores" in texto and "Software" in texto
            assert "···" in texto, (
                "los números tendrían que quedarse en puntos suspensivos")
            assert not errores, errores[:3]
        finally:
            pg.close()

    def test_un_sector_sin_desglose_lo_dice_en_su_seccion(self, navegador,
                                                          servidor):
        pg, errores = self._abre(navegador, servidor)
        try:
            pg.evaluate("abreSector('XLU')")
            pg.wait_for_timeout(800)
            txt = pg.evaluate("() => document.getElementById('sectorIndustrias').innerText")
            assert txt.strip(), "ni lista ni motivo: la sección se queda muda"
            assert "desglose" in txt, txt
            assert not errores, errores[:3]
        finally:
            pg.close()
