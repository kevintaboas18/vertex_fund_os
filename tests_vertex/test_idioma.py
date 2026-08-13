"""En inglés, TODO en inglés. Y en español, todo en español.

Un idioma a medias es peor que un solo idioma: la mitad traducida da la
impresión de que lo demás también lo está, y lo que queda sin traducir no lo
encuentra quien lo escribió — lo encuentra el usuario.

Por eso la mayoría de estos casos no comprueban una frase concreta: abren la
pantalla en un navegador real, la recorren entera y **fallan si queda español**.
Es la única forma de medir «no falta nada» sin ir preguntando cadena por cadena.

    python -m pytest tests_vertex/test_idioma.py -q
"""

from __future__ import annotations

import html
import json
import os
import re
import subprocess
import sys
import time
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

PANEL = ROOT / "vertex_fund_os_platform.html"
CHROMIUM = "/opt/pw-browsers/chromium-1194/chrome-linux/chrome"

# ── Detector de español ─────────────────────────────────────────────────────
#
# Dos señales, no una: el acento basta por sí solo, pero una palabra gramatical
# suelta no —«no», «de» y «es» también son inglesas—, así que se piden dos.
ESP = re.compile(
    r"(?:^|\s)(?:de|del|los|las|una|que|con|para|sin|por|sus|tus|más|ya|cada|toda|"
    r"todos|todas|pero|cuando|aún|hay|sobre|entre|desde|hasta|donde|qué|cómo|porque|"
    r"está|están|puede|tiene|hace|dice|usa|solo|nada|algo|esta|este|estos|estas|"
    r"tienes|puedes|eres|se|es|son|su|tu|el|la|lo|un|al|en|ni|"
    r"cuánto|cuándo|quién|dónde|cuál|otro|otra|otros|otras|mismo|misma)(?:\s|$)", re.I)
#
# `no`, `si`, `y` y `o` NO entran: son palabras inglesas también, y una frase
# inglesa con dos «no» dentro se marcaba como española. Un guardián que grita
# con lo que está bien deja de servir para lo que está mal.
ACENTO = re.compile(r"[áéíóúÁÉÍÓÚñÑ¿¡]")
LETRAS = re.compile(r"[A-Za-zÁÉÍÓÚáéíóúñÑ]{3}")

# Terminaciones que en inglés no existen o son rarísimas. Es lo único que
# delata a una etiqueta corta sin acentos ni palabras gramaticales —
# «Estructura · GEX · niveles», que no tiene ni una— y esas etiquetas son
# justo las cabeceras de tabla y los nombres de pestaña.
#
# Podadas a conciencia: `-arios` marcaba «scenarios», `-ales` marcaba «sales» y
# `-ones` marcaba «zones». Una terminación que también es inglesa convierte el
# guardián en ruido, y un guardián ruidoso se acaba desactivando.
MORFOLOGIA = re.compile(
    r"\b\w{3,}(?:ción|ciones|miento|mientos|idad|idades|ancia|encia|aje|eza|"
    r"ura|uras|anza|ando|endo)\b", re.I)

#: Se quedan igual en los dos idiomas: nombres propios y marcas.
BLANCA = {"Vertex Fund OS", "Warren Buffett Jr", "Victor", "Víctor", "Kevin",
          "Massive", "MarketSnack", "Plaid", "Grok", "Claude", "Gemini",
          "Español", "English", "Tito", "Robinhood", "Yahoo Finance"}


def suena_a_espanol(s: str) -> bool:
    if s in BLANCA or len(s) < 4 or not LETRAS.search(s):
        return False
    if ACENTO.search(s):
        return True
    if len(ESP.findall(" " + s + " ")) >= 2:
        return True
    # Una palabra gramatical suelta no basta (`no` y `de` también son
    # inglesas), pero una gramatical MÁS una terminación española sí, y una
    # etiqueta hecha solo de sustantivos españoles se delata por la
    # terminación aunque no tenga ninguna gramatical.
    return bool(MORFOLOGIA.search(s))


# ── Lo que se puede comprobar sin navegador ─────────────────────────────────

