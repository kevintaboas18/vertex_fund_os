"""El almacén: los datos del agente viven en ARCHIVOS y sobreviven al servidor.

El problema que resuelve
------------------------
Render en plan `free` **no tiene disco persistente**. Cada redeploy y cada
despertar tras dormir por inactividad borra el sistema de archivos entero. Da
igual el formato: un `.json` se borra exactamente igual que un `.db`. El
problema nunca fue SQLite contra archivos — era *dónde vive el archivo*.

Víctor no tiene este problema porque **no despliega**: su `web/data/` está en su
`.gitignore` y vive solo en su computadora, y sus scripts son `next dev` /
`next start`. Copiar su enfoque al pie de la letra significaría correr el agente
en la máquina de Kevin, y eso mataría el multiusuario.

La solución
-----------
El almacén es un **clon del propio repositorio en una rama aparte** (`datos`).
Todo lo que el agente guarda cae ahí como archivo normal, y un hilo de fondo
hace `commit` y `push`. Al arrancar, un contenedor nuevo clona esa rama y
recupera todo lo que había.

Consecuencias, que son justo lo que se pedía:

- **Nada se pierde**: aunque Render borre el disco, o borres el servicio entero,
  los datos siguen en GitHub.
- **Son archivos que se abren**: JSON y Markdown, legibles en GitHub o
  descargables como carpeta.
- **No dependes de Render**: mañana mueves el servicio a Fly, a Railway o a tu
  casa y los datos ya están ahí; solo hace falta la misma variable de entorno.
- **Historial gratis**: `git log` dice qué cambió cada día.

Por qué una RAMA aparte y no `main`
-----------------------------------
Porque el agente commitea decenas de veces al día. Mezclarlo con el código
haría ilegible el historial de `main` y convertiría cada análisis en un
despliegue nuevo en Render (que reconstruye desde `main`). La rama `datos` es
**huérfana**: no comparte historia con `main` y nunca dispara un build.

Qué NO sube, y por qué
----------------------
- Las claves de API (`API/`) — nunca, por regla del proyecto.
- Los hashes de contraseña y el token de Plaid van **cifrados** (`Privado/`,
  Fernet con `VERTEX_DB_KEY`). Sin esa clave, este módulo **se niega a
  subirlos**: un hash en claro en un repo, aunque sea privado, es un objetivo
  de fuerza bruta offline, y eso no se hace en silencio.

Degradación
-----------
Sin `VERTEX_GIT_TOKEN` el almacén **sigue funcionando** como directorio local:
se escribe todo igual y no se sube nada. El agente no se entera; lo dice
`/api/almacen` y lo dice el panel. Es el modo en el que corren los tests y el
desarrollo en la máquina de Kevin.
"""

from __future__ import annotations

import json
import logging
import os
import re
import shutil
import subprocess
import threading
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

#: El log del almacén. Existe porque en Render el log es lo ÚNICO que se ve sin
#: abrir el panel, y hay avisos aquí —un respaldo frenado, un push rechazado—
#: que solo se leían desde `/api/almacen`. Guardados nada más que en memoria no
#: se los cuentan a nadie hasta que ya da igual.
log = logging.getLogger(__name__)

__all__ = [
    "Almacen",
    "almacen",
    "RAMA_POR_DEFECTO",
    "DIR_ACCIONES",
    "DIR_OPCIONES",
    "DIR_MEMORIA",
    "DIR_PERFILES",
    "DIR_PRIVADO",
    "DIR_SERIES",
]

#: La rama huérfana donde viven los datos. No comparte historia con `main`, así
#: que un `push` aquí **no** dispara un despliegue en Render.
RAMA_POR_DEFECTO = "datos"

# ── Las carpetas. Cada agente tiene la SUYA, a propósito ────────────────────
#
# Kevin lo pidió explícito: «cada uno guarda reportes diferente y en lugar
# diferente». No es capricho — son dos productos distintos:
#
#   · el de ACCIONES produce una tesis de inversión a 1-3 años, con 6
#     especialistas, valuación y gates de perfil;
#   · el de OPCIONES produce un scorecard de flujo 0-100 con 6 sub-agentes y
#     escenarios a 10/20/30 días.
#
# Meterlos en la misma carpeta obligaría a mirar dentro del archivo para saber
# de cuál es. Separados, la ruta ya lo dice.

#: Agente de ACCIONES (Analyze / Explore). Es la ruta que `CLAUDE.md` ya define
#: y de la que come `wbj track`: NO se mueve.
DIR_ACCIONES = "Reportes"

#: Agente de OPCIONES (Proyecciones). Carpeta nueva y separada.
DIR_OPCIONES = "Proyecciones"

DIR_MEMORIA = "Memoria"
DIR_PERFILES = "Perfiles"
#: Lo cifrado. Nunca contiene texto plano.
DIR_PRIVADO = "Privado"
#: Las series de mercado que acumula el motor de Víctor.
DIR_SERIES = "Series"

