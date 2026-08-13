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