class TestElDiccionarioEstaSano:
    """Un diccionario con una clave mal escrita no falla: no traduce y calla."""

    @staticmethod
    def _entradas():
        texto = PANEL.read_text(encoding="utf-8")
        bloque = texto.split("const VX_ES2EN = {", 1)[1].split("\n};", 1)[0]
        pares = re.findall(r'^\s*("(?:[^"\\]|\\.)*")\s*:\s*("(?:[^"\\]|\\.)*")\s*,\s*$',
                           bloque, re.M)
        return [(json.loads(k), json.loads(v)) for k, v in pares]

    def test_hay_diccionario_y_no_es_un_adorno(self):
        entradas = self._entradas()
        assert len(entradas) > 700, f"solo {len(entradas)} entradas: falta media interfaz"

    def test_ninguna_clave_lleva_espacio_de_sobra(self):
        """`vxFrase` busca con el espacio colapsado. Una clave con un salto de
        línea dentro no casa con nada, y el fallo es invisible."""
        malas = [k for k, _ in self._entradas()
                 if k != re.sub(r"\s+", " ", k).strip()]
        assert not malas, f"claves que nunca casarán: {malas[:5]}"

    def test_ninguna_traduccion_es_la_misma_frase(self):
        iguales = [k for k, v in self._entradas() if k == v]
        assert not iguales, f"entradas que no traducen nada: {iguales[:5]}"

    def test_ninguna_traduccion_se_quedo_en_espanol(self):
        crudas = [(k, v) for k, v in self._entradas()
                  if ACENTO.search(v) and not ACENTO.search(k[:0] or "")
                  and v not in BLANCA]
        # Los valores pueden llevar acentos legítimos (nombres propios), pero no
        # frases enteras sin traducir.
        sospechosas = [(k, v) for k, v in crudas if len(ESP.findall(" " + v + " ")) >= 2]
        assert not sospechosas, f"traducciones que siguen en español: {sospechosas[:3]}"

    def test_los_patrones_conservan_lo_que_capturan(self):
        """Un patrón que captura `$1` y no lo devuelve BORRA un dato en pantalla."""
        texto = PANEL.read_text(encoding="utf-8")
        bloque = texto.split("const VX_PAT = [", 1)[1].split("\n];", 1)[0]
        for linea in re.findall(r"\[/(.+?)/,\s*\n?\s*'(.*?)'\]", bloque, re.S):
            patron, salida = linea
            grupos = len(re.findall(r"(?<!\\)\((?!\?)", patron))
            usados = {int(n) for n in re.findall(r"\$(\d)", salida)}
            assert usados == set(range(1, grupos + 1)), (
                f"el patrón /{patron[:50]}…/ captura {grupos} grupos y usa {sorted(usados)}: "
                "lo que no se devuelve desaparece de la pantalla")


