#!/usr/bin/env python3
"""Reproduce el despliegue de Render, paso por paso, y dice DÓNDE falla.

    python engine/scripts/preflight_render.py            # sobre el árbol actual
    python engine/scripts/preflight_render.py --remoto   # clon limpio de origin/main

Existe porque «Deploy failed» en el panel de Render no dice nada por sí solo, y
el ciclo para averiguarlo es de minutos: cambias algo, empujas, esperas, vuelve
a fallar. Aquí las cinco cosas que Render hace —y en el orden en que las hace—
corren en local y cada una dice si pasa o no:

  1. El Blueprint es YAML válido y declara build, start y health check.
  2. El repositorio no tiene nada que rompa un checkout en Linux.
  3. `pip install -r requirements.txt` resuelve.
  4. `import vertex_api` funciona en un proceso limpio — CON y SIN las
     variables de entorno de Render.
  5. `uvicorn vertex_api:app` arranca y BINDEA el puerto.
  6. El health check (`GET /`) responde 200 con el panel dentro, y las rutas
     del tab degradan en vez de reventar cuando faltan las claves.

El paso 4 existe por un despliegue caído de verdad: el arranque moría con
`EDGAR_USER_AGENT` DEFINIDA —una configuración correcta— y en local no se veía
porque nadie define esa variable para desarrollar. La ausencia de configuración
salvaba el arranque, que es exactamente al revés de lo que se asume al probar.

Con `--remoto` clona `origin/main` en un temporal y corre lo mismo ahí: es la
diferencia entre «funciona en mi árbol» y «funciona lo que Render va a bajar»,
que ya nos separó una vez —el `main` LOCAL estaba 15 commits por detrás del
remoto y el clon salía con un HTML de 145 KB menos—.

No necesita claves: sin ellas las rutas deben degradar, y comprobar eso es
justamente el punto. Lo que NO comprueba es que Massive y MarketSnack
respondan: eso es `preflight_vivo.py`, que sí las necesita.
"""
from __future__ import annotations

import argparse
import json
import os
import re
import shutil
import signal
import subprocess
import sys
import tempfile
import time
import urllib.error
import urllib.request
from pathlib import Path

RAIZ = Path(__file__).resolve().parents[2]
V, X, W = "\033[32m✓\033[0m", "\033[31m✗\033[0m", "\033[33m!\033[0m"
_fallos = 0


def chk(ok: bool, msg: str, aviso: bool = False) -> bool:
    global _fallos
    if ok:
        print(f"  {V} {msg}")
    elif aviso:
        print(f"  {W} {msg}")
    else:
        print(f"  {X} {msg}")
        _fallos += 1
    return ok


def sec(t: str) -> None:
    print(f"\n\033[1m── {t} {'─' * max(2, 58 - len(t))}\033[0m")


# ── 1 · El Blueprint ─────────────────────────────────────────────────────────
def paso_blueprint(raiz: Path) -> dict:
    sec("1. El Blueprint que Render lee")
    f = raiz / "render.yaml"
    if not chk(f.is_file(), "existe render.yaml"):
        return {}
    try:
        import yaml
        d = yaml.safe_load(f.read_text(encoding="utf-8"))
    except Exception as e:                            # noqa: BLE001
        chk(False, f"render.yaml no es YAML válido: {e}")
        return {}
    svc = (d.get("services") or [{}])[0]
    chk(bool(svc.get("buildCommand")), f"buildCommand: {svc.get('buildCommand')}")
    chk(bool(svc.get("startCommand")), f"startCommand: {svc.get('startCommand')}")
    chk(svc.get("healthCheckPath") == "/", f"healthCheckPath: {svc.get('healthCheckPath')}")
    # La versión de Python se declara en DOS sitios y tienen que coincidir: si
    # divergen, Render usa una y `runtime.txt` promete otra.
    env = {v.get("key"): v.get("value") for v in (svc.get("envVars") or [])}
    rt = (raiz / "runtime.txt")
    pin = rt.read_text(encoding="utf-8").strip() if rt.is_file() else ""
    chk(not pin or not env.get("PYTHON_VERSION")
        or pin.replace("python-", "") == env["PYTHON_VERSION"],
        f"la versión de Python es una sola: runtime.txt={pin or '—'} · "
        f"PYTHON_VERSION={env.get('PYTHON_VERSION') or '—'}")
    return svc