#: Cada cuánto se agrupan los cambios normales (reportes, memoria, perfiles).
#: Corto para que una caída del contenedor pierda poco; no tan corto como para
#: hacer un commit por cada escritura.
SEGUNDOS_RAPIDO = int(os.environ.get("VERTEX_ALMACEN_SEG", "20") or 20)

#: Cada cuánto entran ADEMÁS las series de mercado (`Series/`).
#:
#: Van a otro ritmo porque cambian en CADA consulta y pesan: el archivo de
#: trades de un ticker llega a 1,7 MB (5.000 filas, el tope de `store.ts`), y
#: reescribirlo entero 20 veces al día metería ~34 MB diarios de objetos en el
#: repo. Una foto cada pocas horas conserva lo que importa —el sub-agente 6, el
#: IV Rank y la calibración necesitan DÍAS de historia, no minutos— sin inflar
#: nada.
SEGUNDOS_LENTO = int(os.environ.get("VERTEX_ALMACEN_SEG_SERIES", "21600") or 21600)

#: Reintentos del `push` ante un fallo de red, con espera creciente. El commit
#: local ya está hecho, así que un push fallido no pierde nada mientras el
#: proceso viva — pero el proceso puede morir, así que se reintenta pronto.
REINTENTOS_PUSH = 4

#: Aviso cuando el almacén crece de más. GitHub empieza a quejarse cerca de
#: 1 GB por repo; esto avisa mucho antes para que dé tiempo a reaccionar.
AVISO_MB = int(os.environ.get("VERTEX_ALMACEN_AVISO_MB", "400") or 400)


def _ahora() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _sin_secretos(texto: str) -> str:
    """Borra el token de cualquier texto ANTES de que llegue a un log.

    Git mete la URL del remoto en casi todos sus mensajes de error, y esa URL
    lleva el token incrustado. Sin esta limpieza, un fallo de red rutinario
    escribiría la credencial en los logs de Render, que son visibles en el
    dashboard y quedan guardados. Es el mismo criterio del resto del proyecto:
    una credencial no aparece en un output, nunca.
    """
    texto = re.sub(r"https://[^@\s]*@", "https://***@", texto)
    for var in ("VERTEX_GIT_TOKEN", "GITHUB_TOKEN", "GH_TOKEN"):
        v = os.environ.get(var, "").strip()
        if v and len(v) > 6:
            texto = texto.replace(v, "***")
    return texto



def _lados_del_conflicto(texto: str) -> list[str]:
    """Los dos textos que git dejó pegados, cada uno por su cuenta.

    Un conflicto anidado deja varias capas de marcas; se quitan todas las
    líneas de marca y se devuelven las dos lecturas posibles —quedarse con lo
    de arriba de cada `=======` o con lo de abajo—, que es lo que git habría
    escrito si alguien hubiera resuelto a mano.
    """
    arriba, abajo, cual = [], [], "arriba"
    for linea in texto.splitlines():
        if linea.startswith("<<<<<<< "):
            cual = "arriba"
        elif linea.startswith("======="):
            cual = "abajo"
        elif linea.startswith(">>>>>>> "):
            cual = "arriba"
        elif cual == "arriba":
            arriba.append(linea)
        else:
            abajo.append(linea)
    return ["\n".join(arriba), "\n".join(abajo)]