class TestNoQuedaEspanolSINTRADUCIR_EnElCodigo:
    """El guardián que mira el CÓDIGO, no la pantalla.

    El barrido del navegador solo ve lo que se pintó: una frase que solo aparece
    cuando una fuente falla, o cuando una tabla viene vacía, o en la pestaña que
    ese test no abrió, se le escapa entera. Este caso lee el archivo, saca cada
    cadena que puede acabar en pantalla y falla si alguna no tiene traducción.

    Es lo que convierte «añadir texto nuevo lo deja en español» en un fallo que
    salta antes de desplegar, y no en algo que encuentra el usuario. Ya cazó
    catorce, entre ellas las descripciones de las cuatro pestañas.
    """

    #: Se quedan sin entrada, con su motivo. Nada más entra aquí sin uno.
    DECLARADAS = {
        # Trozo de plantilla que el extractor corta en el `${`: la frase entera
        # lleva el precio del flip dentro y va por `VX_PAT`, no por el
        # diccionario. La versión pintada sí la mide el test del navegador.
        "(sobre todo si se pierde el gamma flip $",
    }

    @staticmethod
    def _decodifica(x):
        x = html.unescape(x)
        return re.sub(r"\\u([0-9a-fA-F]{4})", lambda m: chr(int(m.group(1), 16)), x)

    @staticmethod
    def _entera(s):
        """Un literal de JS solo cuenta si se sostiene solo.

        Los trozos de plantilla (`, no una orden de`) no aparecen nunca sueltos
        en pantalla, así que exigirles traducción sería ruido.
        """
        if not s or re.search(r'["\'`<>{}]', s):
            return False
        if re.match(r"^[)\].,;:%·—–+]", s):
            return False
        if s[0].islower() and not re.match(
                r"^(el|la|los|las|un|una|de|del|sin|con|por|para|no|y|o|se|es|son|que|su|tu)\b",
                s, re.I):
            return False
        return True

    def test_toda_cadena_del_panel_tiene_su_traduccion(self):
        h = PANEL.read_text(encoding="utf-8")
        dic = h.split("const VX_ES2EN = {", 1)[1].split("\n};", 1)[0]
        pat = h.split("const VX_PAT = [", 1)[1].split("\n];", 1)[0]

        # marcado visible y atributos que se leen
        cuerpo = re.sub(r"<script\b.*?</script>", "", h, flags=re.S | re.I)
        cuerpo = re.sub(r"<style\b.*?</style>", "", cuerpo, flags=re.S | re.I)
        cuerpo = re.sub(r"<!--.*?-->", "", cuerpo, flags=re.S)
        cand = list(re.split(r"<[^>]+>", cuerpo))
        for a in ("placeholder", "title", "aria-label", "alt"):
            cand += re.findall(rf'{a}="([^"]+)"', h)

        # literales del JS, quitando el diccionario (sus claves SON español)
        js = "\n".join(re.findall(r"<script\b[^>]*>(.*?)</script>", h, flags=re.S | re.I))
        js = js.replace(dic, "").replace(pat, "")
        js = re.sub(r"/\*.*?\*/", "", js, flags=re.S)
        js = re.sub(r"^\s*//.*$", "", js, flags=re.M)
        lit = []
        for m in re.finditer(r"'((?:[^'\\\n]|\\.){4,300})'|\"((?:[^\"\\\n]|\\.){4,300})\"", js):
            lit.append(m.group(1) or m.group(2))
        for m in re.finditer(r"`((?:[^`\\]|\\.){4,6000})`", js, flags=re.S):
            for trozo in re.split(r"\$\{[^{}]*(?:\{[^{}]*\}[^{}]*)*\}", m.group(1)):
                cand += [t for t in re.split(r"<[^>]*>", trozo)
                         if self._entera(re.sub(r"\s+", " ", t).strip())]
        cand += [s for s in lit if self._entera(re.sub(r"\s+", " ", s).strip())]

        vistos, huecos = set(), []
        for s in cand:
            s = self._decodifica(s)
            if "${" in s or "\\n" in s:
                continue                       # trozo de plantilla: lo mide el navegador
            s = re.sub(r"\s+", " ", s).strip().strip("·—–|")
            s = re.sub(r"\s+", " ", s).strip()
            if not s or s in vistos or not suena_a_espanol(s):
                continue
            vistos.add(s)
            if json.dumps(s, ensure_ascii=False) + ":" in dic:
                continue
            if s in self.DECLARADAS:
                continue
            huecos.append(s)

        assert len(vistos) > 350, (
            f"solo {len(vistos)} candidatas: el extractor dejó de ver el archivo")
        assert not huecos, (
            f"{len(huecos)} cadena(s) que se verían en español al elegir inglés:\n  "
            + "\n  ".join(h[:110] for h in huecos[:12]))


class TestElMecanismoEstaCableado:
    """Traducir bien y no llamar al barrido es lo mismo que no traducir."""

    def test_el_observador_se_enciende_al_arrancar(self):
        """Y ANTES del primer barrido: lo que se pinte entre una cosa y la otra
        no lo mira nadie después."""
        h = PANEL.read_text(encoding="utf-8")
        # Hay varios `DOMContentLoaded` en el archivo; el que importa es el que
        # aplica el idioma. Se busca ESE, no el primero que aparezca.
        bloques = [b for b in h.split("document.addEventListener('DOMContentLoaded'")
                   if "vxAplicaIdioma()" in b[:600]]
        assert bloques, "nadie aplica el idioma al arrancar"
        arranque = bloques[0][:600]
        assert "vxObserva()" in arranque, "nadie enciende el observador"
        assert arranque.index("vxObserva()") < arranque.index("vxAplicaIdioma()"), (
            "el observador tiene que encenderse ANTES del primer barrido, o lo que "
            "se pinte entre medias se queda en español para siempre")

    def test_el_idioma_viaja_en_cada_peticion(self):
        h = PANEL.read_text(encoding="utf-8")
        assert "window.fetch = function" in h, "nadie envuelve fetch"
        assert "'X-Vertex-Idioma'" in h
        # Antes del cuerpo de la aplicación: envolver `fetch` al final deja sin
        # cabecera justo las llamadas de arranque, que son las que cargan la
        # pantalla que el usuario ve primero.
        assert h.index("window.fetch = function") < h.index("<body"), (
            "el envoltorio de fetch está por debajo del <body>: las llamadas de "
            "arranque saldrían sin idioma")

    def test_no_se_traduce_lo_que_escribe_el_usuario(self):
        h = PANEL.read_text(encoding="utf-8")
        assert "'TEXTAREA'" in h.split("VX_NO_TOCAR", 1)[1][:200], (
            "el barrido entraría en los textos del usuario y los reescribiría")