# ── 2 · El checkout ──────────────────────────────────────────────────────────
def paso_checkout(raiz: Path) -> None:
    sec("2. Lo que Render va a bajar")
    # `-z` y no el listado normal: sin él, git ENTRECOMILLA y escapa los
    # nombres no-ASCII (`"Referencias/Screenshot …\342\200\257PM.png"`), y el
    # detector acaba denunciando las comillas que puso el propio git en vez del
    # carácter que las provocó. Con `-z` sale el nombre tal cual.
    r = subprocess.run(["git", "ls-files", "-z"], cwd=raiz,
                       capture_output=True, text=True)
    files = [x for x in r.stdout.split("\0") if x]
    chk(bool(files), f"{len(files)} archivos rastreados")
    # Render construye en Linux. Dos archivos que solo se diferencien en
    # mayúsculas conviven aquí y colisionan en cualquier checkout insensible.
    vistos: dict[str, list[str]] = {}
    for x in files:
        vistos.setdefault(x.lower(), []).append(x)
    dup = [v for v in vistos.values() if len(v) > 1]
    chk(not dup, f"ningún par de rutas colisiona por mayúsculas{': ' + str(dup[:3]) if dup else ''}")
    malos = [x for x in files if any(c in x for c in ':*?"<>|\\')]
    chk(not malos, f"ninguna ruta lleva caracteres inválidos{': ' + str(malos[:3]) if malos else ''}")
    # Espacios y controles INVISIBLES. El caso real: una captura de macOS
    # llegó con U+202F (NARROW NO-BREAK SPACE) entre la hora y el «PM». Se ve
    # igual que un espacio normal en cualquier pantalla, así que no se puede
    # encontrar mirando; y es de los caracteres que se transcodifican distinto
    # según la herramienta que haga el checkout.
    import unicodedata as _u
    raros = [(x, [f"U+{ord(c):04X} {_u.name(c, '?')}" for c in x
                  if ord(c) > 126 and (_u.category(c) in ("Zs", "Cf", "Cc")
                                       or ord(c) in (0x00A0, 0x202F, 0xFEFF))])
             for x in files]
    raros = [(x, cs) for x, cs in raros if cs]
    chk(not raros,
        "ninguna ruta lleva espacios ni controles invisibles"
        + (f" · {raros[0][0]!r} → {raros[0][1]}" if raros else ""))
    # Un Dockerfile o un package.json cambian el runtime que Render detecta.
    intrusos = [n for n in ("Dockerfile", "package.json", ".tool-versions",
                            "go.mod", "Gemfile") if (raiz / n).exists()]
    chk(not intrusos, f"nada que le cambie el runtime a Render{': ' + str(intrusos) if intrusos else ''}")
    # El HTML del panel tiene que estar EN el commit, no solo en el disco.
    html = raiz / "vertex_fund_os_platform.html"
    chk(html.is_file() and "vertex_fund_os_platform.html" in files,
        f"el panel viaja en el commit ({html.stat().st_size:,} bytes)"
        if html.is_file() else "FALTA el panel en el commit")


# ── 3 · Las dependencias ─────────────────────────────────────────────────────
def paso_deps(raiz: Path, instalar: bool) -> None:
    sec("3. pip install -r requirements.txt")
    req = raiz / "requirements.txt"
    if not chk(req.is_file(), "existe requirements.txt"):
        return
    lineas = [x.strip() for x in req.read_text(encoding="utf-8").split("\n")
              if x.strip() and not x.strip().startswith("#")]
    sueltas = [x for x in lineas if not re.search(r"[=<>~!]", x)]
    chk(not sueltas,
        f"ninguna dependencia sin versión{': ' + str(sueltas) if sueltas else ''}")
    # Sin techo de MAJOR, dos despliegues del MISMO commit pueden instalar
    # cosas distintas — que es exactamente la forma que tiene un build de
    # «funcionar ayer y fallar hoy» sin que nadie haya tocado nada.
    sin_techo = [x for x in lineas if not re.search(r"[<=]=?\s*\d", x.split(",")[-1])]
    chk(not sin_techo,
        f"todas llevan techo de versión{': SIN TECHO ' + str(sin_techo) if sin_techo else ''}")
    if not instalar:
        print("      (usa --instalar para resolverlas de verdad en un venv nuevo)")
        return
    tmp = Path(tempfile.mkdtemp(prefix="preflight-venv-"))
    try:
        subprocess.run([sys.executable, "-m", "venv", str(tmp)], check=True,
                       capture_output=True)
        pip = tmp / "bin" / "pip"
        r = subprocess.run([str(pip), "install", "-q", "-r", str(req)],
                           capture_output=True, text=True, timeout=1800)
        chk(r.returncode == 0,
            "resuelve en un venv limpio" if r.returncode == 0
            else f"pip FALLÓ:\n{r.stdout[-1500:]}{r.stderr[-1500:]}")
        if r.returncode == 0:
            mb = sum(f.stat().st_size for f in tmp.rglob("*") if f.is_file()) / 1e6
            chk(mb < 900, f"peso instalado {mb:,.0f} MB", aviso=mb >= 900)
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