class Almacen:
    """Un directorio que es un clon de la rama de datos y se respalda solo."""

    def __init__(self, raiz: str | os.PathLike | None = None,
                 remoto: str = "", rama: str = "", token: str = "") -> None:
        self.raiz = Path(raiz or os.environ.get("VERTEX_ALMACEN", "")
                         or (Path.cwd() / "almacen")).resolve()
        self.rama = rama or os.environ.get("VERTEX_ALMACEN_RAMA", "") or RAMA_POR_DEFECTO
        self._token = token or os.environ.get("VERTEX_GIT_TOKEN", "").strip()
        self._remoto_crudo = remoto or os.environ.get("VERTEX_ALMACEN_REMOTO", "").strip()

        self._lock = threading.RLock()
        self._hilo: threading.Thread | None = None
        self._parar = threading.Event()
        self._ultimo_lento = 0.0
        #: Lo que hay que preparar JUSTO ANTES de commitear. Existe por lo
        #: cifrado: el paquete de cuentas y perfiles no se escribe cuando
        #: alguien crea una cuenta —se regenera aquí, en el mismo instante en
        #: que se va a subir—, así nunca se sube una foto desfasada ni hay que
        #: acordarse de llamar al respaldo desde cada sitio que toca la base.
        self.antes_de_sincronizar: list = []
        self._estado: dict[str, Any] = {
            "activo": False, "motivo": "sin arrancar", "ultimo_push": None,
            "ultimo_error": None, "commits": 0, "restaurado": False,
        }

    # ── Configuración ───────────────────────────────────────────────────

    @property
    def respalda(self) -> bool:
        """¿Hay con qué subir? Sin token el almacén es solo un directorio."""
        return bool(self._token and self._url())

    def _url(self) -> str:
        """La URL con el token incrustado, o '' si no se puede armar.

        Tres fuentes, en orden de fiabilidad:

        1. `VERTEX_ALMACEN_REMOTO`, si el operador la declara;
        2. **`RENDER_GIT_REPO_SLUG`**, que Render pone en el entorno del
           servicio (`owner/repo`);
        3. el `origin` del propio repo de código, con `git remote get-url`.

        La 2 existe porque la 3 no vale en Render y ese era el fallo: se
        respaldaba en local y NO en producción, teniendo el token puesto. El
        directorio desplegado puede no traer `.git` —Render exporta el árbol,
        no siempre el repositorio—, `git` puede no estar en el PATH del
        proceso, y el `origin` puede venir en forma SSH (`git@github.com:…`),
        que la función descartaba por no empezar por `https://`. Cualquiera de
        los tres casos dejaba la URL vacía, `respalda` en `False`, y el aviso
        genérico de «no se pudo deducir el repositorio» junto a un token
        perfectamente válido.

        Con la variable de Render no hace falta `git` ni `.git` para nada: el
        propio servicio dice de qué repositorio salió.
        """
        base = (self._remoto_crudo or self._repo_de_render()
                or self._origin_del_codigo())
        if not base:
            return ""
        if not self._token:
            return base
        if "@" in base.split("//", 1)[-1].split("/", 1)[0]:
            return base                                # ya trae credenciales
        return base.replace("https://", f"https://x-access-token:{self._token}@", 1)

    @staticmethod
    def _repo_de_render() -> str:
        """El repositorio, tal como lo declara el propio Render.

        `RENDER_GIT_REPO_SLUG` vale `owner/repo` y está en el entorno de todo
        servicio desplegado desde git. No necesita `git`, ni `.git`, ni que el
        proceso pueda ejecutar subprocesos: es la fuente que no se puede caer.
        """
        slug = (os.environ.get("RENDER_GIT_REPO_SLUG", "") or "").strip().strip("/")
        # Dos segmentos y nada raro: no se construye una URL con lo que venga.
        if not re.fullmatch(r"[A-Za-z0-9._-]+/[A-Za-z0-9._-]+", slug):
            return ""
        return f"https://github.com/{slug}.git"

    @staticmethod
    def _origin_del_codigo() -> str:
        try:
            r = subprocess.run(["git", "remote", "get-url", "origin"],
                               cwd=str(Path(__file__).resolve().parent),
                               capture_output=True, text=True, timeout=10)
            url = (r.stdout or "").strip()
        except Exception:
            return ""
        # La forma SSH es la del `origin` de media máquina de desarrollo y de
        # varios despliegues, y se descartaba entera por no empezar por
        # `https://`. Es el MISMO repositorio escrito de otra manera: se
        # traduce en vez de tirarla.
        m = re.fullmatch(r"git@([^:]+):(.+?)(?:\.git)?/?", url)
        if m:
            return f"https://{m.group(1)}/{m.group(2)}.git"
        if not url.startswith("https://"):
            return ""
        # Se le quitan las credenciales que traiga: las pone `_url` a partir de
        # `VERTEX_GIT_TOKEN`, que es la que el operador controla.
        return re.sub(r"https://[^@/]*@", "https://", url)

    # ── Arranque ────────────────────────────────────────────────────────

    def _foto(self) -> dict[str, Any]:
        """El estado con `respalda` incluido.

        `_estado` no lo guarda porque no es estado: se deduce del token y del
        remoto en cada consulta. Pero quien recibe el dict necesita saberlo, y
        cuando faltaba, el arranque leía `None` y avisaba de «SIN RESPALDO»
        justo cuando el respaldo estaba funcionando. Un diagnóstico al revés es
        peor que no tenerlo.
        """
        return {**self._estado, "respalda": self.respalda}

    def restaura(self, timeout: float = 120.0) -> dict[str, Any]:
        """Trae lo que había. Es lo PRIMERO que corre en un contenedor nuevo.

        Tres caminos:
          1. no hay con qué subir → se crea el directorio y punto (modo local);
          2. el directorio ya es un clon → `fetch` + `reset --hard`;
          3. no existe → `clone` de la rama; si la rama aún no existe en el
             remoto, se crea huérfana en local y nacerá con el primer push.

        `reset --hard` y no `pull`: el servidor NUNCA edita a mano estos
        archivos, así que un conflicto solo puede significar que el disco
        efímero tenía basura a medio escribir. Manda el remoto.
        """
        self.raiz.mkdir(parents=True, exist_ok=True)
        if not self.respalda:
            self._estado.update(activo=False, restaurado=False,
                                motivo=self._motivo_apagado())
            return self._foto()

        try:
            if (self.raiz / ".git").is_dir():
                self._git("remote", "set-url", "origin", self._url(), timeout=timeout)
                self._git("fetch", "--depth", "1", "origin", self.rama, timeout=timeout)
                self._git("reset", "--hard", f"origin/{self.rama}", timeout=timeout)
                self._git("clean", "-fd", timeout=timeout)
            else:
                tmp = self.raiz.parent / (self.raiz.name + ".clon")
                shutil.rmtree(tmp, ignore_errors=True)
                hecho = subprocess.run(
                    ["git", "clone", "--depth", "1", "--branch", self.rama,
                     self._url(), str(tmp)],
                    capture_output=True, text=True, timeout=timeout)
                if hecho.returncode == 0:
                    # Lo que ya hubiera en el directorio (un contenedor que
                    # escribió antes de restaurar) se conserva: se mueve el
                    # `.git` clonado encima en vez de borrar la carpeta.
                    shutil.move(str(tmp / ".git"), str(self.raiz / ".git"))
                    for hijo in tmp.iterdir():
                        destino = self.raiz / hijo.name
                        if not destino.exists():
                            shutil.move(str(hijo), str(destino))
                    shutil.rmtree(tmp, ignore_errors=True)
                    self._git("checkout", "--", ".", timeout=timeout, tolera=True)
                else:
                    # La rama todavía no existe en el remoto: primer arranque.
                    shutil.rmtree(tmp, ignore_errors=True)
                    self._git("init", "-q", timeout=timeout)
                    self._git("checkout", "-q", "--orphan", self.rama, timeout=timeout)
                    self._git("remote", "add", "origin", self._url(),
                              timeout=timeout, tolera=True)
            self._identidad()
            self._gitignore()
            # Lo que llega del remoto puede venir roto: durante el atasco se
            # commitearon 27 series con marcas de conflicto dentro del JSON. Se
            # limpian AQUÍ, nada más traerlas, para que ningún sub-agente se
            # encuentre un archivo ilegible.
            reparados = self._repara_marcas()
            self._estado.update(activo=True, restaurado=True, motivo="",
                                ultimo_error=None)
            if reparados:
                self._estado["motivo"] = (
                    f"{reparados} archivo(s) venían con marcas de conflicto "
                    "dentro y se repararon al restaurar.")
        except Exception as e:
            # Un fallo aquí NO puede tumbar el arranque del servidor: se sigue
            # en modo local y se dice por qué. Perder el respaldo es grave;
            # quedarse sin servicio, más.
            self._estado.update(activo=False, restaurado=False,
                                motivo=f"no se pudo restaurar: {_sin_secretos(str(e))[:200]}")
        return self._foto()

    def _identidad(self) -> None:
        self._git("config", "user.email", "agente@vertexfund.os", tolera=True)
        self._git("config", "user.name", "Vertex Fund OS", tolera=True)

    def _gitignore(self) -> None:
        """El `.gitignore` DEL ALMACÉN. Existe para que nada temporal ni ningún
        secreto en claro entre por accidente."""
        p = self.raiz / ".gitignore"
        contenido = (
            "# Generado por vertex_almacen.py — no editar a mano.\n"
            "#\n"
            "# Lo temporal de una escritura atómica a medias.\n"
            "*.tmp\n"
            "*.json.lock\n"
            "__pycache__/\n"
            "\n"
            "# Los secretos SOLO viajan cifrados, en Privado/*.enc. Cualquier\n"
            "# otra cosa dentro de Privado/ se queda en el disco efímero: es la\n"
            "# red de seguridad por si algún día alguien escribe ahí en claro.\n"
            f"{DIR_PRIVADO}/*\n"
            f"!{DIR_PRIVADO}/*.enc\n"
        )
        if not p.exists() or p.read_text(encoding="utf-8") != contenido:
            p.write_text(contenido, encoding="utf-8")

    # ── Escritura y lectura ─────────────────────────────────────────────

    def ruta(self, *partes: str) -> Path:
        """Una ruta DENTRO del almacén, verificada.

        La comprobación no es paranoia de manual: los reportes se guardan por
        ticker, y el ticker viene de la caja de texto del usuario. Sin este
        cierre, un `../../` escribiría fuera del almacén — y ahí ya no se
        respalda ni se restaura, así que el dato se perdería en silencio, que
        es justo lo contrario de lo que este módulo existe para hacer.
        """
        destino = (self.raiz / Path(*partes)).resolve()
        if destino != self.raiz and self.raiz not in destino.parents:
            raise ValueError(f"ruta fuera del almacén: {'/'.join(partes)}")
        return destino

    def guarda(self, ruta_rel: str, contenido: str | bytes | dict | list) -> Path:
        """Escribe un archivo y lo deja listo para el próximo respaldo.

        La escritura es atómica (temporal + `os.replace`): un proceso muerto a
        media escritura no puede dejar un JSON truncado que luego se lea como
        «no hay datos» — que es exactamente cómo se pierde un track record sin
        que nadie se entere.
        """
        destino = self.ruta(ruta_rel)
        destino.parent.mkdir(parents=True, exist_ok=True)
        if isinstance(contenido, (dict, list)):
            datos = json.dumps(contenido, ensure_ascii=False, indent=2).encode("utf-8")
        elif isinstance(contenido, str):
            datos = contenido.encode("utf-8")
        else:
            datos = contenido
        tmp = destino.with_suffix(destino.suffix + ".tmp")
        with open(tmp, "wb") as fh:
            fh.write(datos)
            fh.flush()
            os.fsync(fh.fileno())
        os.replace(tmp, destino)
        return destino

    def lee(self, ruta_rel: str) -> bytes | None:
        try:
            return self.ruta(ruta_rel).read_bytes()
        except (OSError, ValueError):
            return None

    def lee_json(self, ruta_rel: str) -> Any:
        crudo = self.lee(ruta_rel)
        if crudo is None:
            return None
        try:
            return json.loads(crudo.decode("utf-8"))
        except (ValueError, UnicodeDecodeError):
            return None

    def lista(self, prefijo: str = "", patron: str = "*") -> list[Path]:
        """Los archivos bajo `prefijo`, ordenados. El `.git` no cuenta."""
        base = self.ruta(prefijo) if prefijo else self.raiz
        if not base.is_dir():
            return []
        return sorted(p for p in base.rglob(patron)
                      if p.is_file() and ".git" not in p.parts)

    def borra(self, ruta_rel: str) -> bool:
        try:
            self.ruta(ruta_rel).unlink()
            return True
        except (OSError, ValueError):
            return False

    # ── Respaldo ────────────────────────────────────────────────────────

    def _git(self, *args: str, timeout: float = 60.0, tolera: bool = False):
        r = subprocess.run(["git", *args], cwd=str(self.raiz),
                           capture_output=True, text=True, timeout=timeout)
        if r.returncode != 0 and not tolera:
            raise RuntimeError(_sin_secretos(
                f"git {args[0]}: {(r.stderr or r.stdout or '').strip()[:300]}"))
        return r

    def _sanea(self) -> None:
        """Deja el clon en un estado en el que se PUEDA commitear.

        Un `rebase` que choca no falla y ya: deja el árbol con archivos sin
        fusionar. A partir de ahí **todo** commit muere con «Committing is not
        possible because you have unmerged files», y como nadie entra a ese
        directorio a mano, se queda así para siempre. Le pasó a la instancia de
        Render: 18 archivos parados y el último respaldo con fecha del día que
        chocó por primera vez. El panel decía «Respaldo con errores» —lo decía
        bien— pero el error no se curaba solo.

        Así que antes de cada ciclo se limpia lo que haya quedado a medias. Es
        barato (tres comprobaciones de archivo) y convierte un atasco eterno en
        un ciclo perdido.

        En los choques manda **el disco**: son datos que el agente acaba de
        escribir, y el remoto no tiene nada que el disco no vaya a volver a
        generar.
        """
        if not (self.raiz / ".git").is_dir():
            return
        g = self.raiz / ".git"
        if (g / "rebase-merge").exists() or (g / "rebase-apply").exists():
            self._git("rebase", "--abort", tolera=True)
        if (g / "MERGE_HEAD").exists():
            self._git("merge", "--abort", tolera=True)
        if (g / "CHERRY_PICK_HEAD").exists():
            self._git("cherry-pick", "--abort", tolera=True)
        # Y si aún quedan archivos sin fusionar, se resuelven tomando NUESTRO
        # lado limpio — nunca el árbol de trabajo.
        #
        # Aquí había un `git add -A`, y era el mecanismo exacto que rompió los
        # datos: en un conflicto, el archivo del árbol de trabajo lleva dentro
        # las marcas `<<<<<<< HEAD` / `=======` / `>>>>>>>`, así que `add -A`
        # las COMMITEA. Así acabaron 27 series de mercado con marcas de git
        # dentro del JSON, ilegibles para el motor: el IV Rank se quedó en 0/60
        # y las predicciones en 0 aunque los archivos estuvieran ahí.
        #
        # `checkout --ours` escribe la versión nuestra tal cual, sin marcas.
        sin_fusionar = [l.strip() for l in
                        (self._git("diff", "--name-only", "--diff-filter=U",
                                   tolera=True).stdout or "").splitlines() if l.strip()]
        for ruta in sin_fusionar:
            r = self._git("checkout", "--ours", "--", ruta, tolera=True)
            if r.returncode:                      # no había lado nuestro
                self._git("checkout", "--theirs", "--", ruta, tolera=True)
            self._git("add", "--", ruta, tolera=True)
        self._repara_marcas()

    #: Las tres marcas que git deja dentro de un archivo en conflicto.
    _MARCAS = ("<<<<<<< ", "=======", ">>>>>>> ")

    def _repara_marcas(self) -> int:
        """Limpia los archivos que YA tienen marcas de conflicto dentro.

        No es teórico: 27 archivos de `Series/` llegaron así al repositorio y el
        motor no podía leer ninguno. Un JSON con marcas dentro no es un JSON, y
        una serie que no se puede leer es una serie que no existe — el panel
        decía «0/60 días de IV» con el archivo delante.

        Se intenta rescatar el JSON de UNO de los dos lados; el que más datos
        tenga gana, porque estas series solo crecen. Si no se puede rescatar
        ninguno, el archivo se borra: se vuelve a acumular desde hoy, y eso es
        mejor que un archivo que rompe al sub-agente cada vez que lo abre.
        """
        arreglados = 0
        for ruta in self.raiz.rglob("*.json"):
            if ".git" in ruta.parts:
                continue
            try:
                texto = ruta.read_text(encoding="utf-8")
            except (OSError, UnicodeDecodeError):
                continue
            if not any(m in texto for m in self._MARCAS):
                continue
            mejor = None
            for lado in _lados_del_conflicto(texto):
                try:
                    dato = json.loads(lado)
                except (ValueError, TypeError):
                    continue
                if mejor is None or len(str(dato)) > len(str(mejor)):
                    mejor = dato
            if mejor is None:
                ruta.unlink(missing_ok=True)
            else:
                ruta.write_text(json.dumps(mejor, ensure_ascii=False), encoding="utf-8")
            arreglados += 1
        if arreglados:
            self._estado["marcas_reparadas"] = (
                self._estado.get("marcas_reparadas", 0) + arreglados)
        return arreglados

    def _reconstruye(self) -> None:
        """Última red: si el clon no hay forma de arreglarlo, se rehace.

        `_sanea()` deshace lo que quedó a medias, pero si aun así el commit
        sigue fallando el clon está roto de una forma que no se prevé. Antes eso
        significaba quedarse atascado para siempre y perder todo lo que hubiera
        en disco sin subir — que es exactamente lo que le pasó a Kevin: cuentas
        que dejaban de existir y reportes que desaparecían en el siguiente
        reinicio.

        Aquí se aparta lo que hay en disco, se clona de cero y se devuelve
        encima. Los datos ganan al clon: el clon se puede rehacer y los datos no.
        """
        if not self.respalda:
            return
        aparte = self.raiz.parent / (self.raiz.name + ".rescate")
        shutil.rmtree(aparte, ignore_errors=True)
        shutil.copytree(self.raiz, aparte,
                        ignore=shutil.ignore_patterns(".git"))
        shutil.rmtree(self.raiz, ignore_errors=True)
        self.restaura()                       # clona limpio
        # Y se devuelve lo que había, machacando lo que trajo el remoto: en
        # disco está lo más nuevo.
        for origen in aparte.rglob("*"):
            if not origen.is_file():
                continue
            destino = self.raiz / origen.relative_to(aparte)
            destino.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(origen, destino)
        shutil.rmtree(aparte, ignore_errors=True)
        self._estado["reconstrucciones"] = self._estado.get("reconstrucciones", 0) + 1

    def _hay_cambios(self, incluir_series: bool) -> bool:
        args = ["status", "--porcelain"]
        if not incluir_series:
            args += ["--", ".", f":!{DIR_SERIES}"]
        return bool(self._git(*args, tolera=True).stdout.strip())

    def sincroniza(self, incluir_series: bool = True, mensaje: str = "") -> dict[str, Any]:
        """Commitea y sube lo que haya cambiado. Devuelve el estado.

        Es idempotente y silenciosa cuando no hay nada que hacer: se llama cada
        pocos segundos.
        """
        with self._lock:
            # Los preparativos corren SIEMPRE, respalde o no: en modo local
            # también hace falta que el paquete cifrado exista en disco, porque
            # es lo que se restaura si el proceso se reinicia sin perder disco.
            for prep in list(self.antes_de_sincronizar):
                try:
                    aviso = prep()
                    if aviso:
                        self._estado["ultimo_error"] = str(aviso)[:300]
                        # Y AL LOG. En Render el log es lo único que se ve sin
                        # abrir el panel, y esto es lo que hay que leer cuando
                        # alguien dice «mi cuenta no existe»: un respaldo
                        # frenado guardado solo en memoria no se lo cuenta a
                        # nadie hasta que ya da igual.
                        log.warning("RESPALDO PRIVADO FRENADO — %s",
                                    _sin_secretos(str(aviso))[:300])
                except Exception as e:           # noqa: BLE001
                    self._estado["ultimo_error"] = _sin_secretos(str(e))[:300]
                    log.warning("RESPALDO PRIVADO ROTO — %s",
                                _sin_secretos(str(e))[:300])
            if not self.respalda:
                self._estado["motivo"] = self._motivo_apagado()
                return self._foto()
            try:
                # Lo PRIMERO: que el clon no venga roto del ciclo anterior.
                self._sanea()
                if not self._hay_cambios(incluir_series):
                    return self._foto()
                if incluir_series:
                    self._git("add", "-A")
                else:
                    # Todo MENOS las series, que van a su propio ritmo.
                    self._git("add", "-A", "--", ".", f":!{DIR_SERIES}")
                # `--allow-empty` no: si el `add` no dejó nada preparado (por
                # ejemplo solo cambió algo ignorado), un commit vacío por ciclo
                # llenaría el historial de ruido.
                if not self._git("diff", "--cached", "--quiet", tolera=True).returncode:
                    return self._foto()
                self._git("commit", "-q", "-m",
                          mensaje or f"datos del agente · {_ahora()}")
                self._estado["commits"] += 1
                self._empuja()
                self._estado.update(ultimo_push=_ahora(), ultimo_error=None,
                                    activo=True, motivo="")
            except Exception as e:
                self._estado["ultimo_error"] = _sin_secretos(str(e))[:300]
                # Un clon que sigue sin dejar commitear después de sanearlo no
                # se arregla esperando: se rehace. Un intento por ciclo, y si
                # tampoco puede se dice en el estado en vez de callar.
                if "unmerged" in str(e).lower() or "conflict" in str(e).lower():
                    try:
                        self._reconstruye()
                    except Exception as e2:      # noqa: BLE001
                        self._estado["ultimo_error"] = _sin_secretos(
                            f"no se pudo reconstruir el clon: {e2}")[:300]
            return self._foto()

    def _empuja(self) -> None:
        """`push` con reintento y espera creciente.

        El commit local ya está hecho, así que un push fallido no pierde nada
        *mientras el proceso viva*. Pero el proceso de Render puede morir en
        cualquier momento, y con él el commit local — de ahí que se reintente
        aquí y no «la próxima vez».
        """
        ultimo = None
        for intento in range(REINTENTOS_PUSH):
            try:
                self._git("push", "-q", "origin", f"HEAD:{self.rama}", timeout=120)
                return
            except Exception as e:
                ultimo = e
                # Otro proceso (otro worker, o tu máquina) empujó primero.
                self._git("fetch", "--depth", "1", "origin", self.rama,
                          timeout=120, tolera=True)
                self._reasienta()
                time.sleep(2 ** intento)
        raise RuntimeError(f"push falló tras {REINTENTOS_PUSH} intentos: {ultimo}")

    def _reasienta(self) -> None:
        """Pone NUESTROS ARCHIVOS encima del remoto, sin reaplicar historia.

        Aquí llegó la avería que dejó 37 archivos sin subir y 31 MB parados.
        El camino anterior era `rebase` y, si fallaba, `merge -X ours`. Los dos
        tienen el mismo problema de fondo: **cuestan en proporción a la
        historia y al tamaño del árbol**. Con un ciclo cada 20 s, cada minuto
        que el push falla añade tres commits locales más que reaplicar; y con
        31 MB de datos el rebase se pasaba del minuto de plazo. Al agotarse,
        `_sanea` abortaba —bien—, HEAD volvía a un commit que NO desciende del
        remoto, y el push siguiente salía `non-fast-forward`. Otra vez. Y otra.
        Un bucle que se aprieta solo: cuanto más tarda en arreglarse, más caro
        es arreglarlo.

        Y reaplicar esa historia no servía para nada. Esto es un ESPEJO de
        archivos, no un proyecto: de los commits viejos no se rescata nada, lo
        único que importa es que el árbol de ahora acabe publicado.

        Así que:

          1. se mueve la rama al remoto **sin tocar el disco** (`reset --soft`),
             con lo que el índice sigue siendo el nuestro;
          2. se recuperan los archivos que existan en el remoto y no aquí — sin
             esto, el commit siguiente los BORRARÍA, y eso es justo la avería
             del 14/08 con otro nombre;
          3. se commitea una vez.

        El resultado es la unión de los dos lados, ganando el disco en lo que
        choque —que es lo que hacía `merge -X ours`— pero en tiempo constante:
        una operación, da igual si el atasco lleva un minuto o tres días.
        """
        remoto = f"origin/{self.rama}"
        if self._git("rev-parse", "--verify", "-q", remoto, tolera=True).returncode:
            return                               # no hay remoto: nada que juntar
        self._sanea()
        if self._git("reset", "--soft", remoto, timeout=60, tolera=True).returncode:
            return
        # Lo que el remoto tiene y nosotros no. `--diff-filter=D` sobre el
        # índice: «borrados respecto a lo que hay en la rama remota».
        faltan = self._git("diff", "--cached", "--name-only", "--diff-filter=D",
                           timeout=60, tolera=True)
        rutas = [r for r in (faltan.stdout or "").splitlines() if r.strip()]
        for i in range(0, len(rutas), 200):      # por tandas: la línea de
            self._git("checkout", remoto, "--",  # órdenes tiene un límite
                      *rutas[i:i + 200], timeout=60, tolera=True)
        self._git("add", "-A", timeout=60, tolera=True)
        self._git("commit", "-q", "-m", "juntar lo del remoto",
                  timeout=60, tolera=True)

    def _motivo_apagado(self) -> str:
        if not self._token:
            return ("Sin VERTEX_GIT_TOKEN: se guarda en disco pero NO se respalda. "
                    "En Render eso significa que se borra en el próximo redeploy.")
        if not self._url():
            # El token está puesto y aun así no se respalda: hay que decir
            # exactamente QUÉ se intentó, o el mensaje parece contradecir lo
            # que el operador ve en su panel («pero si tengo el token»).
            intentos = [
                f"VERTEX_ALMACEN_REMOTO={'puesta' if self._remoto_crudo else 'sin definir'}",
                f"RENDER_GIT_REPO_SLUG={os.environ.get('RENDER_GIT_REPO_SLUG') or 'sin definir'}",
                f"origin del código={self._origin_del_codigo() or 'no se pudo leer'}",
            ]
            return ("TIENES el token, pero no se pudo deducir a qué repositorio "
                    "subir, así que NO se respalda nada. Intentado → "
                    + " · ".join(intentos)
                    + ". Arréglalo definiendo VERTEX_ALMACEN_REMOTO con la URL "
                      "https del repositorio (https://github.com/usuario/repo.git).")
        return ""

    # ── Hilo de fondo ───────────────────────────────────────────────────

    def arranca(self) -> None:
        """Lanza el respaldo periódico. Idempotente."""
        if self._hilo and self._hilo.is_alive():
            return
        self._parar.clear()
        self._hilo = threading.Thread(target=self._bucle, name="almacen",
                                      daemon=True)
        self._hilo.start()

    def _bucle(self) -> None:
        while not self._parar.wait(SEGUNDOS_RAPIDO):
            try:
                ahora = time.monotonic()
                toca_lento = (ahora - self._ultimo_lento) >= SEGUNDOS_LENTO
                self.sincroniza(incluir_series=toca_lento)
                if toca_lento:
                    self._ultimo_lento = ahora
            except Exception:
                pass          # el bucle NUNCA muere: es la red de seguridad

    def cierra(self, timeout: float = 60.0) -> dict[str, Any]:
        """Último respaldo antes de apagar, con las series incluidas.

        Render manda SIGTERM y espera antes de matar el proceso; esta es la
        ventana en la que se salva lo de los últimos segundos. Sin esto, cada
        redeploy perdería hasta un ciclo de trabajo.
        """
        self._parar.set()
        if self._hilo and self._hilo.is_alive():
            self._hilo.join(timeout=5)
        return self.sincroniza(incluir_series=True,
                               mensaje=f"cierre del servicio · {_ahora()}")

    # ── Diagnóstico ─────────────────────────────────────────────────────

    def peso_mb(self) -> float:
        total = 0
        for p in self.raiz.rglob("*"):
            if p.is_file() and ".git" not in p.parts:
                try:
                    total += p.stat().st_size
                except OSError:
                    pass
        return round(total / 1024 / 1024, 2)

    def estado(self) -> dict[str, Any]:
        """Lo que se pinta en `/api/almacen`. Nunca incluye el token."""
        con_datos = {
            "acciones": len(self.lista(DIR_ACCIONES, "*.json")),
            "opciones": len(self.lista(DIR_OPCIONES, "*.json")),
            "memoria": len(self.lista(DIR_MEMORIA, "*.md")),
            "perfiles": len(self.lista(DIR_PERFILES, "*.md")),
            "privado": len(self.lista(DIR_PRIVADO, "*.enc")),
        }
        mb = self.peso_mb()
        pendientes = 0
        if self.respalda and (self.raiz / ".git").is_dir():
            try:
                pendientes = len([l for l in self._git(
                    "status", "--porcelain", tolera=True).stdout.splitlines() if l.strip()])
            except Exception:
                pendientes = -1
        return {
            **self._foto(),
            "raiz": str(self.raiz),
            "rama": self.rama,
            "archivos": con_datos,
            "peso_mb": mb,
            "pendientes": pendientes,
            "aviso_peso": (f"El almacén pesa {mb} MB y el aviso está en {AVISO_MB} MB. "
                           f"Revisa qué está creciendo antes de que GitHub se queje.")
                          if mb > AVISO_MB else None,
        }


#: La instancia que usa el servidor. Se crea vacía y `vertex_api` la arranca en
#: el `lifespan`; así los tests pueden montar la suya con `Almacen(tmp_path)`.
almacen = Almacen()