class TestElServidorHablaElIdiomaDeLaSesion:
    """El diccionario no alcanza a la prosa del modelo: esa se pide en su idioma."""

    def test_ningun_prompt_clava_el_idioma(self):
        fuente = (ROOT / "vertex_api.py").read_text(encoding="utf-8")
        for clavado in ("SIEMPRE en espanol", "SIEMPRE en español",
                        "en español MUY simple"):
            assert clavado not in fuente, (
                f"«{clavado}» deja al agente hablando español aunque elijas inglés")

    def test_nadie_habla_con_un_modelo_por_fuera_del_envoltorio(self):
        """El guardián que hace que esto no dependa de acordarse.

        Ponerle el idioma a cada prompt en su sitio de llamada es exactamente el
        error que este trabajo vino a arreglar: eran ocho, y el noveno que
        alguien añada saldría en español sin que nadie se entere hasta que lo
        encuentre el usuario. Todas las peticiones pasan por `_gemini_genera` /
        `_claude_crea`, y este caso falla si aparece una que no.
        """
        fuente = (ROOT / "vertex_api.py").read_text(encoding="utf-8")

        # La única llamada cruda permitida es la que hace el propio envoltorio.
        cuerpo_gemini = fuente.split("def _gemini_genera(", 1)[1].split("\ndef ", 1)[0]
        assert "client_gemini.models.generate_content(**kw)" in cuerpo_gemini
        crudas = fuente.count("client_gemini.models.generate_content(")
        assert crudas == 1, (
            f"{crudas - 1} llamada(s) a Gemini saltándose `_gemini_genera`: "
            "esos prompts no reciben el idioma")

        cuerpo_claude = fuente.split("def _claude_crea(", 1)[1].split("\ndef ", 1)[0]
        assert "cliente.messages.create(**kw)" in cuerpo_claude
        sueltas = fuente.count(".messages.create(") - 1
        assert sueltas == 0, (
            f"{sueltas} llamada(s) a Anthropic saltándose `_claude_crea`")

        # OpenAI entra por dos rutas y las dos mandan un `system`.
        for marca in ("_con_idioma(system_msg)", "sysmsg = _con_idioma("):
            assert marca in fuente, f"la ruta de OpenAI sin idioma: falta {marca}"

    def test_el_idioma_va_al_FINAL_del_prompt(self):
        """Lo último que lee el modelo es lo que manda. El esquema de respuesta
        lleva «en español» escrito en la descripción de un campo, así que la
        instrucción tiene que ir detrás para ganarle."""
        import vertex_api as V

        tok = V._IDIOMA_CTX.set("en")
        try:
            t = V._con_idioma("Responde en español lo que quieras.")
        finally:
            V._IDIOMA_CTX.reset(tok)
        assert t.startswith("Responde en español")
        assert t.rstrip().endswith("stay as they are.")

    def test_el_envoltorio_le_pone_el_idioma_a_lo_que_de_verdad_se_envia(self,
                                                                        monkeypatch):
        """No basta con que la función exista: tiene que llegar al proveedor."""
        import vertex_api as V

        visto = {}

        class _Modelos:
            @staticmethod
            def generate_content(**kw):
                visto.update(kw)
                return "ok"

        monkeypatch.setattr(V, "client_gemini",
                            type("C", (), {"models": _Modelos})())
        tok = V._IDIOMA_CTX.set("en")
        try:
            V._gemini_genera(model="m", contents="Analiza WULF.")
        finally:
            V._IDIOMA_CTX.reset(tok)
        assert "in English" in visto["contents"], visto["contents"][:120]
        assert visto["contents"].startswith("Analiza WULF.")

        class _Mensajes:
            @staticmethod
            def create(**kw):
                visto.clear()
                visto.update(kw)
                return "ok"

        tok = V._IDIOMA_CTX.set("es")
        try:
            V._claude_crea(type("A", (), {"messages": _Mensajes})(),
                           system="Eres un analista.", messages=[])
        finally:
            V._IDIOMA_CTX.reset(tok)
        assert "IDIOMA" in visto["system"] and "español" in visto["system"]

    def test_la_cabecera_manda_y_un_valor_raro_cae_a_español(self, monkeypatch):
        import vertex_api as V

        for cabecera, espera in [("en", "en"), ("es", "es"), ("EN", "en"),
                                 ("klingon", "es"), ("", "es")]:
            tok = V._IDIOMA_CTX.set(cabecera.strip().lower())
            try:
                assert V._idioma_actual() == espera, f"«{cabecera}» → {espera}"
            finally:
                V._IDIOMA_CTX.reset(tok)

    def test_la_instruccion_se_escribe_en_el_idioma_de_destino(self):
        import vertex_api as V

        tok = V._IDIOMA_CTX.set("en")
        try:
            t = V._instruccion_idioma()
        finally:
            V._IDIOMA_CTX.reset(tok)
        assert "in English" in t and "Spanish" in t
        assert "español" not in t, (
            "pedirle en español que responda en inglés funciona peor que pedírselo "
            "en inglés")

    def test_la_ruta_recibe_la_cabecera(self):
        from fastapi.testclient import TestClient

        import vertex_api as V

        c = TestClient(V.app)
        r = c.get("/api/auth/status", headers={"X-Vertex-Idioma": "en"})
        assert r.status_code == 200, r.text