# ── 4 y 5 · El arranque y el health check ────────────────────────────────────
#: Lo que Render tiene puesto y una máquina de desarrollo no.
#:
#: Esta lista es la lección del despliegue caído: el arranque moría con
#: `EDGAR_USER_AGENT` DEFINIDA, y aquí no se veía porque nadie define esa
#: variable para desarrollar. O sea que una configuración CORRECTA rompía el
#: servicio y la falta de configuración lo salvaba — al revés de lo que todo el
#: mundo asume al probar. Cada variable es una rama de código que en local no
#: se ejecuta nunca; el preflight las enciende todas.
ENTORNO_RENDER = {
    "EDGAR_USER_AGENT": "Vertex Fund OS preflight@ejemplo.com",
    "VERTEX_API_TOKEN": "token-de-preflight-largo",
    "VERTEX_ORIGIN": "https://ejemplo.onrender.com",
    "VERTEX_DB_KEY": "clave-de-preflight-para-fernet-0123456789",
    "VERTEX_GIT_TOKEN": "ghp_tokenDePreflightQueNoSirve",
    "MASSIVE_MAX_PAGES": "40",
    "PLAID_ENV": "sandbox",
    "JUDGE_MODEL": "claude-sonnet-4",
}


def paso_import(raiz: Path, py: str) -> None:
    """`import vertex_api` en un proceso NUEVO, con y sin configuración.

    Va antes que uvicorn porque es donde de verdad se cae: uvicorn no hace más
    que importar el módulo, y si eso lanza, el proceso sale con código 1 y
    Render dice «Exited with status 1 while running your code» sin más pista.
    """
    sec("4. import vertex_api — en un proceso limpio")
    base = {"PATH": os.environ.get("PATH", "/usr/bin:/bin"),
            "HOME": os.environ.get("HOME", "/tmp")}
    for nombre, extra in (("sin ninguna variable", {}),
                          ("con el entorno de Render", ENTORNO_RENDER)):
        env = dict(base)
        env.update(extra)
        r = subprocess.run([py, "-c", "import vertex_api"], cwd=str(raiz),
                           env=env, capture_output=True, text=True, timeout=300)
        ok = chk(r.returncode == 0, f"importa {nombre}")
        if not ok:
            print("      " + (r.stdout + r.stderr).strip()[-900:].replace("\n", "\n      "))