# ── Lo que solo se puede comprobar mirando la pantalla ──────────────────────

def _servidor(puerto, tmp):
    entorno = dict(os.environ,
                   EDGAR_USER_AGENT="Vertex test@example.com",
                   VERTEX_DB=str(tmp / "vertex.db"),
                   VERTEX_ALMACEN=str(tmp / "almacen"))
    entorno.pop("VERTEX_GIT_TOKEN", None)
    p = subprocess.Popen(
        [sys.executable, "-m", "uvicorn", "vertex_api:app",
         "--host", "127.0.0.1", "--port", str(puerto)],
        cwd=str(ROOT), env=entorno,
        stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    import urllib.request
    for _ in range(120):
        try:
            urllib.request.urlopen(f"http://127.0.0.1:{puerto}/", timeout=1)
            return p
        except Exception:                        # noqa: BLE001
            if p.poll() is not None:
                raise RuntimeError("el servidor murió al arrancar")
            time.sleep(0.5)
    p.kill()
    raise RuntimeError("el servidor no respondió")


BARRE = """() => {
    document.querySelectorAll('.view-section').forEach(v => v.classList.remove('hidden'));
    document.querySelectorAll('.hidden').forEach(v => v.classList.remove('hidden'));
    const fuera = [];
    const NO = new Set(['SCRIPT','STYLE','TEXTAREA','CODE','PRE']);
    const it = document.createTreeWalker(document.body, NodeFilter.SHOW_TEXT);
    for (let n = it.nextNode(); n; n = it.nextNode()) {
        const p = n.parentNode;
        if (p && NO.has(p.nodeName)) continue;
        const t = (n.nodeValue || '').trim().replace(/\\s+/g, ' ');
        if (t) fuera.push(t);
    }
    ['placeholder','title','aria-label','alt'].forEach(a => {
        document.querySelectorAll('[' + a + ']').forEach(el => {
            const v = (el.getAttribute(a) || '').trim().replace(/\\s+/g, ' ');
            if (v) fuera.push(v);
        });
    });
    return [...new Set(fuera)];
}"""


@pytest.mark.skipif(not Path(CHROMIUM).exists(), reason="sin Chromium")
class TestEnInglesNoQuedaEspanol:
    """El caso que da nombre al archivo. Se mide, no se supone."""

    @pytest.fixture(scope="class")
    def pagina(self, tmp_path_factory):
        from playwright.sync_api import sync_playwright

        tmp = tmp_path_factory.mktemp("idioma")
        proc = _servidor(8123, tmp)
        pw = sync_playwright().start()
        b = pw.chromium.launch(executable_path=CHROMIUM)
        pg = b.new_page(viewport={"width": 1280, "height": 1200})
        pg.add_init_script("try{localStorage.setItem('vertex_idioma','en')}catch(e){}")
        pg.goto("http://127.0.0.1:8123/", wait_until="domcontentloaded")
        pg.wait_for_timeout(3000)
        yield pg
        b.close()
        pw.stop()
        proc.kill()

    def test_el_armazon_entero_esta_en_ingles(self, pagina):
        resto = sorted({s for s in pagina.evaluate(BARRE) if suena_a_espanol(s)})
        assert not resto, ("quedan " + str(len(resto)) + " textos en español: "
                           + str(resto[:6]))

    def test_el_cuestionario_del_perfil_tambien(self, pagina):
        """Lo sirve el SERVIDOR, en español. Llega traducido igual porque el
        barrido no distingue de dónde vino el texto: solo mira el DOM."""
        pagina.evaluate("""async () => {
            await fetch('/api/auth/registro', {method:'POST',
                headers:{'Content-Type':'application/json'},
                body: JSON.stringify({email:'en@ejemplo.com', nombre:'Ann',
                                      password:'ClaveLarga123!'})});
        }""")
        pagina.reload(wait_until="domcontentloaded")
        pagina.wait_for_timeout(2500)
        pagina.evaluate("switchView('perfilView'); pfCargar();")
        pagina.wait_for_timeout(1800)
        pagina.evaluate("pfTab('perfil'); pfModoSel='personalizado'; pfPintaModo();")
        pagina.wait_for_timeout(900)
        textos = pagina.evaluate("""() => {
            const sel = '#pfPreguntas [data-pregunta] span, #pfPreguntas [data-pregunta] p';
            const hojas = [...document.querySelectorAll(sel)]
                .filter(e => !e.querySelector('span,p'))
                .map(e => (e.innerText || '').trim())
                .filter(Boolean);
            return [...new Set(hojas)];
        }""")
        assert len(textos) > 30, f"el cuestionario no se pintó ({len(textos)} textos)"
        resto = [t for t in textos if ACENTO.search(t)]
        assert not resto, f"preguntas todavía en español: {resto[:5]}"


@pytest.mark.skipif(not Path(CHROMIUM).exists(), reason="sin Chromium")
class TestElInterruptorVaYVuelve:
    """Traducir es fácil; DESTRADUCIR es donde se rompe."""

    def test_al_volver_a_espanol_no_queda_ingles(self, tmp_path):
        from playwright.sync_api import sync_playwright

        proc = _servidor(8124, tmp_path)
        try:
            with sync_playwright() as pw:
                b = pw.chromium.launch(executable_path=CHROMIUM)
                pg = b.new_page(viewport={"width": 1280, "height": 900})
                errores = []
                pg.on("pageerror", lambda e: errores.append(str(e)))
                pg.goto("http://127.0.0.1:8124/", wait_until="domcontentloaded")
                pg.wait_for_timeout(3000)

                def texto():
                    return pg.evaluate("""() => {
                        document.querySelectorAll('.view-section')
                            .forEach(v => v.classList.remove('hidden'));
                        return document.body.innerText;
                    }""")

                # Se mide con la pantalla de entrada, que se ve SIN sesión: es
                # lo único que un test puede dar por presente siempre.
                ES = "Entra a tu cuenta de Vertex Fund OS"
                EN = "Sign in to your Vertex Fund OS account"
                assert ES in texto()
                pg.evaluate("vxIdioma('en')")
                pg.wait_for_timeout(700)
                en = texto()
                assert EN in en
                assert ES not in en

                pg.evaluate("vxIdioma('es')")
                pg.wait_for_timeout(700)
                es = texto()
                assert ES in es, "no se deshizo la traducción"
                assert EN not in es, (
                    "quedó inglés colado al volver: la guarda de «ya está hecho» "
                    "no puede aplicarse también a la vuelta")
                assert not errores, errores[:3]
                b.close()
        finally:
            proc.kill()