def paso_arranque(raiz: Path, py: str) -> None:
    sec("5. uvicorn vertex_api:app — arranque y health check")
    puerto = 8791
    env = dict(os.environ)
    env.update(ENTORNO_RENDER)
    # Render arranca SIN las claves de datos la primera vez. Que las rutas
    # degraden en vez de reventar es parte del contrato, no un detalle.
    for k in ("MASSIVE_API_KEY", "MARKETSNACK_COOKIE"):
        env.pop(k, None)
    p = subprocess.Popen([py, "-m", "uvicorn", "vertex_api:app",
                          "--host", "127.0.0.1", "--port", str(puerto)],
                         cwd=str(raiz), env=env, text=True,
                         stdout=subprocess.PIPE, stderr=subprocess.STDOUT)
    t0, listo = time.time(), None
    try:
        for _ in range(60):
            time.sleep(2)
            if p.poll() is not None:
                chk(False, f"el proceso murió con código {p.returncode}")
                print((p.stdout.read() or "")[-3000:])
                return
            try:
                with urllib.request.urlopen(f"http://127.0.0.1:{puerto}/", timeout=5) as r:
                    cuerpo = r.read().decode("utf-8", "replace")
                listo = time.time() - t0
                break
            except Exception:                          # noqa: BLE001, PERF203
                continue
        if listo is None:
            chk(False, "nunca respondió en 120 s — eso es un health check fallido")
            return
        chk(True, f"health check OK a los {listo:.1f} s")
        chk(len(cuerpo) > 200_000, f"el panel llega entero ({len(cuerpo):,} bytes)")
        for fn in ("renderProjTape", "renderProjWheel", "renderProjIdeas",
                   "renderVictorTargets"):
            chk(fn in cuerpo, f"…con {fn} dentro")

        sec("6. Las rutas del tab, sin claves de datos")
        # Con `VERTEX_API_TOKEN` definida —y en Render lo está— la API EXIGE
        # credencial: un 401 aquí es la seguridad C-02 funcionando, no un
        # fallo. Se manda el token, que es lo que hace el navegador con su
        # cookie de sesión; sin él este paso mediría la puerta, no las rutas.
        cab = {"X-Vertex-Token": env.get("VERTEX_API_TOKEN", "")}
        for ruta in ("/api/projection-targets?ticker=DEMO", "/api/tito-ideas",
                     "/api/tito-tape?ticker=DEMO", "/api/tito-wheel",
                     "/api/almacen"):
            try:
                pet = urllib.request.Request(f"http://127.0.0.1:{puerto}{ruta}",
                                             headers=cab)
                with urllib.request.urlopen(pet, timeout=30) as r:
                    d = json.loads(r.read())
                    cod = r.status
            except urllib.error.HTTPError as e:
                cod, d = e.code, {}
            except Exception as e:                     # noqa: BLE001
                chk(False, f"{ruta} → excepción {e!r}")
                continue
            ok = d.get("ok")
            chk(cod == 200,
                f"{ruta} → {cod}"
                + ("" if ok is not False else f"  · degrada: {str(d.get('error'))[:52]}"))
            if ok is False:
                chk(bool(d.get("error")), f"{ruta} dice POR QUÉ degrada")
    finally:
        p.send_signal(signal.SIGINT)
        try:
            p.wait(timeout=10)
        except subprocess.TimeoutExpired:
            p.kill()


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--remoto", action="store_true",
                    help="clona origin/main en un temporal y corre ahí")
    ap.add_argument("--instalar", action="store_true",
                    help="resuelve requirements.txt de verdad (tarda minutos)")
    a = ap.parse_args()

    raiz, tmp = RAIZ, None
    if a.remoto:
        url = subprocess.run(["git", "remote", "get-url", "origin"], cwd=RAIZ,
                             capture_output=True, text=True).stdout.strip()
        tmp = Path(tempfile.mkdtemp(prefix="preflight-render-"))
        raiz = tmp / "repo"
        print(f"clonando {url} (rama main) …")
        r = subprocess.run(["git", "clone", "-q", "--depth", "1", "--branch", "main",
                            url, str(raiz)], capture_output=True, text=True)
        if r.returncode != 0:
            print(f"{X} no se pudo clonar: {r.stderr[:400]}")
            return 1
        sha = subprocess.run(["git", "log", "--oneline", "-1"], cwd=raiz,
                             capture_output=True, text=True).stdout.strip()
        print(f"    origin/main = {sha}")

    print(f"\n\033[1mPREFLIGHT DE RENDER\033[0m · {raiz}")
    try:
        paso_blueprint(raiz)
        paso_checkout(raiz)
        paso_deps(raiz, a.instalar)
        paso_import(raiz, sys.executable)
        paso_arranque(raiz, sys.executable)
    finally:
        if tmp:
            shutil.rmtree(tmp, ignore_errors=True)

    print("\n" + "=" * 62)
    if _fallos:
        print(f"  \033[31m{_fallos} FALLO(S)\033[0m — eso es lo que Render está viendo.")
    else:
        print("  \033[32mTodo verde.\033[0m Si Render sigue diciendo «failed», el "
              "motivo NO está\n  en el código: mira el log del panel — build "
              "minutes agotados, la\n  rama del servicio, o una variable de entorno "
              "obligatoria sin valor.")
    return 1 if _fallos else 0


if __name__ == "__main__":
    raise SystemExit(main())
