import os
import sys
import contextvars
import hashlib
import concurrent.futures
import json
import logging
import math
import re
import secrets
import time
import threading
import traceback
import numpy as np
import requests
from concurrent.futures import ThreadPoolExecutor
from contextlib import asynccontextmanager
from datetime import datetime, timedelta, timezone
from fastapi import FastAPI, HTTPException, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import HTMLResponse, JSONResponse, Response
# Datos de mercado desde las fuentes PRINCIPALES (FMP/FinnHub), no de
# yfinance: Victor fijó FMP, FinnHub, FRED y EDGAR, y su repo no depende
# de Yahoo en ninguna parte. `vertex_market.Ticker` mantiene la misma
# forma para no reescribir 45 llamadores en un archivo de 13.500 líneas.
import vertex_market
from pydantic import BaseModel, Field
from google import genai
from google.genai import types
from urllib.parse import urlparse

# ─────────────────────────────────────────────────────────────────────────────
# CARGA DE CREDENCIALES
# Lee vertex.env (gitignored) hacia el entorno para que TODAS las API keys
# (GEMINI/OPENAI/FMP/FINNHUB/FRED/PLAID…) queden disponibles
# vía os.environ. Nunca se imprime ni se commitea su contenido.
# ─────────────────────────────────────────────────────────────────────────────
try:
    from dotenv import load_dotenv
    _BASE_DIR = os.path.dirname(os.path.abspath(__file__))
    _ENV_PATH = os.path.join(_BASE_DIR, "vertex.env")
    load_dotenv(_ENV_PATH)                 # no falla si el archivo no existe
    # API/.env es el que lee el engine de Victor (wbj.config). Lo cargamos también para que
    # ajustes como JUDGE_MODEL lleguen a os.environ: wbj.config usa dotenv_values(), que
    # devuelve un dict y NO puebla el entorno. Sin override para que vertex.env siga
    # mandando si ambos definen la misma variable.
    load_dotenv(os.path.join(_BASE_DIR, "API", ".env"), override=False)
except Exception:
    pass  # sin python-dotenv: se usan las variables ya presentes en el entorno


# Cliente Gemini (modelo principal de la IA). GEMINI_API_KEY vive en vertex.env.
# Si no hay key, client_gemini queda en None y los endpoints de IA degradan a
# su respaldo (OpenAI) o devuelven un error limpio en vez de romper.
API_KEY = os.environ.get("GEMINI_API_KEY", "") or os.environ.get("GOOGLE_API_KEY", "")
try:
    client_gemini = genai.Client(api_key=API_KEY) if API_KEY else None
except Exception:
    client_gemini = None

# Ruta del engine determinista de Victor. Se define aquí arriba porque
# `_sec_user_agent()` (A-01) la necesita al importar el módulo, mucho antes
# de que se use para el scoring.
_WBJ_ENGINE_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "engine")
# …y se pone en `sys.path` AQUÍ, al importar el módulo, no cuando a alguien le
# haga falta.
#
# Esta línea es el arreglo de un despliegue caído. `engine/` llegaba al path
# solo como EFECTO SECUNDARIO de `_sec_user_agent()`, que lo insertaba dentro
# de su rama de respaldo. Con `EDGAR_USER_AGENT` definida —que es el caso en
# Render, y el que la SEC exige— esa función devuelve el valor y RETORNA ANTES
# de insertar nada. El `from wbj.tito.scorecard import ...` de más abajo, que
# es de nivel de módulo, moría entonces con `ModuleNotFoundError: No module
# named 'wbj'` y uvicorn salía con código 1: «Exited with status 1 while
# running your code».
#
# En local no se veía porque nadie define `EDGAR_USER_AGENT` para desarrollar:
# sin ella la función caía al respaldo, insertaba el path de paso, y todo lo
# demás importaba. Una variable de entorno CORRECTAMENTE configurada rompía el
# arranque; la ausencia de configuración lo salvaba.
#
# El path de un motor que vive dentro del repositorio no puede depender de a
# quién se le ocurra tocarlo primero. Los `sys.path.insert` que quedan repartidos
# por el archivo son inocuos —todos preguntan `if not in sys.path`— y se dejan
# para que ningún import suelto vuelva a depender del orden.
if _WBJ_ENGINE_PATH not in sys.path:
    sys.path.insert(0, _WBJ_ENGINE_PATH)

# ─────────────────────────────────────────────────────────────────────────────
# EL ALMACÉN — dónde viven de verdad los datos
#
# Render en plan `free` no tiene disco persistente: cada redeploy y cada
# despertar tras dormir borra el sistema de archivos entero. Da igual que sea
# SQLite o JSON — se borran los dos. El almacén (`vertex_almacen.py`) resuelve
# eso poniendo los archivos en un clon de la rama `datos` del repositorio, que
# se restaura al arrancar y se respalda solo.
#
# Este bloque decide DÓNDE cae cada cosa. Las tres rutas de abajo se fijan
# ANTES de que nadie las lea, porque los módulos que las usan las resuelven al
# importarse.
# ─────────────────────────────────────────────────────────────────────────────

def _dir_almacen() -> str:
    """La raíz del almacén. `VERTEX_ALMACEN` la manda; si no, junto al repo."""
    return (os.environ.get("VERTEX_ALMACEN", "").strip()
            or os.path.join(os.path.dirname(os.path.abspath(__file__)), "almacen"))


def _arranca_almacen():
    """Restaura lo guardado y deja el respaldo periódico corriendo.

    Devuelve el estado para que `/api/almacen` y los logs digan la verdad
    —incluido el caso de «no está respaldando y por qué»—, que es lo que separa
    perder datos de saber que los estás perdiendo.
    """
    log = logging.getLogger(__name__)
    try:
        from vertex_almacen import DIR_SERIES, almacen as _alm

        # Las series de mercado del motor de Víctor viven DENTRO del almacén.
        # Es lo que hace que el sub-agente 6 (Confirmación), el IV Rank real y
        # la auto-calibración sobrevivan a un redeploy: las tres necesitan
        # DÍAS de historia acumulada, y hasta ahora empezaban de cero cada vez.
        #
        # Va ANTES de `restaura()`, no después. Si la restauración falla —red,
        # token, rama— el proceso sigue vivo y sirviendo, y todo lo que analice
        # a partir de ese momento tiene que caer DENTRO del almacén igualmente:
        # así el primer respaldo que sí funcione se lo lleva. Con el orden
        # anterior, un fallo al restaurar mandaba las series a `./data/tito`
        # —fuera de lo que se respalda— y se perdían enteras sin que nada lo
        # dijera, porque el aviso que se pinta es el de la restauración.
        os.environ.setdefault("WBJ_TITO_DATA",
                              str(_alm.ruta(DIR_SERIES, "tito")))

        estado = _alm.restaura()

        # Los tres stores de series pasaron al formato exacto de Víctor
        # (`{ticker, updatedAt, snapshots}`, camelCase). Esto convierte lo que
        # se hubiera guardado con el formato anterior; sin ello se leería como
        # vacío y se perderían los días acumulados — el dato que más tarda en
        # recuperarse, porque solo crece a una foto por día de mercado.
        try:
            from wbj.tito.stores import migra_series

            hecho = migra_series()
            if any(hecho.values()):
                log.info("series migradas al formato de Víctor: %s", hecho)
        except Exception as e:                   # noqa: BLE001
            log.warning("no se pudieron migrar las series: %s", e)

        # El motivo por el que NO se pudo restaurar se guarda y se dice. Antes
        # se descartaba: si la clave no cuadraba o el tar venía roto, la base
        # se quedaba vacía en silencio y lo único que veía la persona era «tu
        # cuenta no existe» — sin una línea, en ninguna parte, que explicara
        # que había un respaldo bueno que no se pudo abrir.
        global _MOTIVO_RESTAURA, _USUARIOS_AL_ARRANCAR
        _MOTIVO_RESTAURA = _restaura_privado(_alm) or ""
        if _MOTIVO_RESTAURA:
            log.error("PRIVADO SIN RESTAURAR — %s", _MOTIVO_RESTAURA)
        # La marca contra la que se comprueba que el respaldo no encoja. Se
        # toma DESPUÉS de restaurar: es «con cuántas cuentas empezamos hoy».
        _USUARIOS_AL_ARRANCAR = max(_cuenta_usuarios(), 0)
        log.info("cuentas tras restaurar: %d", _USUARIOS_AL_ARRANCAR)
        # El paquete cifrado se regenera justo antes de cada respaldo, no
        # cuando alguien crea una cuenta: así no hay que acordarse de llamarlo
        # desde los cinco sitios que tocan la base, y nunca se sube una foto
        # vieja de las cuentas.
        if _respalda_privado not in _alm.antes_de_sincronizar:
            _alm.antes_de_sincronizar.append(_respalda_privado)
        _alm.arranca()

        if estado.get("respalda"):
            log.info("almacen: %s (rama %s)%s", _alm.raiz, _alm.rama,
                     " · restaurado" if estado.get("restaurado") else "")
        else:
            # No es un detalle de log: sin respaldo, todo lo que se analice hoy
            # desaparece en el próximo redeploy. Tiene que verse.
            log.warning("ALMACEN SIN RESPALDO — %s", estado.get("motivo"))
        return estado
    except Exception as e:                       # noqa: BLE001
        log.warning("no se pudo arrancar el almacen: %s", e, exc_info=True)
        return {"respalda": False, "motivo": f"error al arrancar: {e}"}


# ── Lo sensible: viaja CIFRADO o no viaja ────────────────────────────────────
#
# Los reportes, la memoria y los índices son texto plano y se leen en GitHub —
# ese es el punto. Pero tres cosas no pueden ir así:
#
#   · los hashes de contraseña de las cuentas,
#   · el token de Plaid (da lectura a cuentas bancarias reales),
#   · los perfiles, que llevan el capital de cada persona.
#
# Van en UN archivo cifrado con Fernet (`VERTEX_DB_KEY`), la misma clave que ya
# protege el token de Plaid en la base. Y la regla dura: **sin clave no se sube
# nada de esto**. Un hash de contraseña en un repo, aunque sea privado, es un
# objetivo de fuerza bruta offline; preferir «se pierde» a «se filtra» es la
# decisión correcta, y se dice en voz alta en vez de hacerlo en silencio.
#
# `.enc` es lo único que el `.gitignore` del almacén deja salir de `Privado/`.

#: Nombre del paquete cifrado y de su testigo de contenido.
_PRIVADO_ENC = "privado.enc"
#: El SHA-256 del contenido EN CLARO. Sin él no habría forma de saber si algo
#: cambió: Fernet usa un IV aleatorio, así que cifrar dos veces lo mismo da dos
#: bytes distintos y cada ciclo parecería un cambio. Con el testigo, un día sin
#: actividad no genera ni un commit.
_PRIVADO_SHA = "privado.sha256"


def _copia_la_base(destino: str) -> str:
    """Copia la base a `destino`. Dos caminos; si fallan los dos, lanza.

    `VACUUM INTO` es el primero porque además compacta, pero es el más
    exigente: reconstruye el archivo entero y necesita sitio para la copia
    NUEVA mientras la vieja sigue ahí. En un disco apretado —el plan free de
    Render lo está— es lo primero que revienta, y cuando revienta el respaldo
    se quedaba sin base dentro.

    El segundo es la API de respaldo en caliente de SQLite (`Connection.backup`),
    que es la forma canónica de copiar una base viva: no compacta, así que la
    copia sale más grande, pero no reconstruye nada y aguanta donde el `VACUUM`
    no. Una copia grande es un respaldo; ninguna copia no lo es.

    Devuelve cuál de los dos funcionó, para que el log lo diga.
    """
    import sqlite3
    from pathlib import Path

    fallos = []
    con = sqlite3.connect(DB_PATH, timeout=15)
    try:
        try:
            con.execute("VACUUM INTO ?", (destino,))
            return "vacuum"
        except Exception as e:                   # noqa: BLE001
            fallos.append(f"VACUUM INTO: {e}")
            # `VACUUM INTO` no escribe nada si falla, pero si dejó un archivo a
            # medias hay que quitarlo: `backup()` exige que el destino no sea un
            # archivo roto, y copiar sobre restos es cómo nace una base corrupta
            # que solo se descubre el día que hace falta restaurarla.
            try:
                Path(destino).unlink(missing_ok=True)
            except OSError:
                pass
        try:
            destino_con = sqlite3.connect(destino)
            try:
                con.backup(destino_con)
            finally:
                destino_con.close()
            logging.getLogger(__name__).warning(
                "la base se copió con backup() porque VACUUM INTO falló: %s",
                fallos[0])
            return "backup"
        except Exception as e:                   # noqa: BLE001
            fallos.append(f"backup(): {e}")
    finally:
        con.close()
    raise RuntimeError(" · ".join(fallos))


def _privado_paquete() -> bytes:
    """Un tar con la base y los perfiles. En memoria: no toca el disco en claro.

    La base se copia con `VACUUM INTO`, no leyendo el archivo: leer un SQLite
    en caliente puede capturar una escritura a medias y dar una copia corrupta
    que solo se descubre el día que hace falta restaurarla.
    """
    import io
    import sqlite3
    import tarfile
    import tempfile

    buf = io.BytesIO()
    with tarfile.open(fileobj=buf, mode="w") as tar:
        try:
            with tempfile.TemporaryDirectory() as tmp:
                copia = os.path.join(tmp, "vertex.db")
                _copia_la_base(copia)
                tar.add(copia, arcname="vertex.db", filter=_tar_estable)
        except Exception as e:                   # noqa: BLE001
            # LANZA. Antes se anotaba en el log y se seguía, y eso construía un
            # paquete SIN la base: un tar con cuatro perfiles, perfectamente
            # válido, que se cifraba y se subía ENCIMA del bueno. Medido en el
            # despliegue real el 14/08: el respaldo pasó de 1.843.300 bytes a
            # 163.940 en dos ciclos —justo el tamaño de los perfiles sin base—
            # y todas las cuentas dejaron de existir.
            #
            # Un respaldo al que le falta lo que se respalda no es un respaldo
            # a medias: es un borrado con otro nombre. Mejor no subir nada y
            # decirlo que subir algo que destruye lo que había.
            raise RuntimeError(
                f"no se pudo copiar la base ({e}): el paquete se habría subido "
                "sin ella y habría borrado las cuentas del respaldo") from e
        if os.path.isdir(_PERFIL_DIR):
            for nombre in sorted(os.listdir(_PERFIL_DIR)):
                if nombre.endswith(".md"):
                    tar.add(os.path.join(_PERFIL_DIR, nombre),
                            arcname=f"perfiles/{nombre}", filter=_tar_estable)
        # …y los de CADA usuario, que viven un nivel más abajo. Esto faltaba y
        # no se notaba: `os.listdir` no baja a `usuarios/`, así que el único
        # `.md` que viajaba era el de referencia. Al reiniciar Render volvía la
        # base con el perfil de todo el mundo, pero NO el archivo que
        # `_load_investor_profile()` lee — y esa función, al no encontrarlo,
        # cae a `Kevin.md` sin decir nada. Resultado: el análisis de otra
        # persona contado con el capital y la tolerancia de Kevin.
        _usuarios = os.path.join(_PERFIL_DIR, "usuarios")
        if os.path.isdir(_usuarios):
            for nombre in sorted(os.listdir(_usuarios)):
                if nombre.endswith(".md"):
                    tar.add(os.path.join(_usuarios, nombre),
                            arcname=f"perfiles/usuarios/{nombre}",
                            filter=_tar_estable)
    return buf.getvalue()


def _tar_estable(info):
    """Borra del tar todo lo que cambia sin que cambie el contenido.

    Sin esto, el mismo dato produce bytes distintos en cada empaquetado —el tar
    guarda la fecha de modificación, el uid y el nombre de usuario— y entonces
    el testigo SHA nunca coincide: se re-cifraría y se subiría un `privado.enc`
    nuevo cada 20 segundos, para siempre, aunque nadie hubiera tocado nada.
    """
    info.mtime = 0
    info.uid = info.gid = 0
    info.uname = info.gname = ""
    return info


def _paquete_restaurable(claro: bytes) -> str:
    """Abre el paquete recién hecho y comprueba que sirve. `""` si sirve.

    Un respaldo que nadie ha probado no es un respaldo: es un archivo que se
    espera que funcione el día que ya no se puede comprobar. Y el día que hizo
    falta, no funcionaba — el paquete que se subió el 14/08 no llevaba base
    dentro y nadie lo miró hasta que las cuentas ya no existían.

    Así que se abre el tar, se saca `vertex.db`, se abre como SQLite de verdad
    y se cuenta. Si el número no cuadra con el de la base viva, no se sube: eso
    es exactamente lo que pasó en el escalón intermedio, una base a medias que
    parecía bien por fuera.
    """
    import io
    import sqlite3
    import tarfile
    import tempfile

    try:
        with tarfile.open(fileobj=io.BytesIO(claro), mode="r") as tar:
            miembro = next((m for m in tar.getmembers()
                            if m.isfile() and m.name == "vertex.db"), None)
            if miembro is None:
                return "el paquete no lleva la base dentro"
            datos = tar.extractfile(miembro)
            if datos is None:
                return "la base del paquete no se puede leer"
            with tempfile.TemporaryDirectory() as tmp:
                ruta = os.path.join(tmp, "prueba.db")
                with open(ruta, "wb") as fh:
                    fh.write(datos.read())
                dentro = _cuenta_en_db(ruta)
        if dentro < 0:
            return "la base del paquete no se pudo abrir"
    except Exception as e:                       # noqa: BLE001
        return f"el paquete no se pudo abrir para comprobarlo ({e})"

    vivas = _cuenta_usuarios()
    if vivas >= 0 and dentro != vivas:
        return (f"el paquete lleva {dentro} cuenta(s) y la base viva tiene "
                f"{vivas}: la copia salió a medias")
    return ""


def _cuenta_en_db(ruta: str) -> int:
    """Cuentas dentro de un archivo SQLite suelto. `-1` si no se pudo abrir.

    Sin tabla `usuarios` son CERO: es un despliegue donde nadie se ha
    registrado, no un archivo roto.
    """
    import sqlite3

    try:
        con = sqlite3.connect(ruta, timeout=5)
        try:
            fila = con.execute("SELECT COUNT(*) FROM usuarios").fetchone()
            return int(fila[0]) if fila else 0
        except sqlite3.Error:
            return 0
        finally:
            con.close()
    except Exception:                            # noqa: BLE001
        return -1


def _cuentas_en_el_respaldo(alm=None) -> int:
    """Cuántas cuentas hay DENTRO del respaldo remoto. `-1` si no se sabe.

    Es contra esto y no contra una marca de arranque contra lo que se compara
    antes de sobrescribir: la pregunta que importa no es «cuántas había cuando
    encendimos» sino «¿voy a subir menos de las que ya están guardadas?».
    """
    import io
    import tarfile
    import tempfile

    from vertex_almacen import DIR_PRIVADO, almacen as _def

    a = alm or _def
    f = _fernet()
    if f is None:
        return -1
    try:
        cifrado = a.lee(f"{DIR_PRIVADO}/{_PRIVADO_ENC}")
        if not cifrado:
            return 0                             # no hay respaldo: nada que perder
        with tarfile.open(fileobj=io.BytesIO(f.decrypt(cifrado)), mode="r") as tar:
            m = next((x for x in tar.getmembers()
                      if x.isfile() and x.name == "vertex.db"), None)
            if m is None:
                return 0                         # respaldo sin base: nada que perder
            datos = tar.extractfile(m)
            if datos is None:
                return -1
            with tempfile.TemporaryDirectory() as tmp:
                ruta = os.path.join(tmp, "respaldo.db")
                with open(ruta, "wb") as fh:
                    fh.write(datos.read())
                return _cuenta_en_db(ruta)
    except Exception:                            # noqa: BLE001
        return -1


def _cuenta_usuarios() -> int:
    """Cuántas cuentas hay en la base local. `-1` si no se pudo saber.

    `-1` y no `0`: son cosas distintas y la diferencia decide si se sobrescribe
    el respaldo de todo el mundo. «No pude mirar» tiene que poder frenar lo
    mismo que frena «hay menos que antes».
    """
    import sqlite3

    if not os.path.exists(DB_PATH):
        return 0
    try:
        con = sqlite3.connect(DB_PATH, timeout=5)
        try:
            fila = con.execute("SELECT COUNT(*) FROM usuarios").fetchone()
            return int(fila[0]) if fila else 0
        except sqlite3.Error:
            # La tabla todavía no existe: nadie se ha registrado nunca. Eso son
            # CERO cuentas, no «no pude mirar». Confundirlos dejaba el respaldo
            # bloqueado para siempre en un despliegue recién estrenado.
            return 0
        finally:
            con.close()
    except Exception:                            # noqa: BLE001
        return -1


#: Cuántas cuentas trajo la restauración al arrancar. Es la marca contra la que
#: se comprueba que el respaldo no ENCOJA: si ahora hay menos de las que había
#: al arrancar y nadie ha borrado ninguna, algo se perdió por el camino y
#: subirlo convertiría esa pérdida local en una pérdida definitiva.
#:
#: `None` mientras no se sepa (antes de restaurar). Baja sola cuando alguien
#: borra su cuenta de verdad, que es el único descenso legítimo.
_USUARIOS_AL_ARRANCAR: int | None = None

#: Por qué no se pudo devolver lo privado a su sitio, o `""`. Se pinta en
#: `/api/almacen` y en el aviso de persistencia: un respaldo que existe y no se
#: puede abrir es peor que no tenerlo, porque nadie va a ir a buscarlo.
_MOTIVO_RESTAURA: str = ""


def _respalda_privado_frenado(alm=None) -> str:
    """Por qué NO se debe sobrescribir el respaldo ahora mismo, o `""`.

    Aparte de `_respalda_privado` para que `/api/almacen` pueda decirlo sin
    escribir nada: un respaldo frenado es correcto —mejor uno viejo que uno
    vacío— pero callarlo es cómo se pierde todo sin enterarse.
    """
    from vertex_almacen import DIR_PRIVADO, almacen as _def

    a = alm or _def
    guardadas = _cuentas_en_el_respaldo(a)
    if guardadas <= 0:
        # Ni hay respaldo, ni el que hay tiene cuentas dentro: no hay nada que
        # proteger y una instalación nueva tiene que poder estrenarse. `-1` es
        # «no se pudo mirar», y ahí tampoco se frena: bloquear el respaldo por
        # no poder leer el remoto dejaría al sistema sin respaldar por una
        # avería de red.
        return ""

    hay = _cuenta_usuarios()

    # ── Cerrojo 1: no se pisa un respaldo CON cuentas con uno SIN cuentas ──
    #
    # Pasó de verdad, el 14/08. Si la restauración falla —red, token, la base a
    # medio escribir— el contenedor sigue vivo con la base VACÍA, y veinte
    # segundos después el hilo de respaldo sube esa base vacía encima de la
    # buena. En veinte segundos, y sin que nada lo diga, desaparece todo el
    # mundo.
    #
    # Ningún estado legítimo dice «no queda ni una cuenta y aun así piso el
    # respaldo que sí las tiene». Se prefiere un respaldo viejo a ninguno.
    if hay == 0:
        return (f"La base local no tiene ni una cuenta y el respaldo guarda "
                f"{guardadas}: NO se sobrescribe. Reinicia el servicio para "
                "restaurar desde él.")
    if hay < 0:
        return ("No se pudo leer la base local para contar las cuentas: NO se "
                "sobrescribe el respaldo hasta saber qué hay dentro.")

    # ── Cerrojo 2: el respaldo no ENCOGE sin que nadie haya borrado nada ──
    #
    # Se compara contra lo que hay DENTRO del respaldo, no contra una marca del
    # arranque: la pregunta que importa no es «cuántas había cuando encendimos»
    # sino «¿voy a subir menos de las que ya están guardadas?».
    #
    # Y se cuentan CUENTAS, no bytes, a propósito. El paquete encoge a menudo
    # sin que se pierda nada: la SQLite es caché —los reportes viven como
    # archivos en `Reportes/`, que es la fuente de verdad— así que el payload de
    # un análisis entra en la base y sale de ella según se reconstruya, y eso
    # mueve el tamaño cientos de kilobytes en cada reinicio. Frenar por eso
    # dejaría el respaldo bloqueado casi siempre.
    #
    # Las CUENTAS son distintas: no están en ningún archivo, solo aquí. Perder
    # una es definitivo, y por eso es lo único que se cuenta.
    if hay < guardadas:
        return (f"La base local tiene {hay} cuenta(s) y el respaldo guarda "
                f"{guardadas}: NO se sobrescribe con menos de lo que había. "
                "Si borraste una cuenta a propósito, hazlo también en el "
                "respaldo o restaura primero.")
    return ""


def _respalda_privado(alm=None) -> str:
    """Cifra y guarda lo sensible. Devuelve por qué NO se hizo, o ''.

    Tres cerrojos antes de escribir, y los tres existen por lo mismo: este
    archivo es la ÚNICA copia de las cuentas, así que una escritura mala aquí
    no es un respaldo fallido — es un borrado.
    """
    import hashlib

    from vertex_almacen import DIR_PRIVADO, almacen as _def

    a = alm or _def
    f = _fernet()
    if f is None:
        return ("Sin VERTEX_DB_KEY no se respaldan cuentas ni perfiles: un hash "
                "de contraseña sin cifrar no se sube a un repositorio.")

    frenado = _respalda_privado_frenado(a)
    if frenado:
        return frenado

    try:
        # ── Cerrojo 3: sin base dentro, no hay paquete ──
        # `_privado_paquete` lanza si no pudo copiarla, y aquí NO se traga: un
        # tar de perfiles sueltos es un archivo válido que borra las cuentas.
        claro = _privado_paquete()
        # ── Cerrojo 4: el paquete se ABRE y se cuenta antes de subirlo ──
        # Es el único que comprueba lo que de verdad importa —que se puede
        # restaurar— en vez de fiarse de que el empaquetado no lanzó.
        roto = _paquete_restaurable(claro)
        if roto:
            return f"NO se sube el respaldo: {roto}."
        sha = hashlib.sha256(claro).hexdigest()
        # OJO con este testigo: `Privado/*` está en el `.gitignore` del almacén
        # salvo `*.enc`, así que el `.sha256` NO viaja en la rama. Vive en el
        # disco efímero y desaparece en cada reinicio, y por eso la primera
        # sincronización tras arrancar SIEMPRE reescribe.
        #
        # Se deja así a propósito —es un hash del contenido en claro y la regla
        # de la casa es que de ahí no sale nada sin cifrar—, pero conviene saber
        # que esto ahorra commits DENTRO de una vida del contenedor y no protege
        # de nada entre reinicios. Lo que protege son los cerrojos de arriba.
        if (a.lee(f"{DIR_PRIVADO}/{_PRIVADO_SHA}") or b"").decode("utf-8", "replace").strip() == sha:
            return ""                            # nada cambió: ni un commit
        a.guarda(f"{DIR_PRIVADO}/{_PRIVADO_ENC}", f.encrypt(claro))
        a.guarda(f"{DIR_PRIVADO}/{_PRIVADO_SHA}", sha)
        return ""
    except Exception as e:                       # noqa: BLE001
        return f"no se pudo respaldar lo privado: {e}"


def _restaura_privado(alm=None) -> str:
    """Descifra y devuelve la base y los perfiles a su sitio.

    Solo actúa si la base local está VACÍA de datos. Un contenedor que ya tiene
    cuentas o reportes vivos no puede ser pisado por una foto del remoto.

    Y «vacía» significa sin filas, no «el archivo no existe»: `init_db()` corre
    al importar el módulo, así que para cuando esto se ejecuta el archivo SIEMPRE
    existe, con 110 KB de esquema y cero datos. Comprobar el archivo hacía que la
    restauración no actuara nunca — las cuentas se recuperaban en el almacén y
    no llegaban a la base, y el usuario no podía entrar. Es el fallo que este
    comentario existe para que no vuelva.
    """
    import io
    import sqlite3
    import tarfile

    from vertex_almacen import DIR_PRIVADO, almacen as _def

    a = alm or _def
    # El candado mira las CUENTAS, no «si hay algo».
    #
    # Miraba `usuarios` O `reports`, y ahí estaba la trampa que dejó a Kevin
    # fuera con el respaldo intacto al lado: tras una restauración fallida el
    # contenedor se queda con cero cuentas, se guarda un análisis —y `reports`
    # pasa a tener filas—, y en el siguiente arranque este candado ve «ya hay
    # datos» POR LOS REPORTES y salta la restauración. Las cuentas no vuelven
    # nunca, y lo único que se ve es «email o contraseña incorrectos».
    #
    # Las dos tablas no valen lo mismo. `reports` es CACHÉ: los análisis viven
    # como archivos en `Reportes/`, que es la fuente de verdad, y de ahí se
    # rehace. Las cuentas no están en ningún archivo — solo aquí—, así que
    # perderlas es definitivo. Cuando hay que elegir, gana lo irrecuperable.
    hay = _cuenta_usuarios()
    if hay > 0:
        return "la base local ya tiene cuentas; no se restaura encima"
    if hay < 0:
        return ("no se pudo leer la base local para saber si tiene cuentas; "
                "no se restaura a ciegas")
    cifrado = a.lee(f"{DIR_PRIVADO}/{_PRIVADO_ENC}")
    if not cifrado:
        return ""                                # primer arranque: no hay nada
    f = _fernet()
    if f is None:
        return ("Hay un respaldo cifrado pero falta VERTEX_DB_KEY: las cuentas "
                "no se pueden recuperar sin la clave con la que se guardaron.")
    # Cuántas filas de caché se van a desplazar. Restaurar reescribe la base
    # entera, así que los reportes indexados que todavía no estuvieran en el
    # respaldo pierden su fila —no el análisis, que está en `Reportes/`—. Se
    # cuenta antes para poder DECIRLO: una restauración que mueve datos y no lo
    # menciona es la que hace que nadie se fíe de la siguiente.
    desplazados = 0
    try:
        con = sqlite3.connect(DB_PATH, timeout=5)
        try:
            fila = con.execute("SELECT COUNT(*) FROM reports").fetchone()
            desplazados = int(fila[0]) if fila else 0
        except sqlite3.Error:
            desplazados = 0
        finally:
            con.close()
    except Exception:                            # noqa: BLE001
        desplazados = 0

    try:
        claro = f.decrypt(cifrado)
        with tarfile.open(fileobj=io.BytesIO(claro), mode="r") as tar:
            for m in tar.getmembers():
                if not m.isfile():
                    continue
                # El nombre viene del propio paquete, pero se comprueba igual:
                # un tar con `../` en un nombre escribiría fuera del destino.
                if m.name == "vertex.db":
                    destino = DB_PATH
                elif m.name.startswith("perfiles/usuarios/"):
                    hoja = m.name[len("perfiles/usuarios/"):]
                    if not hoja or "/" in hoja or hoja.startswith("."):
                        continue          # `..`, subcarpetas y ocultos, fuera
                    os.makedirs(os.path.join(_PERFIL_DIR, "usuarios"),
                                exist_ok=True)
                    destino = os.path.join(_PERFIL_DIR, "usuarios", hoja)
                elif m.name.startswith("perfiles/") and "/" not in m.name[9:]:
                    os.makedirs(_PERFIL_DIR, exist_ok=True)
                    destino = os.path.join(_PERFIL_DIR, m.name[9:])
                else:
                    continue
                datos = tar.extractfile(m)
                if datos is None:
                    continue
                with open(destino, "wb") as fh:
                    fh.write(datos.read())
        if desplazados:
            logging.getLogger(__name__).warning(
                "restauradas las cuentas desde el respaldo; %d fila(s) de "
                "reportes de la caché local se rehacen desde Reportes/",
                desplazados)
        return ""
    except Exception as e:                       # noqa: BLE001
        return f"no se pudo restaurar lo privado: {e}"


@asynccontextmanager
async def _vertex_lifespan(app: FastAPI):
    """Arranque de la aplicación: el planificador y el aviso de claves.

    Esto era `@app.on_event("startup")`, que FastAPI marca como obsoleto y
    va a retirar — el aviso salía en cada corrida de los tests. `lifespan`
    es el reemplazo oficial y además da un lugar donde apagar cosas (tras
    el `yield`) que `on_event` no tenía.

    `_vertex_startup` se define mucho más abajo, junto al planificador que
    arranca: el cuerpo sólo se ejecuta al levantar el servidor, así que el
    nombre ya está resuelto para entonces.
    """
    # ── EL ALMACÉN VA PRIMERO ────────────────────────────────────────────
    #
    # Antes que nada, porque todo lo demás lee de él. En un contenedor nuevo
    # esto CLONA la rama de datos y recupera reportes, memoria, perfiles,
    # cuentas y series; sin esto, un redeploy de Render arrancaría con el disco
    # en blanco y el agente creería que nunca ha analizado nada.
    #
    # Es bloqueante a propósito: si `_vertex_startup` arrancara el planificador
    # antes de que los datos estén, el primer ciclo escribiría sobre un
    # directorio vacío y el `push` siguiente BORRARÍA lo que había en el
    # remoto. La restauración tiene que terminar antes de que nadie escriba.
    _arranca_almacen()

    _vertex_startup()
    # El índice de tickers se cargaba perezosamente, con la PRIMERA búsqueda.
    # Tarda 1,8 s, así que las primeras teclas del usuario caían a FMP: 750-1000
    # ms por pulsación justo en el momento en que estrena la página. En Render,
    # donde el servicio se duerme, ese arranque frío es lo primero que ve cada
    # vez. Se dispara aquí, en segundo plano, para que esté listo antes de que
    # dé tiempo a escribir la primera letra.
    try:
        _indice_actual()
    except Exception:
        logging.getLogger(__name__).warning(
            "no se pudo precalentar el indice de tickers", exc_info=True)
    yield
    # ── Y AL APAGAR, EL ÚLTIMO RESPALDO ──────────────────────────────────
    #
    # Render manda SIGTERM y espera unos segundos antes de matar el proceso.
    # Esta es la ventana para salvar lo escrito desde el último ciclo. Sin
    # ella, cada redeploy perdería hasta un ciclo entero de trabajo.
    try:
        from vertex_almacen import almacen as _alm

        _alm.cierra()
    except Exception:
        logging.getLogger(__name__).warning(
            "no se pudo cerrar el almacen", exc_info=True)


app = FastAPI(title="Vertex Fund OS Core", lifespan=_vertex_lifespan)

# ─────────────────────────────────────────────────────────────────────────────
# AUTENTICACIÓN (C-02)
#
# La app se despliega públicamente en Render. Sin esto, los ~60 endpoints son
# anónimos: cualquiera con la URL puede leer el portafolio, y `/api/analyze`
# permite vaciar las cuotas de FMP/Gemini/Anthropic desde fuera.
#
# Modelo (un solo usuario):
#   - `VERTEX_API_TOKEN` en el entorno es la contraseña.
#   - El navegador se autentica una vez (`POST /api/login`) y recibe una cookie
#     HttpOnly + SameSite=Strict. HttpOnly = JavaScript no puede leerla, así que
#     un XSS no la roba; SameSite=Strict = el navegador no la manda en peticiones
#     originadas por otro sitio, que es la defensa contra CSRF.
#   - Los scripts (curl, cron) usan la cabecera `X-Vertex-Token`.
#
# Default seguro: si `VERTEX_API_TOKEN` NO está definido, sólo se atiende a
# localhost. Así el desarrollo local sigue funcionando sin configurar nada, y
# un despliegue público al que se le olvidó la variable queda CERRADO en vez de
# abierto — el fallo por omisión nunca debe ser "todo el mundo entra".
# ─────────────────────────────────────────────────────────────────────────────
VERTEX_API_TOKEN = os.environ.get("VERTEX_API_TOKEN", "").strip()
_AUTH_COOKIE = "vertex_session"
_LOCAL_HOSTS = {"127.0.0.1", "::1", "localhost", "testclient"}

#: Cookie de la sesión de USUARIO. Es distinta de `_AUTH_COOKIE`, que es la
#: puerta del despliegue entero (un secreto compartido). Aquí va el token de
#: una persona concreta, y de él sale su perfil y su archivo de reportes.
_USER_COOKIE = "vertex_usuario"

#: Quién puede crear cuenta:
#:   · `abierto`     — cualquiera con la URL (por defecto: Kevin quiere que la
#:                     gente se registre).
#:   · `invitacion`  — hace falta `VERTEX_INVITE_CODE`.
#:   · `cerrado`     — nadie; solo entran las cuentas que ya existen.
#: Se declara aquí, arriba, porque es la decisión de seguridad más consecuente
#: del archivo y no puede quedar enterrada en una ruta.
VERTEX_REGISTRO = (os.environ.get("VERTEX_REGISTRO", "abierto").strip().lower()
                   or "abierto")
VERTEX_INVITE_CODE = os.environ.get("VERTEX_INVITE_CODE", "").strip()

#: Rutas sin autenticación: el HTML de la app (que por sí solo no expone datos —
#: todo lo pinta vía /api/*), los iconos del PWA y el propio login.
#:
#: Las de cuentas son públicas por necesidad: quien aún no tiene sesión no puede
#: pasar la puerta, y sin poder llamarlas nunca la tendría.
_PUBLIC_PATHS = {"/", "/legacy", "/wbj", "/manifest.webmanifest",
                 "/api/login", "/api/auth/status", "/favicon.ico",
                 "/api/auth/registro", "/api/auth/entrar", "/api/auth/salir",
                 "/api/auth/yo"}


def _client_host(request) -> str:
    return (request.client.host if request.client else "") or ""


#: El usuario de ESTA petición.
#:
#: Es un `ContextVar` y no un global a propósito. Un global sería el último que
#: entró contestándole a todos los demás en cuanto hubiera dos peticiones a la
#: vez. Un `ContextVar` es por contexto asíncrono, y Starlette copia el contexto
#: al hilo donde corre cada ruta síncrona, así que cada petición ve el suyo.
#:
#: Existe para que `_load_investor_profile()` sepa de quién es el perfil sin
#: tener que hilar `request` por media docena de capas de Analyze y Explore —
#: que son justo las que no se pueden tocar.
_USUARIO_CTX: contextvars.ContextVar = contextvars.ContextVar("vertex_usuario",
                                                              default=None)

#: El idioma de la sesión, por el mismo camino y por el mismo motivo.
#:
#: La pantalla la traduce el panel con su diccionario, pero hay un texto que
#: ningún diccionario alcanza: el que ESCRIBE el modelo. Esa prosa no existe
#: hasta que se pide, así que el idioma tiene que viajar hasta el prompt. Va en
#: una cabecera y llega aquí, para no hilar `request` por Analyze y Explore.
_IDIOMA_CTX: contextvars.ContextVar = contextvars.ContextVar("vertex_idioma",
                                                             default="es")
#: Cabecera que manda el panel en cada petición.
_IDIOMA_HEADER = "X-Vertex-Idioma"


def _idioma_actual() -> str:
    """`es` o `en`. Nunca otra cosa: un valor raro cae a español."""
    return "en" if _IDIOMA_CTX.get() == "en" else "es"


def _instruccion_idioma() -> str:
    """La frase que se le pone al modelo para fijar el idioma de la respuesta.

    Se declara en el idioma de destino a propósito: pedir en español que
    responda en inglés funciona peor que pedírselo en inglés.
    """
    if _idioma_actual() == "en":
        return ("\n\nLANGUAGE: write EVERY field of your answer in English. "
                "Do not use Spanish anywhere, not even for headings, labels or "
                "quoted terms. Numbers, tickers and proper names stay as they are.")
    return ("\n\nIDIOMA: escribe TODOS los campos de tu respuesta en español. "
            "No uses inglés en ninguna parte, ni en títulos ni en etiquetas. "
            "Los números, los tickers y los nombres propios se quedan igual.")


def _con_idioma(texto) -> str:
    """El texto que se le manda a un modelo, con el idioma pegado al final.

    Va al FINAL a propósito: es lo último que lee el modelo, y gana sobre
    cualquier «responde en X» que venga escrito más arriba en el prompt o en la
    descripción de un campo del esquema.
    """
    return f"{texto or ''}{_instruccion_idioma()}"


def _gemini_genera(**kw):
    """TODA petición a Gemini pasa por aquí.

    Ponerle el idioma en cada sitio de llamada es exactamente el error que este
    trabajo vino a arreglar: son ocho, y el noveno que alguien añada saldrá en
    español sin que nadie se entere. Aquí no hay nada que recordar — y un test
    prohíbe llamar al cliente por fuera.
    """
    kw["contents"] = _con_idioma(kw.get("contents"))
    return client_gemini.models.generate_content(**kw)


def _claude_crea(cliente, **kw):
    """Lo mismo para Anthropic. El idioma se pega al `system`, que es el que
    manda sobre el tono y la forma de la respuesta."""
    kw["system"] = _con_idioma(kw.get("system"))
    return cliente.messages.create(**kw)


def _usuario_actual(request=None):
    """El usuario de la sesión, o `None`. Nunca lanza.

    Con `request`, resuelve desde su cookie. Sin él, devuelve el que el
    middleware dejó en el contexto — que es cómo lo consultan las capas
    profundas.
    """
    if request is None:
        return _USUARIO_CTX.get()
    tok = request.cookies.get(_USER_COOKIE, "")
    if not tok:
        return None
    try:
        conn = _db()
        try:
            return _CU.usuario_de_sesion(conn, tok)
        finally:
            conn.close()
    except Exception:                             # noqa: BLE001
        return None


def _auth_ok(request) -> bool:
    """¿Esta petición puede pasar? Comparación en tiempo constante para no
    filtrar el token por diferencias de tiempo de respuesta.

    Tres llaves, en orden de preferencia:

    1. **Sesión de usuario** — la normal desde que hay cuentas.
    2. **`VERTEX_API_TOKEN`** — la puerta compartida. Sigue valiendo para
       scripts y cron, y es la única forma de entrar antes de que exista la
       primera cuenta.
    3. **Localhost sin token configurado** — desarrollo local.
    """
    if _usuario_actual(request) is not None:
        return True
    if not VERTEX_API_TOKEN:                      # sin token configurado → sólo local
        return _client_host(request) in _LOCAL_HOSTS
    cookie = request.cookies.get(_AUTH_COOKIE, "")
    if cookie and secrets.compare_digest(cookie, VERTEX_API_TOKEN):
        return True
    header = request.headers.get("x-vertex-token", "")
    return bool(header) and secrets.compare_digest(header, VERTEX_API_TOKEN)


@app.middleware("http")
async def _require_auth(request, call_next):
    path = request.url.path
    # Se resuelve el usuario ANTES de decidir, y se deja en el contexto de la
    # petición. Sin esto, las capas profundas (el perfil que lee el agente de
    # acciones) no tendrían forma de saber de quién es la sesión sin recibir el
    # `request`, y recibirlo obligaría a tocar Analyze y Explore.
    #
    # Sin cookie no hay consulta: `_usuario_actual` sale en la primera línea.
    _USUARIO_CTX.set(_usuario_actual(request))
    _IDIOMA_CTX.set((request.headers.get(_IDIOMA_HEADER) or "es").strip().lower())
    if (path in _PUBLIC_PATHS or path.startswith("/assets/")
            or request.method == "OPTIONS"          # preflight de CORS
            or _auth_ok(request)):
        return await call_next(request)
    return JSONResponse(status_code=401, content={
        "ok": False, "error": "unauthorized",
        "detail": ("Esta API requiere autenticación. En el navegador: inicia sesión. "
                   "En un script: manda la cabecera X-Vertex-Token.")})


# N-01: freno a la fuerza bruta sobre /api/login. Un token de 24 bytes no se
# adivina, pero sin freno el endpoint sirve de oráculo y de vector de carga:
# cada intento es gratis para quien lo lanza y trabajo para el servidor.
# Ventana deslizante por IP, en memoria (un solo proceso; suficiente aquí).
_LOGIN_ATTEMPTS: dict[str, list[float]] = {}
_LOGIN_MAX_TRIES = 8
_LOGIN_WINDOW_S = 300.0


def _login_rate_limited(host: str) -> bool:
    now = time.time()
    tries = [t for t in _LOGIN_ATTEMPTS.get(host, []) if now - t < _LOGIN_WINDOW_S]
    _LOGIN_ATTEMPTS[host] = tries
    if len(_LOGIN_ATTEMPTS) > 500:            # cota de memoria: purga lo caducado
        for k in [k for k, v in _LOGIN_ATTEMPTS.items() if not v]:
            _LOGIN_ATTEMPTS.pop(k, None)
    return len(tries) >= _LOGIN_MAX_TRIES


@app.post("/api/login")
def api_login(request: Request, body: dict = None):
    """Canjea el token por una cookie de sesión. Nunca devuelve el token."""
    if not VERTEX_API_TOKEN:
        return {"ok": True, "auth_required": False,
                "detail": "No hay VERTEX_API_TOKEN configurado: acceso limitado a localhost."}
    host = _client_host(request)
    if _login_rate_limited(host):
        return JSONResponse(status_code=429, content={
            "ok": False, "error": "Demasiados intentos. Espera 5 minutos."})
    token = str((body or {}).get("token") or "")
    if not token or not secrets.compare_digest(token, VERTEX_API_TOKEN):
        _LOGIN_ATTEMPTS.setdefault(host, []).append(time.time())
        return JSONResponse(status_code=401,
                            content={"ok": False, "error": "Token incorrecto."})
    _LOGIN_ATTEMPTS.pop(host, None)           # acierto: se limpia el contador
    resp = JSONResponse(content={"ok": True, "auth_required": True})
    # N-02: el flag Secure sale del esquema REAL de la petición, no de que
    # alguien se acuerde de definir VERTEX_ORIGIN. Antes, olvidar esa variable
    # emitía la cookie sin Secure sobre https — viajaría por http en un
    # downgrade. `x-forwarded-proto` es lo que manda el proxy de Render.
    is_https = (request.headers.get("x-forwarded-proto", "").split(",")[0].strip()
                or request.url.scheme) == "https"
    resp.set_cookie(_AUTH_COOKIE, VERTEX_API_TOKEN, httponly=True, samesite="strict",
                    secure=is_https, max_age=60 * 60 * 24 * 30, path="/")
    return resp


@app.post("/api/logout")
def api_logout():
    resp = JSONResponse(content={"ok": True})
    resp.delete_cookie(_AUTH_COOKIE, path="/")
    return resp


@app.get("/api/auth/status")
def api_auth_status(request: Request):
    """Pública a propósito: el frontend la consulta al cargar para saber si debe
    pedir contraseña. No revela el token, sólo si hace falta y si ya hay sesión."""
    return {"ok": True, "auth_required": bool(VERTEX_API_TOKEN),
            "authenticated": _auth_ok(request),
            # Antes de que nadie escriba un email: si las cuentas no se
            # respaldan, avisarlo después de crearla llega tarde.
            "aviso_persistencia": _aviso_persistencia()}


# ═══════════════════════════════════════════════════════════════════════════
#  CUENTAS DE USUARIO
#
#  Antes de esto, "iniciar sesión" era una ficción del navegador: el usuario y
#  su contraseña **en texto plano** vivían en `localStorage`, o sea una base de
#  datos por Chrome. Entrar desde el móvil era imposible porque la cuenta no
#  existía fuera de aquel portátil, y cualquiera con la consola abierta leía la
#  contraseña de todos.
#
#  Ahora la cuenta vive en SQLite y la sesión es una cookie HttpOnly. Ver
#  `vertex_cuentas.py` para el hashing y el modelo.
# ═══════════════════════════════════════════════════════════════════════════

def _pon_cookie_usuario(resp, request, token):
    """Cookie de sesión. `Secure` sale del esquema REAL de la petición, no de
    que alguien se acuerde de definir una variable."""
    is_https = (request.headers.get("x-forwarded-proto", "").split(",")[0].strip()
                or request.url.scheme) == "https"
    resp.set_cookie(_USER_COOKIE, token, httponly=True, samesite="strict",
                    secure=is_https, max_age=60 * 60 * 24 * 30, path="/")
    return resp


def _publico(usuario):
    """Lo que se le devuelve al navegador de un usuario. Sin `pass_hash`,
    obviamente, y sin el perfil entero: eso tiene su propia ruta."""
    if not usuario:
        return None
    return {"id": usuario["id"], "email": usuario["email"], "nombre": usuario["nombre"]}


def _aviso_persistencia() -> str:
    """Por qué esta cuenta puede desaparecer, o cadena vacía si no puede.

    Es el fallo que más caro sale de todos los que tiene este despliegue, y el
    único que no se nota hasta que ya pasó: creas la cuenta, funciona, cierras
    sesión, y al volver «no existe». En medio, Render se durmió —el plan free
    borra el disco al despertar— y la cuenta se fue con él.

    Las cuentas viajan en `Privado/privado.enc`, cifradas con `VERTEX_DB_KEY`.
    Sin esa clave NO se suben: un hash de contraseña en un repositorio, aunque
    sea privado, es un objetivo de fuerza bruta offline, y se prefiere perderlo
    a filtrarlo. Esa decisión es correcta; lo que estaba mal es que se tomaba
    EN SILENCIO, justo en el momento en que el usuario cree lo contrario.
    """
    try:
        import vertex_almacen as _AL
        respalda = bool(_AL.almacen.estado().get("respalda"))
    except Exception:                             # noqa: BLE001
        return ("No se pudo comprobar el respaldo: da por hecho que esta cuenta "
                "NO sobrevive a un reinicio del servidor.")
    if not respalda:
        return ("Esta cuenta NO se está respaldando: falta VERTEX_GIT_TOKEN. "
                "Si el servidor se reinicia o se duerme, tendrás que volver a "
                "registrarte — y el email quedará libre otra vez.")
    # Un respaldo que EXISTE y no se pudo abrir es peor que no tenerlo: nadie
    # va a ir a buscarlo. Va delante de todo lo demás porque es el único caso
    # en que los datos siguen ahí y aun así la persona ve «tu cuenta no existe».
    if _MOTIVO_RESTAURA:
        return ("Hay un respaldo de las cuentas que NO se pudo restaurar: "
                + _MOTIVO_RESTAURA
                + " Las cuentas viejas no aparecerán hasta que se resuelva; no "
                "vuelvas a registrarte hasta entonces o tendrás dos.")
    # CERO cuentas aquí y cuentas en el respaldo: la restauración no corrió.
    #
    # Es el caso que dejaba a la persona mirando «email o contraseña
    # incorrectos» —un mensaje que apunta a la contraseña— cuando lo que pasa
    # es que su cuenta nunca llegó a este contenedor. El login no puede
    # distinguir «no existe» de «mal escrita» a propósito, para no convertirse
    # en un directorio de emails; pero ESTO sí se puede decir, porque no habla
    # de ninguna cuenta en concreto.
    try:
        if _cuenta_usuarios() == 0 and _cuentas_en_el_respaldo() > 0:
            return ("Este servidor no tiene ninguna cuenta cargada, pero el "
                    "respaldo sí las guarda: la restauración no llegó a correr. "
                    "Reinicia el servicio para recuperarlas. NO te registres de "
                    "nuevo o acabarás con dos cuentas y el mismo email ocupado.")
    except Exception:                             # noqa: BLE001
        pass
    if _fernet() is None:
        return ("Esta cuenta se guarda en disco pero NO se respalda: falta "
                "VERTEX_DB_KEY, la clave con la que se cifran las cuentas antes "
                "de subirlas. Sin ella no se suben a propósito (un hash de "
                "contraseña sin cifrar no se sube a ningún repositorio), así que "
                "si el servidor se reinicia tendrás que registrarte de nuevo. "
                "Ponla en Render: openssl rand -hex 32")
    return ""



def _respalda_ya(motivo: str) -> None:
    """Sube lo que haya, ahora. Nunca lanza.

    Existe para lo que NO puede esperar al hilo de fondo: una cuenta nueva, un
    perfil guardado, un reporte. Todo eso son cosas que la persona acaba de
    hacer y da por hechas, y perderlas por veinte segundos de retraso es
    indistinguible de que el sistema no guarde nada.
    """
    try:
        import vertex_almacen as _AL              # el módulo, no una copia:
        _AL.almacen.sincroniza(mensaje=motivo)    # `almacen` se reemplaza en tests
    except Exception:                             # noqa: BLE001
        pass                                      # el hilo de fondo lo reintenta

@app.post("/api/auth/registro")
async def auth_registro(request: Request):
    """Crea la cuenta y deja la sesión abierta."""
    host = _client_host(request)
    if _login_rate_limited(host):
        return JSONResponse(status_code=429, content={
            "ok": False, "error": "Demasiados intentos. Espera 5 minutos."})
    try:
        body = await request.json()
    except Exception:                             # noqa: BLE001
        return {"ok": False, "error": "Cuerpo no es JSON."}
    if not isinstance(body, dict):
        return {"ok": False, "error": "Cuerpo no es un objeto."}

    if VERTEX_REGISTRO == "cerrado":
        return {"ok": False, "error": "El registro está cerrado en este despliegue."}
    if VERTEX_REGISTRO == "invitacion":
        codigo = str(body.get("invitacion") or "")
        if not VERTEX_INVITE_CODE or not secrets.compare_digest(codigo, VERTEX_INVITE_CODE):
            _LOGIN_ATTEMPTS.setdefault(host, []).append(time.time())
            return {"ok": False, "error": "Código de invitación incorrecto."}

    conn = _db()
    try:
        usuario = _CU.crear_usuario(conn, str(body.get("email") or ""),
                                    str(body.get("nombre") or ""),
                                    str(body.get("password") or ""))
        # El `.md` se escribe YA, con los defaults de Kevin. Así el agente de
        # acciones tiene un perfil que leer desde el primer análisis, aunque la
        # persona no haya contestado todavía — y el propio archivo declara
        # cuántas preguntas siguen heredadas.
        _CU.guardar_perfil(conn, _PERFIL_DIR, usuario, _CU.leer_perfil(conn, usuario["id"]))
        token = _CU.abrir_sesion(conn, usuario["id"])
    except _CU.ErrorDeCuenta as e:
        # `e.publico`, no `str(e)`: el mensaje se redactó en el `raise` para que
        # lo lea una persona («Ya existe una cuenta con ese email»). Ver
        # `vertex_cuentas.ErrorDeCuenta`.
        return {"ok": False, "error": e.publico}
    except Exception as e:                        # noqa: BLE001
        return {"ok": False, "error": _error_publico(e, "Crear la cuenta")}
    finally:
        conn.close()

    # La cuenta se sube AHORA mismo, no dentro de veinte segundos.
    #
    # Es el hueco por el que se perdían: el hilo de fondo sincroniza cada
    # `SEGUNDOS_RAPIDO`, y en Render pulsar «Deploy» mata el contenedor y borra
    # el disco. Una cuenta creada quince segundos antes no llegaba a subir, y al
    # volver «no existía». Registrarse una vez puede costar un segundo más;
    # tener que registrarse otra vez cuesta mucho más que eso.
    _respalda_ya(f"cuenta nueva: {usuario.get('email', '')}")
    return _pon_cookie_usuario(
        JSONResponse(content={"ok": True, "usuario": _publico(usuario), "nuevo": True,
                              # Si esta cuenta no va a sobrevivir a un reinicio,
                              # se dice AQUÍ y no cuando ya no se pueda entrar.
                              "aviso_persistencia": _aviso_persistencia(),
                              # Para que la pantalla pueda decirlo en vez de que
                              # el usuario descubra solo que su archivo cambió.
                              "primera_cuenta": bool(usuario.get("primera_cuenta")),
                              "reportes_adoptados": usuario.get("reportes_adoptados") or 0}),
        request, token)


@app.post("/api/auth/entrar")
async def auth_entrar(request: Request):
    """Email + contraseña. Mismo mensaje para «no existe» y «contraseña mala»:
    distinguirlos convierte el login en un directorio de emails registrados."""
    host = _client_host(request)
    if _login_rate_limited(host):
        return JSONResponse(status_code=429, content={
            "ok": False, "error": "Demasiados intentos. Espera 5 minutos."})
    try:
        body = await request.json()
    except Exception:                             # noqa: BLE001
        return {"ok": False, "error": "Cuerpo no es JSON."}

    conn = _db()
    try:
        usuario = _CU.autenticar(conn, str((body or {}).get("email") or ""),
                                 str((body or {}).get("password") or ""))
        token = _CU.abrir_sesion(conn, usuario["id"])
    except _CU.CredencialInvalida as e:
        _LOGIN_ATTEMPTS.setdefault(host, []).append(time.time())
        return JSONResponse(status_code=401,
                            content={"ok": False, "error": e.publico})
    except Exception as e:                        # noqa: BLE001
        return {"ok": False, "error": _error_publico(e, "Iniciar sesión")}
    finally:
        conn.close()

    _LOGIN_ATTEMPTS.pop(host, None)               # acierto: se limpia el contador
    return _pon_cookie_usuario(
        JSONResponse(content={"ok": True, "usuario": _publico(usuario)}),
        request, token)


@app.post("/api/auth/salir")
def auth_salir(request: Request):
    """Cierra la sesión **en el servidor**, no solo en el navegador.

    Borrar la cookie y dejar la fila viva dejaría el token válido para siempre
    en cualquier sitio donde se hubiera copiado."""
    tok = request.cookies.get(_USER_COOKIE, "")
    if tok:
        conn = _db()
        try:
            _CU.cerrar_sesion(conn, tok)
        finally:
            conn.close()
    resp = JSONResponse(content={"ok": True})
    resp.delete_cookie(_USER_COOKIE, path="/")
    return resp


@app.get("/api/auth/yo")
def auth_yo(request: Request):
    """Quién está dentro. El frontend la consulta al cargar para saber si tiene
    que enseñar el formulario o la app."""
    u = _usuario_actual(request)
    return {"ok": True, "usuario": _publico(u),
            "registro": VERTEX_REGISTRO,
            "necesita_invitacion": VERTEX_REGISTRO == "invitacion"}


# CORS (C-04): con una cookie de sesión en juego, `allow_origins=["*"]` +
# credenciales sería un agujero CSRF. Se restringe al origen real; en local, a
# los puertos de desarrollo habituales.
_ORIGIN = os.environ.get("VERTEX_ORIGIN", "").strip()
app.add_middleware(
    CORSMiddleware,
    allow_origins=([_ORIGIN] if _ORIGIN else
                   ["http://localhost:8000", "http://127.0.0.1:8000"]),
    allow_credentials=True,
    allow_methods=["GET", "POST", "OPTIONS"],
    allow_headers=["content-type", "x-vertex-token"],
)

#: El HTML es el ESQUELETO de la app: todo el JavaScript va dentro. Se servía
#: sin una sola cabecera de caché, así que el navegador decidía por su cuenta
#: cuánto guardarlo — y tras un despliegue seguía ejecutando el JavaScript
#: viejo contra la API nueva. Eso es justo lo que le paso a Victor: la API ya
#: tenía el arreglo y su navegador seguía con el bundle roto, dando
#: "integrityStripHTML is not defined" al analizar.
#:
#: `no-cache` NO significa "no lo guardes": significa "guárdalo, pero
#: pregúntame antes de usarlo". Con `ETag`, si el archivo no cambió el
#: servidor responde 304 y no se transfiere nada — misma velocidad, pero un
#: despliegue llega siempre. `no-store` sí prohibiría guardarlo y penalizaría
#: cada carga en el teléfono.
_HTML_SIN_CACHE = {"Cache-Control": "no-cache, must-revalidate"}


def _servir_html(nombre: str, request: Request | None = None) -> Response:
    base_dir = os.path.dirname(os.path.abspath(__file__))
    ruta = os.path.join(base_dir, nombre)
    if not os.path.exists(ruta):
        return HTMLResponse("<h1>Vertex OS Error: Frontend no encontrado en el servidor.</h1>",
                            status_code=404)
    with open(ruta, "r", encoding="utf-8") as f:
        cuerpo = f.read()
    # El ETag sale del CONTENIDO: cambia sólo cuando el archivo cambia.
    etiqueta = f'W/"{hashlib.sha256(cuerpo.encode("utf-8")).hexdigest()[:16]}"'
    cabeceras = {**_HTML_SIN_CACHE, "ETag": etiqueta}
    # Si el navegador ya tiene esta misma versión, 304 y no se transfiere el
    # cuerpo. Sin esto, `no-cache` costaría medio mega en cada carga — caro
    # en un teléfono. Con esto: se revalida siempre, se descarga sólo cuando
    # de verdad cambió.
    if request is not None and request.headers.get("if-none-match") == etiqueta:
        return Response(status_code=304, headers=cabeceras)
    return HTMLResponse(cuerpo, headers=cabeceras)


@app.get("/", response_class=HTMLResponse)
def serve_frontend(request: Request):
    """Sirve el agente Vertex COMPLETO (Dashboard, Reports, Portfolio, Proyecciones,
    Watchlist, Track Record). El análisis muestra los números de Victor (overlay)."""
    return _servir_html("vertex_fund_os_platform.html", request)


@app.get("/manifest.webmanifest")
def serve_manifest():
    """Manifest de la PWA: permite instalar Vertex en el telefono/tablet
    ("Instalar" en Chrome, "Añadir a inicio" en Safari) y abrirla a pantalla
    completa, sin barra del navegador — se comporta como una app nativa."""
    from fastapi.responses import JSONResponse
    return JSONResponse({
        "name": "Vertex Fund OS",
        "short_name": "Vertex AI",
        "description": "Terminal de analisis de inversiones — metodologia Warren Buffett Jr.",
        "start_url": "/",
        "scope": "/",
        "display": "standalone",
        "orientation": "any",
        "background_color": "#0B0E14",
        "theme_color": "#0B0E14",
        "lang": "es",
        "icons": [
            {"src": "/assets/icon-192.png", "sizes": "192x192", "type": "image/png", "purpose": "any"},
            {"src": "/assets/icon-512.png", "sizes": "512x512", "type": "image/png", "purpose": "any"},
            {"src": "/assets/icon-512.png", "sizes": "512x512", "type": "image/png", "purpose": "maskable"},
        ],
    }, media_type="application/manifest+json")


@app.get("/assets/icon-{size}.png")
def serve_icon(size: str):
    """Iconos de la app. Lista blanca de tamaños — el nombre viene de la URL y no
    debe poder salir de assets/."""
    from fastapi.responses import FileResponse
    if size not in {"180", "192", "512"}:
        raise HTTPException(status_code=404, detail="icono no encontrado")
    base_dir = os.path.dirname(os.path.abspath(__file__))
    path = os.path.join(base_dir, "assets", f"icon-{size}.png")
    if not os.path.exists(path):
        raise HTTPException(status_code=404, detail="icono no encontrado")
    return FileResponse(path, media_type="image/png",
                        headers={"Cache-Control": "public, max-age=604800"})


@app.get("/wbj")
def serve_wbj_terminal():
    """El análisis WBJ de Victor ya vive DENTRO del dashboard principal (sección
    'Análisis WBJ'); ya no hay vista aparte. Redirige a la raíz para no duplicar."""
    from fastapi.responses import RedirectResponse
    return RedirectResponse(url="/")


@app.get("/legacy", response_class=HTMLResponse)
def serve_frontend_legacy():
    """Interfaz anterior (framework de 7 señales) por si se necesita comparar."""
    return _servir_html("vertex_fund_os_platform.html")


# ─────────────────────────────────────────────────────────────────────────────
# PERSISTENCE — SQLite (long-term agent memory + accuracy tracker)
# ─────────────────────────────────────────────────────────────────────────────
import sqlite3

#: Cuentas, perfiles por usuario y el cuestionario. Vive aparte porque no
#: depende de nada de este archivo y así se puede probar solo.
import vertex_cuentas as _CU

DB_PATH = os.environ.get("VERTEX_DB",
                         os.path.join(os.path.dirname(os.path.abspath(__file__)), "vertex.db"))

def _db():
    conn = sqlite3.connect(DB_PATH, timeout=10)
    conn.row_factory = sqlite3.Row
    return conn

def init_db():
    try:
        conn = _db()
        conn.execute("""CREATE TABLE IF NOT EXISTS reports (
            report_id         TEXT PRIMARY KEY,
            ticker            TEXT NOT NULL,
            created_at        TEXT NOT NULL,
            created_ts        REAL NOT NULL,
            price_at_analysis REAL,
            fair_value        REAL,
            upside_pct        REAL,
            recommendation    TEXT,
            conviction        INTEGER,
            target_bull       REAL,
            target_base       REAL,
            target_bear       REAL,
            thesis            TEXT
        )""")
        conn.execute("CREATE INDEX IF NOT EXISTS idx_reports_ticker ON reports(ticker, created_ts)")
        # victor_categories: el score 0-10 de cada uno de los 6 agentes de Victor, que es lo
        # que define el tipo de setup del reporte. ALTER TABLE para bases ya creadas.
        # (signal_scores, de las 7 señales del LLM ya eliminadas, se dejó de crear y de leer.)
        for _col in ("victor_categories",):
            try:
                conn.execute(f"ALTER TABLE reports ADD COLUMN {_col} TEXT")
            except Exception:
                pass
        # #3 — per-horizon target (base) so we can score short-horizon targets, not just 12M.
        for _col in ("target_7d", "target_30d", "target_3m", "target_6m"):
            try:
                conn.execute(f"ALTER TABLE reports ADD COLUMN {_col} REAL")
            except Exception:
                pass
        # #4 — payload completo del reporte (JSON) para un archivo DURABLE y multi-dispositivo en el servidor.
        try:
            conn.execute("ALTER TABLE reports ADD COLUMN payload TEXT")
        except Exception:
            pass
        # MKT-REVMAG-012 (magnitud de revisión de consenso): guarda un SNAPSHOT del consenso en
        # cada análisis. La revisión es un cambio ENTRE DOS MOMENTOS, y ninguna API nos da el
        # consenso de hace N días — así que lo construimos nosotros con el histórico propio.
        conn.execute("""CREATE TABLE IF NOT EXISTS consensus_snapshots (
            ticker      TEXT NOT NULL,
            taken_ts    REAL NOT NULL,
            fiscal_date TEXT,
            eps_avg     REAL,
            revenue_avg REAL,
            n_analysts  INTEGER,
            PRIMARY KEY (ticker, taken_ts)
        )""")
        conn.execute("CREATE INDEX IF NOT EXISTS idx_cons_ticker ON consensus_snapshots(ticker, taken_ts)")
        # Cuentas, sesiones y el registro de contribuciones al pool común.
        _CU.crear_tablas(conn)
        # `usuario_id` en reports: el archivo pasa a ser PRIVADO de cada quien.
        # Las filas anteriores se quedan en NULL — son de la época de un solo
        # usuario y se tratan como de nadie, no como de todos.
        try:
            conn.execute("ALTER TABLE reports ADD COLUMN usuario_id TEXT")
        except Exception:
            pass
        conn.execute("CREATE INDEX IF NOT EXISTS idx_reports_usuario ON reports(usuario_id, created_ts)")
        conn.commit()
        conn.close()
    except Exception as e:
        print(f"[DB] init error: {e}")

init_db()


# ─────────────────────────────────────────────────────────────────────────────
# A-02: SETTINGS DEL ENGINE CON LAS CLAVES DEL ENTORNO
#
# `wbj.config.load_settings()` lee `API/.env` con `dotenv_values()`, que
# devuelve un dict y NO mira `os.environ`. En Render ese archivo no existe —
# las claves llegan por el dashboard — así que `settings.fmp_api_key` sale
# `None`, `FMPProvider.available` es `False` y el provider devuelve `None`
# en silencio.
#
# Media docena de call-sites inyectaban las claves a mano y dos se olvidaron
# (`_wbj_insiders_clasificados`, `_wbj_holders_from_edgar`), así que los items
# obligatorios 4 (13F/13D-G) y 5 (insiders >$1M) de CLAUDE.md salían VACÍOS en
# producción, sin un solo aviso. En local funcionaban porque sí hay `API/.env`.
#
# Este helper es ahora el único camino: usarlo siempre en vez de `load_settings()`.
# ─────────────────────────────────────────────────────────────────────────────
def _engine_settings(base=None):
    """`Settings` del engine con las claves del entorno ya inyectadas."""
    if _WBJ_ENGINE_PATH not in sys.path:
        sys.path.insert(0, _WBJ_ENGINE_PATH)
    from wbj.config import load_settings
    st = base or load_settings()
    for env_name, attr in (("FMP_API_KEY", "fmp_api_key"),
                           ("FINNHUB_API_KEY", "finnhub_api_key"),
                           ("FRED_API_KEY", "fred_api_key"),
                           ("ANTHROPIC_API_KEY", "anthropic_api_key"),
                           ("EDGAR_USER_AGENT", "edgar_user_agent"),
                           ("JUDGE_MODEL", "judge_model")):
        val = (os.environ.get(env_name) or "").strip()
        if val and not (getattr(st, attr, None) or "").strip():
            try:
                setattr(st, attr, val)
            except Exception:
                pass
    return st


# ── ESTIMADOS DE CONSENSO DE FMP ─────────────────────────────────────────────
# Dos trampas, ambas silenciosas, ambas encontradas midiendo con la clave real:
#
# 1. FMP renombro los campos al retirar `/api/v3/`: `estimatedRevenueAvg` es
#    ahora `revenueAvg`, `estimatedEpsAvg` es `epsAvg`. Pedir el nombre viejo
#    devuelve None, no un error.
# 2. Los devuelve en orden DESCENDENTE — para NVDA, 2031 antes que 2027. Coger
#    `[0]` no daba "el proximo año" sino el consenso mas lejano disponible.
#
# Juntas dejaban en None todo el puente de estimados: crecimiento de consenso,
# P/E forward, PEG y la magnitud de revision.

def _est_field(row, *keys):
    """Primer valor no nulo entre `keys` (tolera los dos juegos de nombres)."""
    for k in keys:
        v = (row or {}).get(k)
        if v is not None:
            return v
    return None


def _next_year_estimate(estimates, as_of=""):
    """Fila de consenso del PROXIMO periodo posterior a `as_of`."""
    if not as_of:
        as_of = datetime.now(timezone.utc).date().isoformat()
    futuras = [e for e in (estimates or [])
               if isinstance(e, dict) and str(e.get("date", "")) > str(as_of)]
    return min(futuras, key=lambda e: str(e["date"])) if futuras else None


# ── #5 — CACHÉ COMPARTIDO DE SERIES DE PRECIO ────────────────────────────────
# track-record, calibración, IC y portfolio-fit bajaban 1 año de historia por
# ticker cada uno, por separado. Este caché (TTL 1h) lo comparte y reduce el
# riesgo de ratelimit de yfinance.
_PRICE_SERIES_CACHE = {}

# ── A-06: PRESUPUESTO DE LLAMADAS A FMP ──────────────────────────────────────
# Un solo /api/analyze disparaba 80-120 peticiones a FMP; el plan free son 250
# AL DÍA. Los bucles de pares eran el grueso: 15 profile + 15 income (P/S),
# 20 ohlcv de 2 años (breadth + RS) y otros 15 income + 15 balance (peer ROIC).
# Se recortan a los mínimos que la propia metodología exige — 8 pares es el
# MIN_PEERS que ya se comprobaba más abajo, y 5 filas el mínimo del percentil
# de fuerza relativa. Menos que eso no cambia el número; más solo gasta cuota.
_FMP_MAX_PEERS = 10       # P/S y peer ROIC (umbral real: 8)
_FMP_MAX_BREADTH = 12     # breadth sectorial + universo RS (umbral real: 5)

_PERIODO_DIAS = {"5y": 1825, "2y": 730, "15mo": 460, "1y": 365, "6mo": 183,
                 "3mo": 92, "2mo": 62, "1mo": 31, "5d": 7, "7d": 7}


def _fmp_daily_bars(ticker, period="1y"):
    """#3 RESPALDO de historia diaria: FMP EOD ajustado por splits/dividendos.

Sustituyó a Stooq, que dejó de servir el CSV (responde con un desafío
    anti-bot). Aquel camino se eliminó en M-08: el sistema decía tener tres
    fuentes de historia diaria y tenía dos.

    FMP ya es una fuente del sistema (la usa el engine de Victor y hay clave
    configurada), así que esto no añade dependencias nuevas. Se reusa
    `FMPProvider.ohlcv_daily`, con el mismo patrón de carga que
    `_wbj_fmp_important_insiders`.

    Devuelve [(epoch, open, high, low, close, volume)] de más viejo a más
    nuevo, o [] si no hay clave/datos. Best-effort: nunca lanza.
    """
    if not (os.environ.get("FMP_API_KEY") or "").strip():
        return []
    try:
        if _WBJ_ENGINE_PATH not in sys.path:
            sys.path.insert(0, _WBJ_ENGINE_PATH)
        from wbj.config import load_settings
        from wbj.providers.cache import Cache
        from wbj.providers.fmp import FMPProvider
        _s = _engine_settings()
        dias = _PERIODO_DIAS.get(period, 365)
        anios = 1 if dias <= 300 else (2 if dias <= 700 else 5)
        filas = FMPProvider(_s, Cache(_s.cache_dir)).ohlcv_daily(str(ticker).upper().strip(),
                                                                 years=anios) or []
        if not isinstance(filas, list):
            return []
        corte = time.time() - dias * 86400
        out = []
        for f in filas:
            if not isinstance(f, dict):
                continue
            try:
                ts = datetime.strptime(str(f["date"])[:10], "%Y-%m-%d").timestamp()
                if ts < corte:
                    continue
                out.append((ts, float(f["open"]), float(f["high"]), float(f["low"]),
                            float(f["close"]), int(float(f.get("volume") or 0))))
            except (KeyError, TypeError, ValueError):
                continue
        out.sort(key=lambda x: x[0])          # FMP viene del más nuevo al más viejo
        return out
    except Exception:
        return []


def _fmp_perfil(ticker):
    """Nombre y web de la empresa desde FMP, para cuando `stock.info` viene vacío.

    Yahoo limita `.info` mucho antes que `fast_info`, así que en produccion se da
    el caso de tener precio pero no nombre: la tarjeta mostraba "AAPL" como razón
    social y el logo caía al avatar de iniciales. Reference data, cacheada por el
    propio FMPProvider.

    Devuelve {"name": str, "website": str} o {} si no hay clave/datos. Nunca lanza.
    """
    if not (os.environ.get("FMP_API_KEY") or "").strip():
        return {}
    try:
        if _WBJ_ENGINE_PATH not in sys.path:
            sys.path.insert(0, _WBJ_ENGINE_PATH)
        from wbj.config import load_settings
        from wbj.providers.cache import Cache
        from wbj.providers.fmp import FMPProvider
        _s = _engine_settings()
        p = FMPProvider(_s, Cache(_s.cache_dir)).profile(str(ticker).upper().strip())
        if isinstance(p, list):
            p = p[0] if p else None
        if not isinstance(p, dict):
            return {}
        return {"name": (p.get("companyName") or "").strip(),
                "website": (p.get("website") or "").strip()}
    except Exception:
        return {}


def _resilient_history(stock, ticker, period):
    """Historia diaria OHLCV con respaldo FMP: si yfinance falla o viene vacío (rate-limit/caída),
    reconstruye el DataFrame (Open/High/Low/Close/Volume) desde FMP para que /api/analyze NO se caiga.
    Devuelve un DataFrame estilo yfinance o None si ninguna fuente respondió."""
    try:
        h = stock.history(period=period)
        if h is not None and not h.empty:
            return h
    except Exception:
        pass
    # #3 respaldo REAL: FMP EOD (el único que queda; Stooq se eliminó en M-08).
    try:
        import pandas as pd
        barras = _fmp_daily_bars(ticker, period)
        if barras:
            idx = pd.DatetimeIndex([datetime.fromtimestamp(b[0]) for b in barras])
            return pd.DataFrame({"Open": [b[1] for b in barras], "High": [b[2] for b in barras],
                                 "Low": [b[3] for b in barras], "Close": [b[4] for b in barras],
                                 "Volume": [b[5] for b in barras]}, index=idx)
    except Exception:
        pass
    # Tercer respaldo: eliminado. Stooq dejó de servir el CSV (responde con un
    # desafío anti-bot), así que este camino devolvía None siempre — el sistema
    # decía tener 3 fuentes de historia diaria y tenía 2: yfinance y FMP.
    return None

def _cached_price_series(ticker, period="1y", ttl=3600):
    key = f"{str(ticker).upper()}|{period}"
    nowt = time.time()
    ent = _PRICE_SERIES_CACHE.get(key)
    if ent and nowt - ent[0] < ttl:
        return ent[1]
    series = []
    try:
        h = vertex_market.Ticker(ticker).history(period=period)
        if h is not None and not h.empty and "Close" in h:
            series = [(idx.timestamp(), float(c)) for idx, c in h["Close"].items()]
    except Exception:
        series = []
    if not series:                                  # #3 respaldo: yfinance vacío → FMP
        series = [(b[0], b[4]) for b in _fmp_daily_bars(ticker, period)]
    _PRICE_SERIES_CACHE[key] = (nowt, series)
    return series

def _price_at(series, target_ts):
    """Primer cierre en/después de target_ts; si es futuro, el último; None si no hay serie."""
    if not series:
        return None
    for ts, c in series:
        if ts >= target_ts:
            return c
    return series[-1][1]

def save_report(report_id, ticker, price, fair_value, upside_pct, recommendation, conviction, targets, thesis, victor_categories=None):
    """Persist a report so the agent can remember it and we can score accuracy later.
    victor_categories: {categoría: score_0_10} de los 6 agentes de Victor. Se guarda como JSON
    para poder derivar el tipo de setup del reporte (el agente que domina la tesis) y medir el
    track record por área. Sustituye a las 7 señales del LLM, que se eliminaron."""
    try:
        t12 = (targets or {}).get("12m", {}) or {}
        def _hb(k):
            return ((targets or {}).get(k, {}) or {}).get("base")
        vc_json = json.dumps(victor_categories) if victor_categories else None
        # De quién es este reporte. El archivo es PRIVADO: cada quien ve el
        # suyo. Lo que se comparte es el APRENDIZAJE —la calibración, las
        # series—, no el análisis de nadie. Ver `/api/aprendizaje`.
        _u = _usuario_actual()
        conn = _db()
        conn.execute("""INSERT OR REPLACE INTO reports
            (report_id,ticker,created_at,created_ts,price_at_analysis,fair_value,upside_pct,
             recommendation,conviction,target_bull,target_base,target_bear,thesis,victor_categories,
             target_7d,target_30d,target_3m,target_6m,usuario_id)
            VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
            (report_id, ticker,
             datetime.now().strftime("%Y-%m-%d %H:%M:%S"), datetime.now().timestamp(),
             price, fair_value, upside_pct, recommendation, conviction,
             t12.get("bull"), t12.get("base"), t12.get("bear"), (thesis or "")[:4000], vc_json,
             _hb("7d"), _hb("30d"), _hb("3m"), _hb("6m"),
             (_u or {}).get("id")))
        # Cada análisis alimenta al agente de ACCIONES. Su forma de aprender es
        # la calibración: guarda convicción y objetivos, y el tiempo dice si
        # acertó. Más reportes de más gente = una curva de fiabilidad con más
        # puntos, para todos.
        _CU.registrar_contribucion(conn, "acciones", ticker, (_u or {}).get("id"))
        conn.commit()
        conn.close()
    except Exception as e:
        print(f"[DB] save error: {e}")

def consensus_snapshot(ticker, fiscal_date, eps_avg, revenue_avg, n_analysts, min_gap_days=7):
    """Guarda el consenso de analistas de HOY y devuelve el snapshot ANTERIOR (o None).

    MKT-REVMAG-012 mide `(consenso actual - consenso previo)/|previo|`. Ninguna API entrega el
    consenso de hace N días, así que lo acumulamos nosotros: cada análisis deja su marca y la
    revisión se calcula contra la marca previa. `min_gap_days` evita comparar contra un snapshot
    de hace un rato (dos análisis el mismo día no son una "revisión"). El primer análisis de un
    ticker devuelve None — honesto: aún no hay contra qué comparar.
    """
    prior = None
    try:
        now = datetime.now().timestamp()
        conn = _db()
        row = conn.execute(
            "SELECT fiscal_date,eps_avg,revenue_avg,n_analysts,taken_ts FROM consensus_snapshots "
            "WHERE ticker=? AND taken_ts <= ? ORDER BY taken_ts DESC LIMIT 1",
            (ticker, now - min_gap_days * 86400.0)).fetchone()
        if row:
            prior = {"fiscal_date": row[0], "eps_avg": row[1], "revenue_avg": row[2],
                     "n_analysts": row[3], "taken_ts": row[4]}
        if eps_avg is not None or revenue_avg is not None:
            conn.execute(
                "INSERT OR REPLACE INTO consensus_snapshots "
                "(ticker,taken_ts,fiscal_date,eps_avg,revenue_avg,n_analysts) VALUES (?,?,?,?,?,?)",
                (ticker, now, fiscal_date, eps_avg, revenue_avg, n_analysts))
            conn.commit()
        conn.close()
    except Exception as e:
        print(f"[DB] consensus snapshot error: {e}")
    return prior


#: Tope del payload de UN reporte, en bytes. `VERTEX_PAYLOAD_MAX` lo cambia.
#:
#: 2 MB no es un límite de SQLite —aguanta 1 GB por columna— sino el que hace
#: que `/api/reports/list` siga siendo servible: devuelve hasta 60 payloads
#: COMPLETOS de una vez, así que el tope por reporte multiplica por 60. A 2 MB
#: son 120 MB de respuesta; a 10 MB, 600 MB, y Render se cae antes de acabar.
#:
#: Por eso subirlo solo no basta: hay que subirlo Y dejar que la lista devuelva
#: menos. `_PAYLOAD_RESPUESTA_MAX` es el freno que hace las dos cosas
#: compatibles — recorta cuántos reportes caben, no cuánto pesa cada uno.
def _payload_max() -> int:
    try:
        n = int(os.environ.get("VERTEX_PAYLOAD_MAX", "2000000"))
    except ValueError:
        return 2_000_000
    return n if n > 0 else 2_000_000


#: Tope de la RESPUESTA de `/api/reports/list`, en bytes. Es lo que impide que
#: subir el tope por reporte se convierta en una respuesta de cientos de MB.
def _payload_respuesta_max() -> int:
    try:
        n = int(os.environ.get("VERTEX_LISTA_MAX", "40000000"))     # 40 MB
    except ValueError:
        return 40_000_000
    return n if n > 0 else 40_000_000


def save_report_payload(report_id, payload):
    """#4 — guarda el JSON COMPLETO del reporte en el servidor para un archivo durable y multi-dispositivo.
    save_report() ya insertó la fila; aquí solo rellenamos la columna payload. Best-effort."""
    try:
        # Tope de 2 MB. Antes se aplicaba con `[:2_000_000]`, y CORTAR un JSON
        # por la mitad produce un JSON INVÁLIDO: la fila quedaba escrita pero
        # ilegible, `/api/reports/list` la saltaba con su `except: continue` y
        # el reporte desaparecía del archivo sin que nada avisara.
        #
        # Ahora, si no cabe, se guarda el reporte SIN las series de precio —que
        # son lo que pesa y lo que la gráfica puede volver a pedir— en vez de
        # guardar basura. Y si aun así no cabe, no se escribe nada: un payload
        # ausente se nota y se puede regenerar; uno corrupto se lee como si no
        # existiera el reporte.
        _SERIES = ("historial_precios", "historial_fechas", "historial_ohlc",
                   "historial_volumen", "chart_history")
        _TOPE = _payload_max()
        blob = json.dumps(_json_safe(payload))
        if len(blob) > _TOPE:
            _sin_series = {k: v for k, v in payload.items() if k not in _SERIES}
            _sin_series["_series_omitidas"] = list(_SERIES)   # para que se sepa
            blob = json.dumps(_json_safe(_sin_series))
            print(f"[DB] payload de {report_id} pasaba de {_TOPE/1e6:.0f} MB: "
                  "guardado sin las series")
        if len(blob) > _TOPE:
            print(f"[DB] payload de {report_id} sigue pasando de {_TOPE/1e6:.0f} MB: "
                  "NO se guarda (un JSON cortado no se puede leer)")
            return
        conn = _db()
        conn.execute("UPDATE reports SET payload=? WHERE report_id=?", (blob, report_id))
        conn.commit()
        conn.close()
    except Exception as e:
        print(f"[DB] payload save error: {e}")
    # Y AL ARCHIVO, que es lo que de verdad dura.
    #
    # La base es ahora CACHÉ: sirve para ordenar y filtrar rápido, y se borra
    # con cada redeploy de Render. El archivo es la fuente de verdad, va al
    # almacén y sobrevive. Por eso esto va fuera del `try` de arriba: que la
    # base falle no puede impedir que el reporte se guarde de verdad.
    #
    # Ojo con la diferencia: al archivo va el payload ENTERO, sin el tope de
    # arriba. Ese tope era un límite de la COLUMNA de SQLite, no del dato.
    _archiva_acciones(payload)


def _archiva_acciones(payload):
    """El reporte del agente de ACCIONES → `Reportes/<TICKER>/<fecha>/`.

    Best-effort y silencioso salvo en el log: un fallo del almacén no puede
    tumbar un análisis que el usuario ya está viendo en pantalla. Pero se
    registra, porque un archivo que no se escribe es un reporte que se pierde.
    """
    try:
        import vertex_archivo as _ar

        tk = (payload or {}).get("ticker") or (payload or {}).get("symbol")
        if not tk:
            return
        _ar.guarda_reporte_acciones(tk, payload)
    except Exception as e:                       # noqa: BLE001
        logging.getLogger(__name__).warning(
            "no se pudo archivar el reporte de acciones: %s", e)

def get_prior_report(ticker, exclude_id=None):
    """Most recent PRIOR report for a ticker (for the agent's long-term memory)."""
    try:
        conn = _db()
        if exclude_id:
            row = conn.execute("SELECT * FROM reports WHERE ticker=? AND report_id!=? ORDER BY created_ts DESC LIMIT 1",
                               (ticker, exclude_id)).fetchone()
        else:
            row = conn.execute("SELECT * FROM reports WHERE ticker=? ORDER BY created_ts DESC LIMIT 1",
                               (ticker,)).fetchone()
        conn.close()
        return dict(row) if row else None
    except Exception as e:
        print(f"[DB] read error: {e}")
        return None


def get_recent_reports(ticker, n=6, exclude_id=None):
    """#3 — Últimos N reportes del ticker (memoria profunda, no solo el último)."""
    try:
        conn = _db()
        if exclude_id:
            rows = conn.execute("SELECT * FROM reports WHERE ticker=? AND report_id!=? ORDER BY created_ts DESC LIMIT ?",
                                (ticker, exclude_id, n)).fetchall()
        else:
            rows = conn.execute("SELECT * FROM reports WHERE ticker=? ORDER BY created_ts DESC LIMIT ?",
                                (ticker, n)).fetchall()
        conn.close()
        return [dict(r) for r in rows]
    except Exception as e:
        print(f"[DB] read error: {e}")
        return []


def _deep_memory_block(ticker, current_price, exclude_id=None):
    """#3 — Bloque de memoria para el prompt: las llamadas previas del agente en este ticker
    CON su resultado ya madurado (precio hoy vs precio/target de entonces). Convierte la
    'memoria' de un solo reporte previo en aprendizaje real sobre su propio acierto histórico."""
    reps = get_recent_reports(ticker, n=6, exclude_id=exclude_id)
    if not reps:
        return "", None
    cur = _safe_num(current_price)
    lines, hits, n_scored = [], 0, 0
    for r in reps:
        base = r.get("price_at_analysis")
        rec = _reco_norm(r.get("recommendation")) or "?"
        when = (r.get("created_at") or "")[:10]
        conv = r.get("conviction")
        seg = f"  • {when}: {rec} (conv {conv}) @ ${base}"
        if base and cur:
            ret = (cur - base) / base * 100
            hit = _dir_hit(rec, ret)
            n_scored += 1
            hits += 1 if hit else 0
            tb = r.get("target_base")
            tgt = f", target base ${tb}" if tb else ""
            seg += f" → hoy ${round(cur,2)} ({ret:+.1f}%, {'ACERTÓ' if hit else 'FALLÓ'}{tgt})"
        lines.append(seg)
    hr = round(100 * hits / n_scored, 0) if n_scored else None
    header = (f"MEMORIA DEL AGENTE EN {ticker} — tus {len(reps)} llamadas previas"
              + (f" (acierto direccional histórico {hr:.0f}% en {n_scored} maduradas)" if hr is not None else "")
              + ":\n" + "\n".join(lines)
              + "\nUsa esto para mantener coherencia: si cambias de tesis vs tu llamada previa, justifícalo explícitamente.")
    meta = {"n_prior": len(reps), "hist_hit_rate": hr, "n_scored": n_scored}
    return header, meta


def get_open_options(ticker):
    """#3 — Posiciones de OPCIONES abiertas del usuario sobre este subyacente (option_holdings)."""
    try:
        conn = _db()
        rows = conn.execute("SELECT * FROM option_holdings WHERE UPPER(underlying)=? ORDER BY expiry ASC",
                            (str(ticker).upper(),)).fetchall()
        conn.close()
        return [dict(r) for r in rows]
    except Exception:
        return []


def _open_options_block(ticker):
    """#3 — Bloque para el prompt con las opciones que el usuario YA tiene en este ticker, con su P&L,
    para que el agente recomiende GESTIÓN (rolar/cerrar/promediar), no solo abrir nuevo."""
    pos = get_open_options(ticker)
    if not pos:
        return "", []
    lines = []
    for p in pos:
        typ = (p.get("option_type") or "?").upper()
        strike = p.get("strike"); exp = (p.get("expiry") or "")[:10]
        qty = p.get("contracts"); avg = p.get("avg_price") or p.get("price")
        cur = p.get("price"); val = p.get("value")
        pnl = ""
        if avg and cur and avg > 0:
            pnl = f", P&L {((cur-avg)/avg*100):+.0f}%"
        lines.append(f"  • {typ} ${strike} exp {exp} ×{qty} (entrada ${avg}, hoy ${cur}{pnl}, valor ${val})")
    block = ("POSICIONES DE OPCIONES ABIERTAS DEL USUARIO EN " + str(ticker).upper() + ":\n"
             + "\n".join(lines)
             + "\nTu recomendación DEBE contemplar qué hacer con estas posiciones (mantener/rolar/cerrar/promediar), "
               "no solo si abrir nuevas. Si la tesis cambió, di explícitamente qué hacer con lo abierto.")
    return block, pos


def _init_portfolio_snapshot_db():
    try:
        conn = _db()
        conn.execute("""CREATE TABLE IF NOT EXISTS portfolio_holdings (
            ticker      TEXT PRIMARY KEY,
            name        TEXT,
            value       REAL,
            cost_basis  REAL,
            account_key TEXT,
            updated_at  TEXT
        )""")
        try:
            conn.execute("ALTER TABLE portfolio_holdings ADD COLUMN cost_basis REAL")
        except Exception:
            pass
        conn.execute("""CREATE TABLE IF NOT EXISTS option_holdings (
            id          TEXT PRIMARY KEY,
            underlying  TEXT,
            option_type TEXT,
            strike      REAL,
            expiry      TEXT,
            contracts   REAL,
            price       REAL,
            avg_price   REAL,
            value       REAL,
            updated_at  TEXT
        )""")
        conn.commit(); conn.close()
    except Exception as e:
        print(f"[DB] portfolio table init error: {e}")

_init_portfolio_snapshot_db()

def _init_signal_history_db():
    """Daily snapshots of the full signal set per ticker → forward backtesting of
    confluence direction and projection-target hit-rate."""
    try:
        conn = _db()
        conn.execute("""CREATE TABLE IF NOT EXISTS signal_snapshots (
            id              INTEGER PRIMARY KEY AUTOINCREMENT,
            ticker          TEXT NOT NULL,
            snap_date       TEXT NOT NULL,
            spot            REAL,
            confl_verdict   TEXT,
            confl_direction TEXT,
            confl_score     REAL,
            conv_bias       TEXT,
            conv_strength   REAL,
            net_premium     REAL,
            dark_bias       TEXT,
            call_wall       REAL,
            put_wall        REAL,
            gamma_flip      REAL,
            targets_json    TEXT,
            created_at      TEXT,
            UNIQUE(ticker, snap_date)
        )""")
        conn.commit(); conn.close()
    except Exception as e:
        print(f"[DB] signal history table init error: {e}")

_init_signal_history_db()

def save_portfolio_snapshot(positions, account_key="ALL"):
    """Replace the stored single-user equity book snapshot so the per-stock
    agent can be portfolio-aware."""
    try:
        conn = _db()
        conn.execute("DELETE FROM portfolio_holdings")
        now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        for p in positions:
            conn.execute(
                "INSERT OR REPLACE INTO portfolio_holdings (ticker,name,value,cost_basis,account_key,updated_at) VALUES (?,?,?,?,?,?)",
                (p["ticker"], p.get("name", p["ticker"]), float(p.get("value") or 0),
                 (float(p["cost_basis"]) if p.get("cost_basis") not in (None, "") else None),
                 account_key, now))
        conn.commit(); conn.close()
    except Exception as e:
        print(f"[DB] snapshot save error: {e}")

def get_portfolio_snapshot():
    """Return the stored equity book as list of {ticker,name,value}."""
    try:
        conn = _db()
        rows = conn.execute("SELECT ticker,name,value,cost_basis FROM portfolio_holdings ORDER BY value DESC").fetchall()
        conn.close()
        return [dict(r) for r in rows]
    except Exception as e:
        print(f"[DB] snapshot read error: {e}")
        return []

def save_options_snapshot(options):
    """Replace the stored option-positions snapshot (modular: cualquier fuente —
    Plaid hoy, o `/api/portfolio/import` — alimenta el mismo motor de griegas)."""
    try:
        conn = _db()
        conn.execute("DELETE FROM option_holdings")
        now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        for i, o in enumerate(options):
            oid = f"{o['underlying']}|{o['option_type']}|{o['strike']}|{o['expiry']}|{i}"
            conn.execute(
                "INSERT OR REPLACE INTO option_holdings "
                "(id,underlying,option_type,strike,expiry,contracts,price,avg_price,value,updated_at) "
                "VALUES (?,?,?,?,?,?,?,?,?,?)",
                (oid, o["underlying"], o["option_type"], float(o["strike"]), o["expiry"],
                 float(o.get("contracts") or 0), float(o.get("price") or 0),
                 (float(o["avg_price"]) if o.get("avg_price") not in (None, "") else None),
                 float(o.get("value") or 0), now))
        conn.commit(); conn.close()
    except Exception as e:
        print(f"[DB] options snapshot save error: {e}")

def get_options_snapshot():
    """Return the stored option book as a flat list of normalized positions."""
    try:
        conn = _db()
        rows = conn.execute(
            "SELECT underlying,option_type,strike,expiry,contracts,price,avg_price,value "
            "FROM option_holdings").fetchall()
        conn.close()
        return [dict(r) for r in rows]
    except Exception as e:
        print(f"[DB] options snapshot read error: {e}")
        return []

def build_memory_block(prior, current_price):
    """Text block injected into the Gemini prompt so the agent explains how its view evolved."""
    if not prior:
        return ""
    days_ago = int((datetime.now().timestamp() - prior.get("created_ts", 0)) / 86400)
    pv = prior.get("price_at_analysis")
    change = None
    if pv and current_price:
        try: change = round(((current_price - pv) / pv) * 100, 1)
        except Exception: change = None
    lines = [
        "",
        f"MEMORIA DEL AGENTE (tu propio analisis anterior de {prior.get('ticker')}):",
        f"- Hace {days_ago} dias ({prior.get('created_at')}) tu Fair Value fue ${prior.get('fair_value')}, "
        f"recomendacion {prior.get('recommendation')}, conviccion {prior.get('conviction')}/100.",
        f"- El precio entonces era ${pv}" + (f" (cambio {change:+}% hasta hoy)." if change is not None else "."),
        "INSTRUCCION DE MEMORIA: En 'tesis_inversion_completa' y 'recomendacion_porque', explica EXPLICITAMENTE "
        "como ha evolucionado tu vision desde ese reporte: si subiste o bajaste tu Fair Value y por que, si tu "
        "recomendacion cambia o se mantiene, y que datos nuevos lo justifican. Habla como un CIO que recuerda su tesis previa.",
    ]
    return "\n".join(lines)

def compute_memory_comparison(prior, current_price, current_fair, current_rec, current_conv):
    """Structured prior-vs-now comparison for the UI memory card."""
    if not prior:
        return {"has_prior": False}
    pv = prior.get("price_at_analysis")
    fv = prior.get("fair_value")
    def pct(a, b):
        try: return round(((a - b) / b) * 100, 1) if (a is not None and b) else None
        except Exception: return None
    return {
        "has_prior": True,
        "prior_date": prior.get("created_at"),
        "days_ago": int((datetime.now().timestamp() - prior.get("created_ts", 0)) / 86400),
        "prior_price": pv,
        "prior_fair_value": fv,
        "prior_recommendation": prior.get("recommendation"),
        "prior_conviction": prior.get("conviction"),
        "current_price": current_price,
        "current_fair_value": current_fair,
        "current_recommendation": current_rec,
        "current_conviction": current_conv,
        "price_change_pct": pct(current_price, pv),
        "fair_value_change_pct": pct(current_fair, fv),
        "recommendation_changed": (prior.get("recommendation") != current_rec),
    }


# ─────────────────────────────────────────────────────────────────────────────
# MULTI-MODEL CASCADE — 3rd-model arbiter (OpenAI if available, else Gemini Pro)
# ─────────────────────────────────────────────────────────────────────────────
OPENAI_API_KEY = os.environ.get("OPENAI_API_KEY", "")  # optional independent tiebreaker





# ─────────────────────────────────────────────────────────────────────────────
# INSTITUTIONAL TARGET ENGINE — DCF + Comparables + Technical Levels
# ─────────────────────────────────────────────────────────────────────────────

def calculate_institutional_targets(ticker: str, info: dict, hist) -> dict:
    try:
        closes = hist['Close'].values.astype(float)
        highs  = hist['High'].values.astype(float)
        lows   = hist['Low'].values.astype(float)
        price  = float(closes[-1])

        # ── 1. VOLATILIDAD HISTÓRICA ──────────────────────────
        if len(closes) > 5:
            log_returns = np.diff(np.log(closes))
            daily_vol   = float(np.std(log_returns))
            annual_vol  = daily_vol * math.sqrt(252)
        else:
            daily_vol  = 0.015
            annual_vol = 0.25

        # ── 2. ATR (Average True Range) ─────────
        if len(closes) > 14:
            trs = []
            for i in range(1, min(15, len(closes))):
                tr = max(highs[i] - lows[i], abs(highs[i] - closes[i-1]), abs(lows[i]  - closes[i-1]))
                trs.append(tr)
            atr = float(np.mean(trs))
        else:
            atr = price * 0.02

        # ── 3. ANCLA POR MÚLTIPLO (NO es un DCF) ─────────────────────────────
        # eps × P/E objetivo. Antes se publicaba como "Vertex DCF"/"DCF Fair Value" y
        # contradecía al valor intrínseco del reporte. El ÚNICO DCF del sistema es el
        # FCFF del especialista de valuación de Victor (sc["victor_valuation"]).
        # Esto sobrevive solo como ancla interna de los targets de respaldo, para cuando
        # el modelo de Victor devuelve not_scorable. Nunca se muestra ni se llama DCF.
        eps          = info.get("trailingEps") or info.get("forwardEps") or (price / 25)
        fwd_pe       = info.get("forwardPE") or info.get("trailingPE") or 22
        revenue_growth = info.get("revenueGrowth") or 0.12
        target_pe_base = min(max(float(fwd_pe) * (1 + revenue_growth * 0.5), 15), 80)
        pe_anchor = float(eps) * target_pe_base

        # ── 4. ANALYST CONSENSUS ANCHOR ─────────────────────────────────────
        analyst_high   = info.get("targetHighPrice")  or (price * 1.35)
        analyst_low    = info.get("targetLowPrice")   or (price * 0.75)
        analyst_mean   = info.get("targetMeanPrice")  or (price * 1.12)
        analyst_median = info.get("targetMedianPrice") or analyst_mean

        # ── 5. TARGETS POR TIMEFRAME ANCLADOS A VOLATILIDAD ─────────────
        
        # 7D
        sigma_7d = daily_vol * math.sqrt(7)
        bull_7d  = price * (1 + 1.2 * sigma_7d)
        bear_7d  = price * (1 - 1.2 * sigma_7d)
        base_7d  = price * (1 + 0.3 * sigma_7d)
        
        # 30D
        sigma_30d = daily_vol * math.sqrt(21)
        bull_30d  = price * (1 + 1.5 * sigma_30d) 
        bear_30d  = price * (1 - 1.5 * sigma_30d)
        base_30d  = price * (1 + 0.4 * sigma_30d)

        # 3M
        sigma_3m = annual_vol * math.sqrt(63/252)
        bull_3m  = price * (1 + 1.6 * sigma_3m)
        bear_3m  = price * (1 - 1.6 * sigma_3m)
        base_3m  = (price * (1 + revenue_growth * 0.25) + analyst_median * 0.3) / 1.3

        # 6M
        sigma_6m = annual_vol * math.sqrt(126/252)
        bull_6m  = min(price * (1 + 1.8 * sigma_6m), float(analyst_high) * 1.05)
        bear_6m  = max(price * (1 - 1.8 * sigma_6m), float(analyst_low) * 0.95)
        base_6m  = (pe_anchor * 0.5 + float(analyst_mean) * 0.5)
        base_6m  = (base_6m + price * (1 + 0.5 * sigma_6m)) / 2

        # 12M
        sigma_12m = annual_vol
        bull_12m  = (price * (1 + 2.0 * sigma_12m) * 0.5 + float(analyst_high) * 0.5)
        bear_12m  = (price * (1 - 1.5 * sigma_12m) * 0.5 + float(analyst_low)  * 0.5)
        base_12m  = (pe_anchor * 0.5 + float(analyst_median) * 0.5)
        bull_12m  = max(bull_12m, base_12m * 1.10)
        bear_12m  = min(bear_12m, base_12m * 0.88)

        def rnd(v): return round(float(v), 2)

        return {
            "methodology": {
                "annual_volatility_pct": round(annual_vol * 100, 2),
                "daily_vol_pct": round(daily_vol * 100, 3),
                "atr_14": rnd(atr),
                "analyst_high": rnd(analyst_high),
                "analyst_low": rnd(analyst_low),
                "analyst_mean": rnd(analyst_mean),
                "analyst_median": rnd(analyst_median),
            },
            "targets": {
                "7d":  {"bull": rnd(bull_7d),  "base": rnd(base_7d),  "bear": rnd(bear_7d)},
                "30d": {"bull": rnd(bull_30d), "base": rnd(base_30d), "bear": rnd(bear_30d)},
                "3m":  {"bull": rnd(bull_3m),  "base": rnd(base_3m),  "bear": rnd(bear_3m)},
                "6m":  {"bull": rnd(bull_6m),  "base": rnd(base_6m),  "bear": rnd(bear_6m)},
                "12m": {"bull": rnd(bull_12m), "base": rnd(base_12m), "bear": rnd(bear_12m)},
            }
        }
    except Exception as e:
        price_f = float(hist['Close'].iloc[-1]) if not hist.empty else 100.0
        return {
            "methodology": {"error": _error_publico(e, "operacion")},
            "targets": {
                "7d":  {"bull": round(price_f*1.02,2), "base": round(price_f*1.005,2), "bear": round(price_f*0.98,2)},
                "30d": {"bull": round(price_f*1.06,2), "base": round(price_f*1.02,2),  "bear": round(price_f*0.94,2)},
                "3m":  {"bull": round(price_f*1.12,2), "base": round(price_f*1.05,2),  "bear": round(price_f*0.90,2)},
                "6m":  {"bull": round(price_f*1.20,2), "base": round(price_f*1.09,2),  "bear": round(price_f*0.84,2)},
                "12m": {"bull": round(price_f*1.35,2), "base": round(price_f*1.14,2),  "bear": round(price_f*0.75,2)},
            }
        }


# ─────────────────────────────────────────────────────────────────────────────
# SCHEMA
# ─────────────────────────────────────────────────────────────────────────────

# (SignalDim/SignalScores eliminadas: las 7 señales del LLM se retiraron — quien puntúa
#  es el scorecard de los 6 agentes de Victor.)

class TradeProbabilities(BaseModel):
    p_positive_12m: int = Field(..., description="Probabilidad 0-100 de un retorno positivo (>0%) a 12 meses, anclada en base-rates y la evidencia, no en optimismo.")
    p_touch_bull_12m: int = Field(..., description="Probabilidad 0-100 de que el precio toque el target Bull a 12m.")
    p_touch_bear_12m: int = Field(..., description="Probabilidad 0-100 de que el precio toque el target Bear a 12m.")
    p_up_10pct_3m: int = Field(..., description="Probabilidad 0-100 de un retorno >+10% en los próximos 3 meses.")
    rationale: str = Field(..., description="Una o dos líneas anclando estas probabilidades en base-rates históricos (cuántas veces movimientos así ocurren) y la evidencia concreta, evitando sobreconfianza.")

class VertexDeepAnalysis(BaseModel):
    biggest_pro: str = Field(..., description="El pro más grande y determinante para el crecimiento de la empresa.")
    biggest_risk: str = Field(..., description="El riesgo de ejecución o macroeconómico más severo en una oración.")
    watch_for: str = Field(..., description="Métrica, nivel clave o evento específico que se debe monitorear a corto plazo.")
    company_summary_simple: str = Field(..., description="Resumen de la compañía en palabras simples, qué hace, cómo gana dinero y por qué importa al inversor promedio.")
    analisis_numeros_actuales: str = Field(..., description="Resumen analítico de la capitalización de mercado y situación financiera actual.")
    crecimiento_yoy: str = Field(..., description="Detalle del comportamiento histórico reciente año tras año de ingresos y márgenes operativos.")
    crecimiento_proyectado: str = Field(..., description="Estimaciones de crecimiento para los próximos años impulsados por catalizadores de negocio.")
    sec_filing_10k: str = Field(..., description="Análisis profundo de los factores de riesgo y estados auditados declarados en el último reporte anual 10-K.")
    sec_filing_10q: str = Field(..., description="Análisis del rendimiento y balances del último reporte trimestral 10-Q.")
    sec_filing_8k: str = Field(..., description="Resumen de eventos materiales o comunicados urgentes reportados recientemente en el 8-K.")
    fair_value: float = Field(..., description="Valor justo esperado calculado matemáticamente en base al promedio ponderado de los targets a 1 año de Vertex y el precio objetivo medio de Wall Street.")
    upside_pct: float = Field(..., description="Porcentaje de crecimiento proyectado a 1 año desde el precio spot actual hasta el Fair Value futuro.")
    recommendation: str = Field(..., description="Recomendación (BUY, HOLD, SELL o AVOID). NOTA: la recomendación FINAL la fija el gate determinista de los 6 agentes de Victor y sobrescribe este campo; escríbela coherente con el veredicto que se te dio, nunca lo contradigas.")
    conviccion_score: int = Field(..., description="Puntuación de convicción 0-100. NOTA: se sobrescribe con el raw score de los 6 agentes de Victor. No existe un motor de convicción ponderado; repite el puntaje que se te dio.")
    conviccion_porque: str = Field(..., description="Justificación del puntaje de convicción, explicando de qué agentes de Victor sale (business, financial, market, technical, risk, valuation) y qué lo sube o lo baja.")
    recomendacion_porque: str = Field(..., description="Explicación detallada de la acción sugerida y la lógica financiera basada en proyecciones futuras.")
    tesis_inversion_completa: str = Field(..., description="Tesis completa de inversión de la AI explicando detalladamente por qué es o no una buena asignación de capital.")
    tesis_riesgos: str = Field(..., description="Explicación y desglose analítico de los riesgos inherentes que podrían destruir la tesis.")
    analistas_consenso: str = Field(..., description="Qué dice el consenso de Wall Street, CONTRASTADO con el veredicto de Victor: dónde coinciden, dónde divergen y qué explicaría la diferencia. Contexto, no conclusión.")
    calculos_y_crecimiento_ai: str = Field(..., description="Aritmética REAL del reporte paso a paso: fórmula de los targets de Victor (EPS, crecimiento, múltiplo), el Fair Value como su target base, el DCF FCFF con su WACC, y qué agente empuja el puntaje. Nunca un cálculo inventado.")
    posicion_competitiva: str = Field(..., description="Evaluación cualitativa del Moat o ventaja competitiva frente a rivales.")
    principales_competidores: str = Field(..., description="Lista de los principales competidores de la industria.")
    porque_mejor_peor_inversion: str = Field(..., description="Justificación exacta comparando márgenes, retornos de capital y múltiplos.")
    in_simple_terms: str = Field(..., description="Analogía simplificada del negocio para cualquier tipo de inversor.")
    should_you_buy_now: str = Field(..., description="Resumen definitivo de prudencia o compra agresiva en los niveles de cotización actuales.")
    the_bottom_line: str = Field(..., description="Conclusión ejecutiva final en una sola oración.")
    probabilities: TradeProbabilities = Field(..., description="Probabilidades calibradas del trade (positivo 12m, toca bull/bear, +10% en 3m) ancladas en base-rates. Se usan para dimensionar la posición vía Kelly fraccional.")

class BullCase(BaseModel):
    thesis: str = Field(..., description="La tesis ALCISTA más fuerte posible para esta acción, el steelman del caso comprador.")
    catalysts: str = Field(..., description="3-5 catalizadores concretos (lista en texto) que impulsarían la acción al alza.")
    why_underappreciated: str = Field(..., description="Por qué el mercado está subestimando esta oportunidad ahora mismo.")
    strongest_point: str = Field(..., description="El argumento alcista MÁS fuerte e irrefutable en una oración.")

class BearCase(BaseModel):
    thesis: str = Field(..., description="La tesis BAJISTA más fuerte posible, el steelman del caso vendedor/escéptico.")
    risks: str = Field(..., description="3-5 riesgos o señales de alarma concretos (lista en texto) que destruirían la tesis alcista.")
    what_breaks_it: str = Field(..., description="El escenario específico que rompe la tesis y cuánto downside implica.")
    strongest_point: str = Field(..., description="El argumento bajista MÁS fuerte e irrefutable en una oración.")

class DebateVerdict(BaseModel):
    winner: str = Field(..., description="Quién tiene el caso más fuerte: 'TORO', 'OSO' o 'EMPATE'.")
    lean: str = Field(..., description="Recomendación reconciliada final: BUY, HOLD, SELL o AVOID.")
    confidence: int = Field(..., description="Confianza 0-100 en el veredicto reconciliado, calibrada y honesta.")
    key_disagreement: str = Field(..., description="El punto central donde toro y oso discrepan — la verdadera variable que decide el trade.")
    what_would_flip: str = Field(..., description="La evidencia específica y observable que cambiaría tu recomendación (ej: 'pasa a SELL si el guidance de Q próximo baja >10%').")
    synthesis: str = Field(..., description="Síntesis equilibrada que reconcilia ambos casos en un veredicto accionable, sin sesgo de confirmación.")
    p_bull_correct: int = Field(..., description="Probabilidad 0-100 de que el caso TORO resulte correcto a 12 meses, anclada en base-rates.")

# Modelos para la sección Explore — «descubrir empresas»
class DiscoveredCompany(BaseModel):
    ticker: str
    name: str
    revenue: float
    growth: float
    margin: float
    score10: float
    evidence: int | None = None
    price: float | None = None
    target_base: float | None = None
    upside_base: float | None = None

class ExploreResponse(BaseModel):
    companies: list[DiscoveredCompany]
    prefilter: dict
    # Sin declararlo aqui, FastAPI lo descarta del cuerpo: el response_model
    # filtra. Un campo que la ruta pone y el modelo no nombra no llega nunca.
    metodologia: dict
    generated_at: str


def get_clean_domain(url_string):
    if not url_string: return ""
    try:
        domain = urlparse(url_string).netloc.replace("www.", "")
        return domain
    except:
        return ""

def format_volume(vol):
    if not vol: return "N/A"
    if vol >= 1e9: return f"{vol/1e9:.2f}B"
    if vol >= 1e6: return f"{vol/1e6:.2f}M"
    if vol >= 1e3: return f"{vol/1e3:.2f}K"
    return str(vol)

def obtener_logo(ticker, website):
    domain = get_clean_domain(website)
    if domain:
        return f"https://logo.clearbit.com/{domain}"
    return f"https://ui-avatars.com/api/?name={ticker}&background=0B0E14&color=3b82f6&font-size=0.4&bold=true"


# ─────────────────────────────────────────────────────────────────────────────
# REDDIT + EARNINGS HELPERS (real-time context for the AI agent)
# ─────────────────────────────────────────────────────────────────────────────
def fetch_reddit_posts(ticker, limit=8):
    """Fetch real recent Reddit posts mentioning the ticker from finance subreddits.
    Uses Reddit public JSON API (no auth). Filters to last 90 days and sorts by
    upvotes so the AI gets the most credible/visible community discussion as context."""
    posts = []
    try:
        subreddits = "wallstreetbets+stocks+investing+options+StockMarket"
        url = f"https://www.reddit.com/r/{subreddits}/search.json"
        params = {
            "q": ticker,
            "restrict_sr": "on",
            "sort": "top",
            "t": "month",
            "limit": max(limit * 2, 16),
        }
        headers = {"User-Agent": "VertexFundOS/1.0 (market sentiment research)"}
        resp = requests.get(url, params=params, headers=headers, timeout=15)
        if resp.status_code == 200:
            data = resp.json()
            cutoff = (datetime.now() - timedelta(days=90)).timestamp()
            for child in data.get("data", {}).get("children", []):
                p = child.get("data", {})
                created = p.get("created_utc", 0) or 0
                if created < cutoff:
                    continue
                posts.append({
                    "title":        (p.get("title", "") or "")[:240],
                    "text":         (p.get("selftext", "") or "")[:400],
                    "subreddit":    p.get("subreddit", ""),
                    "upvotes":      p.get("ups", 0) or 0,
                    "num_comments": p.get("num_comments", 0) or 0,
                    "date":         datetime.fromtimestamp(created).strftime("%Y-%m-%d"),
                })
            posts.sort(key=lambda x: x["upvotes"], reverse=True)
            posts = posts[:limit]
    except Exception:
        pass
    return posts


def format_reddit_context(posts):
    """Format Reddit posts into a literal text block to feed the AI as real context."""
    if not posts:
        return ""
    lines = []
    for p in posts:
        snippet = f" — {p['text']}" if p.get("text") else ""
        lines.append(
            f"[r/{p['subreddit']} · {p['date']} · {p['upvotes']} upvotes · "
            f"{p['num_comments']} comentarios] {p['title']}{snippet}"
        )
    return "\n".join(lines)


def fetch_earnings_info(stock, info):
    """Next earnings date, days until, and EPS estimate via yfinance calendar.
    Handles both dict (newer yfinance) and DataFrame (older) calendar formats."""
    out = {"next_date": None, "days_until": None, "eps_estimate": None, "label": "N/A"}
    try:
        next_date = None
        eps_est   = None
        cal = None
        try:
            cal = stock.calendar
        except Exception:
            cal = None

        if isinstance(cal, dict) and cal:
            ed = cal.get("Earnings Date")
            if isinstance(ed, (list, tuple)) and ed:
                next_date = ed[0]
            elif ed:
                next_date = ed
            eps_est = cal.get("Earnings Average") or cal.get("EPS Estimate")
        elif cal is not None and hasattr(cal, "loc"):
            try:
                next_date = cal.loc["Earnings Date"][0]
            except Exception:
                pass

        if eps_est in (None, ""):
            eps_est = info.get("forwardEps")

        nd = None
        if next_date is not None:
            try:
                if isinstance(next_date, str):
                    nd = datetime.strptime(next_date[:10], "%Y-%m-%d")
                elif hasattr(next_date, "year"):
                    nd = datetime(next_date.year, next_date.month, next_date.day)
            except Exception:
                nd = None

        if nd:
            days = (nd.date() - datetime.now().date()).days
            _meses = ["enero","febrero","marzo","abril","mayo","junio","julio",
                      "agosto","septiembre","octubre","noviembre","diciembre"]
            out["next_date"]    = nd.strftime("%Y-%m-%d")
            out["days_until"]   = days
            out["eps_estimate"] = round(float(eps_est), 2) if eps_est not in (None, "") else None
            out["label"]        = f"{nd.day} de {_meses[nd.month-1]} de {nd.year}"
    except Exception:
        pass
    return out



# ─────────────────────────────────────────────────────────────────────────────
# SEC EDGAR — Insider transactions (Form 4) + Institutional holders (13F)
# ─────────────────────────────────────────────────────────────────────────────
def fetch_earnings_history(stock, lookback=8):
    """#2 — Historial real de earnings para dar profundidad a la señal (20%): cuántos beats/misses,
    sorpresa media de EPS, y reacción del precio post-earnings (±% medio y sesgo). yfinance
    get_earnings_dates trae EPS estimado/reportado/sorpresa; el movimiento se calcula de la historia."""
    out = {"n": 0, "beats": 0, "misses": 0, "avg_surprise_pct": None,
           "avg_abs_move_pct": None, "avg_move_pct": None, "rows": []}
    try:
        ed = stock.get_earnings_dates(limit=24)
    except Exception:
        ed = None
    if ed is None or getattr(ed, "empty", True):
        return out
    closes = []
    try:
        px = stock.history(period="2y")
        if px is not None and not px.empty:
            try:
                idx = px.index.tz_localize(None)
            except (TypeError, AttributeError):
                idx = px.index
            closes = list(zip(list(idx), [float(c) for c in px["Close"].tolist()]))
    except Exception:
        closes = []

    def _move(ed_date):
        try:
            before = [c for (d, c) in closes if d.date() <= ed_date]
            after = [c for (d, c) in closes if d.date() > ed_date]
            if before and after:
                c0 = before[-1]; c1 = after[min(1, len(after) - 1)]
                if c0 > 0:
                    return round((c1 - c0) / c0 * 100, 1)
        except Exception:
            pass
        return None

    surprises, moves, rows = [], [], []
    for dt, r in ed.iterrows():
        try:
            rep = r.get("Reported EPS")
            if rep is None or (isinstance(rep, float) and math.isnan(rep)):
                continue                                   # futuro / aún sin reportar
            est = r.get("EPS Estimate")
            est_ok = est is not None and not (isinstance(est, float) and math.isnan(est))
            sp = r.get("Surprise(%)")
            spv = None if (sp is None or (isinstance(sp, float) and math.isnan(sp))) else round(float(sp), 1)
            beat = bool(float(rep) >= float(est)) if est_ok else None
            ed_date = dt.date() if hasattr(dt, "date") else None
            mv = _move(ed_date) if ed_date else None
            if spv is not None:
                surprises.append(spv)
            if mv is not None:
                moves.append(mv)
            if beat is True:
                out["beats"] += 1
            elif beat is False:
                out["misses"] += 1
            rows.append({"date": ed_date.strftime("%Y-%m-%d") if ed_date else str(dt)[:10],
                         "reported": round(float(rep), 2), "estimate": (round(float(est), 2) if est_ok else None),
                         "surprise_pct": spv, "beat": beat, "move_pct": mv})
            if len(rows) >= lookback:
                break
        except Exception:
            continue
    out["n"] = len(rows)
    out["rows"] = rows
    if surprises:
        out["avg_surprise_pct"] = round(sum(surprises) / len(surprises), 1)
    if moves:
        out["avg_abs_move_pct"] = round(sum(abs(m) for m in moves) / len(moves), 1)
        out["avg_move_pct"] = round(sum(moves) / len(moves), 1)
    return out


def _earnings_depth_block(eh, einfo):
    """Bloque de prompt para la señal de earnings con datos reales."""
    if not eh or not eh.get("n"):
        return ""
    parts = [f"{eh['beats']}/{eh['n']} beats en los últimos {eh['n']} reportes"]
    if eh.get("avg_surprise_pct") is not None:
        parts.append(f"sorpresa media de EPS {eh['avg_surprise_pct']:+.1f}%")
    if eh.get("avg_abs_move_pct") is not None:
        parts.append(f"reacción del precio ±{eh['avg_abs_move_pct']:.1f}% post-earnings "
                     f"(sesgo {eh.get('avg_move_pct', 0):+.1f}%)")
    nxt = ""
    if einfo and einfo.get("days_until") is not None:
        nxt = f" Próximo earnings en {einfo['days_until']}d"
        if einfo.get("eps_estimate") is not None:
            nxt += f" (EPS est. ${einfo['eps_estimate']})"
        nxt += "."
    return ("\nEARNINGS — HISTORIAL REAL (señal 20%): " + " · ".join(parts) + "." + nxt +
            " USO: calibra la señal de earnings con ESTO — beats consistentes con reacción alcista suben el "
            "score; misses o reacción negativa lo bajan. Si el próximo earnings cae dentro de tu horizonte, "
            "advierte el riesgo de IV crush en el plan de opciones.")


def _news_catalyst_context(noticias):
    """#3 — convierte titulares crudos en contexto con CATALIZADORES detectados (earnings, upgrades,
    guidance, M&A, SEC/8-K, demandas, productos) + resúmenes, en vez de solo títulos. Da más señal
    a la categoría News/SEC (10%) que una lista de titulares."""
    if not noticias:
        return "N/A", []
    cats = {
        "earnings": ["earnings", "eps", "results", "quarter", "beats", "misses", "guidance"],
        "rating": ["upgrade", "downgrade", "initiat", "price target", "overweight", "underweight", "buy rating", "sell rating"],
        "deal": ["acqui", "merger", "buyout", "stake", "deal", "partnership", "contract"],
        "regulatory": ["sec", "8-k", "lawsuit", "investigation", "probe", "antitrust", "fine", "settle"],
        "product": ["launch", "unveil", "approval", "fda", "recall", "chip", "ai "],
        "guidance": ["raises", "cuts", "outlook", "forecast", "warns"],
    }
    out_lines, tags_all = [], []
    for n in noticias[:6]:
        blob = (str(n.get("title", "")) + " " + str(n.get("summary", ""))).lower()
        tags = [c for c, kws in cats.items() if any(k in blob for k in kws)]
        tags_all.extend(tags)
        tag_s = f" [{'/'.join(tags)}]" if tags else ""
        summ = str(n.get("summary", "") or "")[:160]
        out_lines.append(f"· {n.get('title', '')}{tag_s}" + (f" — {summ}" if summ and summ != "No description available." else ""))
    uniq = sorted(set(tags_all))
    head = (f"Catalizadores detectados: {', '.join(uniq)}. " if uniq else "Sin catalizadores claros en titulares. ")
    return head + " ".join(out_lines), uniq


# A-01: identidad ÚNICA ante la SEC, la misma que usa el engine. Antes había dos
# hardcodeadas y distintas (esta y la de providers/edgar.py, con el correo de
# Victor), y `EDGAR_USER_AGENT` de render.yaml / API/.env no se leía en ninguna.
# La política de fair-access de la SEC bloquea POR user-agent: dos identidades
# significaba dos cuotas separadas, y una de ellas compartida con otro proyecto.
def _sec_user_agent():
    ua = (os.environ.get("EDGAR_USER_AGENT") or "").strip()
    if ua:
        return ua
    try:                       # respaldo: lo que el engine haya resuelto (API/.env)
        if _WBJ_ENGINE_PATH not in sys.path:
            sys.path.insert(0, _WBJ_ENGINE_PATH)
        from wbj.config import load_settings
        from wbj.providers.edgar import edgar_headers
        return edgar_headers(_engine_settings())["User-Agent"]
    except Exception:
        return "Vertex Fund OS research (configura EDGAR_USER_AGENT)"


SEC_HEADERS = {"User-Agent": _sec_user_agent(),
               "Accept-Encoding": "gzip, deflate", "Host": "www.sec.gov"}
_SEC_TICKER_CACHE = {}

def _get_sec_cik(ticker):
    """Map a ticker to its zero-padded 10-digit SEC CIK (cached after first load)."""
    global _SEC_TICKER_CACHE
    try:
        if not _SEC_TICKER_CACHE:
            r = requests.get("https://www.sec.gov/files/company_tickers.json",
                             headers={"User-Agent": SEC_HEADERS["User-Agent"]}, timeout=15)
            if r.status_code == 200:
                for _, row in r.json().items():
                    _SEC_TICKER_CACHE[str(row.get("ticker", "")).upper()] = str(row.get("cik_str", "")).zfill(10)
        return _SEC_TICKER_CACHE.get(ticker.upper())
    except Exception:
        return None


def _wbj_insiders_clasificados(ticker):
    """Compras/ventas de insiders YA CLASIFICADAS, vía el motor de Victor.

    yfinance dejo de poblar las columnas `Transaction` y `Text` de
    `insider_transactions`: vienen como cadena vacia. El clasificador de
    `fetch_insiders_data` decide compra o venta buscando "purchase"/"sale" en ese
    texto, asi que con la columna vacia NADA se clasificaba y el resumen salia
    0 compras / 0 ventas / senal NEUTRAL en todos los tickers -- aunque las
    transacciones si estaban ahi, frescas y materiales (una venta de $153M de un
    director de Apple el 2026-05-27).

    Eso rompia el punto 5 de CLAUDE.md, que pide marcar las operaciones de
    insiders por encima de $1M. El motor usa el `transactionType` de los Form 4
    de FMP, que si viene poblado.

    Devuelve {"bought": float, "sold": float, "trades": [...]} o {}.
    """
    try:
        if _WBJ_ENGINE_PATH not in sys.path:
            sys.path.insert(0, _WBJ_ENGINE_PATH)
        from wbj.config import load_settings
        from wbj.report import _insiders
        d = _insiders(str(ticker).upper().strip(), _engine_settings()) or {}
        if not d.get("available"):
            return {}
        return {"bought": float(d.get("bought") or 0.0),
                "sold": float(d.get("sold") or 0.0),
                "trades": d.get("trades") or []}
    except Exception:
        return {}


def _wbj_holders_from_edgar(ticker):
    """Tenedores >5% desde SEC EDGAR, vía el motor de Victor.

    Reemplaza el escaneo de `13F-HR` bajo el CIK de la empresa, que no podia
    funcionar: un 13F lo presenta el INVERSIONISTA, asi que el historial de
    presentaciones de Apple no contiene ninguno -- ese `form13f` salia vacio en
    todos los tickers, siempre, y no por el limite del plan.

    El motor invierte la busqueda como corresponde: resuelve el CUSIP del perfil
    y consulta las Schedule 13D/13G presentadas sobre ese CUSIP. Un 13D/G se
    presenta al cruzar el 5% de una clase, asi que su conjunto de presentantes ES
    el de los tenedores reconocidos por definicion -- que es lo que pide el punto
    4 de CLAUDE.md -- y cada fila trae el accession que DATA_POLICY.md exige como
    source_locator.

    Complementa, no reemplaza, la lista de yfinance: esa trae 10 tenedores con
    conteo de acciones y porcentaje del trimestre mas reciente; esta trae menos
    nombres y sin conteo, pero es tier 1 y auditable. Devuelve {} si falla.
    """
    try:
        if _WBJ_ENGINE_PATH not in sys.path:
            sys.path.insert(0, _WBJ_ENGINE_PATH)
        from wbj.config import load_settings
        from wbj.report import _ownership
        o = _ownership(str(ticker).upper().strip(), _engine_settings()) or {}
        return {"holders": o.get("holders") or [],
                "source": o.get("holders_source"),
                "executives": o.get("executives") or []}
    except Exception:
        return {}


#: Presentaciones de EDGAR ya resueltas, por (ticker, limite). Se rehacen
#: cada `_EDGAR_TTL_S`.
#:
#: `fetch_edgar_filings` recorre el conjunto trimestral 13F de la SEC, que es
#: un TSV enorme: ~24 s por llamada. Y /api/analyze la invocaba DOS VECES --
#: una para el contexto de insiders y otra para `mandatory_report.edgar` --,
#: o sea ~47 s de los ~55 s que tardaba una peticion con todo lo demas ya
#: cacheado. Las presentaciones no cambian entre esas dos llamadas.
_EDGAR_CACHE: dict[tuple, tuple] = {}
_EDGAR_TTL_S = 1800.0
_EDGAR_CACHE_MAX = 32


def _edgar_cache_put(clave: tuple, valor: dict) -> None:
    import copy as _cp
    if len(_EDGAR_CACHE) >= _EDGAR_CACHE_MAX:
        _EDGAR_CACHE.pop(next(iter(_EDGAR_CACHE)), None)
    _EDGAR_CACHE[clave] = (time.time(), _cp.deepcopy(valor))


def fetch_edgar_filings(ticker, limit=8):
    """Authoritative recent SEC filings for the company: Form 4 (insider) +
    tenedores >5% por Schedule 13D/13G (ver `_wbj_holders_from_edgar`).
    Returns direct EDGAR links so the user can click through to the source filing."""
    _clave = (str(ticker).upper(), int(limit))
    _guardado = _EDGAR_CACHE.get(_clave)
    if _guardado and (time.time() - _guardado[0]) < _EDGAR_TTL_S:
        import copy as _cp
        return _cp.deepcopy(_guardado[1])
    out = {"cik": None, "form4": [], "form13f": [], "holders_5pct": [], "holders_source": None}
    try:
        cik = _get_sec_cik(ticker)
        if not cik:
            return out
        out["cik"] = cik
        r = requests.get(f"https://data.sec.gov/submissions/CIK{cik}.json",
                         headers={"User-Agent": SEC_HEADERS["User-Agent"]}, timeout=15)
        if r.status_code != 200:
            return out
        recent = r.json().get("filings", {}).get("recent", {})
        forms  = recent.get("form", [])
        dates  = recent.get("filingDate", [])
        accns  = recent.get("accessionNumber", [])
        docs   = recent.get("primaryDocument", [])
        cik_int = int(cik)
        for i, frm in enumerate(forms):
            acc_clean = accns[i].replace("-", "") if i < len(accns) else ""
            doc = docs[i] if i < len(docs) else ""
            url = f"https://www.sec.gov/Archives/edgar/data/{cik_int}/{acc_clean}/{doc}" if doc else \
                  f"https://www.sec.gov/cgi-bin/browse-edgar?action=getcompany&CIK={cik}&type={frm}"
            entry = {"date": dates[i] if i < len(dates) else "", "form": frm, "url": url}
            if frm == "4" and len(out["form4"]) < limit:
                out["form4"].append(entry)
            # `13F-HR` ya NO se busca aqui: un 13F lo presenta el inversionista,
            # no la empresa, asi que este bucle nunca encontraba ninguno. Los
            # tenedores llegan por CUSIP mas abajo.
        tenedores = _wbj_holders_from_edgar(ticker)
        if tenedores.get("holders"):
            out["holders_5pct"] = tenedores["holders"][:limit]
            out["holders_source"] = tenedores.get("source")
        _edgar_cache_put(_clave, out)
        return out
    except Exception:
        return out


_8K_ITEMS = {
    "1.01": "acuerdo material (deal/contrato)", "1.02": "terminación de acuerdo material",
    "1.03": "bancarrota / receivership", "2.01": "completó adquisición o venta de activos",
    "2.02": "resultados de operación (earnings)", "2.03": "obligación financiera material (deuda)",
    "2.04": "aceleración de obligación financiera", "2.05": "costos por reestructuración/salida",
    "3.01": "aviso de delisting / incumplimiento de listado", "3.02": "venta no registrada de equity (posible dilución)",
    "4.01": "cambio de auditor", "4.02": "restatement (no confiar en EEFF previos)",
    "5.01": "cambio de control", "5.02": "cambio de directivos/junta (CEO/CFO/director)",
    "5.03": "cambio de estatutos / año fiscal", "5.07": "resultados de votación de accionistas",
    "7.01": "divulgación Reg FD", "8.01": "otro evento material",
    "9.01": "estados financieros y exhibits",
}

def fetch_recent_8k(ticker, lookback_days=45, limit=6):
    """8-K reales recientes de SEC EDGAR (submissions API) con sus ITEM codes traducidos a
    catalizadores. Da a la señal News/SEC (10%) eventos materiales VERIFICADOS (M&A, earnings,
    cambios de directivos, deuda, restatements) en vez de solo titulares de prensa."""
    out = []
    try:
        cik = _get_sec_cik(ticker)
        if not cik:
            return out
        r = requests.get(f"https://data.sec.gov/submissions/CIK{cik}.json",
                         headers={"User-Agent": SEC_HEADERS["User-Agent"]}, timeout=15)
        if r.status_code != 200:
            return out
        recent = r.json().get("filings", {}).get("recent", {})
        forms = recent.get("form", []); dates = recent.get("filingDate", [])
        items = recent.get("items", []); accns = recent.get("accessionNumber", [])
        docs = recent.get("primaryDocument", [])
        cik_int = int(cik)
        cutoff = (datetime.now() - timedelta(days=lookback_days)).date()
        for i, frm in enumerate(forms):
            if not str(frm).startswith("8-K"):
                continue
            try:
                fd = datetime.strptime(str(dates[i])[:10], "%Y-%m-%d").date()
            except Exception:
                continue
            if fd < cutoff:
                continue
            codes = [c.strip() for c in str(items[i] if i < len(items) else "").split(",") if c.strip()]
            human = [_8K_ITEMS.get(c, f"item {c}") for c in codes]
            acc = accns[i].replace("-", "") if i < len(accns) else ""
            doc = docs[i] if i < len(docs) else ""
            url = (f"https://www.sec.gov/Archives/edgar/data/{cik_int}/{acc}/{doc}" if (acc and doc)
                   else f"https://www.sec.gov/cgi-bin/browse-edgar?action=getcompany&CIK={cik}&type=8-K")
            out.append({"date": fd.strftime("%Y-%m-%d"), "form": frm, "items": codes,
                        "items_desc": human, "url": url})
            if len(out) >= limit:
                break
    except Exception:
        return out
    return out


def _8k_catalyst_block(filings):
    """Bloque de prompt + tags de catalizador desde 8-K reales (señal News/SEC)."""
    if not filings:
        return "", []
    tags = sorted({d for f in filings for d in f.get("items_desc", [])})
    lines = [f"· {f['date']}: {', '.join(f.get('items_desc') or []) or '8-K'}" for f in filings[:6]]
    block = ("\n8-K RECIENTES (SEC EDGAR — eventos materiales VERIFICADOS, señal News/SEC 10%): "
             + " ".join(lines) + ". USO: un 8-K de earnings/M&A/cambio de CEO/deuda/restatement es un "
             "catalizador DURO — pondéralo por encima de titulares de prensa.")
    return block, tags


def fetch_insiders_data(stock, ticker):
    """Combine yfinance insider trades + institutional (13F-derived) holders + SEC EDGAR
    filing links into one structured payload for the AI agent and the UI."""
    data = {
        "transactions": [], "summary": {}, "institutional": [],
        "major_holders": {}, "edgar": {"cik": None, "form4": [], "form13f": []},
    }
    # ── Insider transactions (yfinance) ──────────────────────────────────────
    try:
        it = stock.insider_transactions
        if it is not None and hasattr(it, "empty") and not it.empty:
            cols = {c.lower(): c for c in it.columns}
            def col(*names):
                for n in names:
                    if n in cols: return cols[n]
                return None
            c_insider = col("insider")
            c_pos     = col("position")
            c_txn     = col("transaction")
            c_shares  = col("shares")
            c_value   = col("value")
            c_date    = col("start date", "date")
            buys_val = sells_val = 0.0
            buys_n = sells_n = 0
            for _, row in it.head(25).iterrows():
                txn   = str(row[c_txn]) if c_txn else ""
                shares = row[c_shares] if c_shares else None
                value  = row[c_value] if c_value else None
                dt     = row[c_date] if c_date else None
                try: dt_str = dt.strftime("%Y-%m-%d") if hasattr(dt, "strftime") else str(dt)[:10]
                except Exception: dt_str = str(dt)[:10] if dt is not None else ""
                is_buy  = "purchase" in txn.lower() or "buy" in txn.lower()
                is_sell = "sale" in txn.lower() or "sell" in txn.lower()
                v = float(value) if value not in (None, "") and not (isinstance(value, float) and math.isnan(value)) else 0.0
                if is_buy:  buys_val += v;  buys_n += 1
                if is_sell: sells_val += v; sells_n += 1
                data["transactions"].append({
                    "insider":     str(row[c_insider]) if c_insider else "",
                    "position":    str(row[c_pos]) if c_pos else "",
                    "transaction": txn,
                    "shares":      int(shares) if shares not in (None, "") and not (isinstance(shares, float) and math.isnan(shares)) else None,
                    "value":       round(v, 2) if v else None,
                    "date":        dt_str,
                    "is_buy":      is_buy, "is_sell": is_sell,
                })
            data["summary"] = {
                "buys_count": buys_n, "sells_count": sells_n,
                "buys_value": round(buys_val, 2), "sells_value": round(sells_val, 2),
                "net_value": round(buys_val - sells_val, 2),
                "signal": "BULLISH" if buys_val > sells_val else ("BEARISH" if sells_val > buys_val else "NEUTRAL"),
            }
    except Exception:
        pass
    # yfinance dejo de poblar la columna `Transaction`, asi que el bloque de
    # arriba clasifica CERO compras y CERO ventas aunque las operaciones existan.
    # Los Form 4 de FMP si traen `transactionType`, y el motor ya los clasifica:
    # se usa como fuente del resumen cuando el conteo por texto no dio nada.
    try:
        s = data.get("summary") or {}
        if not (s.get("buys_value") or s.get("sells_value")):
            m = _wbj_insiders_clasificados(ticker)
            if m:
                comprado, vendido = m["bought"], m["sold"]
                data["summary"] = {
                    "buys_count": sum(1 for t in m["trades"] if t.get("side") == "buy"),
                    "sells_count": sum(1 for t in m["trades"] if t.get("side") == "sell"),
                    "buys_value": round(comprado, 2), "sells_value": round(vendido, 2),
                    "net_value": round(comprado - vendido, 2),
                    "signal": ("BULLISH" if comprado > vendido
                               else ("BEARISH" if vendido > comprado else "NEUTRAL")),
                    "source": "SEC Form 4 (FMP) via motor",
                }
                # Punto 5 de CLAUDE.md: solo cuentan como importantes las que
                # excedan $1M USD.
                data["important_trades"] = [
                    t for t in m["trades"] if float(t.get("value") or 0) > 1_000_000][:8]
    except Exception:
        pass
    # ── Institutional holders (13F-derived) ──────────────────────────────────
    try:
        inst = stock.institutional_holders
        if inst is not None and hasattr(inst, "empty") and not inst.empty:
            cols = {c.lower(): c for c in inst.columns}
            def icol(*names):
                for n in names:
                    if n in cols: return cols[n]
                return None
            c_holder = icol("holder")
            c_shares = icol("shares")
            c_value  = icol("value")
            c_pct    = icol("pctheld", "% out", "pctout")
            c_date   = icol("date reported", "datereported")
            for _, row in inst.head(15).iterrows():
                dt = row[c_date] if c_date else None
                try: dt_str = dt.strftime("%Y-%m-%d") if hasattr(dt, "strftime") else str(dt)[:10]
                except Exception: dt_str = str(dt)[:10] if dt is not None else ""
                pct_raw = row[c_pct] if c_pct else None
                try: pct = round(float(pct_raw) * 100, 2) if (pct_raw is not None and float(pct_raw) < 1) else (round(float(pct_raw), 2) if pct_raw is not None else None)
                except Exception: pct = None
                data["institutional"].append({
                    "holder": str(row[c_holder]) if c_holder else "",
                    "shares": int(row[c_shares]) if c_shares and row[c_shares] not in (None, "") else None,
                    "value":  round(float(row[c_value]), 2) if c_value and row[c_value] not in (None, "") else None,
                    "pct_held": pct, "date": dt_str,
                })
    except Exception:
        pass
    # ── Major holders breakdown (% insiders / % institutions) ────────────────
    try:
        mh = stock.major_holders
        if mh is not None and hasattr(mh, "empty") and not mh.empty:
            # Newer yfinance: DataFrame indexed by metric name with a 'Value' column
            try:
                idx = {str(i).lower(): i for i in mh.index}
                def mval(key):
                    for k, orig in idx.items():
                        if key in k:
                            v = mh.loc[orig]
                            v = v.iloc[0] if hasattr(v, "iloc") else v
                            return round(float(v) * 100, 2) if float(v) < 1 else round(float(v), 2)
                    return None
                data["major_holders"] = {
                    "pct_insiders":     mval("insiderspercentheld") or mval("insider"),
                    "pct_institutions": mval("institutionspercentheld") or mval("institutions"),
                }
            except Exception:
                pass
    except Exception:
        pass
    # ── SEC EDGAR authoritative filing links ─────────────────────────────────
    data["edgar"] = fetch_edgar_filings(ticker, limit=8)
    return data


def format_insiders_context(ins):
    """Condense the insider/institutional payload into a short text block for the AI prompt."""
    if not ins:
        return ""
    parts = []
    s = ins.get("summary") or {}
    if s:
        parts.append(
            f"INSIDERS (ultimas operaciones): {s.get('buys_count',0)} compras (${s.get('buys_value',0):,.0f}) "
            f"vs {s.get('sells_count',0)} ventas (${s.get('sells_value',0):,.0f}) | Neto: ${s.get('net_value',0):,.0f} | Señal: {s.get('signal','N/A')}"
        )
    # Punto 5 de CLAUDE.md: las operaciones que exceden $1M, nombradas.
    imp = ins.get("important_trades") or []
    if imp:
        detalle = "; ".join(
            f"{t.get('name')} ({t.get('title') or 's/t'}) {t.get('side')} "
            f"${float(t.get('value') or 0):,.0f} al {t.get('last')}" for t in imp[:5])
        parts.append(f"Operaciones de insiders sobre $1M (Forms 4): {detalle}")
    mh = ins.get("major_holders") or {}
    if mh.get("pct_institutions") is not None:
        parts.append(f"Propiedad institucional: {mh.get('pct_institutions')}% | Insiders: {mh.get('pct_insiders','N/A')}%")
    inst = ins.get("institutional") or []
    if inst:
        fechas = {h.get("date") for h in inst[:5] if h.get("date")}
        # La fecha del trimestre 13F importa: sin ella el modelo no puede saber
        # si la posicion es del trimestre pasado o de hace un ano.
        sello = f", al {sorted(fechas)[-1]}" if fechas else ""
        top = ", ".join(f"{h['holder']} ({h.get('pct_held','?')}%)" for h in inst[:5] if h.get('holder'))
        if top:
            parts.append(f"Top tenedores institucionales (13F{sello}): {top}")
    # Tier 1: presentantes de Schedule 13D/13G sobre el CUSIP, que por definicion
    # cruzaron el 5%. Es la fuente auditable (trae accession) frente a la lista
    # anterior, que viene de un agregador.
    edg = ins.get("edgar") or {}
    cinco = edg.get("holders_5pct") or []
    if cinco:
        nombres = ", ".join(
            f"{h.get('name')} ({h.get('filing_date')})" for h in cinco[:6] if h.get("name"))
        if nombres:
            parts.append(f"Tenedores >5% del capital segun SEC EDGAR (Schedule 13D/13G): {nombres}")
    return "\n".join(parts)


# ─────────────────────────────────────────────────────────────────────────────
# ENDPOINTS
# ─────────────────────────────────────────────────────────────────────────────

@app.get("/api/explore", response_model=ExploreResponse)
def get_explore(limit: int = 15):
    """Descubrir empresas — el screener del engine (`wbj.screener`), tal cual.

    Antes esto era el «Market Buzz Explorer»: ordenaba acciones por cuántas
    veces se las menciona en X, Reddit y foros. Eso mide popularidad, no
    evidencia, y la regla del proyecto es que sin número no hay score. Aquí
    el orden lo pone el puntaje del engine sobre datos de la SEC.

    Dos etapas, la barata primero:

    1. Prefiltro sobre TODO el universo de declarantes de la SEC — las
       «frames» XBRL traen ventas de dos años y utilidad neta del mercado
       entero en ~4 peticiones. Se queda con las medianas ($0.8B-$30B:
       grandes de verdad, pero probablemente desconocidas), rentables
       (margen neto > 8%) y creciendo (> 5%).
    2. Scorecard rápido de los 6 agentes sólo para los mejores candidatos.

    La primera corrida tarda 1-2 min: construye un packet por candidato. Las
    siguientes van sobre la caché de EDGAR.

    Es una clasificación de research, nunca una orden de compra.
    """
    try:
        from wbj.screener import (screen as run_screen,
                                  REV_MIN, REV_MAX, MARGIN_MIN, GROWTH_MIN)
        filas = run_screen(limit=limit)
    except Exception as e:
        raise HTTPException(status_code=500, detail=_error_publico(e, "/api/explore"))
    return {
        "companies": filas,
        "prefilter": {"revenue_min": REV_MIN, "revenue_max": REV_MAX,
                      "margin_min": MARGIN_MIN, "growth_min": GROWTH_MIN},
        # El puntaje de aqui NO es el del analisis completo, y la respuesta lo
        # dice en vez de dejar que la interfaz lo suponga.
        #
        # Medido en APH: 7,9 aqui contra 5,7 en el analisis. Casi todo el
        # hueco es UNA categoria -- market 17,2 puntos contra 1,82 -- porque
        # el rapido la puntua con crecimiento adelantado y amplitud de
        # analistas, mientras el agente completo le pide TAM/SAM/SOM y se
        # queda en 0,355 de cobertura. Con eso el override 6 deja el perfil
        # fuera de los gates, que es justo lo que el rapido no puede ver.
        #
        # Los dos numeros son correctos para lo que miden. Lo que estaba mal
        # era llamar "Puntaje" a los dos y dejar que parecieran comparables.
        "metodologia": {
            "tipo": "rapido",
            "metricas_por_categoria": "unas pocas de EDGAR companyfacts",
            "vs_analisis_completo": (
                "El analisis completo corre las 208 metricas de los 6 agentes "
                "y aplica el piso de cobertura del 70%. Suele dar un numero "
                "mas bajo y es el que manda."),
        },
        "generated_at": datetime.now().strftime("%m/%d/%Y, %I:%M:%S %p"),
    }


@app.post("/api/premarket/enviar")
def premarket_enviar(request: Request, forzar: bool = False, seco: bool = False):
    """Manda el correo pre-market desde AQUI, no desde el runner de GitHub.

    GitHub Actions no ve las variables de Render: son dos entornos distintos, y
    tener las claves puestas en el dashboard no hacia nada por el workflow --
    llegaban vacias y el envio moria antes de empezar. La alternativa era
    duplicar cada clave en los secrets del repositorio y mantener las dos
    copias sincronizadas para siempre.

    Asi que el envio vive donde ya viven las claves. El workflow queda como lo
    unico que GitHub sabe hacer bien aqui: un despertador. Sus efectos de lado
    son buenos -- la peticion despierta al servicio dormido del plan free.

    Y aqui la base esta VIVA, asi que los destinatarios salen de `usuarios`
    directamente: sin descifrar el respaldo, sin `cryptography`, sin bajar la
    rama `datos`.

    `forzar` salta la ventana horaria (para probar a mano). `seco` arma el
    correo y dice a quien iria SIN mandarlo.
    """
    import sys as _sys
    _dir = os.path.join(os.path.dirname(os.path.abspath(__file__)), "scripts")
    if _dir not in _sys.path:
        _sys.path.insert(0, _dir)
    try:
        import premarket_email as _pm
    except Exception as e:                        # noqa: BLE001
        raise HTTPException(status_code=500,
                            detail=f"no pude cargar el guion: {type(e).__name__}")

    ahora = datetime.now(_pm.ET)
    if not forzar:
        # La regla vive en UN sitio. Estaba escrita aqui y otra vez en el
        # `main()` del guion: un feriado de 2028 habria que ponerlo en los dos.
        motivo = _pm.motivo_para_saltar(ahora)
        if motivo:
            return {"ok": True, "enviados": 0, "motivo": motivo}

    try:
        gainers, losers = _pm.movers(_pm.GAINERS), _pm.movers(_pm.LOSERS)
    except Exception as e:                        # noqa: BLE001
        raise HTTPException(status_code=502,
                            detail=f"FMP no contesto: {_error_publico(e, 'premarket')}")
    if not gainers and not losers:
        raise HTTPException(status_code=502, detail="FMP contesto sin movers utilizables.")

    # La base esta viva: el email de cada cuenta sale de aqui. Si todavia no hay
    # cuentas, se cae a EMAIL_TO y se dice cual de los dos caminos se uso.
    correos, fuente = [], "cuentas"
    try:
        conn = _db()
        try:
            correos = [c for (c,) in conn.execute(
                "SELECT email FROM usuarios WHERE email IS NOT NULL AND email <> '' "
                "ORDER BY creado_ts").fetchall() if (c or "").strip()]
        finally:
            conn.close()
    except Exception:                             # noqa: BLE001
        correos = []
    if not correos:
        correos, fuente = [d.strip() for d in _pm.EMAIL_TO.split(",") if d.strip()], "EMAIL_TO"

    asunto, texto, html = _pm.build_email(ahora, gainers, losers)
    if seco:
        return {"ok": True, "seco": True, "para": correos, "fuente": fuente,
                "asunto": asunto}
    motivos: list[str] = []
    try:
        enviados = _pm.send_resend(asunto, texto, html, correos, motivos)
    except RuntimeError:
        # El unico RuntimeError que `send_resend` levanta es la clave ausente,
        # y el texto se escribe aqui en vez de reenviar el de la excepcion:
        # reenviar el texto de la excepcion es justo lo que prohibe
        # `test_route_safety` --y con razon: un dia esa excepcion trae una ruta
        # del servidor o un trozo de clave, y va derecha al navegador.
        raise HTTPException(status_code=500, detail=(
            "Falta RESEND_API_KEY en el entorno del servidor. Ponla en las "
            "variables de Render, junto a las demas claves."))
    # Cero de N no es un exito: quien dispare esto tiene que verlo en rojo.
    if not enviados:
        # El motivo lo dice Resend, no yo. Adivinarlo aqui («sera el dominio»)
        # manda a Victor a arreglar lo que no esta roto.
        raise HTTPException(status_code=502, detail=(
            f"ninguno de los {len(correos)} destinatarios acepto el correo. "
            + " | ".join(motivos[:3])))
    return {"ok": True, "enviados": enviados, "de": len(correos),
            "fuente": fuente, "asunto": asunto}


_BUSQUEDA_CACHE = {}          # q -> (ts, resultados)
#: Cuántos resultados locales bastan para NO bajar a la cola larga de FMP. Con
#: el umbral en el cupo entero (8) casi cada tecla disparaba dos peticiones
#: HTTP: 750-1000 ms por pulsación. Tres buenos candidatos locales ya ponen
#: arriba lo que estás buscando; por debajo de eso FMP sí aporta.
_MIN_LOCAL_PARA_NO_PREGUNTAR = 3
#: Un autocompletado que tarda 12 s ya perdió la carrera contra la siguiente
#: tecla y su respuesta se descarta por vieja. Mejor contestar con el índice.
_TIMEOUT_BUSQUEDA_S = 2.5
_INDICE = {"ts": 0.0, "filas": {}, "cargando": False}
_INDICE_LOCK = threading.Lock()
_FONDO_MUTUO = re.compile(r"^[A-Z]{4}X$")     # AGTHX, TESIX: 5 letras terminando en X
# Ruido para quien busca una empresa que analizar: ETFs, fondos, y las series
# preferentes (se delatan por el "%" en el nombre) que comparten el nombre de la
# empresa pero no son la acción común.
_NO_OPERATIVA = re.compile(r"\b(ETF|ETN)s?\b|\bFund\b|\bDaily (Bull|Bear)\b|\b[23]x\b|%", re.I)


def _fmp_cargar_indice():
    """Índice local de las empresas de NASDAQ/NYSE/AMEX: símbolo, nombre y market cap.

    El buscador de FMP no sirve solo para esto por dos razones: devuelve como
    máximo unas 50 filas por término, así que escribir "A" nunca alcanzaba a
    AAPL ni AMZN; y no trae ninguna señal de tamaño, así que ordenando por
    alfabeto salían primero los tickers de dos letras que nadie busca.
    `company-screener` trae las tres cosas de una vez.

    Son 3 llamadas de ~2500 filas: corre en un hilo y el buscador responde
    igual mientras no esté listo, complementando con el buscador de FMP.
    """
    filas = {}
    try:
        clave = (os.environ.get("FMP_API_KEY") or "").strip()
        if not clave:
            return
        for ex in ("NASDAQ", "NYSE", "AMEX"):
            r = requests.get("https://financialmodelingprep.com/stable/company-screener",
                             params={"marketCapMoreThan": 300_000_000, "exchange": ex,
                                     "isActivelyTrading": "true", "limit": 2500, "apikey": clave},
                             timeout=40)
            if r.status_code != 200:
                continue
            for x in (r.json() or []):
                s = (x.get("symbol") or "").upper().strip()
                nombre = (x.get("companyName") or "").strip()
                if s and nombre:
                    filas[s] = (nombre, ex, float(x.get("marketCap") or 0))
    except Exception:
        pass
    finally:
        with _INDICE_LOCK:
            if filas:
                _INDICE["filas"] = filas
                _INDICE["ts"] = time.time()
            _INDICE["cargando"] = False


def _indice_actual(ttl=86400):
    """El índice, lanzando su recarga en segundo plano cuando toca."""
    with _INDICE_LOCK:
        fresco = _INDICE["filas"] and (time.time() - _INDICE["ts"]) < ttl
        if not fresco and not _INDICE["cargando"]:
            _INDICE["cargando"] = True
            threading.Thread(target=_fmp_cargar_indice, daemon=True).start()
        return _INDICE["filas"]


def _rango_coincidencia(termino, simbolo, nombre):
    """Qué tan bien coincide, de 0 (mejor) a 2. None si no coincide.

    Empezar el nombre y empezar *una palabra* del nombre valen igual a
    propósito: si fueran distintos, buscar "coca" pondría a Coca-Cola
    Europacific antes que a The Coca-Cola Company, solo porque la segunda
    empieza con "The". Empatados, decide el market cap.
    """
    n = nombre.upper()
    if simbolo == termino:
        return 0
    if simbolo.startswith(termino):
        return 1
    if n.startswith(termino) or any(p.startswith(termino)
                                    for p in n.replace(",", " ").replace("-", " ").split()):
        return 2
    return None


@app.get("/api/search")
def buscar_tickers(q: str, limite: int = 8):
    """Autocompletado del buscador: escribes siglas y salen empresas con su nombre.

    "A" → AAPL, AMZN, AVGO… ordenado por market cap, que es lo que hace la lista
    útil en vez de alfabética. También busca por nombre, así que "coca" encuentra
    KO y "micro" encuentra MSFT/MU.

    Se sirve del índice local (rápido, cubre ~5600 empresas de EE.UU.) y lo
    complementa con el buscador de FMP para lo que no esté ahí: ADRs, small caps
    y símbolos recién listados. Los fondos mutuos y los ETF apalancados se
    hunden al final -- son ruido cuando buscas una empresa que analizar.
    """
    termino = (q or "").strip().upper()
    if not termino:
        return {"q": termino, "resultados": []}
    limite = max(1, min(int(limite or 8), 20))

    en_cache = _BUSQUEDA_CACHE.get(termino)
    if en_cache and time.time() - en_cache[0] < 900:
        return {"q": termino, "resultados": en_cache[1][:limite]}

    indice = _indice_actual()
    candidatos = {}
    for s, (nombre, bolsa, cap) in indice.items():
        rango = _rango_coincidencia(termino, s, nombre)
        if rango is not None:
            candidatos[s] = (rango, nombre, bolsa, cap)

    # El índice cubre lo grande; FMP cubre la cola larga.
    #
    # Antes se preguntaba a FMP en cuanto el índice no llenaba el cupo de 8, y
    # eso convertía casi cada tecla en DOS peticiones HTTP secuenciales. Medido:
    # 750-1000 ms por tecla, 3,3 s para escribir "NVDA" — mientras que una "Z",
    # que sí llenaba el cupo local, contestaba en 11 ms. El autocompletado no
    # necesita ocho resultados para ser útil: necesita que el que buscas esté
    # arriba. Ahora sólo se baja a la cola larga cuando el índice se queda de
    # verdad corto, que es cuando FMP aporta algo (ADRs, small caps, símbolos
    # recién listados).
    #
    # Pero contar candidatos NO basta, y fallaba justo en el mejor caso. Escribir
    # "NVD" deja UN solo candidato local (NVDA) y "NVDA" también, así que las dos
    # últimas teclas del ticker más buscado seguían pagando dos peticiones HTTP
    # —hasta 2×2,5 s de timeout— para no añadir nada: lo que buscabas ya estaba
    # el primero. Se veía en el número: "escribir NVDA entero" no costaba 42 ms
    # sino 42 ms más lo que tardaran cuatro llamadas a FMP.
    #
    # La regla correcta no es "¿hay pocos?" sino "¿hay una respuesta BUENA?".
    # Un rango 0 (el símbolo exacto) o un rango 1 (el símbolo empieza por lo que
    # tecleaste) ya es la respuesta: FMP solo puede añadir ruido por debajo. La
    # cola larga se conserva entera para lo que de verdad la necesita —un ADR o
    # una small cap que no está en el índice—, donde no hay ningún local que
    # empiece por el término y esta condición no se cumple.
    hay_simbolo_local = any(v[0] <= 1 for v in candidatos.values())
    if len(candidatos) < _MIN_LOCAL_PARA_NO_PREGUNTAR and not hay_simbolo_local:
        clave = (os.environ.get("FMP_API_KEY") or "").strip()
        if not clave and not indice:
            raise HTTPException(status_code=503,
                                detail="Búsqueda no disponible: falta FMP_API_KEY.")

        def _consultar(ruta: str) -> list:
            # 12 s de timeout no tenían sentido aquí: a los 12 s el usuario ya
            # escribió tres letras más y esta respuesta se descarta por vieja.
            # Más vale contestar sólo con el índice local que hacer esperar.
            try:
                r = requests.get(f"https://financialmodelingprep.com/stable/{ruta}",
                                 params={"query": termino, "limit": 50, "apikey": clave},
                                 timeout=_TIMEOUT_BUSQUEDA_S)
                filas = r.json() if r.status_code == 200 else []
            except Exception:
                filas = []
            return filas if isinstance(filas, list) else []

        # Las dos rutas van A LA VEZ. Eran secuenciales y sumaban sus latencias
        # sin necesidad: ninguna depende del resultado de la otra.
        respuestas: list[list] = []
        if clave:
            with ThreadPoolExecutor(max_workers=2) as pool:
                respuestas = list(pool.map(_consultar, ("search-symbol", "search-name")))

        for filas in respuestas:
            for f in filas:
                if not isinstance(f, dict):
                    continue
                s = (f.get("symbol") or "").upper().strip()
                nombre = (f.get("name") or "").strip()
                bolsa = (f.get("exchange") or "").upper().strip()
                if not s or not nombre or s in candidatos:
                    continue
                if bolsa not in ("NASDAQ", "NYSE", "AMEX"):
                    continue
                rango = _rango_coincidencia(termino, s, nombre)
                if rango is not None:
                    candidatos[s] = (rango, nombre, bolsa, indice.get(s, (0, 0, 0.0))[2])

    def orden(par):
        s, (rango, nombre, bolsa, cap) = par
        ruido = 1 if (_FONDO_MUTUO.match(s) or _NO_OPERATIVA.search(nombre)) else 0
        return (ruido, rango, -cap, len(s), s)

    resultados = [{"ticker": s, "nombre": v[1], "bolsa": v[2]}
                  for s, v in sorted(candidatos.items(), key=orden)]
    # No cachear lo que se calculó SIN el índice: en un servidor recién
    # arrancado la primera búsqueda se ordena por alfabeto (A, AA, AB, AC...) y
    # cachearla dejaba ese orden malo fijo 15 minutos, justo en la primera
    # impresión. Sin índice se responde igual, pero se recalcula.
    if indice:
        _BUSQUEDA_CACHE[termino] = (time.time(), resultados)
    return {"q": termino, "resultados": resultados[:limite]}


def _quote_rapido_fmp(ticker: str) -> dict | None:
    """Precio y cambio del dia en UNA peticion, o `None`.

    `/stable/quote` de FMP trae precio, cierre anterior, cambio, volumen y el
    maximo y minimo del dia: todo lo que la tarjeta de precio enseña. La ruta
    larga de abajo, en cambio, pedia un AÑO de historico para mostrar una
    cifra de hoy -- medido, 2,4 s la primera vez que pulsas un ticker, que es
    justo el momento en que el usuario esta mirando la pantalla.
    """
    clave = (os.environ.get("FMP_API_KEY") or "").strip()
    if not clave:
        return None
    try:
        r = requests.get("https://financialmodelingprep.com/stable/quote",
                         params={"symbol": ticker, "apikey": clave}, timeout=4)
        filas = r.json() if r.status_code == 200 else []
    except Exception:
        return None
    q = filas[0] if isinstance(filas, list) and filas and isinstance(filas[0], dict) else None
    if not q or not isinstance(q.get("price"), (int, float)) or q["price"] <= 0:
        return None
    return q


@app.get("/api/quote")
def get_quick_quote(ticker: str):
    ticker_clean = ticker.upper().strip()

    # Via rapida: una sola peticion. Si trae precio, se contesta y ya. La ruta
    # completa de abajo sigue existiendo para cuando FMP no responda -- no se
    # borra un respaldo por tener un atajo.
    rapido = _quote_rapido_fmp(ticker_clean)
    if rapido:
        try:
            precio = float(rapido["price"])
            previo = float(rapido.get("previousClose") or precio) or precio
            alto = float(rapido.get("dayHigh") or precio)
            bajo = float(rapido.get("dayLow") or precio)
            perfil = _fmp_perfil(ticker_clean) or {}
            # EXACTAMENTE las mismas claves que la ruta larga de abajo. Se
            # inventaron en ingles -- `price`, `volume`, `day_high` -- mientras
            # la pantalla lee `precio`, `volumen` y `high`, asi que la tarjeta
            # salio con "undefined" en todo salvo el VWAP, que fue la unica que
            # coincidio por casualidad. Dos rutas que responden al mismo
            # endpoint tienen que devolver la misma forma; si no, la mas rapida
            # rompe a quien la consume.
            return {
                "ticker": ticker_clean,
                "nombre_completo": rapido.get("name") or perfil.get("name") or ticker_clean,
                "precio": round(precio, 2),
                "cambio_pct": round((precio - previo) / previo * 100.0, 2) if previo else 0.0,
                "volumen": format_volume(int(rapido.get("volume") or 0)),
                # VWAP no viene en `quote`; el precio tipico (H+L+C)/3 es el
                # mismo respaldo que ya usaba la ruta larga cuando faltaba.
                "vwap": round((alto + bajo + precio) / 3, 2),
                "high": round(alto, 2),
                "low": round(bajo, 2),
                "after_hours": None,
                "precio_fuente": "fmp",
                "as_of": datetime.now().strftime("%I:%M:%S %p"),
                "logo_url": obtener_logo(ticker_clean, perfil.get("website") or ""),
            }
        except (TypeError, ValueError, ZeroDivisionError):
            pass  # cae a la ruta completa

    try:
        stock = vertex_market.Ticker(ticker_clean)
        # Este endpoint es lo PRIMERO que toca el usuario: el buscador lo llama al
        # escribir, y el frontend esconde la tarjeta ante cualquier !res.ok. Era el
        # unico de la ruta de entrada sin respaldo -- `stock.info` a pelo, y encima
        # como primera linea -- asi que cuando Yahoo limita la IP del servidor
        # ("Too Many Requests. Rate limited.") el except de abajo lo convertia en un
        # 404 y al escribir un ticker no aparecia NADA que analizar. /api/analyze ya
        # tenia este blindaje y por eso seguia respondiendo; aqui faltaba.
        try:
            info = stock.info or {}
        except Exception:
            info = {}
        hist = _resilient_history(stock, ticker_clean, "5d")   # yfinance → respaldo FMP
        tiene_hist = hist is not None and not hist.empty
        fh = None
        if not info:                                           # solo si Yahoo no respondio
            try:
                fh = finnhub_quote(ticker_clean) or {}
            except Exception:
                fh = {}
        fh = fh or {}

        spot = _resolve_spot(ticker_clean)                     # yfinance → Finnhub → serie cacheada
        precio_actual = (spot.get("price") or info.get("currentPrice") or fh.get("price")
                         or (float(hist['Close'].iloc[-1]) if tiene_hist else None))
        if not precio_actual:
            raise HTTPException(status_code=404,
                                detail=f"Sin datos de precio para {ticker_clean} en ninguna fuente.")
        precio_actual = float(precio_actual)

        # Cierre anterior explicito antes que deducirlo de la serie: el respaldo suele ir un
        # dia por detras, y ahi `iloc[-2]` compararia contra el dia equivocado.
        precio_anterior = (info.get("regularMarketPreviousClose") or info.get("previousClose")
                           or fh.get("prev_close")
                           or (float(hist['Close'].iloc[-2]) if tiene_hist and len(hist) > 1 else None)
                           or precio_actual)
        precio_anterior = float(precio_anterior)
        cambio_pct = ((precio_actual - precio_anterior) / precio_anterior * 100) if precio_anterior else 0.0

        volumen  = info.get("regularMarketVolume") or (int(hist['Volume'].iloc[-1]) if tiene_hist else 0)
        high_dia = float(info.get("dayHigh") or fh.get("high")
                         or (float(hist['High'].iloc[-1]) if tiene_hist else precio_actual))
        low_dia  = float(info.get("dayLow") or fh.get("low")
                         or (float(hist['Low'].iloc[-1]) if tiene_hist else precio_actual))
        vwap_dia = info.get("vwap")    or ((high_dia + low_dia + precio_actual) / 3)
        # Nombre y logo: si Yahoo limito `.info` tenemos precio pero no razon social,
        # y la tarjeta mostraba el ticker como nombre. FMP la trae (dato de
        # referencia, cacheado), asi que solo se consulta cuando falta.
        perfil = _fmp_perfil(ticker_clean) if not info.get("longName") else {}
        nombre = info.get("longName") or perfil.get("name") or ticker_clean
        logo_url = obtener_logo(ticker_clean, info.get("website") or perfil.get("website") or "")

        # --- Precio extendido (after-hours / pre-market) ---
        mkt_state = (info.get("marketState") or "").upper()
        reg_close = info.get("regularMarketPrice") or precio_actual
        ext_price, ext_label = None, None
        if mkt_state.startswith("POST") or mkt_state == "CLOSED":
            ext_price, ext_label = info.get("postMarketPrice"), "After-Hours"
        elif mkt_state.startswith("PRE"):
            ext_price, ext_label = info.get("preMarketPrice"), "Pre-Market"
        after_hours = None
        if ext_price and reg_close:
            after_hours = {
                "price": round(float(ext_price), 2),
                "change_pct": round((float(ext_price) - float(reg_close)) / float(reg_close) * 100.0, 2),
                "label": ext_label,
            }

        return {
            "ticker": ticker_clean,
            "nombre_completo": nombre,
            "precio": round(precio_actual, 2),
            "cambio_pct": round(cambio_pct, 2),
            "volumen": format_volume(volumen),
            "vwap": round(vwap_dia, 2),
            "high": round(high_dia, 2),
            "low": round(low_dia, 2),
            "after_hours": after_hours,
            # La fuente real, no "yfinance" siempre: si el precio vino de Finnhub o
            # un respaldo, la tarjeta tiene que decirlo, igual que el resto de los paneles.
            "precio_fuente": spot.get("source") or "yfinance",
            "as_of": spot.get("as_of") or datetime.now().strftime('%I:%M:%S %p'),
            "logo_url": logo_url
        }
    except HTTPException:
        raise
    except Exception as e:
        # 503, no 404: que ninguna fuente responda NO significa que el ticker no
        # exista. Con 404 un simbolo valido durante un rate-limit se veia igual que
        # uno inventado, y no habia forma de distinguirlos desde el frontend.
        raise HTTPException(status_code=503,
                            detail=f"Fuentes de precio no disponibles para {ticker_clean}: {e}")


@app.get("/api/news")
def get_company_news(ticker: str):
    ticker_clean = ticker.upper().strip()
    try:
        headers = {'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64)'}
        url = f"https://query2.finance.yahoo.com/v1/finance/search?q={ticker_clean}&newsCount=25"
        resp = requests.get(url, headers=headers, timeout=5)
        
        noticias_formateadas = []
        
        if resp.status_code == 200:
            data = resp.json()
            for item in data.get("news", []):
                pub_ts = item.get("providerPublishTime", 0)
                pub_time = datetime.fromtimestamp(pub_ts).strftime('%Y-%m-%d %I:%M %p') if pub_ts else "Reciente"
                noticias_formateadas.append({
                    "title": item.get("title", "No Title"),
                    "publisher": item.get("publisher", "Yahoo Finance"),
                    "publish_time": pub_time,
                    "publish_ts": pub_ts,
                    "summary": item.get("summary", "No description available."),
                    "link": item.get("link", "")
                })
        
        if not noticias_formateadas:
            stock = vertex_market.Ticker(ticker_clean)
            raw_news = stock.news if stock.news else []
            for item in raw_news:
                pub_ts = item.get("providerPublishTime", 0)
                pub_time = datetime.fromtimestamp(pub_ts).strftime('%Y-%m-%d %I:%M %p') if pub_ts else "Reciente"
                noticias_formateadas.append({
                    "title": item.get("title", "No Title"),
                    "publisher": item.get("publisher", "Yahoo Finance"),
                    "publish_time": pub_time,
                    "publish_ts": pub_ts,
                    "summary": item.get("summary", "No description available."),
                    "link": item.get("link", "")
                })

        vistos = set()
        feed_final = []
        for n in noticias_formateadas:
            if n["link"] not in vistos:
                vistos.add(n["link"])
                feed_final.append(n)

        feed_final.sort(key=lambda x: x["publish_ts"], reverse=True)
        return {"ticker": ticker_clean, "total": len(feed_final), "noticias": feed_final}
    except Exception as e:
        raise HTTPException(status_code=500, detail=_error_publico(e, "/api/news"))


@app.get("/api/history")
def get_price_history(ticker: str, period: str = "1mo", interval: str = "1d"):
    ticker_clean = ticker.upper().strip()
    interval = (interval or "1d").lower()
    is_intraday = interval in ("1h", "10m")
    if not is_intraday:
        interval = "1d"
        valid_periods = ["7d", "1mo", "3mo", "6mo", "1y"]
        if period not in valid_periods:
            period = "1mo"
        yf_interval = "1d"
    elif interval == "1h":
        yf_interval = "60m"
        if period not in ("5d", "1mo", "3mo", "6mo"):
            period = "1mo"
    else:  # "10m" → se baja 5m y se reagrupa a 10 minutos
        yf_interval = "5m"
        if period not in ("1d", "5d", "1mo"):
            period = "5d"
    try:
        stock = vertex_market.Ticker(ticker_clean)
        # Mismo caso que /api/quote: esto era `stock.history(...)` a secas y un
        # rate-limit de Yahoo salia como 500, dejando la grafica de precio en
        # blanco. El respaldo FMP SOLO sirve para velas diarias, asi que la ruta
        # intradia se queda sin red -- pero al menos lo dice con un 503 honesto en
        # vez de un 500 generico.
        fallo_fuente = False
        try:
            hist = stock.history(period=period, interval=yf_interval)
        except Exception:
            hist, fallo_fuente = None, True
        if (hist is None or hist.empty) and not is_intraday:
            hist = _resilient_history(stock, ticker_clean, period)
            if hist is not None and not hist.empty:
                # _resilient_history no conoce "7d" y su respaldo cae al ano por
                # defecto: sin recortar, la grafica de 7 dias saldria con 250 velas.
                filas = {"7d": 5, "1mo": 22, "3mo": 63, "6mo": 126, "1y": 252}.get(period)
                if filas:
                    hist = hist.tail(filas)
        if hist is None or hist.empty:
            raise HTTPException(
                status_code=503 if fallo_fuente else 404,
                detail=(f"Fuentes de precio no disponibles para {ticker_clean}."
                        if fallo_fuente else "No data"))

        # 10m: reagrupa las velas de 5m a 10 minutos (yfinance no tiene 10m nativo)
        if interval == "10m":
            hist = hist.resample("10min").agg({
                "Open": "first", "High": "max", "Low": "min",
                "Close": "last", "Volume": "sum",
            }).dropna(subset=["Open", "High", "Low", "Close"])
            if hist.empty:
                raise HTTPException(status_code=404, detail="No data")

        precios_hist = [round(float(x), 2) for x in hist['Close'].tolist()]
        fechas_hist  = [x.strftime("%b %d %H:%M" if is_intraday else "%b %d") for x in hist.index.tolist()]

        # OHLC + Volume for candlestick chart (TradingView format)
        # Diario → time = "YYYY-MM-DD" (string) · Intradía → time = epoch segundos (UTC) que pide lightweight-charts
        ohlc_hist    = []
        volumen_hist = []
        for idx, row in hist.iterrows():
            if is_intraday:
                # lightweight-charts pinta el epoch en UTC; sumamos el offset del huso para que el eje muestre la hora de mercado (ET)
                off = idx.utcoffset()
                t = int(idx.timestamp()) + (int(off.total_seconds()) if off else 0)
            else:
                t = idx.strftime("%Y-%m-%d")
            ohlc_hist.append({
                "time":  t,
                "open":  round(float(row['Open']), 2),
                "high":  round(float(row['High']), 2),
                "low":   round(float(row['Low']), 2),
                "close": round(float(row['Close']), 2),
            })
            volumen_hist.append({
                "time":  t,
                "value": int(row['Volume']) if not math.isnan(row['Volume']) else 0,
                "up":    bool(row['Close'] >= row['Open']),
            })

        return {"ticker": ticker_clean, "period": period, "interval": interval,
                "intraday": is_intraday, "precios": precios_hist,
                "fechas": fechas_hist, "ohlc": ohlc_hist, "volumen": volumen_hist}
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=_error_publico(e, "/api/history"))


def _ols_beta(y, X):
    """OLS coefficients for y ~ X (X already includes intercept column)."""
    beta, *_ = np.linalg.lstsq(X, y, rcond=None)
    return beta


def _residualize(y, base):
    """Remove the linear effect of `base` (1D) from `y` (1D); return the residual.
    Used to orthogonalize factors against the market so betas are incremental."""
    X = np.column_stack([np.ones(len(base)), base])
    b = _ols_beta(y, X)
    return y - X @ b


def compute_ticker_vs_portfolio(ticker, holdings, lookback="1y", add_weight=0.05):
    """How a candidate ticker fits the user's CURRENT book: ownership, correlation
    to the book, beta to the book, standalone vol, diversification verdict."""
    import pandas as pd
    eq = [h for h in holdings if h.get("ticker") and float(h.get("value") or 0) > 0]
    if not eq:
        return None
    total = sum(float(h["value"]) for h in eq)
    weights = {h["ticker"]: float(h["value"]) / total for h in eq}
    held = list(weights.keys())
    already = ticker in weights
    cur_w = round(weights.get(ticker, 0.0) * 100, 1)

    syms = list(set(held + [ticker]))
    closes = {}
    for s in syms:
        try:
            h = vertex_market.Ticker(s).history(period=lookback)
            if not h.empty and "Close" in h:
                closes[s] = h["Close"]
        except Exception:
            pass

    base = {"already_held": already, "current_weight_pct": cur_w,
            "book_positions": len(held), "corr_to_book": None, "beta_to_book": None,
            "ticker_vol_pct": None, "most_correlated": None, "diversification": None}

    if ticker not in closes or not any(s in closes for s in held):
        base["note"] = "Datos de precio insuficientes."
        return base

    px = pd.DataFrame(closes).dropna(how="all")
    rets = px.pct_change().dropna(how="all")
    avail = [s for s in held if s in rets.columns]
    if not avail:
        base["note"] = "Sin retornos del book."
        return base

    wv = np.array([weights[t] for t in avail], dtype=float)
    wv = wv / wv.sum()
    Rb = rets[avail].dropna()
    common = Rb.index.intersection(rets[ticker].dropna().index)
    if len(common) < 30:
        base["note"] = "Historial comun insuficiente."
        return base

    book_ret = Rb.loc[common].values @ wv
    tk_ret = rets[ticker].loc[common].values
    ann = math.sqrt(252)
    base["ticker_vol_pct"] = round(float(np.std(tk_ret, ddof=1)) * ann * 100, 1)
    vb = float(np.var(book_ret, ddof=1))
    base["beta_to_book"] = round(float(np.cov(tk_ret, book_ret, ddof=1)[0, 1] / vb), 2) if vb > 0 else None
    corr_book = round(float(np.corrcoef(tk_ret, book_ret)[0, 1]), 2)
    base["corr_to_book"] = corr_book

    most = None
    for s in avail:
        if s == ticker:
            continue
        j = pd.concat([rets[ticker].rename("a"), rets[s].rename("b")], axis=1).dropna()
        if j.shape[0] >= 30:
            c = float(np.corrcoef(j["a"].values, j["b"].values)[0, 1])
            if most is None or c > most["corr"]:
                most = {"ticker": s, "corr": round(c, 2)}
    base["most_correlated"] = most
    base["diversification"] = "alta" if corr_book < 0.4 else "media" if corr_book < 0.7 else "baja"

    # ── #2 FACTOR DECOMPOSITION + MARGINAL CONTRIBUTION TO RISK (Aladdin-style) ──
    # Orthogonalized factor ETFs: market (SPY), AI/semis beyond market (SMH),
    # small-cap beyond market (IWM), rates beyond market (TLT),
    # AI-software beyond market AND beyond semis (IGV) — mide concentración temática de IA honestamente.
    try:
        fetf = {"mercado": "SPY", "ia_semis": "SMH", "small_cap": "IWM", "tasas": "TLT",
                "software_ia": "IGV"}
        fcl = {}
        for k, sym in fetf.items():
            try:
                fh = vertex_market.Ticker(sym).history(period=lookback)
                if not fh.empty and "Close" in fh:
                    fcl[k] = fh["Close"].pct_change()
            except Exception:
                pass
        if len(fcl) >= 2:
            cand_s = pd.Series(tk_ret, index=common)
            book_s = pd.Series(book_ret, index=common)
            alldf = pd.concat([cand_s.rename("cand"), book_s.rename("book"),
                               pd.DataFrame(fcl)], axis=1).dropna()
            order = [k for k in ["mercado", "ia_semis", "small_cap", "tasas", "software_ia"] if k in alldf.columns]
            if len(alldf) >= 40 and order:
                mkt = alldf["mercado"].values if "mercado" in alldf.columns else None
                facmat = {}
                for k in order:
                    v = alldf[k].values
                    facmat[k] = v if (k == "mercado" or mkt is None) else _residualize(v, mkt)
                # software/IA ⊥ semis también (Gram-Schmidt): aísla el riesgo de IA-software
                # que NO explica el mercado ni los semiconductores → evita colinealidad SMH/IGV.
                if "software_ia" in facmat and "ia_semis" in facmat:
                    facmat["software_ia"] = _residualize(facmat["software_ia"], facmat["ia_semis"])
                F = np.column_stack([np.ones(len(alldf))] + [facmat[k] for k in order])
                cand_b = _ols_beta(alldf["cand"].values, F)
                book_b = _ols_beta(alldf["book"].values, F)
                cand_betas = {order[i]: round(float(cand_b[i + 1]), 2) for i in range(len(order))}
                book_betas = {order[i]: round(float(book_b[i + 1]), 2) for i in range(len(order))}
                resid = alldf["cand"].values - F @ cand_b
                ss_res = float(np.sum(resid ** 2))
                yv = alldf["cand"].values
                ss_tot = float(np.sum((yv - yv.mean()) ** 2))
                r2 = round(1 - ss_res / ss_tot, 2) if ss_tot > 0 else None
                a = float(add_weight)
                delta_exp = {k: round(a * (cand_betas[k] - book_betas[k]), 3) for k in order}
                cand_dom = max(order, key=lambda k: abs(cand_betas[k]))
                book_dom = max(order, key=lambda k: abs(book_betas[k]))
                concentrates = (cand_dom == book_dom
                                and cand_betas[cand_dom] * book_betas[book_dom] > 0
                                and abs(book_betas[book_dom]) > 0.3)
                base["factors"] = {
                    "candidate_betas": cand_betas, "book_betas": book_betas,
                    "delta_exposure_at_add": delta_exp, "add_weight_pct": round(a * 100, 1),
                    "candidate_r2": r2, "candidate_dominant": cand_dom,
                    "book_dominant": book_dom, "concentrates_dominant": concentrates,
                    "factor_labels": {"mercado": "Mercado", "ia_semis": "IA/Semis",
                                      "small_cap": "Small-cap", "tasas": "Tasas",
                                      "software_ia": "Software/IA"},
                }
    except Exception:
        pass

    # Marginal contribution to risk: add candidate at weight a, book scaled to (1-a)
    try:
        ann2 = math.sqrt(252)
        sb = float(np.std(book_ret, ddof=1)) * ann2
        sc = float(np.std(tk_ret, ddof=1)) * ann2
        rho = float(corr_book)
        a = float(add_weight)
        snew = math.sqrt(max(((1 - a) ** 2) * sb ** 2 + (a ** 2) * sc ** 2
                             + 2 * a * (1 - a) * rho * sb * sc, 1e-12))
        d_vol = snew - sb
        wavg = (1 - a) * sb + a * sc
        div_ratio = round(snew / wavg, 3) if wavg > 0 else None
        mcr = a * (a * sc ** 2 + (1 - a) * rho * sb * sc) / snew if snew > 0 else 0.0
        mcr_share = round(mcr / snew * 100, 1) if snew > 0 else None
        var_1m_delta = round(1.645 * d_vol / math.sqrt(12) * 100, 2)
        conc_flag = bool((base.get("factors") or {}).get("concentrates_dominant"))
        verdict = ("concentra" if (rho >= 0.8 or conc_flag)
                   else "diversifica" if rho < 0.5 else "moderado")
        base["risk_contribution"] = {
            "book_vol_pct": round(sb * 100, 1), "new_vol_pct": round(snew * 100, 1),
            "delta_vol_pct": round(d_vol * 100, 2), "add_weight_pct": round(a * 100, 1),
            "mcr_share_pct": mcr_share, "diversification_ratio": div_ratio,
            "var_1m_95_delta_pct": var_1m_delta, "verdict": verdict,
        }
    except Exception:
        pass

    return base


def _recompute_risk_contribution(fit, add_weight):
    """#7 — Recalcula la contribución marginal al riesgo (MCR, Δvol, VaR, ratio de diversificación)
    al PESO REALMENTE RECOMENDADO, usando los números ya cacheados en `fit` (no re-descarga historia).
    Antes el MCR se mostraba a un 5% fijo que no correspondía al tamaño Kelly sugerido."""
    try:
        rc = (fit or {}).get("risk_contribution") or {}
        sb = float(rc.get("book_vol_pct") or 0) / 100.0
        sc = float(fit.get("ticker_vol_pct") or 0) / 100.0
        rho = float(fit.get("corr_to_book") if fit.get("corr_to_book") is not None else 0.0)
        a = max(0.0, min(float(add_weight), 1.0))
        if sb <= 0 or sc <= 0 or a <= 0:
            return rc
        snew = math.sqrt(max(((1 - a) ** 2) * sb ** 2 + (a ** 2) * sc ** 2
                             + 2 * a * (1 - a) * rho * sb * sc, 1e-12))
        d_vol = snew - sb
        wavg = (1 - a) * sb + a * sc
        div_ratio = round(snew / wavg, 3) if wavg > 0 else None
        mcr = a * (a * sc ** 2 + (1 - a) * rho * sb * sc) / snew if snew > 0 else 0.0
        mcr_share = round(mcr / snew * 100, 1) if snew > 0 else None
        var_1m_delta = round(1.645 * d_vol / math.sqrt(12) * 100, 2)
        conc = bool((fit.get("factors") or {}).get("concentrates_dominant"))
        verdict = ("concentra" if (rho >= 0.8 or conc) else "diversifica" if rho < 0.5 else "moderado")
        return {"book_vol_pct": round(sb * 100, 1), "new_vol_pct": round(snew * 100, 1),
                "delta_vol_pct": round(d_vol * 100, 2), "add_weight_pct": round(a * 100, 1),
                "mcr_share_pct": mcr_share, "diversification_ratio": div_ratio,
                "var_1m_95_delta_pct": var_1m_delta, "verdict": verdict,
                "at_recommended_weight": True}
    except Exception:
        return (fit or {}).get("risk_contribution") or {}


def format_portfolio_fit(fit):
    """Compact text block on how the ticker fits the user's book, for the AI prompt."""
    if not fit:
        return "N/A (sin portafolio cargado — analiza tu portafolio primero para activar la consciencia de cartera)"
    held = (f"YA lo tienes ({fit['current_weight_pct']}% del book de {fit['book_positions']} posiciones)"
            if fit.get("already_held") else f"NO esta en tu book ({fit['book_positions']} posiciones)")
    if fit.get("note"):
        return f"{held}; {fit['note']}"
    parts = [held]
    if fit.get("corr_to_book") is not None:
        parts.append(f"correlacion con tu portafolio {fit['corr_to_book']}")
    if fit.get("beta_to_book") is not None:
        parts.append(f"beta a tu book {fit['beta_to_book']}")
    if fit.get("ticker_vol_pct") is not None:
        parts.append(f"vol anual {fit['ticker_vol_pct']}%")
    if fit.get("diversification"):
        parts.append(f"diversificacion {fit['diversification']}")
    if fit.get("most_correlated"):
        parts.append(f"mas correlacionado con {fit['most_correlated']['ticker']} ({fit['most_correlated']['corr']})")
    return "; ".join(parts)


def format_factor_risk(fit):
    """Compact factor-exposure + marginal-risk block for the AI prompt (#2)."""
    if not fit:
        return "N/A"
    f = fit.get("factors")
    rc = fit.get("risk_contribution")
    if not f and not rc:
        return "N/A (historial insuficiente para descomposicion por factores)"
    out = []
    if f:
        lab = f.get("factor_labels", {})
        cb = f.get("candidate_betas", {})
        out.append("betas de factor del candidato [" +
                   ", ".join(f"{lab.get(k, k)} {v:+.2f}" for k, v in cb.items()) +
                   f"] (R2 {f.get('candidate_r2')})")
        out.append(f"factor dominante del candidato: {lab.get(f['candidate_dominant'], f['candidate_dominant'])}; "
                   f"de tu book: {lab.get(f['book_dominant'], f['book_dominant'])}")
        if f.get("concentrates_dominant"):
            out.append(f"** CONCENTRA tu factor ya dominante ({lab.get(f['book_dominant'], f['book_dominant'])}) — poca diversificacion **")
        de = f.get("delta_exposure_at_add", {})
        big = sorted(de.items(), key=lambda x: abs(x[1]), reverse=True)[:2]
        if big:
            out.append(f"mayor cambio de exposicion al anadir {f.get('add_weight_pct')}%: " +
                       ", ".join(f"{lab.get(k, k)} {v:+.3f}" for k, v in big))
    if rc:
        out.append(f"al anadir {rc['add_weight_pct']}%: vol del book {rc['book_vol_pct']}%->{rc['new_vol_pct']}% "
                   f"(D {rc['delta_vol_pct']:+}%), VaR-1m-95 D {rc['var_1m_95_delta_pct']:+}%, "
                   f"contribucion marginal al riesgo {rc.get('mcr_share_pct')}%, veredicto {rc.get('verdict')}")
    return "; ".join(out)


# ── OPTIONS INTELLIGENCE: GEX / gamma walls / max pain / positioning ──────────
# Computed FREE from the yfinance option chain (OI + IV per strike) + Black-Scholes
# gamma. No paid feed required. Modular by design: the returned dict carries empty
# slots for dark_pool / tape_flow so Unusual Whales can fill them later without
# changing any caller. (Real-time not needed — chain data is fine for GEX/levels.)
_GEX_CACHE = {}

def _safe_num(v, default=0.0):
    """float() that maps None / NaN / inf / bad values to a default (yfinance often
    returns NaN for missing OI/volume/IV, and 'nan or 0' is NaN since NaN is truthy)."""
    try:
        f = float(v)
        if math.isnan(f) or math.isinf(f):
            return default
        return f
    except (TypeError, ValueError):
        return default

def _json_safe(obj):
    """Recursively replace NaN/inf floats with None so json.dumps never 500s."""
    if isinstance(obj, float):
        return None if (math.isnan(obj) or math.isinf(obj)) else obj
    if isinstance(obj, dict):
        return {k: _json_safe(v) for k, v in obj.items()}
    if isinstance(obj, (list, tuple)):
        return [_json_safe(v) for v in obj]
    return obj

def _gamma_flip(rows, spot):
    """Spot price where net dealer gamma (recomputed across a grid) crosses zero."""
    if not rows:
        return None
    lo, hi, n = spot * 0.80, spot * 1.20, 41
    prev_tot = prev_S = None
    for i in range(n):
        S = lo + (hi - lo) * i / (n - 1)
        tot = 0.0
        for x in rows:
            sign = 1.0 if x["typ"] == "call" else -1.0
            g = _bs_greeks(S, x["K"], x["T"], x["iv"], 0.043, x["typ"])["gamma"]
            tot += g * x["oi"] * 100.0 * S * S * 0.01 * sign
        if prev_tot is not None and prev_tot * tot < 0 and (tot - prev_tot) != 0:
            return prev_S + (S - prev_S) * (0 - prev_tot) / (tot - prev_tot)
        prev_tot, prev_S = tot, S
    return None

def _max_pain(call_oi, put_oi):
    """Strike that minimizes total intrinsic payout to option holders (OI-weighted)."""
    strikes = sorted(set(list(call_oi.keys()) + list(put_oi.keys())))
    if not strikes:
        return None
    best, best_pay = None, None
    for Kx in strikes:
        pay = sum((Kx - Kc) * oi for Kc, oi in call_oi.items() if Kx > Kc)
        pay += sum((Kp - Kx) * oi for Kp, oi in put_oi.items() if Kx < Kp)
        if best_pay is None or pay < best_pay:
            best_pay, best = pay, Kx
    return best




def _et_now():
    """Hora actual en ET (America/New_York), con respaldo a EDT aproximado si no hay zoneinfo."""
    try:
        from zoneinfo import ZoneInfo
        return datetime.now(ZoneInfo("America/New_York"))
    except Exception:
        return datetime.utcnow() - timedelta(hours=4)


def _gex_ttl(base_ttl=300):
    """TTL adaptativo del GEX: en los últimos 90 min de sesión (lun-vie) acorta a 45s para que el GEX 0DTE
    se refresque más rápido cerca del cierre, cuando el gamma 0DTE manda el pin y se mueve rápido."""
    try:
        et = _et_now()
        if et.weekday() < 5:
            mins = (16 - et.hour) * 60 - et.minute      # minutos hasta el cierre 16:00 ET
            if 0 < mins <= 90:
                return min(base_ttl, 45)
    except Exception:
        pass
    return base_ttl



def format_options_intel(gex):
    """Compact GEX/levels block for the AI analyze prompt."""
    if not gex or not gex.get("ok"):
        return "N/A (cadena de opciones no disponible)"
    parts = [
        f"GEX neto {gex['net_gex']:,.0f} ({gex['net_gex_regime']})",
        (f"gamma flip ~${gex['gamma_flip']}" if gex.get("gamma_flip") else None),
        (f"call wall ${gex['call_wall']} (resistencia/iman arriba)" if gex.get("call_wall") else None),
        (f"put wall ${gex['put_wall']} (soporte abajo)" if gex.get("put_wall") else None),
        (f"max pain ${gex['max_pain']}" if gex.get("max_pain") else None),
        (f"P/C prima {gex['pcr_premium']}" if gex.get("pcr_premium") is not None else None),
        (f"P/C vol {gex['pcr_vol']}" if gex.get("pcr_vol") is not None else None),
        (f"P/C OI {gex['pcr_oi']}" if gex.get("pcr_oi") is not None else None),
    ]
    ua = gex.get("unusual_activity") or []
    if ua:
        parts.append("actividad inusual (flujo QD): " + ", ".join(
            f"{'⭐' if u.get('golden') else ''}{u['type']} ${u['strike']} {str(u['exp'])[5:]} vol {u['volume']} ({u['vol_oi']}x OI)" for u in ua[:3]))
    return "; ".join(p for p in parts if p)


# ── DIRECTIONAL TARGETS BY HORIZON (7/30/60/90/120d + 12m) ────────────────────
# Short horizons driven by options positioning (per-expiry GEX walls + flow bias +
# delta conviction); 12m is fundamental (the agent's DCF target, passed in).
def _nearest_expiry(exps, target_dte, now):
    best, bestdiff = None, 1e9
    for e in exps:
        try:
            dte = (datetime.strptime(e, "%Y-%m-%d") - now).days
        except Exception:
            continue
        if dte < 0:
            continue
        diff = abs(dte - target_dte)
        if diff < bestdiff:
            bestdiff, best = diff, (e, dte)
    return best


def _classify_flow_delta(flow, spot, iv_hint=0.4):
    """% of flow premium in directional-conviction deltas (0.60–0.90) vs speculative
    (0.30–0.50). Uses the trade's delta if present, else computes it via Black-Scholes."""
    if not flow:
        return None
    now = datetime.now()
    dir_prem = spec_prem = 0.0
    for t in flow:
        try:
            prem = abs(_safe_num(t.get("premium")))
            if prem <= 0:
                continue
            dlt = t.get("delta")
            if dlt is None:
                K = _safe_num(t.get("strike"))
                cp = str(t.get("cp") or "").lower()
                cp = "call" if cp.startswith("c") else "put"
                try:
                    T = max((datetime.strptime(str(t.get("exp"))[:10], "%Y-%m-%d") - now).days / 365.0, 1/365.0)
                except Exception:
                    T = 30/365.0
                if K <= 0 or spot <= 0:
                    continue
                dlt = _bs_greeks(spot, K, T, iv_hint, 0.043, cp)["delta"]
            ad = abs(_safe_num(dlt))
            if 0.60 <= ad <= 0.90:
                dir_prem += prem
            elif 0.30 <= ad < 0.60:
                spec_prem += prem
        except Exception:
            continue
    tot = dir_prem + spec_prem
    return round(dir_prem / tot * 100, 0) if tot > 0 else None



# (Función _flow_anchor_score eliminada junto con el motor de convicción ponderado.)














def _gamma_flip_from_strikes(rows, spot):
    """Gamma flip / zero-gamma level desde el perfil net-por-strike de Quant Data.
    Método: cruce de signo del net gamma por strike MÁS CERCANO al spot, interpolado, sobre un
    perfil suavizado (ventana 3) y restringido a la zona near-money. Se ata a los walls
    (entre el put wall negativo y el call wall positivo) y NUNCA devuelve un nivel absurdo.
    Reemplaza dos métodos previos que fallaban: (a) el primer-cruce-acumulado desde el strike más
    bajo (daba flip $3.11 con subyacente $194), y (b) la reconstrucción por kernel auto-calibrado
    (el peso n/Γ(spot) explotaba para strikes lejanos y empujaba el flip ~+5% de más, p.ej. $766
    con spot $731). Devuelve el cruce de cero más cercano al spot, o None si no hay uno near-money."""
    if not rows or spot is None or spot <= 0:
        return None
    # near-money: mata el ruido de strikes profundamente OTM (origen de los flips absurdos)
    pts = sorted([(_safe_num(r.get("strike")), _safe_num(r.get("net"))) for r in rows
                  if r.get("strike") is not None and 0.85 * spot <= _safe_num(r.get("strike")) <= 1.15 * spot],
                 key=lambda x: x[0])
    if len(pts) < 3:
        return None
    ks = [k for k, _ in pts]
    ns = [n for _, n in pts]
    sm = []                                       # suavizado ventana 3 (reduce ruido de strike a strike)
    for i in range(len(ns)):
        a = ns[max(0, i - 1)]; b = ns[i]; c = ns[min(len(ns) - 1, i + 1)]
        sm.append((a + b + c) / 3.0)
    best = None
    for i in range(len(ks) - 1):
        n1, n2 = sm[i], sm[i + 1]
        if n1 == 0:
            cross = ks[i]
        elif n1 * n2 < 0:                          # cruce de signo (put-gamma → call-gamma): aquí el net pasa por cero
            cross = ks[i] + (ks[i + 1] - ks[i]) * (0 - n1) / (n2 - n1)
        else:
            continue
        if best is None or abs(cross - spot) < abs(best - spot):   # el cruce más cercano al spot
            best = cross
    if best is None or not (0.85 * spot <= best <= 1.15 * spot):   # clamp de sanidad: nunca un flip absurdo
        return None
    return round(best, 2)


def _flip_confidence(rows, spot):
    """Qué tan LIMPIO/confiable es el nivel del gamma flip. Métrica principal: PUREZA DE SEPARACIÓN — en el
    perfil near-money, qué fracción de strikes tiene net-gamma negativo POR DEBAJO del flip y positivo POR
    ENCIMA (un flip de libro separa limpiamente las dos zonas → pureza ~1.0; un perfil que oscila alrededor de
    cero → pureza ~0.5, frágil). Reporta además la 'sharpness' (lo pronunciada que es la transición).
    Devuelve {level, score 0-100, purity, sharpness} o None."""
    if not rows or spot is None or spot <= 0:
        return None
    pts = sorted([(_safe_num(r.get("strike")), _safe_num(r.get("net"))) for r in rows
                  if r.get("strike") is not None and 0.85 * spot <= _safe_num(r.get("strike")) <= 1.15 * spot],
                 key=lambda x: x[0])
    if len(pts) < 3:
        return None
    ks = [k for k, _ in pts]
    ns = [n for _, n in pts]
    sm = [(ns[max(0, i - 1)] + ns[i] + ns[min(len(ns) - 1, i + 1)]) / 3.0 for i in range(len(ns))]
    typ = (sum(abs(x) for x in sm) / len(sm)) or 1.0
    flip, sharp = None, 0.0                                  # localiza el cruce near-money más cercano al spot
    for i in range(len(sm) - 1):
        if sm[i] * sm[i + 1] < 0:
            kk = ks[i] + (ks[i + 1] - ks[i]) * (0 - sm[i]) / (sm[i + 1] - sm[i])
            jump = abs(sm[i + 1] - sm[i]) / typ
            if flip is None or abs(kk - spot) < abs(flip - spot):
                flip, sharp = kk, jump
    if flip is None:
        return None
    below = [n for k, n in pts if k < flip]
    above = [n for k, n in pts if k >= flip]
    tot = len(below) + len(above)
    ok = sum(1 for n in below if n <= 0) + sum(1 for n in above if n >= 0)
    purity = (ok / tot) if tot else 0.0
    score = max(5.0, min(100.0, 100.0 * purity))            # la pureza manda; la sharpness es informativa
    level = "alta" if score >= 75 else ("media" if score >= 55 else "baja")
    return {"level": level, "score": round(score), "purity": round(purity, 2), "sharpness": round(sharp, 2)}



_QD_EXPWALL_CACHE = {}





def _next_earnings_date(tk):
    """Next earnings datetime from yfinance (or None). Lets targets flag horizons that cross a report."""
    try:
        cal = getattr(tk, "calendar", None)
        ed = None
        if isinstance(cal, dict):
            v = cal.get("Earnings Date")
            ed = v[0] if isinstance(v, (list, tuple)) and v else v
        elif cal is not None:
            try:
                ed = cal.loc["Earnings Date"][0]
            except Exception:
                ed = None
        if ed is None:
            return None
        if hasattr(ed, "to_pydatetime"):
            ed = ed.to_pydatetime()
        if isinstance(ed, datetime):
            return ed
        return datetime(ed.year, ed.month, ed.day)
    except Exception:
        return None


def _expected_move(spot, ann_vol, dte):
    """1-sigma expected move in $ over `dte` calendar days: spot · vol · sqrt(T)."""
    try:
        T = max(_safe_num(dte), 1) / 365.0
        return spot * ann_vol * math.sqrt(T)
    except Exception:
        return 0.0


def _clamp_target(level, spot, direction, em, k_max=1.5):
    """Bound a far-OTM wall to spot ± k_max·(expected move) so a deep strike can't become an
    absurd short-horizon target (e.g. $240 on a $368 spot in 30d). Returns (level, capped)."""
    if not level or spot <= 0 or em <= 0:
        return level, False
    if direction == "up":
        cap = spot + k_max * em
        if level > cap:
            return round(cap, 2), True
    elif direction == "down":
        cap = spot - k_max * em
        if level < cap:
            return round(cap, 2), True
    return level, False


_BT_MIN_HZ_N = 8    # muestra mínima por horizonte para confiar en su hit-rate
_BT_MIN_DIR_N = 12  # muestra mínima para confiar en el acierto direccional global


def _wilson_lower(hits, n, z=1.0):
    """Piso (lower bound) del intervalo de Wilson para una proporción binaria. Penaliza muestra chica
    automáticamente: n baja → intervalo ancho → piso bajo. z=1.0 ≈ 68% de confianza. Devuelve fracción 0-1."""
    if not n or n <= 0:
        return None
    p = max(0.0, min(1.0, hits / n))
    denom = 1.0 + z * z / n
    centre = p + z * z / (2 * n)
    margin = z * math.sqrt((p * (1 - p) + z * z / (4 * n)) / n)
    return max(0.0, (centre - margin) / denom)


def _confidence_from_hit(pct):
    """Map an empirical hit-rate (%) to a confidence label — replaces fixed rules with the
    ticker's real backtest accuracy when available."""
    if pct is None:
        return None
    if pct >= 65:
        return "muy alta"
    if pct >= 55:
        return "alta"
    if pct >= 45:
        return "media"
    if pct >= 35:
        return "media-baja"
    return "baja"



_HZTGT_CACHE = {}


@app.get("/api/projection-targets")
def projection_targets(ticker: str, ai_12m: float = 0.0, horizons: str = "10,20,30"):
    """Targets de Proyecciones — motor de Víctor (Tito Metralleta).

    Sustituye al motor de gamma/flujo (`compute_horizon_targets`) SOLO en este
    panel: devuelve tres escenarios bear/base/bull por horizonte, anclados al
    nodo imán del GEX y recortados al cono de 2σ, más los niveles por
    confluencia precio ∩ opciones.

    `compute_horizon_targets` sigue vivo y alimentando el prompt del agente,
    `_reconcile` y el self-test — ahí no se tocó nada, así que la comparación
    σ/DCF vs gamma/flujo mantiene su contraparte independiente.

    `ai_12m` se acepta por compatibilidad con el frontend viejo; el motor de
    Víctor no lo usa (su base sale del imán del GEX, no de un target externo).

    Horizontes: **10 / 20 / 30 días**, exactamente los `HORIZONS` de Víctor
    (`prediction.py`), con default 20. El panel viejo de Vertex daba hasta 120
    días; eso se descarta a propósito — el escenario base se ancla al nodo imán
    del GEX, y a 90-120 días la cadena que produjo ese imán ya habrá rotado casi
    entera, así que el número existiría pero no significaría lo mismo. Víctor
    corta en 30 y esa es la razón.

    Los 320/120/90 días de `SCOREDCARD/Inusualidad.md` son otro eje: son las
    bandas de DTE del CONTRATO para puntuar Inusualidad (premiar LEAPs sobre
    lotería semanal), no el plazo de la proyección. Viven en `expiry_score`.
    """
    sc = _tito_mod()
    if sc is None:
        return {"ok": False, "error": "Motor de Víctor no disponible (engine/wbj/tito)."}

    from wbj.tito.marketsnack import MarketSnackError, fetch_flow

    tk, err = _tito_ticker(ticker)
    if err:
        return {"ok": False, "error": err}

    try:
        hz = tuple(int(h) for h in horizons.split(",") if h.strip())[:5] or (10, 20, 30)
    except ValueError:
        hz = (10, 20, 30)

    trades, conviction_trades, flow_error = _tito_tape(tk)

    try:
        chain, bars, spot, _meta_cadena = _tito_chain_and_bars(tk)
    except Exception as e:
        # Sin cadena no hay Estructura, ni GEX, ni niveles, ni escenarios. Se
        # devuelve el motivo exacto de Massive en vez de un reporte a medias.
        return {"ok": False, "error": _error_de_fuente(e, "Cadena de Massive"),
                "source": "massive"}

    now = datetime.now(timezone.utc)
    mem = _tito_memory(tk, conviction_trades or trades, chain, bars, now)

    # El motor es un port LITERAL: lanza donde su TypeScript lanza (un
    # `symbol` que no es texto, un `timestamp` nulo, una fila `null` del tape).
    # Eso es correcto dentro del motor y es lo que mide `diff_motor.sh`; lo que
    # no puede pasar es que salga como un 500 sin explicación. El borde de
    # Vertex lo traduce al mismo sobre `{ok: false, error}` que usan las demás
    # rutas — filtrar es trabajo del borde, no del motor.
    try:
        r = sc.run_scorecard(
            tk, trades, chain or [], bars, now=now, spot=spot, horizons=hz,
            iv_history=mem["iv_history"],
            past_flows=mem["past_flows"],
            calibration=mem["calibration"],
            conviction_trades=conviction_trades,
        )
    except Exception as e:                       # noqa: BLE001 — se reporta, no se traga
        return {"ok": False, "error": _error_publico(e, "Motor de Víctor"),
                "source": "motor"}
    out = _tito_json(r)
    out["engine"] = "victor/tito"
    out["chain_source"] = "massive"
    out["memory"] = mem["stats"]
    # Las noticias NO viajan aquí: van en /api/tito-news, igual que Víctor las
    # tiene en su propia ruta y su propio panel. Acoplarlas al scorecard haría
    # que 4 feeds RSS lentos retrasaran los targets, que es lo que de verdad
    # importa — y un feed caído no debe hacer esperar a nadie.
    # Si la predicción no se pudo guardar, entra en la MISMA lista que las otras
    # tres escrituras. El panel ya la pinta; lo que faltaba era que ésta llegara.
    _falla_pred = _tito_remember(tk, r, now)
    if _falla_pred:
        _st = out.get("memory") or {}
        _st["escrituras_fallidas"] = (_st.get("escrituras_fallidas") or []) + [_falla_pred]
        out["memory"] = _st
    if flow_error:
        out["flow_error"] = flow_error
        out["warnings"] = [f"Sin tape de MarketSnack: {flow_error}"] + out.get("warnings", [])
    # Diagnóstico explícito de QUÉ sub-agente está apagado y POR QUÉ.
    #
    # El aviso viajaba solo dentro de `warnings`, que el panel pinta en 9px al
    # final. Con la cookie caducada el resultado es un scorecard que se ve
    # entero —score, veredicto, targets— sostenido por UNA sola categoría, y
    # había que saber leer una línea de texto para enterarse. Aquí va la lista
    # nombrada, la fuente que le falta a cada una y qué hacer.
    _FUENTE = {
        "aggression": ("marketsnack", "Agresividad"),
        "conviction": ("marketsnack", "Convicción"),
        "unusuality": ("marketsnack", "Inusualidad"),
        "iv_context": ("marketsnack", "Contexto IV"),
        "structure":  ("massive", "Estructura"),
        "validation": ("memoria", "Confirmación de Precio"),
    }
    _apagados = [k for k, v in (out.get("scores") or {}).items() if v is None]
    if _apagados:
        _por_fuente = {}
        for k in _apagados:
            fuente, nombre = _FUENTE.get(k, ("desconocida", k))
            _por_fuente.setdefault(fuente, []).append(nombre)
        _ARREGLO = {
            "marketsnack": ("La cookie de MarketSnack no está sirviendo. NO es una API key: "
                            "es una cookie de sesión y CADUCA. Vuelve a copiarla del navegador "
                            "(DevTools → Network → /api/flow_feed → header Cookie) y pégala en "
                            "MARKETSNACK_COOKIE."),
            "massive":     ("Massive no devolvió cadena para este ticker. Revisa "
                            "/api/tito-health para ver si es la credencial, el plan o el ticker."),
            "memoria":     ("Todavía no hay historial suficiente: el sub-agente 6 necesita flows "
                            "guardados de días anteriores. Se llena solo con cada consulta, "
                            "siempre que WBJ_TITO_DATA sea un disco que persista."),
        }
        out["subagentes_apagados"] = {
            "total": len(_apagados), "de": 6,
            "grupos": [{"fuente": f, "categorias": c, "arreglo": _ARREGLO.get(f, "")}
                       for f, c in _por_fuente.items()],
        }
    # Serie histórica para que la gráfica dibuje sin una segunda llamada.
    # 70 velas es lo que pide Víctor (`SimpleChart`: bars.slice(-70)): con el
    # reparto 60/40 del lienzo, más velas encogen el cuerpo por debajo de lo
    # legible y el recorte lo acabaría haciendo `vcBuildScales` de todos modos.
    out["history"] = [
        {"time": b.time, "open": getattr(b, "open", b.close), "high": b.high,
         "low": b.low, "close": b.close}
        for b in bars[-70:]
    ]
    out["levels_for_chart"] = _tito_chart_levels(r)
    out["gex_heatmap"] = _tito_heatmap(chain or [], r, trades, now)
    out["chart_geometry"] = _tito_chart_geometry(r)
    out["flow_clusters"] = _tito_clusters(trades, now)
    # La ficha de la empresa (`CompanyHeader`) y la cadena entera
    # (`OptionChainTable` + `ChartPanel`). El motor no las necesita —puntúa con
    # `chain` ya normalizada— pero son tres de sus componentes, y esta es LA
    # ruta que come el panel. Servirlas aquí ahorra dos rondas de red más.
    out["company"] = _tito_company(tk, r.spot, _meta_cadena["empresa"])
    out.update(_tito_chain_json(chain or [], _meta_cadena["truncated"]))
    # Al archivo, en SU carpeta. Es la ruta que come el panel, así que es la
    # que ve el scorecard completo — con ficha de empresa, cadena y todo.
    _archiva_opciones(out)
    # `_json_safe` = su `JSON.stringify`: NaN/Infinity → null. `_tito_json` ya
    # pasó por ahí, pero estos campos se añaden después.
    return _json_safe(out)


#: Pasos del cono y de las rutas. Son los de su `SimpleChart`: `conePoints(…, 24)`
#: y `wigglePath(…, steps = 30)`.
_CONO_STEPS = 24
_RUTA_STEPS = 30

#: Los tres escenarios de su `SimpleChart`, con la semilla que da a cada ruta su
#: forma. El orden define el z-order al dibujar.
_ESCENARIOS = (("bull", 1.7), ("base", 4.1), ("bear", 8.3))


def _tito_chart_geometry(r):
    """El cono y las rutas de la gráfica, calculados con SUS funciones.

    Su `SimpleChart` importa `conePoints` y `predictionPath` de
    `lib/expectedMove.ts` y los llama en el componente. Aquí la gráfica es un
    SVG del navegador, así que la geometría se calcula en el servidor —con el
    port de esas dos funciones— y viaja ya resuelta.

    Por qué se hace así y no en JS: la fórmula estaba escrita **dos veces**, una
    en `expected_move.py` (sin llamador) y otra a mano dentro de
    `renderVictorProjChart`. Dos copias de la misma matemática es la forma más
    barata de que una se quede atrás sin que nadie lo note; `diff_cono.sh` medía
    la del navegador contra su archivo, pero nada garantizaba que las dos copias
    coincidieran entre sí.

    El `target` que se devuelve es el de `prediction_path` —ya recortado al cono
    de 2σ—, que es exactamente lo que su `SimpleChart` etiqueta: *"El target es
    el de `predictionPath` (ya recortado al cono), no el crudo."*

    Nunca tumba la respuesta: si algo falla, devuelve `None` y la gráfica cae a
    su fórmula local. Ilustra, no decide.
    """
    try:
        from wbj.tito.expected_move import (cone_points, expected_move,
                                            prediction_path)
    except Exception:
        return None
    spot = r.spot
    # `Math.max(iv, 0.01)` es SU suelo, dentro de `expected_move`. Aquí solo se
    # evita el caso sin dato: sin IV no hay cono que dibujar.
    iv = (r.gex.iv if r.gex and r.gex.iv else 0.0) or 0.4
    if not (spot > 0):
        return None
    geo = {}
    for dias, pred in (r.predictions or {}).items():
        try:
            cono = cone_points(spot, iv, float(dias), _CONO_STEPS)
            rutas = {}
            for clave, seed in _ESCENARIOS:
                esc = getattr(pred, clave, None)
                objetivo = getattr(esc, "target", None)
                if objetivo is None:
                    continue
                ruta = prediction_path(spot, float(objetivo), iv, float(dias), _RUTA_STEPS)
                rutas[clave] = {
                    "seed": seed,
                    "target": round(ruta.target, 4),
                    # `clamped` avisa de que el escenario pedía más de lo que la
                    # volatilidad da. La gráfica no puede dibujar fuera del cono.
                    "clamped": bool(ruta.clamped),
                    # `PredictionPath.points` son tuplas `(t, precio)`, que es su
                    # `{ t, price }[]` sin nombre de campo.
                    "points": [{"t": round(t, 4), "price": round(precio, 4)}
                               for t, precio in ruta.points],
                }
        except Exception:
            continue
        # Las tres `wall-stat` de su `ProWallsCard`: el cono se DIBUJABA pero
        # no se escribía. Se veía la banda y no había forma de leer cuánto
        # valía — «±6,4% · $184 – $209 · 68% de los escenarios» es el número
        # con el que se decide un strike, y solo estaba como forma en un SVG.
        # Es su misma `expectedMove`, ya portada y medida por `diff_cono.sh`.
        try:
            em = expected_move(spot, iv, float(dias))
            banda = {"sigma_pct": round(em.sigma_pct, 4),
                     "lower1": round(em.lower1, 4), "upper1": round(em.upper1, 4),
                     "lower2": round(em.lower2, 4), "upper2": round(em.upper2, 4)}
        except Exception:
            banda = None
        geo[str(dias)] = {
            "iv": round(iv, 6),
            "em": banda,
            "cone": [{"t": round(c.t, 4),
                      "upper1": round(c.upper1, 4), "lower1": round(c.lower1, 4),
                      "upper2": round(c.upper2, 4), "lower2": round(c.lower2, 4)}
                     for c in cono],
            "paths": rutas,
        }
    return geo or None


def _tito_clusters(trades, now, top=6):
    """Racimos del tape: bursts agresivos contiguos en la misma dirección.

    Es `detectClusters` de su `flow.ts`, que su `FlowPriceChart` usa para marcar
    en la gráfica DÓNDE se concentró la agresión en vez de dejar el tape como
    una lista plana. Lo que aporta y el scorecard no: la **apuesta neta** de
    cada racimo —comprar puts es bajista, no alcista— y su ventana temporal.

    Se recortan a los `top` de mayor premium: la gráfica no puede dibujar
    cuarenta marcas y el resto ya está en el scorecard.
    """
    if not trades:
        return None
    try:
        from wbj.tito.flow import classify_flow, detect_clusters
        notables = classify_flow(trades, now).interesting
        cl = detect_clusters(notables)
    except Exception:
        return None      # ilustra, no decide: nunca tumba los targets
    # `c.premium` llega crudo del tape y puede ser texto (el `+` de JS
    # concatena, ver `flow.detect_clusters`): ordenar con `-c.premium` reventaba
    # la petición entera por un solo trade mal serializado.
    from wbj.tito.jsmath import js_clave, js_number
    cl = sorted(cl, key=lambda c: -js_number(c.premium))[:top]
    return [{
        "start_sec": c.start_sec, "end_sec": c.end_sec, "count": c.count,
        "premium": c.premium, "direction": c.direction, "score": c.score,
        "unidirectionality": _r(c.unidirectionality, 4),
        "bet": c.bet, "bet_label": c.bet_label,
        "call_premium": c.call_premium, "put_premium": c.put_premium,
        # Los strikes que tocó el racimo, para poder marcarlo en el eje de precio.
        # `js_clave` para deduplicar: un strike que llegue como lista es
        # inhashable en Python y su `Set` lo acepta sin pestañear.
        "strikes": sorted({js_clave(t.strike): t.strike
                           for t in c.trades}.values(), key=js_number),
    } for c in cl] or None


def _tito_heatmap(chain, r, trades, now):
    """GEX por strike × vencimiento — el mapa en sus dos dimensiones.

    El GEX que ya sirve el scorecard es un agregado: un número por strike, con
    todos los vencimientos sumados. Eso esconde lo que el heatmap enseña — que
    un mismo strike puede ser muro en el vencimiento de esta semana y no serlo
    en el de enero, y que el gamma se concentra en los de corto plazo. Es el
    `GexHeatmapCard` de su página.

    Las entradas se arman como en su `page.tsx`: los `HeatTrade` salen de unir
    los trades de convicción con los inusuales, deduplicados por `id`, y solo
    aportan `strike`, `expiration`, `gamma` y `premium`.

    Aquí se pasa `conviction_flow` a secas, y es lo mismo **porque los inusuales
    salen de ahí**: `unusuality_score(conviction_rows)`, así que la unión no
    añade ninguna fila. Es un invariante, no una coincidencia, y lo fija
    `TestLasTresUnionesDeSuPagina` — el día que la inusualidad se calcule sobre
    otro universo, este heatmap dejaría de ver esas filas y el test lo dice.

    Lo que NO se puede hacer es rehacerlo sobre los 5 días de `notable`: ése es
    el universo de los NIVELES, que sí usa la unión ancha. Son tres conjuntos
    distintos en su `page.tsx` y se parecen lo justo para confundirse.
    """
    try:
        from wbj.tito.gex_heatmap import HeatTrade, gex_heatmap
    except Exception:
        return None
    if not chain or not (r.spot > 0):
        return None
    try:
        filas = r.conviction_flow or r.flow.interesting
        ht = [HeatTrade(strike=t.strike, expiration=t.expiration,
                        gamma=t.gamma, premium=t.premium) for t in filas]
        h = gex_heatmap(chain, spot=r.spot, iv=r.gex.iv, now=now, trades=ht)
    except Exception:
        # El heatmap ILUSTRA; no puede tumbar los targets, que son lo que el
        # panel necesita. Mismo criterio que él con el guardado de la cadena.
        return None
    if not h.cells:
        return None
    return {
        "spot": _r(h.spot),
        "iv": _r(h.iv, 4),
        "total_net_gex": h.total_net_gex,
        "max_abs_cell": h.max_abs_cell,
        "strikes": [{"strike": s.strike, "net_gex": s.net_gex, "call_gex": s.call_gex,
                     "put_gex": s.put_gex, "open_interest": s.open_interest,
                     "distance_pct": _r(s.distance_pct)} for s in h.strikes],
        "expirations": [{"expiration": e.expiration, "dte": e.dte,
                         "net_gex": e.net_gex, "open_interest": e.open_interest}
                        for e in h.expirations],
        "cells": [{"strike": c.strike, "expiration": c.expiration, "net_gex": c.net_gex,
                   "call_gex": c.call_gex, "put_gex": c.put_gex,
                   "open_interest": c.open_interest, "intensity": _r(c.intensity, 4)}
                  for c in h.cells],
        "hottest_positive": (None if h.hottest_positive is None else
                             {"strike": h.hottest_positive.strike,
                              "expiration": h.hottest_positive.expiration,
                              "net_gex": h.hottest_positive.net_gex}),
        "hottest_negative": (None if h.hottest_negative is None else
                             {"strike": h.hottest_negative.strike,
                              "expiration": h.hottest_negative.expiration,
                              "net_gex": h.hottest_negative.net_gex}),
    }


def _tito_chart_levels(r, max_per_side=2, min_strength=25):
    """Niveles para la gráfica: los 2 soportes y 2 resistencias más CERCANOS con
    fuerza real, exactamente el filtro de `SimpleChart` de Víctor.

    El recorte no es un capricho: la lista completa mete tanto ruido que tapa
    los escenarios, que es lo que la gráfica tiene que comunicar. El orden es
    por distancia absoluta al spot, no por precio — un soporte al 2% importa
    más que uno al 20% aunque sea más fuerte.
    """
    out = []
    for group in (r.levels.supports, r.levels.resistances):
        picked = sorted(
            (l for l in group if l.strength >= min_strength),
            key=lambda l: abs(l.distance_pct),
        )[:max_per_side]
        for l in picked:
            out.append({
                "price": _r(l.price), "kind": l.kind,
                "strength": l.strength, "why": l.why,
            })
    return out


# ─────────────────────────────────────────────────────────────────────────────
# SCORECARD DE FLUJO (motor Tito) — capa PROPIA dentro de Proyecciones
#
# NO compite con compute_horizon_targets ni lo reemplaza. Son dos lecturas
# distintas del mismo mercado y se reportan por separado, igual que ya se hace
# con σ/DCF vs gamma/flujo: el consumidor las reconcilia, el servidor no las
# promedia. Toda la matemática vive en engine/wbj/tito/ (puro, 282 tests); aquí
# solo hay I/O y traducción a JSON.
# ─────────────────────────────────────────────────────────────────────────────

def _tito_mod():
    """Importa el motor Tito con el engine en el path. None si no está disponible."""
    try:
        if _WBJ_ENGINE_PATH not in sys.path:
            sys.path.insert(0, _WBJ_ENGINE_PATH)
        from wbj.tito import scorecard as _sc
        return _sc
    except Exception:
        return None


def _tito_ticker(ticker, default=""):
    """Valida el ticker EN EL BORDE. Devuelve `(tk, error)`; uno de los dos es None.

    Este es el sitio que en su repo ocupan las rutas de Next: el ticker llega de
    `searchParams`, se normaliza y se rechaza antes de tocar un store. Los
    ports de `store.ts` y `barsStore.ts` son literales precisamente porque esta
    comprobación existe aquí.

    Lo que atrapa y un `.strip().upper()` no: `"!!!"`, `"@@@"`, `"ñ"` pasan el
    "no está vacío" y luego el `fileFor` de Víctor los sanea TODOS a la cadena
    vacía — o sea que escriben en el mismo `.json` y mezclan la memoria de un
    ticker con la de otro. Y no es teórico: `fetch_option_chain` no lanza con
    una cadena vacía (devuelve `rows=[]`), así que el camino se alcanza con
    cualquier cosa que el usuario escriba en la caja.
    """
    crudo = (ticker or default or "").strip()
    if not crudo:
        return None, "Ticker vacío."
    if _WBJ_ENGINE_PATH not in sys.path:
        sys.path.insert(0, _WBJ_ENGINE_PATH)   # `tito_health` valida ANTES de importar el motor
    try:
        from wbj.tito.borde import TickerInvalido, ticker_valido
    except Exception:
        # Sin motor no hay panel, y el llamador ya lo reporta con su propio
        # mensaje. Aquí no se inventa una validación de repuesto: sería un
        # segundo saneado distinto del de Víctor, que es justo lo que `borde`
        # existe para evitar.
        return crudo.upper(), None
    try:
        return ticker_valido(crudo), None
    except TickerInvalido as e:
        return None, str(e)


def _tito_tape(ticker):
    """Las DOS descargas de tape de su `/api/flow`, no una.

    Su ruta baja el flujo dos veces con parámetros distintos, y de ahí salen
    dos universos que puntúan cosas distintas:

    1. **Agresividad** — `period` (5d), premium ≥ $100K, 6 páginas. Es el pulso
       reciente: "¿el dinero de esta semana está entrando al ask?".
    2. **Convicción, Inusualidad y Contexto IV** — `period: "1m"`, premium
       ≥ $1M, 15 páginas y `targetDays: 30`. El comentario de su archivo lo
       dice: *"Convicción revisa una ventana de 30 días (nota del documento)"*.

    El port corría los seis sub-agentes sobre la PRIMERA descarga. Tres de las
    seis categorías puntuaban entonces sobre un universo diez veces más barato y
    seis veces más corto que el suyo — el score no era el mismo número.

    Si la ventana ancha falla, se cae a la corta: es literalmente lo que hace su
    `catch` (*"si falla la ventana ancha, Convicción se calcula con los 5
    días"*). Devuelve `(trades, conviction_trades, error)`.
    """
    from wbj.tito.marketsnack import MarketSnackError, fetch_flow
    from wbj.tito.scorecard import (CONVICTION_DAYS, CONVICTION_MAX_PAGES,
                                    CONVICTION_MIN_PREMIUM, LEAN_MAX_PAGES,
                                    MIN_PREMIUM)

    # Los dos de la ventana corta iban a mano —`100_000` y `6`— teniendo el
    # nombre a un import de distancia. Coincidían con los suyos, así que no
    # cambiaba ningún número; lo que fallaba era el ACOPLE: `/api/tito-tape`
    # baja exactamente esta misma ventana y sí los usa por nombre, así que si
    # Víctor sube `LEAN_MAX_PAGES` a 10, la cinta bajaría 10 páginas y el
    # scorecard seguiría en 6 — las dos pantallas puntuando sobre universos
    # distintos sin que nada lo dijera. Y el cotejo de constantes de la
    # auditoría solo ve los nombres: un número suelto le es invisible.
    try:
        trades = fetch_flow(ticker, period="5d", min_premium=MIN_PREMIUM,
                            max_pages=LEAN_MAX_PAGES).trades
        error = None
    except MarketSnackError as e:
        # Sin tape el motor sigue corriendo con la cadena (GEX + estructura),
        # pero 4 de las 6 categorías quedan sin dato. Se reporta el motivo en
        # vez de devolver un número que aparenta estar completo.
        trades, error = [], str(e)

    anchos = None
    if trades:
        try:
            r = fetch_flow(ticker, period="1m",
                           min_premium=CONVICTION_MIN_PREMIUM,
                           max_pages=CONVICTION_MAX_PAGES,
                           target_days=CONVICTION_DAYS)
            anchos = r.trades or None
        except MarketSnackError:
            anchos = None          # su `catch`: Convicción se queda con los 5 días
    return trades, anchos, error


def _tito_chain_and_bars(ticker):
    """Cadena + barras diarias desde **Massive**, la fuente que usa Víctor.

    Fuente única a propósito: **no hay respaldo a yfinance**. Un fallback
    silencioso a otro proveedor es peor que un error — cambia los datos bajo
    los pies del scorecard sin que nadie se entere, y dos corridas dejan de
    ser comparables. Si Massive no responde, el endpoint lo dice y no publica
    un número.

    El motor no conoce a Massive: recibe `ChainRow`/`LvlBar` ya normalizados,
    así que cambiar de proveedor no toca una línea de `wbj/tito/`.

    Devuelve `(rows, bars, spot)`. Levanta `MassiveError` con el motivo exacto
    (key ausente, key rechazada, rate limit, ticker sin datos).
    """
    from wbj.tito.bars_store import daily_bars_for_panel
    from wbj.tito.massive import MassiveError, fetch_company, fetch_option_chain

    chain_res = fetch_option_chain(ticker)
    # Barras diarias CON cache. Su cabecera de `barsStore.ts` dice que en v1 el
    # store solo lo usa Wheel, y durante todo el port se respetó; se enchufa
    # ahora porque el motivo que él escribió para Wheel —"las barras diarias
    # solo cambian una vez al día"— vale igual aquí: Proyecciones las pide en
    # CADA consulta y el panel se auto-refresca.
    #
    # No se usa su `cached_daily_bars` a pelo: su regla es "cachea por día de
    # mercado" y eso, en un panel en vivo, congela la vela de hoy a media
    # sesión, sella el archivo sin la barra del día si Massive publica tarde y
    # pierde el cache justo el fin de semana. `daily_bars_for_panel` es la capa
    # de política de Vertex y ancla el cache en el DATO —la última sesión
    # cerrada— en vez de en el reloj. Su función queda intacta y verificable.
    bars = daily_bars_for_panel(ticker)
    if not bars:
        raise MassiveError(f"Massive no devolvió barras diarias para {ticker}.")
    # La cadena puede venir vacía (subyacente sin opciones listadas) y el motor
    # lo sabe manejar: Estructura sale NOT_SCORABLE y salta la salvaguarda de
    # liquidez. Sin barras, en cambio, no hay nada que calcular.
    # El SPOT, en el orden exacto de su `page.tsx`:
    #
    #     company?.price ?? chainMeta?.underlyingPrice ?? bars[bars.length - 1].close
    #
    # `company.price` es el snapshot del subyacente (última operación);
    # `underlying_price` es el precio con el que Massive calculó ESA cadena, que
    # no es el mismo cuando la cadena viene de caché o el papel se movió después.
    # El spot ancla los nodos del GEX, la ventana de ±20% que decide qué strikes
    # entran, los niveles, el cono y los tres targets: cogerlo del eslabón
    # equivocado mueve el panel entero en silencio.
    #
    # Se copia su `??` TAL CUAL, y eso incluye lo que NO hace: el `??` solo salta
    # `null`/`undefined`. Un `price: 0` del snapshot **no** se salta — se queda en
    # 0 y entonces su guarda `if (!spot || spot <= 0) return null` corta y NO da
    # lectura. Es deliberado suyo: si el snapshot dice 0 el feed está mal, y
    # bajar en silencio a otro precio es justo el fallback callado que la
    # cabecera de este módulo llama peor que un error. Aquí se corta igual, con
    # el motivo escrito, en vez de publicar un scorecard sobre un precio que
    # nadie pidió.
    empresa = fetch_company(ticker) or {}
    def _nn(*candidatos):
        """`a ?? b ?? c` — devuelve el primero que no sea nulo, no el primero
        que sea "bueno". Un 0 gana a los siguientes, igual que en su archivo."""
        for v in candidatos:
            if v is not None:
                return v
        return None
    spot = _nn(empresa.get("price"), chain_res.underlying_price, bars[-1].close)
    if not isinstance(spot, (int, float)) or isinstance(spot, bool) or spot <= 0:
        # El mensaje SI mejora: un 0 y una clave mala se veian igual, y se
        # arreglan en sitios distintos. `snapshot 0` significa que Massive
        # CONTESTO --la credencial vale-- y devolvio el bloque `day` en ceros,
        # que es lo que Polygon --de quien Massive hereda el modelo-- manda
        # cuando el plan no cubre el intradia. Mandar a revisar la clave por
        # eso es mandar a arreglar lo que no esta roto.
        #
        # Lo que NO cambia es la decision de cortar. Se intento bajar al cierre
        # anterior y `test_el_nulo_baja_pero_el_cero_NO_baja` lo atrapo, con
        # razon: en un panel de opciones el spot ancla los nodos del GEX, la
        # ventana de strikes, los niveles, el cono y los tres targets. El cierre
        # de ayer no degrada el panel, lo coloca mal entero y lo presenta como
        # actual.
        raise MassiveError(
            f"Massive no devolvió un precio utilizable para {ticker} "
            f"(snapshot {empresa.get('price')!r} · cadena {chain_res.underlying_price!r})."
            + (" Un 0 en el snapshot significa que la clave SÍ funciona y el plan "
               "no cubre el intradía: mira /api/tito-health."
               if empresa.get("price") == 0 else ""))
    # La ficha y el aviso de truncado salen por aquí porque ya están: pedir
    # `fetch_company` otra vez desde el payload doblaba la latencia de cada
    # scorecard sobre el MISMO dato. `truncated` es el tope de páginas de
    # Massive — que la cadena llegó incompleta— y solo lo sabe esta función.
    return chain_res.rows, bars, float(spot), {"empresa": empresa,
                                               "truncated": chain_res.truncated}


def _tito_memory(ticker, trades, chain, bars, now):
    """Lee la memoria acumulada y guarda la foto de hoy.

    Es lo que enciende las tres piezas que una sola foto del mercado no puede
    dar: el IV Rank real (sub-agente 5), el backtest de flows (sub-agente 6) y
    la auto-calibración de los targets.

    Nunca revienta la petición: si el disco no está disponible, el motor corre
    igual con lo que haya y `stats` lo refleja.

    Pero degradar en silencio es peor que fallar: sin memoria el scorecard sale
    igual de bonito y con menos evidencia detrás. Por eso `stats.motivo` dice
    SIEMPRE por qué se apagó, y `/api/tito-health` lo repite.
    """
    def _empty(motivo):
        return {"iv_history": [], "past_flows": [], "calibration": None,
                "stats": {"available": False, "motivo": motivo}}

    try:
        from wbj.tito import borde
        from wbj.tito import stores as st
        from wbj.tito.flow import classify_flow
        from wbj.tito.ivcontext import iv_context_score
        from wbj.tito.structure import structure_score
    except Exception as e:
        return _empty(f"el motor no carga: {type(e).__name__}")

    # 1. Guardar la foto de hoy ANTES de leer: así el primer día ya cuenta.
    #
    # Cada escritura va en su propio try, como en su `/api/flow`: allí el
    # `saveTrades` está envuelto con el comentario *"el guardado no debe romper
    # el reporte"*, y su `/api/ideas` hace lo mismo con `.catch(() => null)`.
    # Con un solo try alrededor de todo el bloque, un fallo al ESCRIBIR se
    # llevaba también la LECTURA — o sea el IV Rank real, el sub-agente 6 y la
    # calibración— cuando lo que hay en disco estaba perfectamente bien.
    escrituras: list[str] = []
    guardado = None            # el `SaveResult` de su `saveTrades`

    def _guarda(nombre, fn):
        try:
            return fn()
        except Exception as e:                       # noqa: BLE001
            escrituras.append(f"{nombre}: {type(e).__name__}")
            return None

    if chain:
        # Su `/api/chain` hace `saveChainSnapshot(ticker, structure)`: guarda el
        # StructureScore ya calculado, no los miles de contratos que lo
        # produjeron. Es lo que permite reconstruir después POR QUÉ el
        # sub-agente 4 puntuó lo que puntuó un día concreto.
        _guarda("cadena",
                lambda: st.save_chain_snapshot(ticker, structure_score(chain), now))
        # Cada ticker que alguien mira alimenta al agente de OPCIONES. Su forma
        # de aprender no es la del agente de acciones: no hay calibración de
        # aciertos aquí, hay ACUMULACIÓN HACIA ADELANTE. La IV histórica, las
        # cadenas y el flujo pasado no se pueden comprar en ningún sitio — se
        # juntan una foto por día de mercado, y sin ellas el IV Rank real y el
        # sub-agente 6 se quedan apagados para siempre.
        #
        # Por eso mirar un ticker YA es aportar, aunque no salga ningún reporte:
        # la foto de hoy es lo que hará posible el rank de dentro de un año.
        try:
            _c = _db()
            try:
                _CU.registrar_contribucion(_c, "opciones", ticker,
                                           (_usuario_actual() or {}).get("id"))
            finally:
                _c.close()
        except Exception:                            # noqa: BLE001 — nunca rompe el panel
            pass
    # `trades` es la VENTANA ANCHA (30 d / ≥$1M) — los `convictionRows` que su
    # `/api/flow` persiste (`saveTrades(ticker, convictionRows)`), no los 5 días
    # de Agresividad.
    notable = classify_flow(trades, now).interesting if trades else []
    if notable:
        guardado = _guarda("trades", lambda: st.save_trades(ticker, notable))
        # `saveIvSnapshot(ticker, ivContext)` guarda `s.iv.current`, que es la
        # IV PONDERADA POR PREMIUM — el dinero grande define el contexto. Aquí
        # se hacía un promedio simple, que es el número que su propio módulo
        # descarta por dejarse dominar por los cientos de tickets de 0DTE. Ese
        # número es el que alimenta el IV Rank real durante meses.
        # `saveIvSnapshot(ticker, ivContext)` — el objeto entero, como él. La
        # guarda de «hay IV o no» vive DENTRO del store, que es donde su código
        # la tiene (`if (s.iv.current == null) return existing`).
        ivc = iv_context_score(notable, [], None)
        _guarda("iv", lambda: st.save_iv_snapshot(ticker, ivc, now))

    try:
        # 2. Leer lo acumulado. El filtro por asset_price/timestamp es el de su
        #    /api/validation: un trade sin precio de subyacente no se puede
        #    seguir hacia adelante, así que no entra al backtest.
        iv_history = st.load_iv_history(ticker)
        stored = st.load_trades(ticker)
        crudos = stored.trades if stored else []
        # `load_trades` es literal: devuelve el array del disco sin mirar su
        # contenido, igual que su `loadTrades`. El filtro por forma va aquí, en
        # el borde, que es donde su pipeline lo tiene: en TS `"basura".assetPrice`
        # es `undefined` y la fila se cae sola en este mismo `filter`.
        guardados = borde.trades_utiles(crudos)
        past = [t for t in guardados
                if (t.get("asset_price") or 0) > 0 and t.get("timestamp")]
        journal = st.load_journal(ticker)
        review = st.review_predictions(journal, bars, now)
        calibration = st.calibration_from_review(review)

        return {
            "iv_history": iv_history,
            "past_flows": past,
            "calibration": calibration,
            "stats": {
                "available": True,
                "iv_days": len(iv_history),
                "iv_rank_real_en": max(0, 60 - len(iv_history)),
                # Dos números, no uno: lo que hay en disco y lo que el backtest
                # puede usar. Si el tape pierde `asset_price` el archivo sigue
                # creciendo mientras el sub-agente 6 se queda sin nada — con un
                # solo contador eso se ve como "no se ha guardado nada".
                "flows_guardados": len(guardados),
                "flows_utilizables": len(past),
                "flows_descartados": len(guardados) - len(past),
                # Filas que ni siquiera son objetos: un archivo a medio escribir
                # o editado a mano. Se cuentan aparte de `flows_descartados`
                # porque no son un problema del tape, son un problema del disco.
                "flows_corruptos": len(crudos) - len(guardados),
                # Filas sin `id`. Su dedupe es `t.id` a secas: si el tape deja
                # de traer ese campo, `_base_row` mete 0 en todos y el `Map` se
                # queda con UN solo trade de la corrida — sin error y sin que
                # `added` avise. Es su comportamiento, portado tal cual; lo que
                # no puede es pasar mudo (upstream-tito-store.patch).
                "flows_sin_id": borde.trades_sin_id(crudos),
                # Un fallo de ESCRITURA ya no se lleva la lectura, pero tampoco
                # puede quedar mudo: la memoria se acumula hacia adelante y un
                # día que no se guarda no se recupera.
                "escrituras_fallidas": escrituras or None,
                # El `SaveResult` de su `saveTrades`, que su UI sí muestra y
                # aquí se estaba tirando. `memoria_desde` es el dato que dice si
                # el sub-agente 6 tiene recorrido que evaluar: 5000 flows de
                # esta semana no valen lo que 500 de hace tres meses.
                "flows_nuevos": guardado.added if guardado else 0,
                "memoria_desde": guardado.first_seen if guardado else (
                    min((t.get("timestamp") for t in past if t.get("timestamp")),
                        default=None)),
                "predicciones_vencidas": review.get("matured_count", 0),
                # La PRIMERA `mem-stat` de su `MemoriaCard`: «Error medio del
                # target ±X% sobre N predicciones». `review_predictions` la
                # calcula y aquí se tiraba, así que el panel enseñaba el sesgo
                # —hacia dónde falla— sin enseñar CUÁNTO falla. Y no son lo
                # mismo: +0,2% de sesgo con ±14% de error medio es un agente
                # descalibrado que parece calibrado, porque los fallos hacia
                # arriba y hacia abajo se cancelan en el promedio con signo.
                "error_medio_pct": review.get("mean_abs_error_pct"),
                "sesgo_pct": review.get("bias_pct"),
                "calibracion_activa": bool(
                    calibration.get("bias_pct") is not None
                    and calibration.get("samples", 0) >= 5
                ),
                "dir_hit_rate": review.get("direction_hit_rate"),
                # Su tercera `mem-stat`: «Tocó el target base — llegó al precio
                # previsto». `review_predictions` la calcula y la ruta la
                # tiraba. NO es lo mismo que el acierto de dirección: acertar
                # que sube y no llegar al precio son dos fallos distintos, y
                # con un solo número no se distinguen.
                "base_touch_rate": review.get("base_touch_rate"),
                "motivo": None,
                # El TRACK RECORD fila a fila — su `MemoriaCard`, que no enseña
                # un resumen sino una tabla: fecha, qué predijo, qué pasó de
                # verdad, cuánto se equivocó y qué escenario acertó.
                #
                # `review_predictions` ya se llamaba aquí arriba y sus `evals`
                # se tiraban: solo viajaban los agregados. O sea que el panel
                # decía "6 predicciones vencidas, sesgo +2%" y no había forma de
                # ver NINGUNA. Para lo único que existe esta sección —saber si
                # el agente acierta— el resumen es justo lo que no basta.
                #
                # Las 12 más recientes: `review.evals` ya viene ordenado con la
                # más nueva primero, y más de una docena no se lee.
                "evals": [
                    {"date": e.get("date"), "horizon_days": e.get("horizon_days"),
                     "matured": e.get("matured"), "spot": _r(e.get("spot")),
                     "base": _r(e.get("base")),
                     "actual_close": _r(e.get("actual_close")),
                     "error_pct": _r(e.get("base_error_pct"), 2),
                     "best": e.get("best"),
                     "direction_hit": e.get("direction_hit")}
                    for e in (review.get("evals") or [])[:12]
                ],
            },
        }
    except Exception as e:
        return _empty(f"{type(e).__name__}: {e}"[:200])


@app.get("/api/tito-health")
def tito_health(ticker: str = "AAPL"):
    """Diagnóstico del motor de Víctor: qué fuente está viva y qué falta.

    Responde la pregunta operativa "¿por qué mi scorecard sale incompleto?"
    tocando cada fuente de verdad, una por una, y diciendo qué hacer con la que
    falle. Es el equivalente en servidor del preflight local.

    Nunca imprime credenciales: solo si están puestas, su longitud y si sirven.
    """
    tk, err_tk = _tito_ticker(ticker, default="AAPL")
    checks = []

    def add(nombre, ok, detalle, arreglo=None, impacto=None):
        checks.append({"check": nombre, "ok": bool(ok), "detalle": detalle,
                       "arreglo": arreglo, "impacto": impacto})

    # 0. El ticker. Va primero porque sin él ninguna de las fuentes de abajo se
    #    puede tocar, y porque es el check que explica el fallo más silencioso
    #    del sistema: un ticker que el saneado de Víctor deja en nada.
    if err_tk:
        add("ticker", False, err_tk,
            "escribe el símbolo con letras, números, punto, guion o guion bajo",
            "sin ticker válido no se puede consultar ninguna fuente")
        return {"ok": False, "ticker": None, "checks": checks}
    add("ticker", True, f"{tk} (saneado como lo hace `fileFor`)")

    # 1. El motor
    sc = _tito_mod()
    add("motor", sc is not None,
        "wbj.tito cargado" if sc else "no se pudo importar engine/wbj/tito",
        None if sc else "revisa que engine/ esté en el despliegue",
        None if sc else "Proyecciones no funciona")
    if sc is None:
        return {"ok": False, "ticker": tk, "checks": checks}

    # 2. Massive — cadena y barras (2 de 6 sub-agentes + GEX + niveles)
    key = os.environ.get("MASSIVE_API_KEY", "").strip()
    add("MASSIVE_API_KEY", bool(key),
        f"presente ({len(key)} caracteres)" if key else "no está en el entorno",
        None if key else "ponla en Environment de Render",
        None if key else "sin cadena: no hay Estructura, GEX, niveles ni escenarios")
    if key:
        try:
            from wbj.tito.massive import (fetch_company, fetch_daily_bars,
                                          fetch_option_chain)
            # El SNAPSHOT del subyacente va PRIMERO y aparte, porque es el
            # primer eslabón del spot y el único que falla en silencio:
            # `fetch_company` se traga su error y devuelve None, así que si el
            # plan no cubre `/v2/snapshot/...` el panel sigue funcionando con el
            # precio de la cadena y nadie se entera de que el mejor precio
            # disponible no se está usando. Aquí sí se ve.
            _emp = fetch_company(tk)
            _px = (_emp or {}).get("price")
            add("massive.snapshot", isinstance(_px, (int, float)) and not isinstance(_px, bool) and _px > 0,
                (f"precio {_px}" if _px else "sin precio")
                + (" (el snapshot no respondió o el plan no lo cubre)" if not _emp else ""),
                None if _px else "comprueba que tu plan de Massive incluya /v2/snapshot de acciones",
                None if _px else "el spot cae al precio de la cadena, que puede ir por detrás del mercado")
            ch = fetch_option_chain(tk)
            add("massive.cadena", bool(ch.rows),
                f"{len(ch.rows)} contratos en {ch.pages} página(s)"
                + (" · TRUNCADA" if ch.truncated else ""),
                None if ch.rows else f"¿{tk} tiene opciones listadas?",
                None if ch.rows else "Estructura sin score y salvaguarda de liquidez activa")
            # EN DIRECTO a propósito, sin pasar por `daily_bars_for_panel`: el
            # trabajo de este check es probar que Massive responde, y una
            # respuesta servida del cache taparía justo la caída que busca.
            bars = fetch_daily_bars(tk)
            add("massive.barras", bool(bars), f"{len(bars)} barras diarias",
                None if bars else "sin barras el motor corta",
                None if bars else "Proyecciones devuelve error para este ticker")
            # …y aparte, si el cache del panel está sirviendo o no. Un cache que
            # nunca acierta es una llamada de más en cada consulta.
            from wbj.tito.bars_store import _ultima_sesion_cerrada, load_bars
            _cb = load_bars(tk)
            _corte = _ultima_sesion_cerrada(datetime.now(timezone.utc))
            _vale = bool(_cb and _cb.bars and _cb.bars[-1].time >= _corte)
            add("massive.barras.cache", True,
                (f"{len(_cb.bars)} barras hasta {_cb.bars[-1].time}" if _cb and _cb.bars
                 else "vacío")
                + (" · sirviendo" if _vale else
                   f" · se repedirá (última sesión cerrada: {_corte})"),
                None,
                None)
        except Exception as e:
            add("massive", False, str(e),
                "el mensaje dice si es la credencial (401) o el plan (403), y QUÉ ruta falló",
                "sin cadena: Proyecciones devuelve error, no un número parcial")

    # 3. MarketSnack — el tape (5 de los 6 sub-agentes)
    cookie = os.environ.get("MARKETSNACK_COOKIE", "").strip()
    add("MARKETSNACK_COOKIE", bool(cookie),
        f"presente ({len(cookie)} caracteres)" if cookie else "no está en el entorno",
        None if cookie else "DevTools en app.marketsnack.com → Network → /api/flow_feed → header Cookie",
        None if cookie else "5 de 6 sub-agentes sin dato; solo queda Estructura")
    if cookie:
        try:
            from wbj.tito.marketsnack import fetch_flow
            fl = fetch_flow(tk, period="1d", min_premium=100_000, max_pages=1)
            add("marketsnack.tape", True,
                f"{len(fl.trades)} trades notables (1d)"
                + (" · 0 puede ser mercado cerrado" if not fl.trades else ""),
                None, None)
        except Exception as e:
            caducada = "expirada" in str(e) or "caduc" in str(e).lower()
            add("marketsnack.tape", False, str(e),
                "la cookie caduca sola: sácala otra vez de DevTools y actualízala en Render"
                if caducada else "revisa la conectividad con app.marketsnack.com",
                "5 de 6 sub-agentes sin dato; el scorecard va incompleto y lo declara")

    # 4. Memoria — lo que enciende IV Rank real, sub-agente 6 y calibración
    try:
        from wbj.tito import borde
        from wbj.tito import stores as st
        d = st.data_dir()
        d.mkdir(parents=True, exist_ok=True)
        # Permiso de escritura, SIN escribir. Antes esto creaba y borraba un
        # `.health` en cada llamada, y `/api/tito-health` es un GET: un
        # prefetch, un escáner de enlaces o el back-forward del navegador lo
        # reejecutan sin que nadie lo pida. Lo fija
        # `tests_vertex/test_route_safety.py`.
        #
        # No se pierde nada real. El probe tampoco demostraba lo que parecía:
        # en el plan free de Render el directorio ES escribible —es un tmpfs— y
        # el probe pasaba igual; lo que se pierde ahí es la PERSISTENCIA, y eso
        # no se ve escribiendo, se ve en `flows_guardados` e `iv_days`, que son
        # escrituras de verdad acumuladas entre reinicios. Esos dos números ya
        # están unas líneas más abajo y son la prueba buena.
        escribible = os.access(d, os.W_OK)
        iv_days = len(st.load_iv_history(tk))
        _stored = st.load_trades(tk)
        # `load_trades` es literal y devuelve el array tal como está en disco;
        # el filtro por forma es el del borde, el mismo que usa `_tito_memory`.
        _crudos = _stored.trades if _stored else []
        _sanos = borde.trades_utiles(_crudos)
        flows = len(_sanos)
        add("memoria.disco", escribible,
            f"{d} con permiso de escritura" if escribible
            else f"{d} existe pero NO se puede escribir en él",
            None if escribible else
            "revisa el propietario y los permisos del volumen montado",
            None if escribible else
            "la memoria no se acumula: IV Rank en el proxy y sub-agente 6 apagado")
        add("memoria.iv", iv_days >= 60,
            f"{iv_days}/60 días de IV acumulados",
            None if iv_days >= 60 else f"faltan {60 - iv_days} sesiones; se acumula solo",
            None if iv_days >= 60 else "IV Rank usa el proxy de volatilidad realizada")
        usables = sum(1 for t in _sanos
                      if (t.get("asset_price") or 0) > 0 and t.get("timestamp"))
        add("memoria.flows", usables > 0,
            f"{flows} flows guardados"
            + (f" (tope {st.MAX_PER_TICKER}: ya rota lo más viejo)" if flows >= st.MAX_PER_TICKER else "")
            + (f", {usables} utilizables" if usables != flows else "")
            # `updatedAt` puede faltar en un archivo escrito a mano o por una
            # versión anterior: el port lo pasa tal cual, sin inventar cadena.
            + (f" · última escritura {_stored.updated_at}" if _stored and _stored.updated_at else ""),
            None if flows else "se acumulan con cada consulta",
            None if usables else "sub-agente 6 (Confirmación) sin score")
        # Filas que no son objetos. Su `byId.set(t.id, t)` sobre un `null`
        # tumba CADA guardado —y para siempre, porque la fila sigue en el
        # archivo—, así que esto no es cosmético: es la memoria del sub-agente 6
        # congelada. El port replica su comportamiento; el aviso es de Vertex.
        _rotas = len(_crudos) - len(_sanos)
        if _rotas:
            add("memoria.flows.corrupto", False,
                f"{_rotas} fila(s) corrupta(s) en trades/{tk}.json",
                "borra esas filas del archivo: mientras estén, cada `save_trades` "
                "se cae al recorrerlas y no se guarda un solo flow nuevo",
                "el historial del sub-agente 6 deja de crecer, en silencio")
        # Trades sin `id`. Su dedupe es `t.id` a secas, así que todos colisionan
        # en la misma clave y el `Map` conserva UNO de la corrida entera.
        _sin_id = borde.trades_sin_id(_crudos)
        if _sin_id:
            add("memoria.flows.sin_id", False,
                f"{_sin_id} de {len(_crudos)} trades guardados no traen `id`",
                "revisa que el tape de MarketSnack siga trayendo el campo `id`",
                "el dedupe los colapsa en uno solo: se guarda 1 trade por corrida")
        if flows and not usables:
            add("memoria.flows.formato", False,
                f"los {flows} trades guardados no traen asset_price/timestamp usables",
                "el tape de MarketSnack cambió de esquema: revisa que cada trade traiga "
                "`asset_price` y `timestamp`",
                "el archivo crece pero el sub-agente 6 no puede puntuar nada")

        # ── Las tres coberturas que faltaban ────────────────────────────────
        #
        # Este diagnóstico cubría las fuentes (Massive, MarketSnack) y dos de
        # las tres series que se acumulan (IV y flows). Las otras tres piezas
        # del aprendizaje no las miraba nadie, y son justo las que deciden si
        # el agente MEJORA con el tiempo o se queda estrenándose cada día.

        # 1. Predicciones — el lazo de calibración. Sin ellas los targets nunca
        #    se corrigen: el agente puede llevar seis meses apuntando un 8% de
        #    más y seguir apuntando lo mismo.
        _journal = st.load_journal(tk)
        _preds = len(_journal)
        _rev = st.review_predictions(_journal, [], datetime.now(timezone.utc))
        _venc = _rev.get("matured_count", 0)
        _cal = st.calibration_from_review(_rev)
        _muestras = _cal.get("samples", 0) or 0
        add("memoria.predicciones", _preds > 0,
            f"{_preds} guardadas · {_venc} vencidas · calibración "
            + (f"ACTIVA con {_muestras} muestras (sesgo {_cal.get('bias_pct')}%)"
               if _muestras >= 5 else f"en espera ({_muestras}/5 muestras)"),
            None if _preds else "se guardan solas en cada análisis de este ticker",
            None if _preds else "sin predicciones no hay track record ni calibración: "
                                "los targets nunca se corrigen solos")

        # 2. Cadenas — la serie que permite reconstruir POR QUÉ el sub-agente 4
        #    puntuó lo que puntuó un día concreto. Es la única evidencia que no
        #    se puede recomprar: Massive vende la cadena de HOY, no la del
        #    martes pasado.
        _cad = len(st.load_chain_history(tk))
        add("memoria.cadenas", _cad > 0,
            f"{_cad} fotos diarias de la estructura de la cadena",
            None if _cad else "se acumulan solas con cada análisis",
            None if _cad else "no se podrá comparar la estructura de hoy con la de "
                              "hace un mes: esa serie no se puede comprar después")

        # 3. EL ALMACÉN — el que decide si todo lo anterior sobrevive.
        #    En Render free el disco se borra en cada redeploy y cada vez que el
        #    servicio despierta. Sin respaldo, los tres contadores de arriba
        #    vuelven a cero solos y el agente se estrena cada semana sin que
        #    nadie lo note: los números que enseña son correctos, simplemente
        #    empiezan de nuevo.
        try:
            import vertex_almacen as _AL
            _foto = _AL.almacen.estado()    # `almacen` es la instancia, no una fábrica
            _resp = bool(_foto.get("respalda"))
            add("memoria.respaldo", _resp,
                (f"activo · rama `{_foto.get('rama')}` · {_foto.get('commits')} commits"
                 + (f" · último push {_foto.get('ultimo_push')}" if _foto.get("ultimo_push") else "")
                 ) if _resp else (_foto.get("motivo") or "sin respaldo"),
                None if _resp else "pon VERTEX_GIT_TOKEN en Render (fine-grained, "
                                   "Contents → Read and write sobre este repositorio)",
                None if _resp else "en Render free el disco se borra en cada redeploy: "
                                   "IV, flows, cadenas y predicciones vuelven a cero y "
                                   "el agente se estrena otra vez")
        except Exception as e:                       # noqa: BLE001
            add("memoria.respaldo", False, f"no se pudo leer el almacén: {e}",
                "revisa vertex_almacen.py",
                "sin respaldo, la memoria no sobrevive a un redeploy")
    except Exception as e:
        add("memoria.disco", False, str(e),
            "en Render el plan free NO tiene disco: sube a starter y monta el volumen "
            "en WBJ_TITO_DATA=/var/data/tito",
            "IV Rank atascado en el proxy, sub-agente 6 apagado y sin auto-calibración")

    faltan = [c for c in checks if not c["ok"]]
    return {
        "ok": not faltan,
        "ticker": tk,
        "resumen": ("Todo en orden." if not faltan
                    else f"{len(faltan)} punto(s) por resolver: "
                         + ", ".join(c["check"] for c in faltan)),
        "checks": checks,
    }


#: Parámetros del escaneo de ideas — los de su `/api/ideas/route.ts`, literales.
_IDEAS_MIN_PREMIUM = 100_000   # piso server-side: flujo grande, no solo institucional
_IDEAS_MAX_PAGES = 8
_IDEAS_PERIOD = "1d"           # el sizing usa el precio del trade: cuanto más fresco, mejor
_IDEAS_MAX_IDEAS = 60          # tope de filas devueltas (`MAX_IDEAS` suyo)
_IDEAS_MAX_HISTORY_TICKERS = 25  # tope de llamadas a Massive por escaneo


def _ideas_dedupe(rows):
    """Un solo trade por contrato: el de mayor premium. Su `dedupeByContract`."""
    mejor = {}
    for r in rows:
        prev = mejor.get(r.symbol)
        if prev is None or r.premium > prev.premium:
            mejor[r.symbol] = r
    return list(mejor.values())


# ── DIVERGENCIA DECLARADA · una respuesta, no un stream ─────────────────────
#
# Sus cuatro rutas largas (`/api/analyze`, `/api/flow`, `/api/ideas`,
# `/api/wheel`) son SSE: emiten entre 40 y 100 eventos `{type:"step", label}` y
# el navegador los va enseñando («Conectando con Massive…», «Revisando flujo —
# página 5», «NVDA: 3 candidatos»). Aquí las cuatro devuelven UN JSON al final.
#
# El motivo es el despliegue, no el gusto: esto corre detrás del proxy de Render
# en plan free, que no garantiza el paso de `text/event-stream` sin buffering —
# un stream a medio bufferizar es PEOR que ninguno, porque la pantalla se queda
# congelada en el paso 3 y parece colgada.
#
# Lo que NO cambia: el usuario no se queda mirando un punto fijo. Su propio
# `AnalysisLoader` ya COLAPSA los ~100 pasos en cuatro fases y su comentario lo
# dice — «no leemos el texto de cada paso, solo cuántos han llegado». Esa es la
# pantalla que se portó (`vcLoaderHTML`), con su curva asintótica y su tope del
# 97%; el contador avanza con el reloj en vez de con los eventos. La etiqueta
# fina («página 5 de 6») es lo único que se pierde, y solo mientras carga.
@app.get("/api/tito-ideas")
def tito_ideas(request: Request):
    """Screener de flujo inusual **en todo el mercado** — su `/api/ideas`.

    Es la respuesta a "no quiero tener que escribir un ticker para que el agente
    haga algo". En su app son cuatro pestañas: *Ticker* (el dashboard, que **sí**
    pide símbolo), *Ideas* (esto: escanea el mercado entero y no pide nada),
    *Wheel* y *Time & Sales*. Aquí las dos primeras conviven en el mismo tab —
    sin ticker manda Ideas, con ticker manda el scorecard.

    El pipeline es el suyo, en su orden:

    1. `fetch_market_flow` — la cinta SIN filtro de símbolo, con el piso de
       premium aplicado **server-side** para que el payload no explote.
    2. `classify_flow` — los mismos sub-agentes 1-3 del scorecard.
    3. Capa 1 de `risk.py`: `is_tradeable_idea` (calidad + inusualidad ≥5, el
       umbral del SCREENER, no el 7 institucional) y `within_moneyness` (±25%).
    4. El historial por ticker con `validation_score` — el sub-agente 6 como
       evidencia de si ese patrón se ha desarrollado antes.

    **DIVERGENCIA DECLARADA — el sizing sí se calcula aquí.** Su ruta devuelve
    los griegos y nada más, porque su app no tiene perfil de inversionista. Esta
    sí: `Perfil Inversionista/perfil.json` vive en el servidor, así que la capa 2
    de su propio `risk.py` (`size_flow`) se aplica aquí con ese perfil. El motor
    es el suyo, sin tocar; lo que cambia es que se le pasa un `RiskProfile` real
    en vez de dejar el hueco. Las ideas que no caben **no se esconden**: se
    marcan y se bajan al final.
    """
    sc = _tito_mod()
    if sc is None:
        return {"ok": False, "error": "Motor de Víctor no disponible (engine/wbj/tito)."}
    from wbj.tito.flow import classify_flow
    from wbj.tito.marketsnack import MarketSnackError, fetch_market_flow
    from wbj.tito.massive import fetch_daily_bars
    from wbj.tito.risk import (MONEYNESS_CAP, is_tradeable_idea,
                               passes_quality_filter, within_moneyness)
    from wbj.tito.stores import load_trades, save_trades
    from wbj.tito.validation import FlowLite, validation_score

    now = datetime.now(timezone.utc)
    try:
        res = fetch_market_flow(period=_IDEAS_PERIOD, min_premium=_IDEAS_MIN_PREMIUM,
                                max_pages=_IDEAS_MAX_PAGES)
    except MarketSnackError as e:
        return {"ok": False, "error": _error_de_fuente(e, "Cinta de MarketSnack"),
                "source": "marketsnack"}
    except Exception as e:                       # noqa: BLE001 — se reporta, no se traga
        return {"ok": False, "error": _error_publico(e, "Escaneo de ideas"), "source": "motor"}

    try:
        flow = classify_flow(res.trades, now)
        filas = flow.rows
    except Exception as e:                       # noqa: BLE001 — el port es literal y lanza donde él
        return {"ok": False, "error": f"{type(e).__name__}: {e}", "source": "motor"}

    # Por qué se cae cada contrato. Hace visible el trabajo de la capa 1: sin
    # esto, "0 ideas" y "el mercado está tranquilo" se ven igual en pantalla.
    rechazos = {"theta_alto": 0, "vencido": 0, "sin_theta": 0, "no_inusual": 0, "lejano": 0}
    for r in _ideas_dedupe(filas):
        q = passes_quality_filter(r)
        if not q.ok:
            rechazos[q.reason] = rechazos.get(q.reason, 0) + 1
        elif not is_tradeable_idea(r):
            rechazos["no_inusual"] += 1
        elif not within_moneyness(r):
            rechazos["lejano"] += 1

    operables = sorted(
        _ideas_dedupe([r for r in filas if is_tradeable_idea(r) and within_moneyness(r)]),
        key=lambda r: r.premium if isinstance(r.premium, (int, float)) else 0,
        reverse=True)[:_IDEAS_MAX_IDEAS]
    tickers = list(dict.fromkeys(r.underlying for r in operables))

    # Historial: SOLO para tickers que ya tienen flows guardados. Los demás
    # salen "sin historial" sin gastar una llamada a Massive.
    historial, con_guardado = {}, []
    for tk in tickers:
        try:
            guardado = load_trades(tk)
        except Exception:
            continue
        flows = [FlowLite(id=t.id, timestamp=t.timestamp, type=t.type, strike=t.strike,
                          expiration=t.expiration, asset_price=t.asset_price,
                          premium=t.premium, aggression=t.aggression)
                 for t in (getattr(guardado, "trades", None) or [])
                 if getattr(t, "asset_price", 0) and t.timestamp]
        if flows:
            con_guardado.append((tk, flows))
    for tk, flows in con_guardado[:_IDEAS_MAX_HISTORY_TICKERS]:
        try:
            bars = fetch_daily_bars(tk, 200)
        except Exception:
            continue
        if not bars:
            continue
        try:
            rep = validation_score(flows=flows, bars=bars, now=now)
        except Exception:
            continue
        historial[tk] = {"hit_rate": _r(rep.hit_rate["value"], 0),
                         "median_sessions": rep.speed["median_sessions"],
                         "resolved": rep.hit_rate["resolved"]}

    # ── FILTRO POR PERFIL — lo mismo que hace Wheel con el colateral ─────
    #
    # El perfil da DOS números que aquí mandan:
    #
    #   · el capital, que decide si el contrato te cabe siquiera;
    #   · el riesgo por operación, que decide cuántos te caben.
    #
    # `size_flow` es el mismo `risk.py` de Víctor que la Wheel usa: dice el
    # TECHO de contratos, nunca "compra". Y su capa 1 ya bloquea theta de
    # lotería, vencimientos cortos y cadenas ilíquidas.
    #
    # Lo que NO se hace: esconder las ideas que no te caben. Se marcan y se
    # bajan al final. Que una operación esté fuera de tu presupuesto es
    # información, no ruido — y ocultarla te dejaría creyendo que el mercado no
    # ofrecía nada.
    from wbj.tito.risk import RiskProfile, size_flow
    _perfil = _perfil_leer(request)
    _rp = RiskProfile(account_size=_perfil["capital"], tolerance_pct=_perfil["riesgo_pct"])
    _sizing = {}
    for r in operables:
        try:
            _sz = size_flow(r, _rp, _perfil_horizonte_dias(_perfil))
            _sizing[r.id] = {
                "max_contracts": _sz.max_contracts,
                "cost_per_contract": _r(_sz.cost_per_contract),
                "total_cost": _r(_sz.total_cost),
                "cost_pct_of_account": _r(_sz.cost_pct_of_account, 1),
                "binding": _sz.binding,
                # El dict `{reason, detail}` de su `QualityResult`. Se manda el
                # DETALLE, que es la frase legible; el dict entero llegaba a la
                # pantalla y se pintaba como «[object Object]».
                "blocked": (_sz.blocked or {}).get("detail") if _sz.blocked else None,
                "blocked_reason": (_sz.blocked or {}).get("reason") if _sz.blocked else None,
                # ── La quema de theta: seis campos que el motor calculaba y la
                # ruta tiraba ──────────────────────────────────────────────────
                #
                # Es la mitad de `size_flow` y la mitad de su `IdeaCard`: «el
                # theta se come $X al día por contrato: $Y en N días (Z% de la
                # cuenta)», y el aviso de que el contrato se consume ENTERO
                # dentro del horizonte. Sin esto, «te caben 3» es un techo sin
                # el precio de mantenerlos, que es justo lo que distingue una
                # opción de una acción.
                "burn_days": _sz.burn_days,
                "theta_burn_per_contract": _r(_sz.theta_burn_per_contract),
                "total_burn": _r(_sz.total_burn),
                "burn_pct_of_account": _r(_sz.burn_pct_of_account, 1),
                "fully_decays": _sz.fully_decays,
            }
        except Exception:                        # noqa: BLE001 — el port lanza donde él
            _sizing[r.id] = None
    # Las que caben primero; dentro de cada grupo, el premium manda.
    operables = sorted(
        operables,
        key=lambda r: (0 if (_sizing.get(r.id) or {}).get("max_contracts") else 1,
                       -(r.premium if isinstance(r.premium, (int, float)) else 0)))

    ideas = [{
        "id": r.id, "ticker": r.underlying, "symbol": r.symbol,
        "type": "call" if r.type == "unknown" else r.type,
        "strike": r.strike, "expiration": r.expiration, "dte": r.dte,
        "price": r.price, "theta": r.theta, "theta_pct_daily": _r(r.theta_pct_daily, 2),
        "delta": r.delta, "premium": r.premium, "size": r.size,
        "aggression": r.aggression, "asset_price": r.asset_price, "iv": r.iv,
        "open_interest": r.open_interest, "timestamp": r.timestamp,
        "unusual_score": getattr(r.scores, "total", 0),
        "repeated": bool(getattr(r.flags, "repeated", False)),
        "history": historial.get(r.underlying),
        # Cuántos contratos te caben CON TU perfil. `None` si el motor no pudo
        # dimensionar; `max_contracts: 0` si no te cabe ninguno, y el motivo.
        "sizing": _sizing.get(r.id),
    } for r in operables]

    # La cobertura del historial crece sola: cada escaneo guarda lo que vio,
    # igual que ya hacen chainStore e ivStore.
    guardados = 0
    for tk in tickers:
        propias = [r for r in filas if r.underlying == tk]
        if not propias:
            continue
        try:
            save_trades(tk, propias)
            guardados += 1
        except Exception:
            pass

    return _json_safe({
        "ok": True, "engine": "victor/tito", "ideas": ideas,
        "scanned": len(res.trades), "pages": res.pages, "truncated": res.truncated,
        "tickers": len(tickers), "with_history": len(historial),
        "saved_tickers": guardados, "rejected": rechazos,
        "min_premium": _IDEAS_MIN_PREMIUM, "moneyness_cap": MONEYNESS_CAP,
        # El perfil con el que se ordenó. Va en el payload para que la pantalla
        # pueda decir "con TU capital" en vez de dar una lista sin contexto.
        "perfil": {"capital": _perfil["capital"], "tolerancia": _perfil["tolerancia"],
                   "riesgo_pct": _perfil["riesgo_pct"],
                   "riesgo_por_trade": _perfil["riesgo_por_trade"],
                   # El tope POR POSICIÓN, que es otra cosa que el riesgo por
                   # operación: aquel dice cuánto puedes PERDER, este cuánto
                   # puedes DESPLEGAR. La pantalla enseñaba el primero con el
                   # rótulo del segundo, así que «capital máximo por trade»
                   # repetía el mismo $3.000 que ya salía arriba como 30%.
                   "max_posicion_pct": list(_perfil.get("max_posicion_pct") or [20, 30]),
                   # Los dos presupuestos de su `RiskProfileCard`. El de theta
                   # es un % de la CUENTA (`budgetsOf`: account * 5 / 100), no
                   # del riesgo por operación — con $1.000 al 15% son $50, no
                   # $7,50, y sobre el número equivocado se descartarían
                   # contratos perfectamente operables. Viaja calculado por el
                   # motor para que la pantalla no vuelva a decidirlo.
                   "theta_budget_pct": _THETA_BUDGET_PCT,
                   "theta_budget": _r(_perfil["capital"] * _THETA_BUDGET_PCT / 100),
                   # El horizonte con el que se dimensionó. `size_flow` quema
                   # theta hasta esta fecha, así que el «te cabe» CAMBIA con él:
                   # sin decirlo, el techo de contratos es un número sin unidad.
                   # Su app lo elige con un botón (`HORIZON_LABELS`); aquí sale
                   # del perfil, que es la parte que sí es de Kevin.
                   "horizonte": _perfil.get("horizonte"),
                   "horizonte_dias": _perfil_horizonte_dias(_perfil),
                   "caben": sum(1 for v in _sizing.values()
                                if (v or {}).get("max_contracts"))},
        "period": _IDEAS_PERIOD, "generated_at": now.isoformat(),
    })


#: `THETA_BUDGET_PCT` de su `risk.ts`, leído del motor y no escrito a mano:
#: es el mismo 5% que usa el sizing, y dos copias se desincronizan a la primera.
try:
    from wbj.tito.risk import THETA_BUDGET_PCT as _THETA_BUDGET_PCT
except Exception:                                # el motor puede no estar
    _THETA_BUDGET_PCT = 5.0


#: Concurrencia del escaneo Wheel — su `CONCURRENCY`. Cada ticker son dos
#: llamadas a Massive (cadena + barras); sin tope, 40 símbolos en paralelo se
#: comen la cuota de un tirón.
#: Las cuatro constantes de su `/api/flow` que faltaban, importadas POR NOMBRE
#: desde el motor en vez de escritas a mano en la llamada.
#:
#: Tres estaban aquí como números sueltos —el mismo valor que el suyo, pero sin
#: nombre y sin nadie que los cotejara— y la cuarta tenía otro valor: la tabla
#: de convicción servía 25 filas donde él sirve 150. Con el nombre, el cotejo de
#: constantes de la auditoría las ve; con el número suelto, no.
from wbj.tito.scorecard import (                                # noqa: E402
    _unir as _tito_unir,          # `[...a, ...b]` con dedupe por id, el suyo
    CONVICTION_TABLE_CAP as TITO_CONVICTION_TABLE_CAP,
    LEAN_MAX_PAGES as TITO_LEAN_MAX_PAGES,
    MIN_PREMIUM as TITO_MIN_PREMIUM,
    TABLE_CAP as TITO_TABLE_CAP,
)

_WHEEL_CONCURRENCY = 6

#: Reintentos ante un 429 de Massive, con espera creciente.
#:
#: 40 tickers × 2 llamadas en 6 hilos es una **ráfaga de ~80 peticiones en
#: segundos**, y los planes de Massive limitan por minuto. La firma es
#: inconfundible: unos pocos símbolos pasan y el resto cae de golpe — Kevin vio
#: "35 sin barras diarias" con 5 buenos.
#:
#: No va dentro de `massive.py` a propósito: ese módulo es port literal y su
#: `_get` no reintenta. Reintentar es política de Vertex, y vive aquí igual que
#: `borde.py` o `_tito_tape`.
_WHEEL_REINTENTOS = 3
_WHEEL_ESPERA_BASE = 1.5


def _wheel_con_reintento(fn, *a, **k):
    """Llama a `fn` y reintenta SOLO ante un 429, con espera creciente.

    Cualquier otro error se propaga tal cual: un 403 del plan no mejora por
    esperar, y reintentarlo solo retrasa el diagnóstico.
    """
    from wbj.tito.massive import MassiveError

    for intento in range(_WHEEL_REINTENTOS):
        try:
            return fn(*a, **k)
        except MassiveError as e:
            if getattr(e, "status", None) != 429 or intento == _WHEEL_REINTENTOS - 1:
                raise
            time.sleep(_WHEEL_ESPERA_BASE * (2 ** intento))
    return None


def _wheel_barras(ticker, now):
    """Barras diarias del escaneo, **con el motivo cuando fallan**.

    `cached_daily_bars` hace `.catch(() => [])` —es lo que hace su
    `cachedDailyBars`, y ahí está bien— pero eso convierte un 429, un 403 y un
    ticker sin datos en la misma lista vacía. En un escaneo de 40 símbolos eso
    es la diferencia entre "espera un momento" y "revisa tu plan".

    Se usa el parámetro `fetch` que el propio store expone para poder probarse
    sin red: aquí sirve para capturar el error sin tocar el port.
    """
    from wbj.tito.bars_store import cached_daily_bars
    from wbj.tito.massive import MassiveError, fetch_daily_bars

    fallo = {}

    def _traer(t, d):
        try:
            return _wheel_con_reintento(fetch_daily_bars, t, d)
        except MassiveError as e:
            fallo["motivo"] = ("fuente", str(e))
            raise
        except Exception as e:                   # noqa: BLE001
            fallo["motivo"] = ("error", f"{type(e).__name__}: {e}")
            raise

    bars = cached_daily_bars(ticker, 365, now, fetch=_traer)
    return bars, fallo.get("motivo")


@app.get("/api/tito-wheel")
def tito_wheel(request: Request, preset: str = "balanceado"):
    """Screener de la **Wheel** — su `/api/wheel`.

    Vender *cash-secured puts* sobre su universo curado de 40 símbolos. Por cada
    uno: cadena de puts acotada al DTE del preset, niveles del precio, IV Rank
    propio (proxy de volatilidad realizada, porque no hay serie de IV por
    ticker) y el flag de earnings. Después, `wheel_candidates` decide.

    Dos cosas de este motor se leen **al revés** que el resto del agente, y no
    es un error de copia — lo escribe él:

    - La banda de IV Rank está **invertida** respecto al sub-agente 5. Ahí el
      pico está en 16-30 porque el resto del agente COMPRA opciones y quiere
      vega barata; la Wheel VENDE y quiere la volatilidad cara.
    - Un rendimiento anualizado alto se **castiga**. Un screener que ordena por
      prima pone arriba justo las acciones a punto de desplomarse.

    **DIVERGENCIA DECLARADA — la asequibilidad sí se calcula aquí.** Su
    `wheelAfford.ts` corre en el cliente porque en su app el saldo vive en
    localStorage. Aquí el capital está en `Perfil Inversionista/perfil.json`, en
    el servidor, así que su misma función (`sort_by_afford_then_score`) corre en
    esta ruta. La fórmula es la suya, intacta.
    """
    sc = _tito_mod()
    if sc is None:
        return {"ok": False, "error": "Motor de Víctor no disponible (engine/wbj/tito)."}
    from wbj.tito.bars_store import cached_daily_bars
    from wbj.tito.earnings import earnings_for_ticker
    from wbj.tito.ivcontext import rank_within, realized_vol_series
    from wbj.tito.levels import LvlBar, find_levels
    from wbj.tito.massive import MassiveError, fetch_wheel_chain
    from wbj.tito.wheel import (MAX_SPREAD_PCT as WHEEL_MAX_SPREAD,
                                MIN_OI as WHEEL_MIN_OI, WHEEL_PRESETS,
                                CandidatesInput, wheel_candidates)
    from wbj.tito.wheel_universe import WHEEL_UNIVERSE

    p = WHEEL_PRESETS.get(preset if preset in WHEEL_PRESETS else "balanceado")
    now = datetime.now(timezone.utc)
    todos, fallidos, pasos = [], 0, []

    def _uno(sym):
        """Un ticker. Devuelve (candidatos, motivo) — **sin tocar estado
        compartido**: corren 6 hilos y un `contador += 1` desde varios pierde
        cuentas en silencio.

        El motivo importa tanto como los candidatos. La versión anterior metía
        tres desenlaces muy distintos en el mismo contador y los rotulaba todos
        "sin cadena": un 403 del plan, una cadena vacía de verdad y un ticker
        con cadena llena cuyos strikes no caen en la banda de delta del preset
        se veían idénticos en pantalla. Con 40 de 40 fallando, eso no dejaba
        forma de saber si el problema era la cuenta, el mercado o el filtro.
        """
        try:
            chain = _wheel_con_reintento(fetch_wheel_chain, sym.ticker,
                                         p.dte_min, p.dte_max, now=now)
        except MassiveError as e:
            return [], ("fuente", str(e))
        except Exception as e:                   # noqa: BLE001 — un ticker no tumba el escaneo
            return [], ("error", f"{type(e).__name__}: {e}")

        if not chain.quotes:
            return [], ("sin_cadena",
                        f"sin puts entre {p.dte_min} y {p.dte_max} días")

        # ¿Trae HORQUILLA la cadena? Es la pregunta que decide si este escaneo
        # puede dar algo, y hay que hacerla antes que ninguna otra.
        #
        # Sin bid/ask pasan dos cosas, las dos malas y ninguna evidente:
        #   · no hay `mid`, así que la IV implícita no se puede despejar y el
        #     delta se calcula con volatilidad REALIZADA — los strikes se salen
        #     de la banda del preset y el ticker sale como "fuera_de_banda";
        #   · y si alguno cae dentro, `liquidity_block` lo tumba por "sin_bid".
        #
        # Las dos rutas acaban en cero candidatos por el MISMO motivo, y
        # ninguna de las dos lo nombra. `last_quote` es un añadido de plan en
        # Massive: no es la key y no mejora reintentando.
        # Ya NO se corta por falta de horquilla: con `allow_missing_quote` el
        # motor puntúa igual usando la cascada de precio que él mismo escribió,
        # y el score cobra 0/15 en liquidez por no poder medir el spread. Solo
        # se anota para poder decirlo en pantalla.
        sin_horquilla = not any(q.bid is not None and q.ask is not None
                                for q in chain.quotes)

        # Las barras van ANTES que el spot: hacen falta igual para los niveles
        # y el IV Rank, y su último cierre es el respaldo que nunca falla.
        bars, fallo_barras = _wheel_barras(sym.ticker, now)
        if not bars:
            return [], fallo_barras or ("sin_barras",
                                        "Massive no devolvió barras diarias para este símbolo")

        # ── El SPOT, con la cadena de respaldo COMPLETA ──────────────────
        #
        # Su `page.tsx` lo resuelve así:
        #
        #     company?.price ?? chainMeta?.underlyingPrice ?? bars[last].close
        #
        # Aquí faltaba el TERCER eslabón, que es justo el que nunca falla: las
        # barras ya están descargadas, así que el último cierre sale gratis.
        # Sin él, un plan de Massive que no devuelva `underlying_asset.price`
        # en la cadena tumbaba los 40 símbolos de golpe.
        #
        # `fetch_company` NO se llama en este escaneo, y es deliberado: son 40
        # símbolos, o sea 40 peticiones extra cada 15 minutos, y el diagnóstico
        # demostró que en esta cuenta ese endpoint no responde. Donde sí
        # compensa —el tab de Ticker, UNA petición— se mantiene su precedencia
        # entera. Para strikes a 30-45 días, una fracción de punto en el spot
        # no mueve la banda de delta.
        spot = chain.spot
        if not isinstance(spot, (int, float)) or isinstance(spot, bool) or spot <= 0:
            spot = bars[-1].close
        if not isinstance(spot, (int, float)) or isinstance(spot, bool) or spot <= 0:
            return [], ("sin_precio",
                        "ni la cadena ni el último cierre dieron un precio utilizable")
        # Solo puts OTM. `fetch_wheel_chain` filtra cuando la cadena trae su
        # propio spot; si el precio vino de las barras, ese filtro no se aplicó.
        if chain.spot is None:
            chain.quotes = [q for q in chain.quotes if q.strike <= spot]
            if not chain.quotes:
                return [], ("sin_cadena", f"sin puts OTM bajo ${spot:.2f}")

        try:
            lvl = [LvlBar(time=b.time, high=b.high, low=b.low, close=b.close) for b in bars]
            niveles = find_levels(bars=lvl, spot=spot, now=now)

            # IV Rank propio: proxy de volatilidad realizada. No hay serie de IV
            # por ticker en este escaneo, así que se mide la volatilidad que el
            # precio REALIZÓ contra su propio año.
            rv = realized_vol_series([b.close for b in bars], 30)
            actual = rv[-1] if rv else None
            iv_rank = rank_within(rv, actual) if actual is not None else None

            # Earnings sobre el vencimiento más cercano de la ventana.
            # `front_skew=None` a propósito: este escaneo no computa el
            # sub-agente 5 por ticker, así que "dentro_confirmado" hoy nunca
            # dispara. Es su limitación, declarada en `earnings.py`.
            cerca = min(chain.quotes, key=lambda q: q.dte).expiration
            flag = earnings_for_ticker(sym.ticker, cerca, None, now)

            cands = wheel_candidates(CandidatesInput(
                ticker=sym.ticker, spot=spot, quotes=chain.quotes, preset=p,
                iv_rank=iv_rank, supports=niveles.supports, earnings=flag,
                fallback_iv=(actual / 100) if actual is not None else 0.4,
                # Massive no sirve horquilla de opciones en este plan —lo dice
                # su propio `compute.py`— y sin esto el screener sale SIEMPRE
                # vacío. La salvaguarda no se pierde: el `spread_pct` sigue sin
                # conocerse y el score cobra 0 de 15 en liquidez por ello.
                allow_missing_quote=True))
        except Exception as e:                   # noqa: BLE001
            return [], ("error", f"{type(e).__name__}: {e}")

        if not cands:
            # La cadena estaba, pero NINGÚN strike cae en la banda de delta del
            # preset. No es un fallo: es el preset diciendo que no hay nada de
            # su gusto en ese papel. Confundirlo con "sin cadena" mandaba a
            # revisar la API cuando había que cambiar de preset.
            return [], ("fuera_de_banda",
                        f"{len(chain.quotes)} puts, ninguno con delta "
                        f"{p.delta_min}-{p.delta_max}")
        return cands, ("__sin_horquilla__" if sin_horquilla else None)

    # `mapLimit(WHEEL_UNIVERSE, CONCURRENCY, …)` — el mismo tope en vuelo.
    with concurrent.futures.ThreadPoolExecutor(max_workers=_WHEEL_CONCURRENCY) as ex:
        resultados = list(ex.map(_uno, WHEEL_UNIVERSE))

    _MOTIVO = {
        "fuente":         "la fuente rechazó la petición",
        "error":          "error inesperado",
        "sin_precio":     "sin precio del subyacente",
        "sin_cadena":     "sin puts en la ventana de DTE",
        "sin_barras":     "sin barras diarias",
        "fuera_de_banda": "cadena OK, pero ningún strike en la banda de delta",
        "sin_horquilla":  "la cadena no trae bid/ask (last_quote)",
    }
    motivos, ejemplos, sin_horquilla_n = {}, {}, 0
    for sym, (cands, motivo) in zip(WHEEL_UNIVERSE, resultados):
        todos.extend(cands)
        if motivo == "__sin_horquilla__":
            sin_horquilla_n += 1
            pasos.append(f"{sym.ticker}: {sum(1 for c in cands if not c.blocked)} candidatos "
                         f"(sin horquilla)")
        elif motivo:
            clave, detalle = motivo
            motivos[clave] = motivos.get(clave, 0) + 1
            ejemplos.setdefault(clave, f"{sym.ticker}: {detalle}")
            pasos.append(f"{sym.ticker}: {detalle}")
        else:
            pasos.append(f"{sym.ticker}: {sum(1 for c in cands if not c.blocked)} candidatos")
    fallidos = sum(motivos.values())

    # ── ASEQUIBILIDAD — su `sortByAffordThenScore`, con TU capital ────────
    #
    # **DIVERGENCIA DECLARADA.** Su `wheelAfford.ts` corre en el cliente porque
    # en su app el saldo vive en localStorage. Aquí el capital está en
    # `Perfil Inversionista/perfil.json`, en el servidor, así que su propia
    # función corre aquí. La FÓRMULA es la suya, sin tocar: colateral ≤ caja, y
    # el orden es bloqueado → no asequible → score.
    #
    # Lo que NO se hace: esconder lo que no te cabe. Un put de 100 acciones de
    # NVDA no deja de existir porque tengas $1,000 — se marca y se baja.
    from wbj.tito.wheel_universe import sort_by_afford_then_score
    _perfil = _perfil_leer(request)
    pares = sort_by_afford_then_score(todos, _perfil["capital"])
    todos = [c for c, _ in pares]

    def _parte(x):
        return {"points": x.points, "max": x.max, "band": x.band, "why": x.why}

    def _fila(c, a=None):
        m, sc_ = c.metrics, c.score
        return {
            # Con TU capital: si el colateral cabe, y cuánto te falta si no.
            "afford": None if a is None else {"affordable": a.affordable,
                                              "shortfall": _r(a.shortfall)},
            "ticker": c.ticker, "strike": c.strike, "expiration": c.expiration,
            "dte": c.dte, "spot": _r(c.spot), "delta": _r(c.delta, 3),
            "iv": _r(c.iv, 4), "iv_source": c.iv_source,
            "open_interest": c.open_interest, "spread_pct": _r(c.spread_pct, 1),
            "blocked": c.blocked, "block_reason": c.block_reason,
            "premium": None if c.premium is None else {
                "price": _r(c.premium.price), "source": c.premium.source,
                "raw": _r(c.premium.raw)},
            "metrics": None if m is None else {
                "credit": _r(m.credit), "collateral": _r(m.collateral),
                "return_pct": _r(m.return_pct, 2), "annualized_pct": _r(m.annualized_pct, 1),
                "breakeven": _r(m.breakeven), "cushion_pct": _r(m.cushion_pct, 1),
                "prob_expire_worthless": _r(m.prob_expire_worthless, 1)},
            "score": None if sc_ is None else {
                "total": sc_.total, "annualized": _parte(sc_.annualized),
                "iv_rank": _parte(sc_.iv_rank), "cushion": _parte(sc_.cushion),
                "liquidity": _parte(sc_.liquidity), "earnings": _parte(sc_.earnings)},
        }

    con_candidatos = len({c.ticker for c in todos if not c.blocked})
    # Por qué se BLOQUEÓ cada contrato (distinto de por qué se cayó cada
    # ticker). Si todos caen por lo mismo, no es el mercado: es la fuente.
    _bloqueos = {}
    for c in todos:
        if c.blocked:
            _bloqueos[c.block_reason] = _bloqueos.get(c.block_reason, 0) + 1
    _POR = {
        "sin_bid": ("la cadena no trae precio de compra (bid)",
                    "Sin horquilla no se puede saber lo que cobrarías de verdad, y la "
                    "regla es no enseñar un número que no puedes cobrar. Si TODOS los "
                    "contratos caen aquí, tu plan de Massive no está sirviendo "
                    "`last_quote` en el snapshot de opciones — es un añadido de plan, "
                    "no un problema de la key."),
        "spread_ancho": (f"horquilla más ancha del {WHEEL_MAX_SPREAD}%",
                         "Entrar y salir se comería la prima."),
        "oi_bajo": (f"open interest bajo el mínimo de {WHEEL_MIN_OI}",
                    "Contrato poco negociado: difícil de cerrar."),
    }
    bloqueos = [{"motivo": k, "contratos": v,
                 "que_significa": _POR.get(k, (k, ""))[0],
                 "por_que": _POR.get(k, (k, ""))[1]}
                for k, v in sorted(_bloqueos.items(), key=lambda x: -x[1])]
    return _json_safe({
        "ok": True, "engine": "victor/tito",
        "candidates": [_fila(c, a) for c, a in pares[:120]],
        # El perfil con el que se ordenó — el mismo bloque que devuelve Ideas.
        "perfil": {"capital": _perfil["capital"], "tolerancia": _perfil["tolerancia"],
                   "riesgo_pct": _perfil["riesgo_pct"],
                   "riesgo_por_trade": _perfil["riesgo_por_trade"],
                   "caben": sum(1 for c, a in pares if not c.blocked and a.affordable)},
        "preset": p.label, "preset_id": p.id, "preset_explain": p.explain,
        "presets": [{"id": q.id, "label": q.label, "explain": q.explain,
                     "delta_min": q.delta_min, "delta_max": q.delta_max,
                     "dte_min": q.dte_min, "dte_max": q.dte_max,
                     "take_profit_pct": q.take_profit_pct, "roll_dte": q.roll_dte}
                    for q in WHEEL_PRESETS.values()],
        # Cuántos símbolos se puntuaron SIN horquilla. No es un fallo —el
        # score ya cobra 0/15 en liquidez por ello— pero quien mira la tabla
        # tiene derecho a saber que la prima sale del último precio y no del
        # bid, y que la liquidez no se pudo medir.
        "quotes_missing": sin_horquilla_n,
        "blocked_summary": bloqueos,
        "blocked_total": sum(_bloqueos.values()),
        "scanned": len(WHEEL_UNIVERSE), "failed": fallidos,
        "with_candidates": con_candidatos,
        # El desglose de POR QUÉ se cayó cada ticker, con un ejemplo real de
        # cada motivo. Es el mismo contrato que el screener de Ideas: sin esto,
        # "0 de 40" y "el mercado no ofrece nada" se ven igual en pantalla.
        "rejected": [{"motivo": k, "que_significa": _MOTIVO.get(k, k),
                      "tickers": v, "ejemplo": ejemplos.get(k, "")}
                     for k, v in sorted(motivos.items(), key=lambda x: -x[1])],
        # Su `degraded`: más de la mitad del universo caído. El escaneo devuelve
        # algo, pero decir "estos son los mejores" con medio universo sin mirar
        # sería mentir por omisión.
        "degraded": fallidos > len(WHEEL_UNIVERSE) / 2,
        "generated_at": now.isoformat(),
    })


#: Sus `CHART_PRESETS` de `/api/flow`: piso de premium y tope de páginas POR
#: VENTANA. Cuanto más corta la ventana, más bajo el piso — en 5 días hay poco
#: flujo de $1M, y con el piso alto la gráfica del dinero sale casi vacía.
#: El valor por defecto de un `days` que no esté en la tabla es el suyo.
_TITO_CHART_PRESETS = {5: (250_000, 30), 10: (500_000, 20), 30: (1_000_000, 15)}
_TITO_CHART_PRESET_DEFECTO = (500_000, 20)


@app.get("/api/tito-tape")
def tito_tape(ticker: str, period: str = "5d",
              min_premium: float = TITO_MIN_PREMIUM,
              days: int | None = None):
    """Time & Sales de un ticker — su `/flow`.

    Es la cinta cruda ya clasificada por los sub-agentes 1-3: cada operación con
    su lado de ejecución, su premium, sus griegos y su puntaje de inusualidad.
    A diferencia del scorecard, aquí **no se agrega nada**: se ve el flujo tal
    como entró, que es justo para lo que sirve esta pestaña.

    Con `days`, la MISMA ruta cambia de modo — igual que la suya, que mira
    `searchParams.get("days")` y devuelve JSON para la gráfica en vez del
    stream. Es el flujo que acompaña al marco temporal de la gráfica del
    dinero: 5 días, 10 o 30, cada uno con el piso de premium de su preset.
    """
    sc = _tito_mod()
    if sc is None:
        return {"ok": False, "error": "Motor de Víctor no disponible (engine/wbj/tito)."}
    from wbj.tito.flow import (UNUSUAL_TOTAL, aggression_score, classify_flow,
                               unusual_trade_score)
    from wbj.tito.marketsnack import MarketSnackError, fetch_flow

    tk, err = _tito_ticker(ticker)
    if err:
        return {"ok": False, "error": err}
    now = datetime.now(timezone.utc)
    # Modo gráfica: su `if (Number.isFinite(days) && days > 0)`. El piso y el
    # tope de páginas los pone la ventana, no el `min_premium` del tape.
    _grafica = days is not None and days > 0
    if _grafica:
        _piso, _paginas = _TITO_CHART_PRESETS.get(int(days),
                                                  _TITO_CHART_PRESET_DEFECTO)
    try:
        if _grafica:
            # `period: "1m"` + `targetDays` suyos: pagina hacia atrás hasta
            # cubrir la ventana y para. Sin `target_days` bajaría el mes entero.
            res = fetch_flow(tk, period="1m", min_premium=_piso,
                             max_pages=_paginas, target_days=int(days))
        else:
            res = fetch_flow(tk, period=period, min_premium=min_premium,
                             max_pages=TITO_LEAN_MAX_PAGES)
    except MarketSnackError as e:
        return {"ok": False, "error": _error_de_fuente(e, "Cinta de MarketSnack"),
                "source": "marketsnack"}
    except Exception as e:                       # noqa: BLE001 — se reporta, no se traga
        return {"ok": False, "error": _error_publico(e, "Cinta"), "source": "motor"}

    try:
        flow = classify_flow(res.trades, now)
        agg = aggression_score(flow.interesting)
    except Exception as e:                       # noqa: BLE001 — el port lanza donde él
        return {"ok": False, "error": f"{type(e).__name__}: {e}", "source": "motor"}

    def _fila(t):
        return {
            "id": t.id, "symbol": t.symbol, "underlying": t.underlying,
            "type": t.type, "strike": t.strike, "expiration": t.expiration,
            "dte": t.dte, "price": t.price, "size": t.size,
            "premium": t.premium, "aggression": t.aggression, "side": t.side,
            "bid": t.bid, "ask": t.ask, "delta": t.delta, "gamma": t.gamma,
            "theta": t.theta, "vega": t.vega, "iv": t.iv,
            "open_interest": t.open_interest, "volume": t.volume,
            "timestamp": t.timestamp, "expiry_status": t.expiry_status,
            "unusual": t.unusual, "unusual_score": getattr(t.scores, "total", 0),
            # Su `desglose` de `NotableTable`: «Volumen X/10 · Horario Y/10 ·
            # Repetición Z/10», que va en el `title` de la columna de Puntos.
            # Solo viajaba el total, y un 21/30 sin desglose no dice si vino del
            # tamaño de la orden, de la hora a la que entró o de cuántas veces se
            # repitió el contrato — que son tres señales distintas.
            "unusual_parts": {
                "volume": getattr(t.scores, "volume", 0),
                "timing": getattr(t.scores, "timing", 0),
                "repetition": getattr(t.scores, "repetition", 0),
            },
            # El OTRO puntaje de inusualidad, el de su `UnusualityCard`: seis
            # parámetros sobre 10 (Tam · Δ · Θ · Γ · Leg · Venc), por trade.
            #
            # Son dos escalas distintas y conviven: la de arriba (0-30) decide
            # qué fila se tiñe en la cinta; ésta (0-10) es la que da el score
            # del sub-agente 3 y la que compara con el umbral institucional de
            # 7. El panel enseñaba el PROMEDIO de los seis y ninguno por trade,
            # así que no había forma de ver POR QUÉ un contrato concreto puntúa
            # alto — si por el tamaño, por el delta o por vencer pasado mañana.
            "unusual_scores": (lambda s: {
                "size": s.size, "delta": s.delta, "theta": s.theta,
                "gamma": s.gamma, "leg": s.leg, "expiry": s.expiry,
                "total": s.total,
            })(unusual_trade_score(t)),
            "repeated": bool(getattr(t.flags, "repeated", False)),
            "multileg": bool(getattr(t.flags, "multileg", False)),
            "above_ask": bool(getattr(t.flags, "above_ask", False)),
            "below_bid": bool(getattr(t.flags, "below_bid", False)),
            "exceeded_oi": bool(getattr(t.flags, "exceeded_oi", False)),
            # Las tres que faltaban de su `Flags` de `NotableTable`. `big` y
            # `conv_delta` son las dos "calientes" —$1M+ y delta fuerte— y
            # `simultaneous` marca que otros contratos del mismo subyacente se
            # ejecutaron a la misma hora, que es lo que distingue una pata de
            # una apuesta suelta. Sin ellas la cinta enseñaba cuatro de sus
            # siete señales.
            "big": bool(getattr(t.flags, "big", False)),
            "conv_delta": bool(getattr(t.flags, "conv_delta", False)),
            "leap": bool(getattr(t.flags, "leap", False)),
            "simultaneous": bool(getattr(t.flags, "simultaneous", False)),
            "condition_code": t.condition_code,
            "condition_name": t.condition_name,
        }

    # `interesting.slice(0, TABLE_CAP)` suyo. Su `classifyFlow` ya devuelve
    # `interesting` ordenado por premium descendente, así que el `sorted` es
    # redundante y se queda por si la fuente cambia el orden. El tope era 120.
    if _grafica:
        # Su respuesta del modo gráfica, con sus mismos campos y su mismo tope
        # (`interesting.slice(0, 400)`). No lleva `aggression` ni `unusual_cut`:
        # no se pinta una cinta, se pinta el dinero sobre el precio.
        return _json_safe({
            "ok": True, "engine": "victor/tito", "ticker": tk,
            "days": int(days), "min_premium": _piso,
            "rows": [_fila(t) for t in flow.interesting[:400]],
            "truncated": res.truncated, "pages": res.pages,
            "generated_at": now.isoformat(),
        })

    filas = sorted(flow.interesting,
                   key=lambda t: t.premium if isinstance(t.premium, (int, float)) else 0,
                   reverse=True)[:TITO_TABLE_CAP]
    return _json_safe({
        "ok": True, "engine": "victor/tito", "ticker": tk,
        "trades": [_fila(t) for t in filas],
        "notable": len(flow.interesting), "total": len(flow.rows),
        "pages": res.pages, "truncated": res.truncated,
        "period": period, "min_premium": min_premium,
        "aggression": {"score": agg.score, "ratio": _r(agg.ratio, 4),
                       "premium_ask": agg.premium_ask, "premium_bid": agg.premium_bid,
                       "premium_mid": agg.premium_mid, "n": agg.n},
        # El corte con el que se tiñe una fila, servido en vez de repetido en
        # el panel. La leyenda decía «≥7/30» y el corte real es 24/30: el 7 es
        # de la OTRA escala de inusualidad —la de 0-10 por trade del sub-agente
        # 3— y con los dos números escritos en sitios distintos, uno se quedó
        # atrás. Ahora sale del motor, así que no puede volver a desfasarse.
        "unusual_cut": UNUSUAL_TOTAL,
        "generated_at": now.isoformat(),
    })

# ═══════════════════════════════════════════════════════════════════════════
#  PERFIL DEL INVERSIONISTA — UNO POR USUARIO
#
#  Dos piezas, y la separación entre ellas es la regla del proyecto:
#
#   · **Campos estructurados** (capital, tolerancia, horizonte, instrumentos).
#     Son NÚMEROS y listas cerradas, así que pueden alimentar un filtro
#     determinista — el que ordena las Ideas y el que dimensiona la Wheel.
#   · **Texto libre**, en palabras del inversionista. Va al prompt del agente
#     como contexto y **jamás** se convierte en un score: "sin evidencia no hay
#     número, sin número no hay score".
#
#  Antes había UN perfil global (`perfil.json`) y un solo `Kevin.md`. Con
#  cuentas de verdad eso ya no vale: dos personas compartirían capital y
#  tolerancia. Ahora el perfil vive en la fila del usuario, y su `.md` en
#  `Perfil Inversionista/usuarios/<nombre>-<id>.md`.
#
#  El `.md` existe porque `_load_investor_profile()` ya lo leía, y con él lo
#  leen Analyze y Explore. Lo único que cambió en esa función es CUÁL archivo
#  resuelve — el del usuario de la sesión, con `Kevin.md` de respaldo. Su
#  lógica, su prompt y su pantalla no se han tocado.
#
#  Las preguntas y el hashing viven en `vertex_cuentas.py`.
# ═══════════════════════════════════════════════════════════════════════════

_PERFIL_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                           "Perfil Inversionista")

#: El `.md` de referencia: el que Kevin escribió a mano. Es el respaldo cuando
#: no hay sesión (scripts, cron, el preflight) y el que da los valores por
#: defecto que hereda quien no contesta el cuestionario.
_PERFIL_MD_DEFECTO = os.path.join(_PERFIL_DIR, "Kevin.md")


def _perfil_leer(request=None):
    """El perfil del usuario de la sesión, o el de Kevin si no hay sesión.

    Devuelve siempre un diccionario utilizable: sin sesión y sin archivo, los
    valores por defecto del cuestionario. Nunca lanza — un análisis no puede
    caerse por un archivo de contexto.
    """
    # Sin `request` NO se cae al defecto: se mira el contexto que dejó el
    # middleware. Guardar aquí un `if request is not None` dejaba mudo justo al
    # camino que usa el engine —que no recibe `request`— y el especialista de
    # riesgo volvía a contarle a todo el mundo el capital por defecto.
    u = _usuario_actual(request)
    if u is not None:
        try:
            conn = _db()
            try:
                return _CU.leer_perfil(conn, u["id"])
            finally:
                conn.close()
        except Exception:                        # noqa: BLE001
            pass
    base = _CU.perfil_por_defecto()
    base.update(_CU.derivados(base))
    base["sin_contestar"] = _CU.preguntas_sin_contestar(base)
    return base


def _perfil_horizonte_dias(d):
    """El horizonte del perfil en días, para la quema de theta de `size_flow`."""
    return _CU.horizonte_dias(d)


def _perfil_para_el_engine():
    """Traduce el perfil de la sesión al diccionario que espera `risk.py`.

    Son dos vocabularios distintos —el cuestionario habla en español y en
    porcentajes enteros, el especialista en inglés y en fracciones— y traducir
    aquí es lo que evita que el engine tenga que saber del cuestionario.

    `fields_parsed` va relleno a propósito: estos campos NO se adivinaron de un
    markdown, los contestó una persona. `fields_defaulted` lleva las preguntas
    que siguen heredadas, para que el reporte pueda decir "este tope no lo
    fijaste tú" en vez de presentarlo como si sí.
    """
    d = _perfil_leer()
    pos = d.get("max_posicion_pct") or [20, 30]
    anios = _CU._anios_de_horizonte(d.get("horizonte"))
    heredadas = list(d.get("sin_contestar") or [])
    _CAMPOS = {"capital": "capital_usd", "horizonte": "horizon_years",
               "max_posicion_pct": "max_position_pct"}
    return {
        "objective": ",".join(d.get("objetivos") or []) or "capital_growth",
        "horizon_years": (anios[0], anios[1]),
        "max_loss_tolerance": d.get("tolerancia") or "agresivo",
        "style": d.get("tolerancia") or "aggressive_speculative",
        "capital_usd": float(d.get("capital") or 0),
        "max_position_pct": (pos[0] / 100.0, pos[1] / 100.0),
        "geography": ",".join(d.get("mercados") or []) or "us_only",
        "excludes": tuple(d.get("excluir") or ()),
        "source": f"cuestionario de {d.get('nombre') or 'el inversionista'}",
        "fields_parsed": [v for k, v in _CAMPOS.items() if k not in heredadas],
        "fields_defaulted": [v for k, v in _CAMPOS.items() if k in heredadas],
    }


# ═══════════════════════════════════════════════════════════════════════════
#  APRENDIZAJE COMPARTIDO
#
#  «Todo lo que analice cada usuario alimenta a los agentes en general.»
#
#  Los dos agentes aprenden de forma DISTINTA, y contarlos juntos escondería
#  precisamente lo que los diferencia:
#
#   · **Acciones** (Analyze/Explore) aprende por CALIBRACIÓN. Cada reporte
#     guarda convicción y objetivos de precio; el tiempo dice si acertó. Más
#     reportes de más gente = una curva de fiabilidad con más puntos. Es un
#     lazo cerrado: se puede comprobar si un «70%» acierta el 70% de las veces.
#
#   · **Opciones** (Proyecciones) aprende por ACUMULACIÓN HACIA ADELANTE. La IV
#     histórica, las cadenas y el flujo pasado no los vende nadie; se juntan
#     una foto por día de mercado. No hay nada que "acertar" aquí: hay serie o
#     no la hay. Sin ella el IV Rank real (sub-agente 5) y la confirmación de
#     precio (sub-agente 6) se quedan apagados.
#
#  Lo que se comparte es ESO. El análisis de cada quien es privado — esta ruta
#  no devuelve nunca quién analizó qué, solo cuánto hay y de cuántas personas.
# ═══════════════════════════════════════════════════════════════════════════

@app.get("/api/wbj-explicacion")
def wbj_explicacion(request: Request, report_id: str):
    """El 2º pase del LLM, a petición y DESPUÉS del análisis.

    Es la mitad que faltaba. La explicación existía desde hacía tiempo pero
    estaba detrás de `?explain=1`, y la pantalla nunca lo pedía: el texto libre
    del perfil viajaba hasta el prompt y ahí se paraba, así que escribieras lo
    que escribieras no cambiaba una palabra de lo que veías.

    No se metió en `/api/analyze` porque cuesta ~18 s y ese endpoint ya roza el
    corte de Render. Aquí se reconstruye el contexto desde el reporte YA
    guardado —los mismos números, ninguno recalculado— y el navegador la pide
    cuando el análisis ya está en pantalla.

    **Solo tu propio reporte.** Se filtra por `usuario_id` igual que el archivo:
    de nada serviría un archivo privado si esta ruta explicara el de otro.
    """
    u = _usuario_actual(request)
    try:
        conn = _db()
        if u is not None:
            fila = conn.execute(
                "SELECT ticker, payload FROM reports WHERE report_id=? AND usuario_id=?",
                (report_id, u["id"])).fetchone()
        else:
            fila = conn.execute(
                "SELECT ticker, payload FROM reports WHERE report_id=? AND usuario_id IS NULL",
                (report_id,)).fetchone()
        conn.close()
    except Exception as e:                       # noqa: BLE001
        return {"ok": False, "error": _error_publico(e, "Leer el reporte")}

    # No se distingue «no existe» de «no es tuyo»: distinguirlos convertiría la
    # ruta en un oráculo de qué ha analizado la gente.
    if fila is None or not fila["payload"]:
        return {"ok": False, "error": "No se encontró ese reporte."}
    try:
        payload = json.loads(fila["payload"])
    except Exception:                            # noqa: BLE001
        return {"ok": False, "error": "El reporte guardado no se puede leer."}

    if payload.get("wbj_explanation"):
        # Ya se generó (p. ej. con `?explain=1`). Devolverla en vez de pagar
        # otros 18 s por el mismo texto.
        return _json_safe({"ok": True, "explicacion": payload["wbj_explanation"],
                           "fuente": payload.get("wbj_explanation_source"),
                           "cacheada": True})
    try:
        ctx = _wbj_explain_context(fila["ticker"],
                                   payload.get("nombre_completo") or fila["ticker"],
                                   payload.get("precio_actual"), payload)
        expl, fuente = _wbj_explain(ctx)
    except Exception as e:                       # noqa: BLE001
        return {"ok": False, "error": _error_publico(e, "Generar la explicación")}
    if not expl:
        return {"ok": False, "error": "El proveedor de IA no devolvió la explicación."}

    # Se guarda en el reporte: la próxima vez que abras este análisis, sale al
    # instante y sin gastar otra llamada.
    try:
        payload["wbj_explanation"] = expl
        payload["wbj_explanation_source"] = fuente
        save_report_payload(report_id, payload)
    except Exception:                            # noqa: BLE001
        pass
    return _json_safe({"ok": True, "explicacion": expl, "fuente": fuente,
                       "cacheada": False})


@app.get("/api/aprendizaje")
def aprendizaje(request: Request):
    """Cuánto han aprendido los agentes del uso de TODOS, y cuánto aportaste tú."""
    u = _usuario_actual(request)
    ahora = time.time()
    salida = {"ok": True, "agentes": {}, "tuyo": {}, "generado": ahora}

    try:
        conn = _db()
    except Exception as e:                       # noqa: BLE001
        return {"ok": False, "error": _error_publico(e, "Abrir la base")}
    try:
        # ── Lo aportado, por agente ────────────────────────────────────
        for agente in _CU.AGENTES:
            fila = conn.execute(
                "SELECT COUNT(*) n, COUNT(DISTINCT ticker) tk, "
                "       COUNT(DISTINCT usuario_id) us, MAX(creado_ts) ult "
                "FROM contribuciones WHERE agente=?", (agente,)).fetchone()
            recientes = conn.execute(
                "SELECT COUNT(*) FROM contribuciones WHERE agente=? AND creado_ts > ?",
                (agente, ahora - 30 * 86400)).fetchone()[0]
            salida["agentes"][agente] = {
                "analisis": fila["n"] or 0,
                "tickers": fila["tk"] or 0,
                # Cuántas personas han aportado. Es un conteo, nunca una lista.
                "personas": fila["us"] or 0,
                "ultimo": fila["ult"],
                "ultimos_30d": recientes,
            }
            if u is not None:
                mio = conn.execute(
                    "SELECT COUNT(*) n, COUNT(DISTINCT ticker) tk FROM contribuciones "
                    "WHERE agente=? AND usuario_id=?", (agente, u["id"])).fetchone()
                salida["tuyo"][agente] = {"analisis": mio["n"] or 0,
                                          "tickers": mio["tk"] or 0}

        # ── Cómo aprende cada uno, con la cifra que lo demuestra ───────
        #
        # ACCIONES: la calibración necesita reportes ya VENCIDOS. Un reporte de
        # ayer no dice nada todavía; el lazo se cierra cuando pasa el horizonte.
        cerrados = conn.execute(
            "SELECT COUNT(*) FROM reports WHERE created_ts < ?",
            (ahora - 90 * 86400,)).fetchone()[0]
        total_rep = conn.execute("SELECT COUNT(*) FROM reports").fetchone()[0]
        salida["agentes"]["acciones"].update({
            "como_aprende": "calibración",
            "explicacion": ("Cada reporte guarda su convicción y sus objetivos. Cuando "
                            "pasa el horizonte, el precio real dice si acertó. Con más "
                            "reportes de más gente, la curva de fiabilidad tiene más "
                            "puntos — y se puede comprobar si un «70%» acierta el 70%."),
            "reportes": total_rep,
            "reportes_vencidos": cerrados,
            "listo": cerrados >= 5,
            "falta": ("aún no hay reportes con 90 días cumplidos: la calibración "
                      "necesita tiempo, no solo volumen" if cerrados < 5 else None),
        })
    except Exception as e:                       # noqa: BLE001
        return {"ok": False, "error": _error_publico(e, "Leer el aprendizaje")}
    finally:
        conn.close()

    # OPCIONES: la serie vive en disco (`WBJ_TITO_DATA`), no en SQLite. Se mide
    # lo que de verdad importa — cuántos días de historia hay— porque el IV Rank
    # real necesita 52 semanas y hasta entonces usa un proxy.
    try:
        from wbj.tito import stores as _st
        # El umbral sale de SU módulo, no de un número escrito aquí. Si mañana
        # cambia el mínimo de historia, este panel se entera solo; una copia se
        # quedaría diciendo "ya está listo" cuando el motor sigue con el proxy.
        from wbj.tito.ivcontext import MIN_IV_HISTORY_DAYS as _MIN_IV
        _dir = str(_st.data_dir())
        _tickers_iv, _dias_max = 0, 0
        _iv_dir = os.path.join(_dir, "iv")
        if os.path.isdir(_iv_dir):
            for _f in os.listdir(_iv_dir):
                if not _f.endswith(".json"):
                    continue
                _tickers_iv += 1
                try:
                    with open(os.path.join(_iv_dir, _f), "r", encoding="utf-8") as fh:
                        _dias_max = max(_dias_max, len(json.load(fh) or []))
                except Exception:                # noqa: BLE001
                    continue
        salida["agentes"]["opciones"].update({
            "como_aprende": "acumulación hacia adelante",
            "explicacion": ("La IV histórica, las cadenas y el flujo pasado no los vende "
                            "ninguna fuente: se juntan una foto por día de mercado. Mirar "
                            "un ticker YA aporta, aunque no salga ningún reporte — la foto "
                            "de hoy es la que hará posible el IV Rank real más adelante."),
            "tickers_con_serie": _tickers_iv,
            "dias_de_serie": _dias_max,
            "dias_necesarios": _MIN_IV,
            "listo": _dias_max >= _MIN_IV,
            "falta": (f"faltan {_MIN_IV - _dias_max} días de mercado para el IV Rank "
                      "real; hasta entonces se usa el proxy de volatilidad realizada"
                      if _dias_max < _MIN_IV else None),
        })
    except Exception:                            # noqa: BLE001 — el bloque de acciones ya vale
        salida["agentes"]["opciones"].setdefault("como_aprende", "acumulación hacia adelante")

    # Dicho sin ambigüedad, porque es la pregunta que se hace cualquiera que
    # comparte una herramienta: qué sale de aquí y qué no.
    salida["privacidad"] = {
        "compartido": "Las series y el track record agregados: es lo que mejora al agente.",
        "privado": "Tus reportes. Nadie más ve qué analizaste ni qué te salió.",
        "nunca": "Esta ruta no devuelve quién analizó qué. Solo cuánto hay y de cuánta gente.",
    }
    return _json_safe(salida)


@app.get("/api/perfil")
def perfil_get(request: Request):
    """El cuestionario, tus respuestas y lo que el sistema deriva de ellas.

    Devuelve las preguntas ENTERAS —enunciado, ayuda, opciones y el valor por
    defecto de cada una— para que la pantalla no tenga que llevar una copia. Una
    copia en el HTML se desincronizaría con la primera pregunta que se añada, y
    entonces el formulario preguntaría una cosa y el servidor guardaría otra.
    """
    u = _usuario_actual(request)
    perfil = _perfil_leer(request)
    return _json_safe({
        "ok": True,
        "perfil": perfil,
        "usuario": _publico(u),
        "preguntas": _CU.PREGUNTAS,
        "tolerancias": [{"id": k, **v} for k, v in _CU.TOLERANCIAS.items()],
        # Cuántas ha contestado esta persona y cuántas hereda de Kevin. Un
        # perfil heredado presentado como propio haría que el reporte hable con
        # una confianza que no tiene.
        # El denominador son las OBLIGATORIAS, no todas. Contar las opcionales
        # dejaría el perfil eternamente incompleto por no escribir un texto que
        # nadie tiene que escribir, y la advertencia de «hereda el perfil de
        # Kevin» sería falsa justo cuando ya no hereda nada.
        "progreso": {"total": len(_CU.OBLIGATORIAS),
                     "contestadas": len([q for q in (perfil.get("respondidas") or [])
                                         if q in _CU.OBLIGATORIAS]),
                     "opcionales": len(_CU.PREGUNTAS) - len(_CU.OBLIGATORIAS),
                     "sin_contestar": perfil.get("sin_contestar") or []},
        "archivos": {
            "para_los_agentes": (
                os.path.relpath(_CU.ruta_md_de(_PERFIL_DIR, u), os.path.dirname(_PERFIL_DIR))
                if u else "Perfil Inversionista/Kevin.md (defaults)"),
        },
        # Sin sesión no se puede guardar: no habría dónde. Se dice aquí para que
        # la pantalla enseñe el formulario en modo lectura en vez de fallar al
        # pulsar el botón.
        "editable": u is not None,
    })


@app.post("/api/perfil")
async def perfil_post(request: Request):
    """Guarda las respuestas del cuestionario.

    El cuerpo es `{"respuestas": {"<id de pregunta>": valor, ...}}`. Solo entran
    en `respondidas` los ids presentes: mandar el formulario sin tocar una
    pregunta la deja heredada, y la pantalla lo puede decir.
    """
    u = _usuario_actual(request)
    if u is None:
        return JSONResponse(status_code=401, content={
            "ok": False, "error": "Inicia sesión para guardar tu perfil."})
    try:
        body = await request.json()
    except Exception:                            # noqa: BLE001
        return {"ok": False, "error": "Cuerpo no es JSON."}
    if not isinstance(body, dict):
        return {"ok": False, "error": "Cuerpo no es un objeto."}
    respuestas = body.get("respuestas")
    modo = body.get("modo")
    if modo is not None and modo not in _CU.MODOS:
        return {"ok": False, "error": f"Modo desconocido: {modo!r}"}
    if respuestas is None and modo is None:
        return {"ok": False, "error": "Falta `respuestas` o `modo`."}
    if respuestas is not None and not isinstance(respuestas, dict):
        return {"ok": False, "error": "`respuestas` tiene que ser un objeto."}

    conn = _db()
    try:
        # Se parte de lo GUARDADO, no de lo efectivo: en modo `default` lo
        # efectivo son los valores de Kevin, y usarlo como base convertiría sus
        # respuestas en las tuyas en cuanto guardaras cualquier cosa.
        guardadas = _CU.leer_perfil(conn, u["id"])
        base = {**guardadas, **(guardadas.get("respuestas") or {})}
        if modo is not None:
            base["modo"] = modo
        perfil, error = _CU.perfil_desde_respuestas(respuestas or {}, base)
        if error:
            # Una sola respuesta inválida aborta el guardado entero: un perfil a
            # medias es peor que uno viejo, porque nadie sabría qué parte es suya.
            return {"ok": False, "error": error}
        _CU.guardar_perfil(conn, _PERFIL_DIR, u, perfil)
        guardado = _CU.leer_perfil(conn, u["id"])
    except Exception as e:                       # noqa: BLE001
        return {"ok": False, "error": _error_publico(e, "Guardar el perfil")}
    finally:
        conn.close()

    # El perfil también se sube al momento: es lo que leen los tres agentes, y
    # perderlo devuelve al usuario a los valores de Kevin sin decírselo.
    _respalda_ya("perfil guardado")
    return _json_safe({"ok": True, "perfil": guardado,
                       "progreso": {"total": len(_CU.OBLIGATORIAS),
                                    "contestadas": len([q for q in (guardado.get("respondidas") or [])
                                                        if q in _CU.OBLIGATORIAS]),
                                    "opcionales": len(_CU.PREGUNTAS) - len(_CU.OBLIGATORIAS),
                                    "sin_contestar": guardado.get("sin_contestar") or []}})



@app.get("/api/tito-news")
def tito_news(ticker: str, call_pct: float | None = None, name: str | None = None):
    """Tarea 7 — noticias en dos capas + bandera de contradicción.

    Ruta propia, como la tiene Víctor (`/api/news` en su app): el panel de
    noticias se carga y se refresca por separado del scorecard.

    La bandera **NO toca los 100 pts**: las 6 categorías ya suman 100%. Es
    contexto que confronta la dirección del dinero contra la de los titulares.

    `call_pct` es el % del premium notable en calls — el mismo número que usa el
    resumen de Prediction Pro. Sin él la bandera sale `none` (no hay apuesta
    dominante que contrastar), nunca inventada.
    """
    sc = _tito_mod()
    if sc is None:
        return {"ok": False, "error": "Motor de Víctor no disponible."}

    tk, err = _tito_ticker(ticker)
    if err:
        return {"ok": False, "error": err}

    try:
        from wbj.tito import news as N
        from wbj.tito.massive import fetch_ticker_name

        rep = N.build_news_report(tk, name or fetch_ticker_name(tk), datetime.now(timezone.utc))
        fbias = N.flow_bias(call_pct) if call_pct is not None else "neutral"
        flag = N.contradiction_flag(fbias, rep.bias)

        def it(x):
            return {"title": x.title, "url": x.url, "publisher": x.publisher,
                    "published_utc": x.published_utc, "sentiment": x.sentiment,
                    "reasoning": x.reasoning, "matched_by": x.matched_by}

        return {
            "ok": True,
            "ticker": tk,
            "company": [it(x) for x in rep.company[:8]],
            "macro": [it(x) for x in rep.macro],
            "promoted": [it(x) for x in rep.promoted],
            "bias": {"bias": rep.bias.bias, "score": _r(rep.bias.score, 3),
                     "positive": rep.bias.positive, "negative": rep.bias.negative,
                     "neutral": rep.bias.neutral},
            "flow_bias": fbias,
            "flag": {"kind": flag.kind, "title": flag.title, "detail": flag.detail},
            "feeds_ok": rep.feeds_ok, "feeds_total": rep.feeds_total,
            "afecta_scorecard": False,
        }
    except Exception as e:
        return {"ok": False, "error": f"No se pudieron leer las noticias: {e}"}


@app.get("/api/almacen")
def almacen_estado():
    """Dónde viven los datos y si de verdad se están respaldando.

    Existe porque el modo degradado es SILENCIOSO: sin `VERTEX_GIT_TOKEN` todo
    funciona igual, los reportes se ven, y nada avisa de que en el próximo
    redeploy desaparecen. Esta ruta y la tira del panel son lo que convierte
    «se perdió todo» en «llevas 3 días sin respaldo y lo sabías».
    """
    from vertex_almacen import almacen as _alm

    e = _alm.estado()
    # QUÉ CÓDIGO ESTÁ CORRIENDO. Sin esto, «¿está el arreglo desplegado?» solo
    # se puede contestar adivinando por los efectos —y adivinando se pierde un
    # día entero mirando el fallo equivocado. Render pone el commit en el
    # entorno; en local no hay ninguno y se dice «local».
    e["version"] = _version_desplegada()
    e["privado"] = {
        # Sin decir la clave ni nada de su contenido: solo si el candado existe.
        "cifrado_disponible": _fernet() is not None,
        "hay_respaldo": bool(_alm.lee(f"Privado/{_PRIVADO_ENC}")),
        # Cuántas cuentas hay AHORA y cuántas trajo el arranque. Las dos, y no
        # solo una: el número que importa no es «cuántas hay» sino «¿hay menos
        # que antes?», que es la forma que tiene esta avería de anunciarse.
        "cuentas": _cuenta_usuarios(),
        "cuentas_al_arrancar": _USUARIOS_AL_ARRANCAR,
        # …y cuántas guarda el respaldo. Es el par que importa: si la de arriba
        # es menor que esta, algo se perdió y el respaldo está —bien— frenado.
        "cuentas_en_el_respaldo": _cuentas_en_el_respaldo(_alm),
        # Por qué no se pudo abrir el respaldo, si es que pasó. Un respaldo que
        # existe y no se puede leer es peor que no tenerlo.
        "restauracion": _MOTIVO_RESTAURA or "",
        # Y si el respaldo está FRENADO por los cerrojos. Frenar es lo correcto
        # —mejor un respaldo viejo que uno vacío—, pero callarlo no.
        "respaldo_frenado": _respalda_privado_frenado(),
    }
    if not e["privado"]["cifrado_disponible"]:
        e["privado"]["motivo"] = (
            "Sin VERTEX_DB_KEY no se respaldan cuentas ni perfiles. Un hash de "
            "contraseña sin cifrar no se sube a un repositorio, así que se "
            "prefiere perderlos a filtrarlos.")
    return e


@app.post("/api/almacen/sincronizar")
def almacen_sincronizar():
    """Fuerza un respaldo AHORA, sin esperar al ciclo.

    Para el día del despliegue y para cuando acabas un análisis que no quieres
    arriesgar. No hace nada distinto del ciclo automático: solo lo adelanta.
    """
    from vertex_almacen import almacen as _alm

    _alm.sincroniza(incluir_series=True, mensaje="respaldo a peticion")
    return _alm.estado()


@app.get("/api/archivo/{agente}")
def archivo_lista(agente: str, ticker: str = ""):
    """Los análisis guardados de un agente, LEÍDOS DEL DIRECTORIO.

    No de la base: es la comprobación en vivo de que los archivos son la fuente
    de verdad. Si SQLite desapareciera ahora mismo, esta ruta seguiría
    contestando lo mismo.
    """
    import vertex_archivo as _ar

    try:
        filas = _ar.lista_reportes(agente, ticker)
    except ValueError:
        # El texto se escribe AQUÍ, no se reenvía el de la excepción. La regla
        # del proyecto es que ninguna excepción cruda llega al navegador: hoy
        # este `ValueError` trae un mensaje inofensivo, pero mañana podría
        # traer una ruta del disco, y para entonces nadie se acordaría.
        raise HTTPException(
            status_code=400,
            detail=(f"Agente desconocido. Los que hay son "
                    f"'{_ar.ACCIONES}' (Reportes) y '{_ar.OPCIONES}' (Proyecciones)."))
    return {"ok": True, "agente": agente, "carpeta": _ar.carpeta_de(agente),
            "n": len(filas), "reportes": filas}


# ═══════════════════════════════════════════════════════════════════════════
#  EL MAPA DE SECTORES — la pantalla de arranque del panel
#
#  Catorce casillas: tres referencias (SPY, RSP, QQQ) y los once sectores del
#  S&P, cada una con precio, cambio del día y RSI. Al pulsar un sector se
#  despliegan sus industrias.
#
#  Dos datos por ticker y dos orígenes distintos, a propósito:
#
#   · **Precio y cambio** de `/stable/quote` — es intradía. Un mapa del mercado
#     con el cierre de ayer no sirve para mirar el mercado de hoy.
#   · **RSI** de las velas DIARIAS, que es lo que significa «RSI de 1 día». Se
#     calcula aquí con la fórmula de Wilder (`wbj.sectores`), no se pide a
#     ningún proveedor: así el número no depende de qué suavizado use cada uno.
#
#  Son 14 tickers × 2 peticiones. En serie eso es medio minuto; se piden en
#  paralelo y se cachean, porque los catorce son los MISMOS para todo el mundo
#  y el mercado no se mueve tanto en dos minutos.
# ═══════════════════════════════════════════════════════════════════════════

#: Cuánto vale una foto del mapa. Dos minutos: lo bastante fresco para mirar el
#: mercado y lo bastante largo para que abrir el panel diez veces no gaste diez
#: veces la cuota de FMP.
_SECTORES_TTL = 120
_SECTORES_CACHE: dict[str, tuple[float, dict]] = {}
_SECTORES_LOCK = threading.Lock()

#: Cuántos tickers se piden a la vez. Seis es el mismo tope que usa el escaneo
#: de la Wheel: suficiente para que catorce tarden ~2 s y poco para no hacer
#: que FMP nos limite por ráfaga.
_SECTORES_CONCURRENCIA = 6

#: Los once, cargados una vez. Se resuelve perezosamente para que importar
#: `vertex_api` sin el engine en el path no reviente el arranque.
try:
    from wbj.sectores import SECTORES as _SECTORES_LISTA
except Exception:                                # noqa: BLE001
    _SECTORES_LISTA = ()


#: Cuántos tickers se aceptan de una vez. El sector más poblado tiene cinco
#: industrias, así que 25 es holgado con margen para crecer — y es el tope que
#: impide que alguien pida doscientos y nos gaste la cuota de FMP en una
#: petición. Una ruta que cotiza lo que le manden necesita un borde.
_SECTORES_MAX_PEDIDOS = 25

#: Cuánta historia diaria se baja por ticker. Con nombre y en un sitio porque
#: NO es un detalle de I/O: de aquí depende que la ventana de «1A» y la media
#: de 200 existan. Pedir un año justo dejaba la de «1A» vacía siempre, y un
#: test lo vigila ahora contra las ventanas que el panel anuncia.
_SECTORES_PERIODO = "15mo"


def _sectores_pedidos(crudo: str) -> list[str]:
    """La lista de tickers de la query, saneada y sin repetidos.

    El panel es quien sabe qué industrias tiene cada sector, así que es él quien
    los pide. Pero «el panel los pide» y «cualquiera puede pedir lo que sea» son
    lo mismo desde el servidor, y por eso esto no se fía: se valida cada ticker
    con el mismo saneador de las demás rutas y se corta en `_SECTORES_MAX_PEDIDOS`.

    Se conserva el ORDEN pedido: la sección enseña las industrias en el orden en
    que están escritas, y devolverlas barajadas movería las filas de sitio.
    """
    fuera = []
    for parte in str(crudo or "").split(","):
        bruto = str(parte).strip().upper()
        # El saneador general limpia los caracteres raros, y por eso solo no
        # basta aquí: «../etc/passwd» sale como «ETCPASSWD», un ticker que no
        # existe pero que pasa el filtro y acaba siendo una fila muerta en
        # pantalla, con sus puntos suspensivos para siempre. Un símbolo de
        # verdad son de una a seis letras y nada más.
        if not re.fullmatch(r"[A-Z]{1,6}", bruto):
            continue
        tk, err = _tito_ticker(bruto)
        if err or not tk or tk in fuera:
            continue
        fuera.append(tk)
        if len(fuera) >= _SECTORES_MAX_PEDIDOS:
            break
    return fuera


#: Las acciones de un ETF cambian poco y la lista es cara de pedir, así que se
#: guarda un día entero. Es la diferencia entre que entrar en una industria
#: cueste una llamada o cueste dos.
_HOLDINGS_TTL = 86400
_HOLDINGS_CACHE: dict[str, tuple[float, list]] = {}

#: Cuántas acciones se enseñan por industria. Diez es lo que se lee de un
#: vistazo y suele cubrir más de la mitad del peso del ETF; con cuarenta la
#: pantalla deja de responder a la pregunta («¿quién lo está subiendo?») y pasa
#: a ser un listado.
_HOLDINGS_TOPE = 10


#: Lo que un ETF lleva dentro y NO es una empresa: efectivo, divisa, futuros y
#: los apuntes de tesorería. Pasan el filtro de «una a seis letras» —«USD» lo
#: es— y acababan como una fila más con su nombre y sus puntos suspensivos
#: para siempre, porque no hay precio que cotizar para el efectivo.
_HOLDINGS_NO_SON_EMPRESAS = {
    "USD", "CASH", "EUR", "GBP", "JPY", "CAD", "AUD", "CHF", "MXN",
    "N/A", "NA", "TBILL", "XTSLA", "MVRXX", "FGXXX", "GOVXX", "WMPXX",
}

#: Los dos caminos de FMP a las posiciones de un ETF. El primero es el actual;
#: el segundo es el de siempre, y existe aquí porque el nuevo es «exclusivo» en
#: los planes bajos: pedirlo devuelve 403 y el tercer piso se quedaba vacío sin
#: que se pudiera saber si era el plan, el ETF o un fallo nuestro.
_HOLDINGS_RUTAS = (
    ("https://financialmodelingprep.com/stable/etf/holdings", "symbol"),
    ("https://financialmodelingprep.com/api/v3/etf-holder/{t}", None),
)


def _etf_holdings(ticker: str):
    """Las mayores posiciones de un ETF: `([(ticker, nombre)], motivo)`.

    Esto SÍ se baja, a diferencia de la tabla de industrias. La diferencia es
    que las industrias de un sector no cambian nunca —por eso están escritas en
    el panel— y las posiciones de un ETF cambian cada trimestre. Escribir a
    mano trescientas noventa acciones sería una lista que parece autoridad y se
    queda vieja sin que nadie lo note.

    Devuelve también el MOTIVO cuando vuelve vacío. Sin él, «tu plan no sirve
    este dato», «este ETF no tiene posiciones» y «se cayó la petición» se ven
    exactamente igual en pantalla: un hueco.

    Nunca lanza.
    """
    tk = str(ticker).upper().strip()
    ahora = time.time()
    guardado = _HOLDINGS_CACHE.get(tk)
    if guardado and ahora - guardado[0] < _HOLDINGS_TTL:
        return guardado[1], ""
    clave = (os.environ.get("FMP_API_KEY") or "").strip()
    if not clave:
        return [], "Falta FMP_API_KEY."

    motivos = []
    for plantilla, param in _HOLDINGS_RUTAS:
        url = plantilla.format(t=tk)
        params = {"apikey": clave}
        if param:
            params[param] = tk
        try:
            r = requests.get(url, params=params, timeout=8)
        except Exception as e:                   # noqa: BLE001
            motivos.append(f"{type(e).__name__}")
            continue
        if r.status_code != 200:
            motivos.append(f"HTTP {r.status_code}")
            continue
        try:
            datos = r.json()
        except Exception:                        # noqa: BLE001
            motivos.append("respuesta ilegible")
            continue
        filas = []
        for h in (datos if isinstance(datos, list) else []):
            if not isinstance(h, dict):
                continue
            sub = str(h.get("asset") or h.get("symbol") or "").upper().strip()
            if not re.fullmatch(r"[A-Z]{1,6}", sub):
                continue
            if sub in _HOLDINGS_NO_SON_EMPRESAS:
                continue
            nombre = str(h.get("name") or sub).strip()
            if sub not in {t for t, _ in filas}:
                filas.append((sub, nombre))
        if filas:
            filas = filas[:_HOLDINGS_TOPE]
            _HOLDINGS_CACHE[tk] = (ahora, filas)
            return filas, ""
        motivos.append("sin posiciones en la respuesta")
    return [], " · ".join(motivos)


def _sector_fila(ticker: str) -> dict:
    """Precio, cambio, RSI y la serie diaria de UN ticker. Nunca lanza.

    Lo que falte va a `None`: una casilla sin dato se pinta «—» y el resto del
    mapa se ve igual. Que un ETF raro no responda no puede dejar la pantalla en
    blanco.

    Los `cierres` y `volumenes` viajan dentro de la fila —no se pintan— porque
    la rotación los necesita: el RS Ratio de un sector se calcula contra la
    serie del SPY, así que hace falta tener las dos a la vez.
    """
    from wbj.sectores import (cambio_pct, cambios_por_ventana, distancia_sma,
                              nombre_de, respalda_el_volumen, rsi, sma,
                              volumen_por_ventana)

    tk = str(ticker).upper().strip()
    fila = {"ticker": tk, "nombre": nombre_de(tk), "precio": None,
            "cambio_pct": None, "rsi": None, "sma200": None,
            "sma200_dist": None, "cambios": {}, "volumen": {},
            "cierres": [], "volumenes": []}
    try:
        q = _quote_rapido_fmp(tk) or {}
        precio = q.get("price")
        if isinstance(precio, (int, float)) and precio > 0:
            fila["precio"] = round(float(precio), 2)
            # `previousClose` y no `change`: el cambio se calcula con la misma
            # fórmula para todos, venga el campo que venga del proveedor.
            c = cambio_pct(precio, q.get("previousClose"))
            fila["cambio_pct"] = None if c is None else round(c, 2)
    except Exception:                            # noqa: BLE001 — se degrada, no se cae
        pass
    try:
        # QUINCE MESES, y el motivo es aritmético: el cambio a «1A» compara
        # contra el cierre de hace 252 SESIONES, así que necesita 253 cierres
        # en la serie. Un año de calendario da unas 251 — tres de menos— y la
        # ventana de «1A» salía vacía SIEMPRE, en los catorce y en cada
        # industria, sin que nada lo dijera.
        #
        # No se pide «2y» porque el proveedor salta de 2 a 5 años en ese
        # umbral: quince meses dan ~317 sesiones, sobra margen para la de 252 y
        # para la media de 200, y se bajan dos años en vez de cinco.
        barras = _fmp_daily_bars(tk, _SECTORES_PERIODO)
        fila["cierres"] = [b[4] for b in barras]
        fila["volumenes"] = [b[5] for b in barras]
        v = rsi(fila["cierres"])
        fila["rsi"] = None if v is None else round(v, 1)
        m = sma(fila["cierres"])
        fila["sma200"] = None if m is None else round(m, 2)
        # La distancia se mide contra el precio EN VIVO, no contra el último
        # cierre: es la que dice si ahora mismo está por encima o por debajo.
        d = distancia_sma(fila["precio"], m) if fila["precio"] else None
        fila["sma200_dist"] = None if d is None else round(d, 2)
        fila["cambios"] = cambios_por_ventana(fila["cierres"])
        # El volumen viaja YA RESUELTO por ventana —medio y relativo— y con el
        # juicio hecho: «¿lo respalda el volumen?» es un sí/no/no-se-sabe que
        # decide el engine, no el panel. Si el panel lo dedujera del relativo,
        # el umbral viviría en dos sitios y un día dirían cosas distintas.
        vol = volumen_por_ventana(fila["volumenes"])
        for datos in vol.values():
            datos["respalda"] = respalda_el_volumen(datos.get("relativo"))
        fila["volumen"] = vol
    except Exception:                            # noqa: BLE001
        pass
    return fila


def _sectores_rotacion(filas: dict) -> dict:
    """Las tres capas de la rotación sobre las filas ya bajadas.

    `filas` es `{ticker: fila}`. Devuelve el bloque entero —salud, matriz,
    flujo y diagnóstico— o lo que se pueda de él. Nunca lanza: si falta el SPY
    no hay contra qué medir, y eso se dice en vez de devolver ceros.
    """
    from wbj.sectores import (CATEGORIAS, CUADRANTES, categoria_de,
                              clasifica_sector, diagnostico, dispersion,
                              estela_rrg, lideres_del_dia_rojo,
                              salud_del_mercado, serie_rs)

    spy = filas.get("SPY") or {}
    rsp = filas.get("RSP") or {}
    cierres_spy = spy.get("cierres") or []
    ret_spy = spy.get("cambio_pct")
    if not cierres_spy:
        return {"disponible": False,
                "motivo": "Sin la serie del SPY no hay contra qué medir la "
                          "fuerza relativa de los sectores."}

    # 1 · Salud: RSP contra SPY. La distancia entre los dos es la amplitud.
    salud_clave, salud_frase, salud_pend = salud_del_mercado(
        serie_rs(rsp.get("cierres") or [], cierres_spy), ret_spy)

    # 2 · Matriz: cada sector contra el SPY.
    por_sector, retornos, estelas = {}, {}, {}
    for tk, _ in _SECTORES_LISTA:
        f = filas.get(tk) or {}
        d = clasifica_sector(f.get("cierres") or [], f.get("volumenes") or [],
                             cierres_spy, f.get("cambio_pct"), ret_spy)
        d["categoria"] = categoria_de(tk)
        por_sector[tk] = d
        if f.get("cambio_pct") is not None:
            retornos[tk] = f["cambio_pct"]
        # La estela: los cinco últimos puntos del RRG. Un punto solo dice dónde
        # está el sector; la estela dice HACIA DÓNDE va, que es la pregunta.
        # Se calcula aquí porque es donde están las dos series a la vez.
        e = estela_rrg(f.get("cierres") or [], cierres_spy)
        if e:
            estelas[tk] = e

    matriz = {c: sorted(t for t, d in por_sector.items() if d.get("cuadrante") == c)
              for c in CUADRANTES}

    def _r6(v):
        return None if v is None else round(float(v), 4)

    return {
        "disponible": True,
        "salud": {"clave": salud_clave, "frase": salud_frase,
                  "pendiente": _r6(salud_pend)},
        "matriz": matriz,
        "cuadrantes": CUADRANTES,
        "categorias": [{"clave": c, "nombre": n, "sectores": list(ts)}
                       for c, n, ts in CATEGORIAS],
        "sectores": {t: {k: (_r6(v) if isinstance(v, (int, float)) else v)
                         for k, v in d.items()}
                     for t, d in por_sector.items()},
        "estelas": estelas,
        "entrando": sorted(t for t, d in por_sector.items() if d.get("flujo") == "entrada"),
        "saliendo": sorted(t for t, d in por_sector.items() if d.get("flujo") == "salida"),
        "dispersion": _r6(dispersion(list(retornos.values()))),
        "dia_rojo": [{"ticker": t, "cambio_pct": r}
                     for t, r in lideres_del_dia_rojo(ret_spy, retornos)],
        "diagnostico": diagnostico(por_sector, salud_clave),
    }


def _version_desplegada() -> str:
    """El commit que está sirviendo, corto. `"local"` fuera de Render.

    Existe por una tarde perdida: el respaldo se rompió dos veces, el arreglo
    estaba en `main`, y no había forma de saber si el contenedor lo tenía. Un
    despliegue que no dice qué versión es obliga a deducirlo por los síntomas,
    que es exactamente lo que no funciona cuando los síntomas son ambiguos.
    """
    for var in ("RENDER_GIT_COMMIT", "VERTEX_COMMIT", "SOURCE_VERSION"):
        sha = (os.environ.get(var) or "").strip()
        if sha:
            return sha[:8]
    return "local"


def _reloj_ahora():
    """El estado del mercado AHORA. Nunca lanza; `None` si no se pudo saber.

    Va en la respuesta, no lo deduce el navegador: calcularlo en el cliente lo
    ataría al reloj del teléfono, que en un viaje diría que Wall Street abre a
    las tres de la madrugada. Y se recalcula en cada respuesta —también en las
    servidas de caché— porque es el único dato de esta pantalla que caduca solo.
    """
    try:
        from wbj.sectores import estado_del_mercado

        return estado_del_mercado()
    except Exception:                            # noqa: BLE001
        return None


def _sectores_filas(tickers) -> list[dict]:
    """Las filas de varios tickers, en paralelo y EN EL ORDEN pedido.

    El orden importa: la parrilla se lee como un mapa —SPY, RSP y QQQ arriba,
    los sectores debajo— y si las casillas se colocaran según quién conteste
    antes, cambiarían de sitio en cada carga.
    """
    tickers = list(tickers)
    if not tickers:
        return []
    with ThreadPoolExecutor(max_workers=_SECTORES_CONCURRENCIA) as ex:
        return list(ex.map(_sector_fila, tickers))


#: Lo que se manda cuando la amplitud no se pudo calcular. Con la forma
#: completa: media respuesta obliga al panel a comprobar cada clave.
_AMPLITUD_VACIA = {"n": 0, "confianza": None, "frase": "", "fuertes": [],
                   "debiles": [], "neutrales": [], "pct_fuertes": None,
                   "empujan": [], "frenan": []}


def _amplitud(filas):
    """Una amplitud POR VENTANA sobre las filas ya bajadas. Nunca lanza.

    Por ventana y no una sola porque si no la pantalla se contradice: eliges
    «1A», ves +45% en cada fila y debajo «2 de 5 al alza», que era el reparto
    de HOY. El número que se lee y el que se cuenta tienen que hablar de lo
    mismo.
    """
    try:
        from wbj.sectores import VENTANAS_CAMBIO, amplitud_por_ventana

        return amplitud_por_ventana(filas)
    except Exception:                            # noqa: BLE001
        try:
            from wbj.sectores import VENTANAS_CAMBIO
            return {e: dict(_AMPLITUD_VACIA) for e, _ in VENTANAS_CAMBIO}
        except Exception:                        # noqa: BLE001
            return {}


@app.get("/api/sectores")
def api_sectores(tickers: str = ""):
    """El mapa entero, o los tickers que se le pidan.

    Sin `tickers`: las catorce casillas de la parrilla.
    Con `tickers`: cotiza EXACTAMENTE esos, en ese orden.

    El segundo modo existe porque el panel ya sabe qué industrias tiene cada
    sector —esa lista no cambia nunca y por eso vive allí, para poder pintarla
    sin esperar a nadie—. Tenerla también aquí era una segunda copia que había
    que vigilar para nada: el servidor no necesita saber qué industria es cuál,
    solo cotizar lo que le pidan. Una tabla, un sitio.
    """
    from wbj.sectores import REFERENCIAS, SECTORES, universo

    pedidos = _sectores_pedidos(tickers)
    clave = ",".join(pedidos) if pedidos else "_parrilla"
    ahora = time.time()
    with _SECTORES_LOCK:
        guardado = _SECTORES_CACHE.get(clave)
        if guardado and ahora - guardado[0] < _SECTORES_TTL:
            return {**guardado[1], "reloj": _reloj_ahora(), "cacheado": True}

    if not (os.environ.get("FMP_API_KEY") or "").strip():
        # Se dice cuál falta y qué se deja de ver. Un mapa vacío sin motivo se
        # lee como «el mercado no existe hoy».
        return {"ok": False, "error": "Falta FMP_API_KEY: sin ella no hay "
                                      "precio ni RSI de los sectores.",
                "filas": []}

    if pedidos:
        _f = [{k: v for k, v in f.items() if k not in ("cierres", "volumenes")}
              for f in _sectores_filas(pedidos)]
        # La amplitud viaja con las filas: cuántos empujan y cuántos frenan es
        # la pregunta de esta pantalla, y calcularla en el navegador la habría
        # dejado escrita dos veces con dos umbrales que se separan.
        salida = {"ok": True, "tickers": pedidos, "filas": _f,
                  "amplitud": _amplitud(_f), "motivo": None}
    else:
        filas = _sectores_filas(universo())
        por_ticker = {f["ticker"]: f for f in filas}
        rotacion = _sectores_rotacion(por_ticker)
        salida = {
            "ok": True,
            "referencias": [t for t, _ in REFERENCIAS],
            "sectores": [t for t, _ in SECTORES],
            # Las series se quitan de la respuesta: son ~125 cierres y 125
            # volúmenes por ticker —cerca de un megabyte para catorce— y ya
            # cumplieron su función al calcular la rotación. La pantalla pinta
            # números, no series.
            "filas": [{k: v for k, v in f.items()
                       if k not in ("cierres", "volumenes")} for f in filas],
            "rotacion": rotacion,
            # Los ONCE sectores contra el mercado: la misma pregunta un piso más
            # arriba. Se filtra por LOS SECTORES, no por «lo que no es
            # referencia»: con esa forma, el VIX —que se baja para la franja de
            # estado— habría entrado a contar como un sector más, y un día de
            # pánico, con el VIX disparado, habría salido como un voto AL ALZA.
            "amplitud": _amplitud([f for f in filas
                                   if f["ticker"] in {t for t, _ in SECTORES}]),
            # Cuántas empresas de cada sector van por encima de su media de 50.
            # Se sirve lo que haya guardado y, si está viejo, el recálculo se
            # dispara EN SEGUNDO PLANO: son ~110 peticiones y la pantalla no
            # puede esperarlas. La primera visita de un contenedor nuevo lo verá
            # vacío y a los dos minutos lleno; con su fecha al lado siempre.
            "interna": _amplitud_interna_al_dia(),
            "motivo": None,
        }
    salida["generado"] = datetime.now(timezone.utc).isoformat()
    with _SECTORES_LOCK:
        _SECTORES_CACHE[clave] = (ahora, salida)
    return {**salida, "reloj": _reloj_ahora(), "cacheado": False}


# ═══════════════════════════════════════════════════════════════════════════
#  LA LECTURA DEL MERCADO EN PALABRAS
#
#  El modelo NO puntúa nada aquí. La matriz, los cuadrantes, el flujo y la
#  salud ya los decidió `wbj/sectores.py` con los números; esto solo los cuenta
#  en un párrafo que se entiende sin saber qué es un RS Ratio.
#
#  Es la regla del proyecto y aquí importa el doble: un párrafo bien escrito
#  suena a análisis aunque los números digan otra cosa, así que el prompt lleva
#  los datos YA DECIDIDOS y prohíbe explícitamente inventar un veredicto.
# ═══════════════════════════════════════════════════════════════════════════

_LECTURA_TTL = 900          # 15 min: el diagnóstico no cambia cada minuto


def _amplitud_de_ventana(amplitudes, ventana):
    """La amplitud de UNA ventana. Cae a la primera si no se pide ninguna.

    El servidor manda una por ventana; la lectura habla de la que el usuario
    está mirando, o la explicación contaría un reparto que no es el de la
    pantalla.
    """
    if not isinstance(amplitudes, dict) or not amplitudes:
        return {}
    if ventana in amplitudes:
        return amplitudes[ventana]
    return next(iter(amplitudes.values()), {})


def _ventana_valida(ventana: str) -> str:
    """La ventana pedida, o la primera que existe. Nunca una inventada.

    Sirve para leer `fila["volumen"][ventana]` sin que un `?ventana=../` acabe
    devolviendo `None` en silencio y borrando el volumen de toda la lectura.
    """
    try:
        from wbj.sectores import VENTANAS_CAMBIO

        etiquetas = [e for e, _ in VENTANAS_CAMBIO]
    except Exception:                            # noqa: BLE001
        return str(ventana or "")
    v = str(ventana or "").upper().strip()
    return v if v in etiquetas else (etiquetas[0] if etiquetas else "")


# ═══════════════════════════════════════════════════════════════════════════
#  LA AMPLITUD INTERNA DE CADA SECTOR
#
#  «XLK sube un 2%» y «el 80% de las empresas de XLK están sobre su media de
#  50» dicen cosas distintas: lo primero lo puede hacer Nvidia sola.
#
#  Es CARO: hay que bajar la serie diaria de cada empresa de cada sector — unas
#  110 peticiones. Pedirlo cada vez que alguien abre la pantalla sería gastar
#  la cuota de FMP en un número que cambia una vez al día, así que se calcula
#  en segundo plano una vez cada 24 h y se guarda en el almacén.
#
#  En el almacén y no en memoria porque Render borra el disco en cada redeploy
#  y duerme el servicio: en memoria, el dato se perdería justo cuando el
#  usuario vuelve, y la pantalla tardaría dos minutos en tenerlo otra vez.
# ═══════════════════════════════════════════════════════════════════════════

#: Dónde vive dentro del almacén. Bajo `Series/` porque es lo mismo que hay
#: ahí: una foto de mercado que se acumula, no un reporte de nadie.
_AMPLITUD_INTERNA_RUTA = "Series/amplitud_interna.json"

#: Una vez al día. El número se mueve con los cierres, así que calcularlo más
#: veces daría el mismo resultado gastando cuota.
_AMPLITUD_INTERNA_TTL = 24 * 3600

_AMPLITUD_INTERNA_LOCK = threading.Lock()
_AMPLITUD_INTERNA_CALCULANDO = False


def _amplitud_interna_guardada() -> dict:
    """Lo último calculado, o `{}`. Nunca lanza."""
    try:
        from vertex_almacen import almacen as _alm

        return _alm.lee_json(_AMPLITUD_INTERNA_RUTA) or {}
    except Exception:                            # noqa: BLE001
        return {}


def _amplitud_interna_calcula() -> dict:
    """Recorre los once sectores y mide cuántos de sus miembros van por encima.

    Solo mira las posiciones que sirve `_etf_holdings` —las diez mayores—, y
    eso se dice en el resultado (`miembros`) en vez de venderlo como «el
    sector»: es una MUESTRA, la de más peso, y llamarla otra cosa sería un
    número que dice más de lo que sabe.
    """
    from wbj.sectores import SECTORES, amplitud_interna

    salida = {"generado": datetime.now(timezone.utc).isoformat(), "sectores": {}}
    for tk, _ in SECTORES:
        try:
            posiciones, motivo = _etf_holdings(tk)
            if not posiciones:
                salida["sectores"][tk] = {"pct": None, "n": 0,
                                          "motivo": motivo or "sin posiciones"}
                continue
            series = {}
            for sub, _n in posiciones:
                barras = _fmp_daily_bars(sub, "1y")
                if barras:
                    series[sub] = [b[4] for b in barras]
            a = amplitud_interna(series)
            salida["sectores"][tk] = {"pct": a["pct"], "n": a["n"],
                                      "encima": a["encima"],
                                      "miembros": len(posiciones), "motivo": None}
        except Exception as e:                   # noqa: BLE001
            # Un sector que falla no puede tumbar los otros diez: se anota el
            # motivo y se sigue. Un `None` sin explicación en pantalla manda a
            # mirar donde no es.
            salida["sectores"][tk] = {"pct": None, "n": 0, "motivo": str(e)[:120]}
    return salida


def _amplitud_interna_al_dia() -> dict:
    """Lo guardado, y si está viejo dispara el recálculo EN SEGUNDO PLANO.

    Nunca bloquea la petición: son ~110 llamadas a FMP y la pantalla no puede
    esperar dos minutos. Mientras se recalcula se sirve lo de ayer con su fecha
    al lado, que es más útil que un hueco.
    """
    guardado = _amplitud_interna_guardada()
    fresco = False
    try:
        gen = guardado.get("generado")
        if gen:
            edad = (datetime.now(timezone.utc)
                    - datetime.fromisoformat(gen)).total_seconds()
            fresco = edad < _AMPLITUD_INTERNA_TTL
    except Exception:                            # noqa: BLE001
        fresco = False
    if fresco or not (os.environ.get("FMP_API_KEY") or "").strip():
        return guardado

    global _AMPLITUD_INTERNA_CALCULANDO
    with _AMPLITUD_INTERNA_LOCK:
        if _AMPLITUD_INTERNA_CALCULANDO:
            return guardado
        _AMPLITUD_INTERNA_CALCULANDO = True

    def _trabaja():
        global _AMPLITUD_INTERNA_CALCULANDO
        try:
            datos = _amplitud_interna_calcula()
            from vertex_almacen import almacen as _alm

            _alm.guarda(_AMPLITUD_INTERNA_RUTA, datos)
        except Exception:                        # noqa: BLE001
            logging.getLogger(__name__).warning(
                "no se pudo calcular la amplitud interna", exc_info=True)
        finally:
            with _AMPLITUD_INTERNA_LOCK:
                _AMPLITUD_INTERNA_CALCULANDO = False

    threading.Thread(target=_trabaja, daemon=True).start()
    return guardado


_LECTURA_CACHE: dict[str, tuple[float, dict]] = {}


def _lectura_datos(rot: dict, filas: list, ventana: str = "",
                   interna: dict | None = None) -> str:
    """Los números, ordenados para que el modelo no tenga que adivinar nada.

    Va como texto y no como JSON crudo a propósito: un JSON con veinte claves
    invita a describir la estructura («el campo cuadrante indica…») en vez de
    contar lo que pasa.
    """
    por = (rot or {}).get("sectores") or {}
    matriz = (rot or {}).get("matriz") or {}
    salud = (rot or {}).get("salud") or {}
    idx = {f["ticker"]: f for f in (filas or [])}

    def _linea(t):
        f, d = idx.get(t) or {}, por.get(t) or {}
        trozos = [f"{t} ({f.get('nombre', t)})",
                  f"cambio hoy {f.get('cambio_pct')}%",
                  f"RSI {f.get('rsi')}"]
        if f.get("sma200_dist") is not None:
            trozos.append(f"{f['sma200_dist']:+.1f}% respecto a su media de 200")
        if d.get("cuadrante"):
            trozos.append(f"cuadrante {d['cuadrante']}")
        if d.get("flujo"):
            trozos.append(f"flujo {d['flujo']}")
        if d.get("volumen_rel") is not None:
            trozos.append(f"volumen hoy {d['volumen_rel']:.2f}x su media de 20")
        # Y el de la VENTANA que se está mirando, que es otra pregunta: uno
        # dice si hoy hubo prisa, el otro si el movimiento del mes lo acompañó
        # dinero. Con solo el de hoy, un mes entero de goteo sin volumen se lee
        # igual que una rotación de verdad.
        vf = _volumen_frase(f, ventana)
        if vf:
            trozos.append(vf)
        # Cuántos de dentro van por encima de su media de 50. Es lo que separa
        # «el sector sube» de «una empresa del sector sube»: sin este dato el
        # modelo no puede distinguir las dos cosas, y suenan igual de bien.
        s_int = ((interna or {}).get("sectores") or {}).get(t) or {}
        if s_int.get("pct") is not None:
            trozos.append(f"{s_int['pct']:.0f}% de sus {s_int.get('n')} mayores "
                          "posiciones por encima de su media de 50")
        return " · ".join(str(x) for x in trozos)

    partes = ["REFERENCIAS DEL MERCADO"]
    partes += [f"  {_linea(t)}" for t in ("SPY", "RSP", "QQQ") if t in idx]
    partes.append("")
    partes.append(f"AMPLITUD (RSP contra SPY): {salud.get('frase', 'sin dato')}")
    partes.append("")
    partes.append("SECTORES")
    partes += [f"  {_linea(t)}" for t in por]
    partes.append("")
    partes.append("MATRIZ DE ROTACIÓN (ya calculada, no la recalcules)")
    for clave, etiqueta in (("leading", "Liderando"), ("improving", "Despertando"),
                            ("weakening", "Agotándose"), ("lagging", "Rezagados")):
        partes.append(f"  {etiqueta}: {', '.join(matriz.get(clave) or []) or 'ninguno'}")
    partes.append("")
    partes.append(f"ENTRA DINERO (con volumen alto): {', '.join((rot or {}).get('entrando') or []) or 'ninguno'}")
    partes.append(f"SALE DINERO (con volumen alto): {', '.join((rot or {}).get('saliendo') or []) or 'ninguno'}")
    if (rot or {}).get("dispersion") is not None:
        partes.append(f"DISPERSIÓN entre sectores hoy: {rot['dispersion']:.2f}")
    if (rot or {}).get("dia_rojo"):
        aguantan = ", ".join(f"{x['ticker']} {x['cambio_pct']}%"
                             for x in rot["dia_rojo"])
        partes.append(f"AGUANTAN EL DÍA ROJO: {aguantan}")
    if (rot or {}).get("diagnostico"):
        partes.append("REGLAS QUE YA SE DISPARARON: "
                      + " | ".join(rot["diagnostico"]))
    return "\n".join(partes)


_LECTURA_SYSTEM = (
    "Eres un analista de mercado explicando la rotación sectorial a alguien "
    "que sabe invertir pero no habla en jerga. Te dan números YA CALCULADOS y "
    "clasificaciones YA DECIDIDAS por un motor determinista.\n\n"
    "REGLAS INNEGOCIABLES:\n"
    "1. No recalcules ni contradigas la matriz, el flujo ni la amplitud. Son "
    "datos, no sugerencias. Si un sector está en 'lagging', está rezagado.\n"
    "2. No inventes números. Si algo no está en los datos, no existe: dilo o "
    "cállatelo. Ni un precio, ni un porcentaje, ni un nivel.\n"
    "3. Nada de recomendaciones de compra o venta, ni objetivos de precio. "
    "Esto explica lo que ESTÁ pasando y qué suele seguir; la decisión no es "
    "tuya.\n"
    "4. Frases cortas. Sin 'cabe destacar', sin 'en el actual entorno "
    "macroeconómico'. Si una frase no dice un hecho, sobra.\n"
    "5. Si los datos no dan para una sección, escribe una línea diciendo que "
    "hoy no hay nada claro ahí. Rellenar es peor que dejarlo corto.\n"
    "6. El VOLUMEN es la prueba, no un adorno. Un sector que sube sin volumen "
    "que lo respalde no es lo mismo que uno que sube con dinero entrando: lo "
    "primero se deshace solo, lo segundo es rotación. Cuando el dato esté, "
    "dilo con esas palabras; cuando no esté, no supongas que lo hubo.\n\n"
    "FORMATO exacto, con estos cinco titulares en negrita markdown y nada más:\n"
    "**Qué está pasando** — dos o tres frases: el estado del mercado hoy y si "
    "la subida (o caída) es amplia o de unos pocos.\n"
    "**Dónde está entrando el dinero** — qué sectores y por qué se sabe "
    "(cuadrante, volumen). Nombra los tickers.\n"
    "**De dónde está saliendo** — igual, al revés.\n"
    "**Dónde dejó de entrar** — los que estaban fuertes y pierden impulso "
    "('agotándose'). Esta es la sección que más se ignora y la que más avisa.\n"
    "**Qué esperar de cada uno** — una línea por familia (crecimiento, "
    "cíclicos, defensivos) con lo que suele venir después de este patrón, "
    "dicho como probabilidad y no como certeza."
)


@app.get("/api/sectores/acciones")
def api_sectores_acciones(etf: str = ""):
    """Las acciones de una industria, con su amplitud.

    El tercer piso: `Dashboard › XLK › SMH`. Contesta la misma pregunta que los
    de arriba —quién empuja, cuántos, cuánta confianza— pero con las empresas
    de dentro del ETF.

    Esta lista SÍ se baja, a diferencia de las industrias: las posiciones de un
    ETF cambian cada trimestre. Se cachean un día, así que la segunda visita no
    cuesta nada.
    """
    tk = str(etf or "").upper().strip()
    if not re.fullmatch(r"[A-Z]{1,6}", tk):
        return {"ok": False, "error": "Ticker no válido.", "filas": []}
    if not (os.environ.get("FMP_API_KEY") or "").strip():
        return {"ok": False, "error": "Falta FMP_API_KEY: sin ella no hay "
                                      "precio ni RSI de las acciones.",
                "filas": []}
    clave = f"_acciones_{tk}"
    ahora = time.time()
    with _SECTORES_LOCK:
        guardado = _SECTORES_CACHE.get(clave)
        if guardado and ahora - guardado[0] < _SECTORES_TTL:
            return {**guardado[1], "reloj": _reloj_ahora(), "cacheado": True}

    posiciones, motivo = _etf_holdings(tk)
    if not posiciones:
        # Se dice POR QUÉ, no se deja en blanco: «tu plan no sirve este dato»,
        # «este ETF no tiene posiciones» y «se cayó la petición» se ven igual en
        # una pantalla vacía, y solo una de las tres se arregla desde aquí.
        return {"ok": False, "etf": tk, "filas": [],
                "error": f"No se pudieron leer las empresas de {tk}"
                         + (f" ({motivo})." if motivo else ".")}
    nombres = dict(posiciones)
    filas = [{k: v for k, v in f.items() if k not in ("cierres", "volumenes")}
             for f in _sectores_filas([t for t, _ in posiciones])]
    for f in filas:
        # El nombre de la EMPRESA, que es lo que se lee. `nombre_de` no las
        # conoce —solo sabe de la parrilla— y devolvería el ticker otra vez.
        f["nombre"] = nombres.get(f["ticker"], f["ticker"])
    salida = {"ok": True, "etf": tk, "filas": filas,
              "amplitud": _amplitud(filas),
              "generado": datetime.now(timezone.utc).isoformat()}
    with _SECTORES_LOCK:
        _SECTORES_CACHE[clave] = (ahora, salida)
    return {**salida, "reloj": _reloj_ahora(), "cacheado": False}


_LECTURA_SYSTEM_DENTRO = (
    "Eres un analista explicando qué pasa DENTRO de un grupo del mercado a "
    "alguien que sabe invertir pero no habla en jerga. Te dan los miembros del "
    "grupo con sus números y un reparto de fuerza YA CALCULADO.\n\n"
    "REGLAS INNEGOCIABLES:\n"
    "1. No recalcules el reparto ni la confianza. Son datos.\n"
    "2. No inventes números. Si algo no está, no existe.\n"
    "3. Nada de recomendar comprar o vender, ni objetivos de precio.\n"
    "4. Frases cortas, sin relleno.\n"
    "5. La AMPLITUD es el punto: que suba con muchos dentro empujando no es "
    "lo mismo que que lo suba uno solo. Lo segundo se da la vuelta en cuanto "
    "ese uno se cansa, y hay que decirlo con esas palabras.\n"
    "6. El VOLUMEN es la prueba de que hay alguien detrás. Si un miembro se "
    "mueve sin volumen que lo respalde, dilo: el movimiento no está pagado. "
    "Si no hay dato de volumen, no lo inventes ni lo des por bueno.\n\n"
    "FORMATO exacto, cuatro titulares en negrita markdown y nada más:\n"
    "**Cómo está el grupo** — dos frases: qué hace y si va con el mercado o "
    "contra él.\n"
    "**Quién lo está subiendo** — nombra los que empujan, con su número.\n"
    "**Quién lo está frenando** — igual, al revés.\n"
    "**Cuánta confianza merece** — cuántos van a favor de cuántos, y qué "
    "significa eso: repartido es fiable, uno solo tirando no lo es. Di también "
    "qué haría falta ver para que cambiara."
)


def _volumen_frase(fila: dict, ventana: str):
    """«volumen 1.4x lo normal (respaldado)», o `None` si no hay dato.

    `None` y no «sin volumen»: una línea que dice que no hay dato ocupa el
    mismo sitio que una que lo dice, y el modelo acaba escribiendo sobre la
    falta de dato en vez de sobre el mercado.
    """
    v = ((fila or {}).get("volumen") or {}).get(ventana) or {}
    rel = v.get("relativo")
    if rel is None:
        return None
    respalda = v.get("respalda")
    sello = ("respaldado por volumen" if respalda is True
             else "sin volumen que lo respalde" if respalda is False
             else "volumen sin juicio")
    return f"volumen {rel:.2f}x lo normal ({sello})"


def _lectura_dentro(nombre: str, filas: list, amp: dict, ventana: str = "") -> str:
    """Los datos de un grupo (industrias de un sector, o acciones de una
    industria) ordenados para el modelo."""
    partes = [f"GRUPO: {nombre}", "", "MIEMBROS"]
    for f in filas or []:
        trozos = [f"{f.get('ticker')} ({f.get('nombre')})",
                  f"cambio {f.get('cambio_pct')}%", f"RSI {f.get('rsi')}"]
        if f.get("sma200_dist") is not None:
            trozos.append(f"{f['sma200_dist']:+.1f}% respecto a su media de 200")
        # El volumen de la ventana que se está mirando: es lo que separa «subió»
        # de «subió y entró dinero de verdad», y sin él el modelo no puede
        # distinguir una rotación de un rebote sin nadie detrás.
        vf = _volumen_frase(f, ventana)
        if vf:
            trozos.append(vf)
        partes.append("  " + " · ".join(str(x) for x in trozos))
    a = amp or {}
    partes += ["", "REPARTO DE FUERZA (ya calculado, no lo recalcules)",
               f"  al alza: {', '.join(a.get('fuertes') or []) or 'ninguno'}",
               f"  a la baja: {', '.join(a.get('debiles') or []) or 'ninguno'}",
               f"  ni una cosa ni otra: {', '.join(a.get('neutrales') or []) or 'ninguno'}",
               f"  confianza: {a.get('confianza')} — {a.get('frase')}"]
    if a.get("empujan"):
        partes.append("  los que más tiran al alza: "
                      + ", ".join(f"{x['ticker']} {x['cambio_pct']}%"
                                  for x in a["empujan"]))
    if a.get("frenan"):
        partes.append("  los que más frenan: "
                      + ", ".join(f"{x['ticker']} {x['cambio_pct']}%"
                                  for x in a["frenan"]))
    return "\n".join(partes)


@app.get("/api/sectores/lectura")
def api_sectores_lectura(refrescar: int = 0, ambito: str = "",
                         tickers: str = "", ventana: str = ""):
    """La rotación del día contada en palabras.

    Se apoya en la MISMA foto que pinta la pantalla (la cache de `/api/sectores`
    o una nueva), así que lo que se lee y lo que se ve nunca se contradicen —
    que es lo que pasaría pidiendo los datos dos veces con dos minutos de
    diferencia.
    """
    ahora = time.time()
    amb = str(ambito or "").upper().strip()
    clave = f"{_idioma_actual()}|{amb or '_mercado'}|{ventana or '-'}"
    guardado = _LECTURA_CACHE.get(clave)
    if guardado and not refrescar and ahora - guardado[0] < _LECTURA_TTL:
        return {**guardado[1], "cacheado": True}

    # Con `ambito`, la lectura es de un GRUPO —las industrias de un sector o
    # las acciones de una industria— y la pregunta cambia: ahí lo que importa
    # no es la rotación del mercado sino cuántos de dentro empujan.
    if amb:
        if not re.fullmatch(r"[A-Z]{1,6}", amb):
            return {"ok": False, "texto": "", "error": "Ámbito no válido."}
        dentro = (api_sectores(tickers=tickers) if tickers
                  else api_sectores_acciones(etf=amb))
        if not dentro.get("ok"):
            return {"ok": False, "texto": "",
                    "error": dentro.get("error") or "Sin datos del grupo."}
        texto, fuente, err = _texto_llm(
            _LECTURA_SYSTEM_DENTRO,
            "Estos son los datos de hoy. Explícalos siguiendo el formato:\n\n"
            + _lectura_dentro(amb, dentro.get("filas") or [],
                              _amplitud_de_ventana(dentro.get("amplitud"), ventana),
                              _ventana_valida(ventana)),
            temp=0.3, max_tokens=1100)
        if not texto:
            return {"ok": False, "texto": "",
                    "error": f"Ningún modelo pudo escribir la lectura ({err})."}
        salida = {"ok": True, "texto": texto, "fuente": fuente, "ambito": amb,
                  "generado": datetime.now(timezone.utc).isoformat()}
        _LECTURA_CACHE[clave] = (ahora, salida)
        return {**salida, "cacheado": False}

    base = api_sectores()
    if not base.get("ok"):
        return {"ok": False, "error": base.get("error") or "Sin datos de mercado.",
                "texto": ""}
    rot = base.get("rotacion") or {}
    if not rot.get("disponible"):
        return {"ok": False, "texto": "",
                "error": rot.get("motivo") or "Sin rotación que explicar."}

    texto, fuente, err = _texto_llm(
        _LECTURA_SYSTEM,
        "Estos son los datos de hoy. Explícalos siguiendo el formato:\n\n"
        + _lectura_datos(rot, base.get("filas") or [], _ventana_valida(ventana),
                         base.get("interna")),
        temp=0.3, max_tokens=1400)
    if not texto:
        # El motivo de CADA proveedor, no solo el del último. Un 429 de cuota
        # reportado como «falta la clave» manda a mirar donde no es.
        return {"ok": False, "texto": "",
                "error": f"Ningún modelo pudo escribir la lectura ({err})."}
    salida = {"ok": True, "texto": texto, "fuente": fuente,
              "generado": datetime.now(timezone.utc).isoformat()}
    _LECTURA_CACHE[clave] = (ahora, salida)
    return {**salida, "cacheado": False}


@app.get("/api/tito-logo")
def tito_logo(ticker: str):
    """Su `/api/logo` — el logo de la empresa, servido por proxy.

    Existe porque la URL del logo que da Massive **exige la Authorization**:
    sin este proxy la clave tendría que viajar al navegador. Aquí la petición
    la hace el servidor y lo único que cruza es el binario.

    Un ticker sin logo es 404, no 500: la cabecera cae a las iniciales y el
    panel no se entera.
    """
    tk, err = _tito_ticker(ticker)
    if err:
        raise HTTPException(status_code=400, detail=err)
    try:
        from wbj.tito.massive import fetch_logo_image

        logo = fetch_logo_image(tk)
    except Exception:
        raise HTTPException(status_code=502, detail="error")
    if not logo:
        raise HTTPException(status_code=404, detail="sin logo")
    datos, tipo = logo
    # Su `Cache-Control: public, max-age=86400`. Un logo no cambia en un día y
    # cada petición cuesta dos llamadas a Massive.
    return Response(content=datos, media_type=tipo,
                    headers={"Cache-Control": "public, max-age=86400"})


#: Su tabla de marcos temporales de `/api/bars`, literal. `5m5d` es el valor por
#: defecto también en su ruta: un `tf` desconocido no es un error, cae aquí.
_TITO_TF = {
    "1y":     (1, "day", 365),
    "15m10d": (15, "minute", 10),
    "5m5d":   (5, "minute", 5),
}


@app.get("/api/tito-bars")
def tito_bars(ticker: str, tf: str = "5m5d"):
    """Su `/api/bars` — barras del subyacente para la gráfica de flujo.

    Era la única de sus once rutas cuyo equivalente estaba a medias: el payload
    de proyecciones ya traía el diario (`history`), que es lo que piden dos de
    sus tres consumidores (`SimpleChart` y `ProWallsCard`, los dos con `tf=1y`),
    pero el tercero —`FlowPriceChart`— ofrece **intradía** y eso no existía.

    La diferencia no es cosmética: agregado por día se ve QUÉ día entró el
    dinero grande; en velas de 5 minutos se ve si el precio se movió ANTES o
    DESPUÉS de que entrara, que es exactamente lo que mide el sub-agente 6.
    """
    tk, err = _tito_ticker(ticker)
    if err:
        raise HTTPException(status_code=400, detail=err)
    # `TF[tf] ?? TF["5m5d"]` suyo: un marco desconocido cae al de por defecto.
    mult, span, days = _TITO_TF.get(tf, _TITO_TF["5m5d"])
    try:
        from wbj.tito.massive import fetch_bars

        barras = fetch_bars(tk, mult, span, days)
    except Exception:
        # Su ruta responde 502 con el mensaje de `MassiveError`. Aquí el
        # mensaje NO viaja: `_describe` puede citar el cuerpo de la respuesta de
        # Massive, y eso es superficie de fuga (lo fija `test_route_safety`).
        raise HTTPException(status_code=502, detail="Error al cargar barras.")
    return {"ticker": tk, "tf": tf if tf in _TITO_TF else "5m5d",
            "bars": [{"time": b.time, "open": b.open, "high": b.high,
                      "low": b.low, "close": b.close} for b in barras]}


# ── El puente watchlist ↔ agente (su `/api/watchlist`) ───────────────────────
#
#   GET                            → { broker, granularity, pending, failed, legacy }
#   POST { contract, broker }      → encola un contrato para empujarlo al broker
#   POST { synced: [...], broker }  → el agente confirma lo que entró
#   DELETE ?symbol= | ?ticker=     → lo saca de la cola
#
# El watchlist en sí NO pasa por aquí: vive en localStorage (bloque `wlLocal*`
# del panel). Por este puente viaja lo mínimo para identificar el contrato en el
# broker —ticker y, si el broker acepta contratos, tipo/strike/vencimiento—. Los
# griegos, tu sizing y tu saldo se quedan en el navegador. `add_to_outbox`
# recorta según la granularidad, así que un broker que solo entiende subyacentes
# recibe solo el ticker.
#
# La sincronización tampoco ocurre aquí: el MCP del broker lo conduce el agente,
# no el servidor web. Esta ruta expone `pending` para que el agente sepa qué
# falta. Nada de esto coloca una orden — igual que en su app.


def _wl_item(i) -> dict:
    """Un `OutboxItem` como lo serializa su ruta: los campos de contrato solo
    cuando la fila los tiene.

    Se añaden dos campos que su ruta no manda porque en su app los calcula el
    cliente: `label` (`outboxLabel`) y `query` (`contractQuery`). El segundo no
    es comodidad — es la búsqueda EXACTA con la que el agente resuelve el
    contrato en el broker, con el strike a cuatro decimales. Reinventarla del
    otro lado es la vía directa a una búsqueda vacía que no dice por qué.
    """
    from wbj.tito.watchlist import ContractRef, contract_query, outbox_label

    d = {"ticker": i.ticker, "broker": i.broker,
         "addedAt": i.addedAt, "syncedAt": i.syncedAt,
         "label": outbox_label(i)}
    if i.symbol:
        d.update(symbol=i.symbol, type=i.type,
                 strike=i.strike, expiration=i.expiration)
        # `None` cuando falta strike o vencimiento: quien lo recibe cae al
        # subyacente en vez de adivinar entre decenas de contratos.
        d["query"] = contract_query(ContractRef(i.ticker, i.type, i.strike,
                                                i.expiration))
    if i.failedAt:
        d.update(failedAt=i.failedAt, failReason=i.failReason)
    return d


def _wl_payload(items, broker: str) -> dict:
    """Su `payload(items, broker)`, campo por campo."""
    from wbj.tito.watchlist import broker_by_id, failed_outbox, pending_outbox

    b = broker_by_id(broker)
    sincronizados = sorted(
        i.syncedAt for i in items if i.broker == broker and i.syncedAt
    )
    return {
        "broker": broker,
        "granularity": b.granularity if b else "none",
        "pending": [_wl_item(i) for i in pending_outbox(items, broker)],
        "failed": [_wl_item(i) for i in failed_outbox(items, broker)],
        # Para que la UI diga cuándo pasó el drenador por última vez.
        "lastSyncedAt": sincronizados[-1] if sincronizados else None,
    }


@app.get("/api/tito-watchlist")
def tito_watchlist(broker: str = "robinhood"):
    """`legacy` sirve a la migración única desde el viejo `watchlist.json`. En
    cuanto el navegador lo importa y marca la bandera, deja de mirarlo."""
    from dataclasses import asdict

    from wbj.tito.outbox_store import load_outbox
    from wbj.tito.watchlist import BROKERS
    from wbj.tito.watchlist_store import load_watchlist

    caja = load_outbox()
    viejo = load_watchlist()
    out = _wl_payload(caja["items"], broker)
    out["legacy"] = {
        "entries": [asdict(e) for e in viejo["entries"]],
        "broker": viejo["broker"],
    }
    # Los brokers viajan con la respuesta porque el selector del panel se pinta
    # desde aquí: añadir uno es añadir una entrada en `watchlist.py`, no tocar
    # el HTML en dos sitios.
    out["brokers"] = [
        {"id": b.id, "name": b.name, "kind": b.kind,
         "granularity": b.granularity, "caveat": b.caveat,
         "quoteUrl": b.quote_url("__T__") if b.quote_url else None}
        for b in BROKERS
    ]
    return out


def _wl_contrato(crudo):
    """Su `readContract`: un contrato válido trae al menos símbolo y ticker; el
    resto puede faltar."""
    from wbj.tito.watchlist import OutboxTarget

    if not isinstance(crudo, dict):
        return None
    if not isinstance(crudo.get("symbol"), str) or not isinstance(crudo.get("ticker"), str):
        return None
    if crudo.get("type") not in ("call", "put"):
        return None
    strike = crudo.get("strike")
    exp = crudo.get("expiration")
    return OutboxTarget(
        symbol=crudo["symbol"],
        ticker=crudo["ticker"].upper(),
        type=crudo["type"],
        strike=strike if isinstance(strike, (int, float)) and not isinstance(strike, bool) else None,
        expiration=exp if isinstance(exp, str) else None,
    )


@app.post("/api/tito-watchlist")
async def tito_watchlist_post(request: Request):
    from wbj.tito.outbox_store import load_outbox, save_outbox
    from wbj.tito.watchlist import (add_to_outbox, broker_by_id,
                                    mark_outbox_failed, mark_outbox_synced)

    try:
        body = await request.json()
    except Exception:
        return JSONResponse(status_code=400, content={"error": "Cuerpo inválido."})
    if not isinstance(body, dict):
        return JSONResponse(status_code=400, content={"error": "Cuerpo inválido."})

    broker_id = body.get("broker") or "robinhood"
    broker = broker_by_id(broker_id) if isinstance(broker_id, str) else None
    if not broker:
        return JSONResponse(status_code=400, content={"error": "Broker desconocido."})

    ahora = datetime.now(timezone.utc)
    items = load_outbox()["items"]

    sincro = body.get("synced")
    if isinstance(sincro, list) and sincro:
        items = mark_outbox_synced(items, [str(k) for k in sincro], broker_id, ahora)

    # El drenador aparca lo irresoluble para no reintentarlo cada 15 minutos
    # para siempre.
    fallidos = body.get("failed")
    if isinstance(fallidos, list) and fallidos:
        motivo = body.get("reason")
        motivo = motivo.strip() if isinstance(motivo, str) and motivo.strip() \
            else "No se pudo resolver el contrato en el broker."
        items = mark_outbox_failed(items, [str(k) for k in fallidos],
                                   broker_id, motivo, ahora)

    if "contract" in body:
        contrato = _wl_contrato(body.get("contract"))
        if contrato is None:
            return JSONResponse(status_code=400, content={"error": "Contrato inválido."})
        # Con granularidad `contracts` el broker resuelve por subyacente+tipo+
        # strike+vencimiento (ver `contract_query`). Sin strike o sin
        # vencimiento el contrato es insincronizable, y aceptarlo solo aplaza el
        # fallo hasta el drenador, donde ya no hay a quién avisar. Se rechaza en
        # la puerta.
        if broker.granularity == "contracts" and (
                contrato.strike is None or not contrato.expiration):
            return JSONResponse(status_code=400, content={
                "error": "Faltan strike o vencimiento: sin ellos no se puede "
                         "resolver en el broker."})
        items = add_to_outbox(items, contrato, broker, ahora)

    guardado = save_outbox(items)
    return _wl_payload(guardado["items"], broker_id)


@app.delete("/api/tito-watchlist")
def tito_watchlist_delete(symbol: str | None = None, ticker: str | None = None,
                          broker: str = "robinhood"):
    """Desencola. Con `symbol` quita ese contrato; con `ticker` quita todo lo de
    esa empresa —que es lo que corresponde cuando el broker solo guarda
    subyacentes—.

    Conviene mandar **ambos** con granularidad `contracts`: el `ticker` es lo
    único que alcanza a las filas viejas de solo-tickers, que con `symbol` a
    secas eran imborrables. El filtrado vive en `remove_from_outbox`, que es
    puro y está cubierto por tests.
    """
    from wbj.tito.outbox_store import load_outbox, save_outbox
    from wbj.tito.watchlist import remove_from_outbox

    if not symbol and not ticker:
        return JSONResponse(status_code=400,
                            content={"error": "Falta el símbolo o el ticker."})
    items = remove_from_outbox(load_outbox()["items"],
                               {"symbol": symbol, "ticker": ticker}, broker)
    guardado = save_outbox(items)
    return _wl_payload(guardado["items"], broker)


def _tito_remember(ticker, result, now):
    """Guarda la predicción del día para que el lazo de calibración cierre.

    Solo se guardan las fiables: archivar una predicción marcada NO FIABLE
    contaminaría el sesgo histórico con ruido que el agente ya sabía malo.

    Devuelve `None` si todo fue bien, o el motivo del fallo. **No se traga el
    error en silencio**: las otras tres escrituras del panel (cadena, trades,
    IV) ya se contaban en `escrituras_fallidas`, y justo ésta —de la que
    depende TODA la calibración, o sea lo único que hace que el agente mejore
    con el tiempo— fallaba muda. Un disco lleno o un permiso mal puesto podían
    dejar el track record congelado durante meses sin que nada lo dijera, y el
    panel seguiría enseñando «0 predicciones vencidas» como si fuera pronto.
    """
    try:
        from wbj.tito import stores as st
        for h, p in (result.predictions or {}).items():
            if p.caveat and "NO FIABLE" in p.caveat:
                continue
            st.save_prediction(ticker, st.PredictionSnapshot(
                date=st.market_date_str(now), horizon_days=int(h), spot=p.spot,
                bear=p.bear.target, base=p.base.target, bull=p.bull.target,
                direction=p.direction, confidence=p.confidence,
                # `savedAt: now.toISOString()` de su `savePrediction`.
                saved_at=now.astimezone(timezone.utc).strftime(
                    "%Y-%m-%dT%H:%M:%S.") + f"{now.microsecond // 1000:03d}Z",
            ))
    except Exception as e:                           # noqa: BLE001
        # La memoria es acumulativa: perder un día no rompe la corrida — pero
        # sí se dice, que es la diferencia entre degradarse y mentir.
        logging.getLogger(__name__).warning(
            "no se pudo guardar la predicción de %s: %s", ticker, e)
        return f"predicciones: {type(e).__name__}"
    return None


#: Lo que NO se archiva del scorecard, con su peso medido.
#:
#: Son datos DERIVADOS: se vuelven a bajar de Massive cuando hagan falta, y no
#: participan en ninguna decisión que el archivo tenga que poder justificar
#: después. Archivarlos costaría ~1,8 GB al año con 20 tickers —la cadena sola
#: son 372 KB por reporte, 1.500 filas a 254 bytes— y el repositorio dejaría de
#: caber en GitHub en meses.
#:
#: Lo que SÍ se queda es todo lo que sostiene el veredicto: los 6 scores con su
#: desglose, los escenarios, los niveles, los muros del GEX, las filas de
#: convicción, las advertencias y `chain_meta`/`top_contracts` —que dicen sobre
#: cuántos contratos se calculó y cuáles pesaban más—. La regla del proyecto es
#: que ningún número viaje sin su evidencia; ninguno de estos cinco campos es
#: evidencia de nada, son la materia prima.
_OPCIONES_NO_SE_ARCHIVA = ("chain", "history", "gex_heatmap", "levels_for_chart",
                           "chart_geometry",
                           # Hasta 150 trades con sus griegos y sus seis
                           # sub-puntajes. Es materia prima de pantalla: los
                           # promedios y el recuento —que son la evidencia del
                           # score— sí se archivan, dentro de `subagents`.
                           "unusual_rows")


def _sin_derivado(out):
    """El scorecard sin la materia prima, listo para archivar."""
    recortado = {k: v for k, v in out.items() if k not in _OPCIONES_NO_SE_ARCHIVA}
    # Se declara lo que falta, en vez de que el archivo mienta por omisión.
    recortado["_no_archivado"] = list(_OPCIONES_NO_SE_ARCHIVA)
    return recortado


def _archiva_opciones(out):
    """El scorecard del agente de OPCIONES → `Proyecciones/<TICKER>/<fecha>/`.

    Carpeta SEPARADA de la de acciones a propósito. Son dos productos
    distintos: aquí un score de flujo 0-100 con escenarios a 10/20/30 días;
    allí una tesis a 1-3 años con valuación y gates. Compartir carpeta
    obligaría a abrir el archivo para saber de cuál es.

    Se archiva **una vez al día por ticker**: el panel se auto-refresca cada
    minuto, y guardar cada refresco llenaría el repositorio de fotos casi
    idénticas del mismo día. La última del día pisa a la anterior, que es la
    que vale — el `prediccion.json` del motor es lo que congela el histórico
    intradía, y ese ya se guarda aparte.
    """
    try:
        import vertex_archivo as _ar

        if not (out or {}).get("ok") or not out.get("ticker"):
            return                               # un error no es un reporte
        _ar.guarda_reporte_opciones(out["ticker"], _sin_derivado(out))
    except Exception as e:                       # noqa: BLE001
        logging.getLogger(__name__).warning(
            "no se pudo archivar el scorecard de opciones: %s", e)


def _r(x, n=2):
    """Redondeo de JS (`Math.round`, mitad SIEMPRE hacia arriba), no el de Python.

    El `round()` de Python es bancario: `round(2.675, 2)` y `round(0.5)` no dan
    lo mismo que `(2.675).toFixed(2)` y `Math.round(0.5)`. Todo lo que sale por
    esta ruta se pinta al lado de números que su panel formatea con `toFixed`,
    así que el criterio tiene que ser el suyo. Es la misma regla que
    `jsmath.js_round` fija dentro del motor — aquí faltaba en el borde.

    `None` y los no finitos pasan tal cual: `_json_safe` los convierte después.
    """
    if x is None or not isinstance(x, (int, float)) or isinstance(x, bool):
        return x
    if x != x or x in (float("inf"), float("-inf")):
        return x
    from wbj.tito.jsmath import js_round
    f = 10 ** n
    return js_round(x * f) / f


def _tito_json(r):
    """Aplana el ScorecardResult a JSON. Solo lo que el panel necesita pintar.

    Sale por `_json_safe`, que convierte `NaN`/`Infinity` en `null`. Es lo mismo
    que hace su `JSON.stringify` en Next.js, y aquí hace falta de verdad:
    `compute.to_row` es traducción literal de su `compute.ts`, así que un
    contrato con `open_interest: "abc"` produce un nocional `NaN` — y
    `json.dumps` escribiría `NaN` a pelo, que NO es JSON y el `JSON.parse` del
    navegador rechaza. La misma fila, en su lado, sale como `null`.
    """
    def scen(s):
        return {"target": _r(s.target), "change_pct": _r(s.change_pct, 1),
                "probability": _r(s.probability, 3), "driver": s.driver}

    # `const agresividadIds = new Set(interesting.map(r => r.id))` suyo. El
    # cruce de su `UnusualityCard`: qué trades vio TAMBIÉN el sub-agente 1.
    _agresivos = {t.id for t in ((r.flow.interesting if r.flow else None) or [])}

    # `probTouch(spot, l.price, iv, horizonDays)` de su `NivelesSimples`: la
    # probabilidad de que el precio LLEGUE a ese nivel dentro del horizonte.
    #
    # Él la calcula en el componente, con la IV del GEX y el horizonte elegido.
    # Aquí no existía: la tabla de niveles enseñaba precio, fuerza y distancia,
    # y no cuán probable era llegar. Un soporte de fuerza 80 al 15% de distancia
    # y otro de fuerza 50 al 2% se leían igual de "fuertes", que es justo lo que
    # esta columna desambigua. El motor ya trae `prob_touch`; solo faltaba
    # llamarlo.
    from wbj.tito.expected_move import prob_touch as _prob_touch

    _iv_niveles = r.gex.iv if r.gex and r.gex.iv else 0.4
    #: El horizonte medio de los suyos (10/20/30) — su `NivelesSimples` usa el
    #: que el usuario tenga elegido y el panel no tiene ese selector aquí.
    _dias_niveles = 20

    def lvl(l):
        try:
            toque = _prob_touch(r.spot, l.price, _iv_niveles, _dias_niveles) if r.spot > 0 else None
        except Exception:
            toque = None
        return {"price": _r(l.price), "kind": l.kind, "strength": l.strength,
                "distance_pct": _r(l.distance_pct, 1), "why": l.why,
                "flipped": l.flipped, "touch": _r(toque, 3)}

    return _json_safe({
        "ok": True,
        "ticker": r.ticker,
        "spot": _r(r.spot),
        "score": r.score,
        "verdict": r.verdict,
        "verdict_meaning": r.verdict_meaning,
        "active": r.active,
        "scores": r.scores,
        "gex": {
            "iv": _r(r.gex.iv, 4),
            "regime": r.gex.regime,
            "king_strike": r.gex.king_strike,
            "flip_strike": _r(r.gex.flip_strike) if r.gex.flip_strike else None,
            "direction": r.gex.direction,
            "confidence": r.gex.confidence,
            "low_liquidity": r.gex.low_liquidity,
            # `totalNetGex` y `n` de su `GexAnalysis`. Son los dos números que
            # dicen SOBRE QUÉ se calculó el régimen: sin ellos la etiqueta
            # "γ+ / γ−" es una palabra sin magnitud ni muestra detrás.
            "total_net_gex": r.gex.total_net_gex,
            "n": r.gex.n,
            # Los nodos: GEX neto por strike con su lado. Es lo que dibuja el
            # gráfico de gamma por strike y de donde salen los MUROS (el nodo
            # call de mayor magnitud y el put de mayor magnitud), que es como
            # los saca su `ProWallsCard`. Antes ese gráfico y esas cards venían
            # de Quant Data — otro proveedor midiendo lo mismo.
            "nodes": [{"strike": n.strike, "net_gex": n.net_gex,
                       "call_gex": n.call_gex, "put_gex": n.put_gex,
                       "trade_premium": n.trade_premium,
                       "trade_count": n.trade_count,
                       "concentration": _r(n.concentration, 4), "side": n.side}
                      for n in r.gex.nodes],
        },
        # Sub-agente 4 sobre la cadena completa. `call_pct`/`put_pct` son el
        # reparto del NOCIONAL entre calls y puts —el Put/Call de la card— y
        # `vol_oi` cuántos contratos negociaron más de su open interest, que es
        # la definición de "actividad inusual" que él usa sobre la cadena.
        "structure": {
            "score": r.structure.score,
            "call_pct": _r(r.structure.strikes["call_pct"], 1),
            "put_pct": _r(r.structure.strikes["put_pct"], 1),
            "dominant_side": r.structure.strikes["dominant_side"],
            "avg_notional": r.structure.notional["avg_per_strike"],
            "low_liquidity": r.structure.notional["low_liquidity"],
            "vol_oi": {"pct": _r(r.structure.vol_oi["pct"], 1),
                       "exceeded": r.structure.vol_oi["exceeded"],
                       "considered": r.structure.vol_oi["considered"]},
            # `pct_of_total` es la sexta columna de su tabla "Top strikes por
            # nocional". Sin ella, "$268M" no dice si ese strike es la mitad de
            # la cadena o una esquina.
            "top_strikes": [{"strike": t.strike, "notional": t.notional,
                             "pct_of_total": _r(t.pct_of_total, 1),
                             "side": t.side, "dominant": t.dominant,
                             "dominance_pct": _r(t.dominance_pct, 1),
                             "open_interest": t.open_interest, "volume": t.volume}
                            for t in r.structure.strikes["top"][:8]],
        },
        # Sub-agente 3: los trades más inusuales, con el desglose por parámetro.
        # Es su `UnusualityCard` — la tabla de "actividad inusual" del tab salía
        # de Quant Data y medía otra cosa (volumen contra OI de la cadena).
        "unusual": [{"id": t[0].id, "symbol": t[0].symbol, "type": t[0].type,
                     "strike": t[0].strike, "expiration": t[0].expiration,
                     "dte": t[0].dte, "premium": t[0].premium,
                     "aggression": t[0].aggression, "total": t[1].total,
                     "size": t[1].size, "delta": t[1].delta, "theta": t[1].theta,
                     "gamma": t[1].gamma, "leg": t[1].leg, "expiry": t[1].expiry}
                    for t in r.unusuality.top[:12]],
        # ── El DETALLE de los 6 sub-agentes — su `<details>` "Detalle de
        # sub-agentes", que es donde se ve POR QUÉ cada categoría puntúa lo que
        # puntúa. El motor lo calculaba entero desde el primer día y el payload
        # servía solo el titular 0-10: un número sin su evidencia, que es
        # justo lo que la regla innegociable del proyecto prohíbe.
        "subagents": {
            # 1 · Agresividad — cuánto premium fue al ask contra el bid.
            "aggression": {
                "score": r.aggression.score, "ratio": _r(r.aggression.ratio, 4),
                "premium_ask": r.aggression.premium_ask,
                "premium_bid": r.aggression.premium_bid,
                "premium_mid": r.aggression.premium_mid, "n": r.aggression.n,
            },
            # 2 · Convicción — spread, dominancia y calidad de la ejecución.
            "conviction": {
                "score": r.conviction.score, "n": r.conviction.n,
                "spread": {"avg_pct": _r(r.conviction.spread["avg_pct"], 2),
                           "points": r.conviction.spread["points"],
                           "wide_count": r.conviction.spread["wide_count"]},
                "dominance": {"side": r.conviction.dominance["side"],
                              "dominant_pct": _r(r.conviction.dominance["dominant_pct"], 1),
                              "ask_pct": _r(r.conviction.dominance["ask_pct"], 1),
                              "bid_pct": _r(r.conviction.dominance["bid_pct"], 1),
                              "points": r.conviction.dominance["points"]},
                # `avg_raw` es la "Fuerza de ejecución" de su `ConvictionCard`:
                # el promedio SIN redondear de lo agresivas que fueron las
                # órdenes. `points` es el mismo número ya redondeado a la
                # escala del sub-agente, así que servir solo `points` perdía la
                # métrica que él enseña con un decimal.
                "execution": {"points": _r(r.conviction.execution["points"], 1),
                              "avg_raw": _r(r.conviction.execution["avg_raw"], 2),
                              "counts": r.conviction.execution["counts"]},
            },
            # 3 · Inusualidad — el promedio por parámetro, que es el desglose
            # que su `UnusualityCard` pone bajo el 0-10.
            "unusuality": {
                "score": r.unusuality.score, "n": r.unusuality.n,
                "unusual_count": r.unusuality.unusual_count,
                # Su `confirmedCount`: de los inusuales, cuántos vio TAMBIÉN el
                # sub-agente 1. Él lo deja escrito y conviene repetirlo — «es un
                # proceso aparte y NO afecta el scoreboard»: verifica la
                # etiqueta, no la puntúa.
                "confirmed_count": sum(
                    1 for t, _ in r.unusuality.top if t.id in _agresivos),
                "avg_by_param": {k: _r(v, 1) for k, v in r.unusuality.avg_by_param.items()},
            },
            # 4 · Estructura — nocional por strike, dominio y volumen>OI.
            "structure": {
                "score": r.structure.score,
                "notional": {"avg_per_strike": r.structure.notional["avg_per_strike"],
                             "total": r.structure.notional["total"],
                             "strike_count": r.structure.notional["strike_count"],
                             "points": r.structure.notional["points"]},
                "strikes": {"dominant_count": r.structure.strikes["dominant_count"],
                            "considered_count": r.structure.strikes["considered_count"],
                            "points": r.structure.strikes["points"]},
                "vol_oi_points": r.structure.vol_oi["points"],
                "expirations": [{"expiration": e.expiration, "notional": e.notional,
                                 "pct_of_total": _r(e.pct_of_total, 1),
                                 "call_notional": e.call_notional,
                                 "put_notional": e.put_notional,
                                 "contracts": e.contracts}
                                for e in r.structure.expirations[:6]],
            },
            # 5 · Contexto IV — la banda, el rank CON SU FUENTE y el skew.
            # `rank.source` no es decorativo: dice si el IV Rank sale del
            # historial propio o de un proxy de volatilidad realizada, y eso
            # cambia cuánto vale el número.
            "iv_context": {
                "score": r.iv_context.score, "regime": r.iv_context.regime,
                "note": r.iv_context.note,
                "front_skew": _r(r.iv_context.front_skew, 1) if r.iv_context.front_skew is not None else None,
                "iv": {"current": _r(r.iv_context.iv["current"], 1),
                       "points": r.iv_context.iv["points"],
                       "band": r.iv_context.iv["band"],
                       "special": r.iv_context.iv["special"],
                       "contracts": r.iv_context.iv["contracts"]},
                "rank": {"value": _r(r.iv_context.rank["value"], 0),
                         "points": r.iv_context.rank["points"],
                         "band": r.iv_context.rank["band"],
                         "source": r.iv_context.rank["source"],
                         "days": r.iv_context.rank["days"],
                         "low": _r(r.iv_context.rank["low"], 1),
                         "high": _r(r.iv_context.rank["high"], 1)},
                "by_expiration": [{"expiration": e.expiration, "dte": e.dte,
                                   "trades": e.trades, "avg_iv": _r(e.avg_iv, 1),
                                   "premium": e.premium}
                                  for e in r.iv_context.by_expiration[:6]],
            },
            # 6 · Confirmación de precio — el backtest. `coverage.below_target`
            # es la advertencia de "preliminar" que ya viajaba en `warnings`;
            # aquí va con los números que la sostienen.
            "validation": {
                "score": r.validation.score, "verdict": r.validation.verdict,
                "threshold_pct": _r(r.validation.threshold_pct, 1),
                "weighted_hit_rate": _r(r.validation.weighted_hit_rate, 0),
                "avg_mfe": _r(r.validation.avg_mfe, 1),
                "avg_mae": _r(r.validation.avg_mae, 1),
                "hit_rate": {"value": _r(r.validation.hit_rate["value"], 0),
                             "points": r.validation.hit_rate["points"],
                             "validated": r.validation.hit_rate["validated"],
                             "resolved": r.validation.hit_rate["resolved"],
                             "band": r.validation.hit_rate["band"]},
                "speed": {"median_sessions": r.validation.speed["median_sessions"],
                          "points": r.validation.speed["points"],
                          "band": r.validation.speed["band"]},
                "by_direction": r.validation.by_direction,
                # ── La EVIDENCIA del sub-agente 6, que no salía de la ruta ──
                #
                # Su `ValidationCard` no enseña solo la tasa: enseña la tabla
                # «Qué pasó después de cada flow» — cada operación pasada con
                # cuánto recorrió a favor, cuánto en contra, cuánto tardó y si
                # acabó validada o absorbida. `outcomes` se calculaba entero en
                # `validation.py` y moría en el servidor.
                #
                # Es justo lo que la regla del proyecto prohíbe perder: «sin
                # evidencia, no hay número». El 62% de tasa de validación era un
                # número sin las 25 filas que lo producen — y sin ellas no se
                # puede ver si vino de tres aciertos grandes o de veinte
                # pequeños, que se leen distinto.
                #
                # Las 25 más recientes, su `outcomes.slice(0, 25)`.
                "outcomes": [
                    {"id": o.id, "timestamp": o.timestamp, "type": o.type,
                     "strike": o.strike, "expiration": o.expiration,
                     "premium": o.premium, "direction": o.direction,
                     "entry_price": _r(o.entry_price),
                     "mfe_pct": _r(o.mfe_pct, 1), "mae_pct": _r(o.mae_pct, 1),
                     "days_to_mfe": o.days_to_mfe, "days_to_mae": o.days_to_mae,
                     "days_to_validate": o.days_to_validate,
                     "days_to_invalidate": o.days_to_invalidate,
                     "sessions_observed": o.sessions_observed,
                     "days_elapsed": o.days_elapsed,
                     "validated": o.validated, "resolved": o.resolved}
                    for o in (r.validation.outcomes or [])[:25]
                ],
                "coverage": {"days": r.validation.coverage["days"],
                             "flows": r.validation.coverage["flows"],
                             "pending": r.validation.coverage["pending"],
                             "below_target": r.validation.coverage["below_target"]},
            },
        },
        "levels": {
            "supports": [lvl(l) for l in r.levels.supports],
            "resistances": [lvl(l) for l in r.levels.resistances],
            # Los tres campos de su `LevelsResult` que la ruta tiraba.
            #
            # `tolerance_pct` es lo que explica la nota de su `LevelsCard`
            # —«$299 y $301 son el mismo techo, no dos»—: sin él la nota tenía
            # que inventarse el número o callarlo. `key_support` y
            # `key_resistance` son SU par elegido: los dos niveles que él
            # destaca arriba del todo, que no son sin más el primero de cada
            # lista (pesa la coincidencia precio ∩ opciones).
            "tolerance_pct": r.levels.tolerance_pct,
            "key_support": lvl(r.levels.key_support) if r.levels.key_support else None,
            "key_resistance": (lvl(r.levels.key_resistance)
                               if r.levels.key_resistance else None),
        },
        "predictions": {
            str(h): {
                "bear": scen(p.bear), "base": scen(p.base), "bull": scen(p.bull),
                "confidence": p.confidence, "direction": p.direction,
                "summary": p.summary, "caveat": p.caveat,
                "calibration": p.calibration,
            }
            for h, p in r.predictions.items()
        },
        # Las advertencias NO son decorativas: la salvaguarda de liquidez y el
        # aviso de categorías faltantes deben viajar con el número siempre.
        "warnings": r.warnings,
        # Los DOS contadores, como su `meta.notableCount` y su
        # `convictionMeta.total`: el pulso de la semana y la ventana ancha de 30
        # días. Con uno solo no se puede saber sobre qué universo puntuaron
        # Convicción, Inusualidad y Contexto IV.
        "notable_trades": len(r.flow.interesting),
        "conviction_trades": len(r.conviction_flow),
        "conviction_window": r.conviction_window,
        # Las FILAS de convicción, no solo su contador — su
        # `ConvictionTransactions`, "Transacciones revisadas". Estos trades son
        # el universo sobre el que puntúan Convicción, Inusualidad y Contexto
        # IV: servir únicamente el número dejaba tres de las seis categorías
        # sin nada que las respalde en pantalla.
        #
        # Se mandaban 25 y él manda `CONVICTION_TABLE_CAP` = 150. No era solo
        # una tabla más corta: estas filas alimentan sus TRES tarjetas
        # —`ConvictionTransactions`, `ActivityCard` y `MoneyFlowCard`— y la
        # última es la gráfica que dice "el dinero de CADA DÍA". Con las 25 de
        # mayor premium no es el dinero del día: es el de los 25 trades más
        # grandes, y un día entero de trades medianos desaparecía del gráfico.
        # Las TRES mayores de `convRows ∪ notable`, dedupe por `id` y orden
        # por premium — su `topFlows`, que `PredictionCard` pinta bajo los
        # escenarios como "Top 3 flows notables".
        #
        # Faltaba entero. Es el único sitio del panel donde los escenarios de
        # Prediction Pro van acompañados de las operaciones CONCRETAS que los
        # sostienen: sin él, los tres targets salen sin que se pueda ver de qué
        # dinero se dedujeron. La unión es la suya y no `conviction_flow` a
        # secas: la ventana corta trae operaciones recientes que la de 30 días
        # todavía no tiene.
        "top_flows": [
            {"id": t.id, "type": t.type, "strike": t.strike,
             "expiration": t.expiration, "dte": t.dte, "premium": t.premium,
             "aggression": t.aggression,
             # `(call ∧ ask) ∨ (put ∧ bid)`, su regla literal: comprar calls y
             # vender puts son la misma apuesta.
             "alcista": (t.type == "call" and t.aggression == "ask")
                        or (t.type == "put" and t.aggression == "bid")}
            for t in sorted(
                _tito_unir(r.conviction_flow or [], r.flow.interesting or []),
                key=lambda x: x.premium if isinstance(x.premium, (int, float)) else 0,
                reverse=True)[:3]
        ],
        "conviction_rows": [
            {"id": t.id, "underlying": t.underlying, "type": t.type,
             "strike": t.strike, "expiration": t.expiration, "dte": t.dte,
             "expiry_status": t.expiry_status, "premium": t.premium,
             "size": t.size, "aggression": t.aggression,
             "timestamp": t.timestamp, "repeated": t.flags.repeated,
             "multileg": t.flags.multileg}
            for t in sorted(r.conviction_flow,
                            key=lambda t: t.premium if isinstance(t.premium, (int, float)) else 0,
                            reverse=True)[:TITO_CONVICTION_TABLE_CAP]
        ],
        # Su `unusualRows`: `unusuality.top` con el puntaje de los seis
        # parámetros pegado a cada fila y la marca de si el sub-agente 1 vio el
        # MISMO trade. Es lo que alimenta su `UnusualityCard`, que hasta ahora
        # aquí se resumía en seis promedios — y un promedio no dice QUÉ
        # contrato disparó la señal, que es justo lo que se va a mirar.
        #
        # `confirmed_by_aggression` es su cruce: `agresividadIds.has(row.id)`,
        # el set de los notables del sub-agente 1. Él lo deja escrito y es
        # importante — «es un proceso aparte y NO afecta el scoreboard»: es una
        # verificación, no un peso.
        "unusual_rows": [
            {"id": t.id, "underlying": t.underlying, "type": t.type,
             "strike": t.strike, "expiration": t.expiration, "dte": t.dte,
             "timestamp": t.timestamp, "premium": t.premium,
             "delta": t.delta, "gamma": t.gamma,
             "theta_pct_daily": _r(t.theta_pct_daily, 2),
             "condition_code": t.condition_code,
             "condition_name": t.condition_name,
             "repeated": bool(t.flags.repeated),
             "multileg": bool(t.flags.multileg),
             "confirmed_by_aggression": t.id in _agresivos,
             "unusual_scores": {"size": s.size, "delta": s.delta,
                                "theta": s.theta, "gamma": s.gamma,
                                "leg": s.leg, "expiry": s.expiry,
                                "total": s.total}}
            for t, s in (r.unusuality.top if r.unusuality else [])
        ],
        # % del premium notable en calls. Es el mismo número que usa el resumen
        # de Prediction Pro, y el que /api/tito-news necesita para confrontar la
        # dirección del dinero contra la de los titulares.
        "call_pct": _tito_call_pct(r),
    })


def _tito_call_pct(r):
    """% del premium notable que está en calls, o None si no hay flujo direccional.

    Se calcula sobre `convictionRows` —la ventana ancha de 30 días y ≥$1M—,
    igual que el `callPct` de su `page.tsx`. Es el mismo número que usa el
    resumen de Prediction Pro y el que `/api/tito-news` necesita para confrontar
    la dirección del dinero contra la de los titulares.

    `premium` llega crudo: `sum()` de Python lanza con un texto y su aritmética
    coacciona. `Math.round`, no `round()`, por lo mismo de siempre.
    """
    from wbj.tito.jsmath import js_number, js_round
    filas = r.conviction_flow or r.flow.interesting
    call_p = sum(js_number(x.premium) for x in filas if x.type == "call")
    put_p = sum(js_number(x.premium) for x in filas if x.type == "put")
    total = call_p + put_p
    return js_round(call_p / total * 100) if total > 0 else None


@app.get("/api/tito-scorecard")
def tito_scorecard(ticker: str, horizons: str = "10,20,30"):
    """Scorecard de flujo 0-100 (6 sub-agentes) + 3 escenarios por horizonte.

    Fuente del tape: MarketSnack (MARKETSNACK_COOKIE).
    Cadena y barras: Massive (MASSIVE_API_KEY), sin respaldo.
    """
    sc = _tito_mod()
    if sc is None:
        return {"ok": False, "error": "Motor Tito no disponible (engine/wbj/tito)."}

    from wbj.tito.marketsnack import MarketSnackError, fetch_flow

    tk, err = _tito_ticker(ticker)
    if err:
        return {"ok": False, "error": err}

    try:
        hz = tuple(int(h) for h in horizons.split(",") if h.strip())[:5] or (10, 20, 30)
    except ValueError:
        hz = (10, 20, 30)

    trades, conviction_trades, flow_error = _tito_tape(tk)
    if flow_error and not trades:
        # Sin tape no hay scorecard: 4 de las 6 categorías dependen de él. Se
        # devuelve el motivo exacto en vez de un reporte a medias sin avisar.
        return {"ok": False, "error": flow_error, "source": "marketsnack"}

    try:
        chain, bars, spot, meta_cadena = _tito_chain_and_bars(tk)
    except Exception as e:
        return {"ok": False, "error": _error_de_fuente(e, "Cadena de Massive"),
                "source": "massive"}

    # Ver la nota del borde en `/api/tito-targets`: el motor es literal y lanza
    # donde su archivo lanza; aquí se traduce a un error con forma de JSON.
    try:
        r = sc.run_scorecard(
            tk, trades, chain or [], bars,
            now=datetime.now(timezone.utc), spot=spot, horizons=hz,
            conviction_trades=conviction_trades,
        )
    except Exception as e:                       # noqa: BLE001 — se reporta, no se traga
        return {"ok": False, "error": _error_publico(e, "Motor de Víctor"),
                "source": "motor"}
    out = _tito_json(r)
    out["chain_source"] = "massive"
    # La ficha de la empresa (`CompanyHeader`) y la cadena entera
    # (`OptionChainTable` + `ChartPanel`). El motor no las necesita —puntúa con
    # `chain` ya normalizada— pero son dos de sus componentes, así que viajan
    # con el payload en vez de pedir otra ronda de red desde el navegador.
    out["company"] = _tito_company(tk, r.spot, meta_cadena["empresa"])
    out.update(_tito_chain_json(chain or [], meta_cadena["truncated"]))
    return out


#: Tope de filas de cadena que viajan al panel. Su tabla las pinta TODAS, y
#: aquí también hasta este número: por encima el payload de un subyacente muy
#: listado (SPY pasa de 10.000 contratos) se come el tope de guardado del
#: reporte y se perdería lo demás. El recorte se declara en `chain_meta` y la
#: tabla lo dice en pantalla — nunca se recorta en silencio. Las que se quedan
#: son las de mayor open interest, que es el orden en que él las sirve.
TITO_CHAIN_MAX = int(os.environ.get("VERTEX_CHAIN_MAX", "1500") or 1500)


def _tito_company(ticker: str, spot, c: dict | None):
    """La `CompanyInfo` de su `types.ts`, en snake_case.

    Nunca lanza: la ficha es contexto de cabecera, y quedarse sin ella no puede
    tumbar un scorecard que ya está calculado. Sin Massive devuelve lo mínimo
    —ticker y el spot que el motor ya usó— para que la cabecera no salga vacía.
    """
    base = {"ticker": ticker, "name": None, "exchange": None, "sector": None,
            "price": _r(spot), "change": None, "change_percent": None,
            "market_cap": None, "day_volume": None, "day_low": None,
            "day_high": None, "prev_close": None, "employees": None,
            "has_logo": False}
    if not c:
        return base
    base.update({
        "name": c.get("name"),
        "sector": c.get("sector"),
        # `exchange` y `employees` estaban DECLARADOS en el dict base de arriba
        # y `vcCompanyHTML` ya los leía —el subtítulo es `[exchange, sector]` y
        # hay una casilla de empleados—, pero nadie los rellenaba: el eslabón
        # que faltaba era `fetch_company`, que no los pedía. El subtítulo salía
        # con el sector solo y la casilla vacía, sin que nada fallara.
        "exchange": c.get("exchange"),
        "employees": c.get("employees"),
        # El spot del motor manda sobre el del snapshot: es el que ancló los
        # nodos del GEX, los niveles y los tres targets. Que la cabecera diga
        # un precio y la gráfica otro es exactamente el fallo que la cadena de
        # respaldo de su `page.tsx` evita.
        "price": _r(spot) if spot else _r(c.get("price")),
        "change": _r(c.get("change")),
        "change_percent": _r(c.get("change_percent"), 2),
        "market_cap": c.get("market_cap"),
        "day_volume": c.get("day_volume"),
        "day_low": _r(c.get("day_low")),
        "day_high": _r(c.get("day_high")),
        "prev_close": _r(c.get("prev_close")),
        # `hasLogo` suyo: `Boolean(branding.logo_url || branding.icon_url)`.
        #
        # Antes iba forzado a `True` porque comprobarlo parecía costar otra
        # llamada. No cuesta ninguna: la marca viene en el MISMO
        # `/v3/reference/tickers/` que ya trae el nombre y el sector, así que
        # ahora se usa el valor de verdad. La diferencia es un 404 de menos por
        # cada ticker sin logo — y dejar de prometer una imagen que no existe.
        #
        # Si la ficha no llegó, se mantiene la promesa optimista: la cabecera
        # pide el logo y cae a las iniciales si no está, que es mejor que
        # esconderlo por no haber podido preguntar.
        "has_logo": bool(c.get("has_logo")) if "has_logo" in c else True,
    })
    return base


def _tito_chain_json(chain, truncada: bool):
    """La cadena para su `OptionChainTable` y su `ChartPanel`.

    Las filas van con los mismos nombres de columna que su tabla y en el mismo
    orden en que él las sirve (open interest descendente). `chain_meta` lleva
    los tres números de su cabecera —contratos, vencimientos, truncado— y
    `top_contracts` los 5 de mayor nocional, que son las líneas que su
    `ChartPanel` dibuja sobre las velas.
    """
    from wbj.tito.compute import count_expirations, sort_by_open_interest_desc

    orden = sort_by_open_interest_desc(list(chain))
    recortada = orden[:TITO_CHAIN_MAX]

    def fila(x):
        return {"option_ticker": x.option_ticker, "contract_type": x.contract_type,
                "expiration": x.expiration, "strike": _r(x.strike),
                "open_interest": x.open_interest, "volume": x.volume,
                "price": _r(x.price), "open_premium": _r(x.open_premium),
                "notional_value": _r(x.notional_value), "price_source": x.price_source}

    top = sorted(orden, key=lambda x: x.notional_value or 0, reverse=True)[:5]
    return {
        "chain": [fila(x) for x in recortada],
        "chain_meta": {
            "contract_count": len(orden),
            "expiration_count": count_expirations(orden),
            # Dos verdades distintas: `truncated` es el tope de páginas de
            # Massive (la cadena que llegó ya venía incompleta) y `capped` es
            # este recorte. Fundirlas escondería cuál de los dos pasó.
            "truncated": bool(truncada),
            "capped": len(orden) > len(recortada),
            "shown": len(recortada),
        },
        "top_contracts": [fila(x) for x in top],
    }


def _moneyness(K, spot, opt):
    """ATM / ITM / OTM para un strike dado. call: ITM=strike<spot · put: ITM=strike>spot."""
    if not K or not spot:
        return None
    d = (K - spot) if opt == "call" else (spot - K)   # d>0 = OTM, d<0 = ITM
    if abs(d) / spot < 0.01:
        return "ATM"
    return "OTM" if d > 0 else "ITM"


def _institutional_strike(rows, bias, near_dte=None, dte_tol=45):
    """Del flujo $1M+ direccional (ventana), encuentra el strike donde se concentró MÁS premium
    direccional para el sesgo dominante: alcista→CALLs comprados (lado ASK/above) · bajista→PUTs
    comprados. Si near_dte se da, des-pondera concentración de vencimientos lejanos. Devuelve
    {strike, premium, trades, cp, exp_top, total_strikes} o None."""
    if not rows or bias not in ("alcista", "bajista"):
        return None
    want_cp = "CALL" if bias == "alcista" else "PUT"
    buys = ("ASK", "ABOVE_ASK")
    agg = {}
    for r in rows:
        if (r.get("cp") or "").upper() != want_cp:
            continue
        if (r.get("side") or "").upper() not in buys:
            continue
        K = _safe_num(r.get("strike")); prem = _safe_num(r.get("premium"))
        if K <= 0 or prem <= 0:
            continue
        w = prem
        if near_dte is not None and r.get("dte") is not None:
            if abs(_safe_num(r.get("dte")) - near_dte) > dte_tol:
                w *= 0.35
        a = agg.setdefault(K, {"prem": 0.0, "n": 0, "exps": {}})
        a["prem"] += w; a["n"] += 1
        ex = r.get("exp")
        if ex:
            a["exps"][ex] = a["exps"].get(ex, 0.0) + prem
    if not agg:
        return None
    K_best = max(agg, key=lambda k: agg[k]["prem"])
    a = agg[K_best]
    exp_top = max(a["exps"], key=a["exps"].get) if a["exps"] else None
    return {"strike": round(K_best, 2), "premium": round(a["prem"], 0), "trades": a["n"],
            "cp": want_cp, "exp_top": exp_top, "total_strikes": len(agg)}


def _kevin_long_strike(anchor, spot, opt, atm):
    """Regla de Kevin: comprar ATM o ITM, NUNCA OTM. Ancla al strike institucional pero si está OTM
    lo jala a ATM; si la institución está ITM, respeta ese ITM (Kevin acepta igual o más ITM)."""
    if anchor is None or anchor <= 0:
        return atm
    if opt == "call":
        return min(anchor, atm)   # OTM (>spot) → ATM ; ITM (<spot) → se queda ITM
    return max(anchor, atm)        # put: OTM (<spot) → ATM ; ITM (>spot) → se queda ITM


def _build_debit_spread(spot, long_K, short_K, level, dte, iv, RF, opt, stop_frac, capital, budget, ref_entry_c, alloc=None):
    """Débito vertical compra long_K / vende short_K. Devuelve dict listo para el frontend o None."""
    width = (short_K - long_K) if opt == "call" else (long_K - short_K)
    if width <= 0:
        return None
    long_entry = _bs_price(spot, long_K, dte / 365.0, iv, RF, opt)
    short_entry = _bs_price(spot, short_K, dte / 365.0, iv, RF, opt)
    net_debit = long_entry - short_entry
    if net_debit <= 0:
        return None
    net_debit_c = net_debit * 100.0
    max_val_c = width * 100.0
    long_val = _bs_price(level, long_K, max(dte / 2.0, 0.5) / 365.0, iv, RF, opt)
    short_val = _bs_price(level, short_K, max(dte / 2.0, 0.5) / 365.0, iv, RF, opt)
    val_fast_c = min(max((long_val - short_val) * 100.0, 0.0), max_val_c)
    reward_fast_c = val_fast_c - net_debit_c
    be = (long_K + net_debit) if opt == "call" else (long_K - net_debit)
    risk_c = net_debit_c * stop_frac
    cap_for_n = min(float(capital), float(alloc)) if alloc is not None else float(capital)   # Kelly: limita prima desplegada
    n_cap = int(cap_for_n // net_debit_c) if net_debit_c > 0 else 0
    n_risk = int(budget // risk_c) if risk_c > 0 else 0
    n = max(0, min(n_cap, n_risk))
    lm, sm = _moneyness(long_K, spot, opt), _moneyness(short_K, spot, opt)
    return {"long_strike": long_K, "short_strike": short_K, "width": round(width, 2),
            "net_debit": round(net_debit, 2), "net_debit_contract": round(net_debit_c, 0),
            "max_profit_contract": round(max_val_c - net_debit_c, 0), "max_value_contract": round(max_val_c, 0),
            "breakeven": round(be, 2), "exit_value_fast": round(val_fast_c, 0),
            "reward_fast_contract": round(reward_fast_c, 0),
            "rr_fast": round(reward_fast_c / risk_c, 2) if risk_c > 0 else None,
            "stop_pct": round(stop_frac * 100, 0), "planned_risk_contract": round(risk_c, 0),
            "contracts": n, "n_cap": n_cap, "n_risk": n_risk,
            "fits_capital": bool(n_cap >= 1),
            "risk_pct_at_1": round(risk_c / float(capital) * 100, 1) if capital else None,
            "total_cost": round(net_debit_c * max(n, 1 if n_cap >= 1 else 0), 0),
            "cost_saving_pct": round((1 - net_debit_c / ref_entry_c) * 100, 0) if ref_entry_c > 0 else None,
            "long_moneyness": lm, "short_moneyness": sm, "combo": f"{lm}/{sm}"}


_QMAP_CACHE = {}

def _q_lookup(qmap, exp, cp, K):
    """Quote real para (exp, cp, K): match exacto o el strike listado MÁS CERCANO dentro de ~2.5%."""
    if not qmap or not exp:
        return None
    cp = str(cp).upper(); Kr = round(_safe_num(K), 2)
    hit = qmap.get((exp, cp, Kr))
    if hit:
        return hit
    cand = [(k, v) for (e, c, k), v in qmap.items() if e == exp and c == cp]
    if not cand:
        return None
    bk, bv = min(cand, key=lambda kv: abs(kv[0] - Kr))
    return bv if abs(bk - Kr) <= max(0.75, 0.025 * Kr) else None


_AICORR_CACHE = {}
def _ai_concentration(ticker, proxy="SMH", ttl=3600):
    """Correlación de retornos del ticker vs el complejo IA/semis (SMH) en ~6m. Advierte, al analizar o
    dimensionar, que el nombre añade poca diversificación a una cartera ya cargada de IA: ρ alto = es 'la misma
    apuesta'. Auto-contenido (no necesita el book): mide cuánto ES este nombre la apuesta de IA. Punto ciego real
    de un libro NVDA/AMD/PLTR/GOOGL donde la diversificación efectiva tiende a 1."""
    key = str(ticker).upper(); now = time.time()
    hit = _AICORR_CACHE.get(key)
    if hit and now - hit[0] < ttl:
        return hit[1]
    out = None
    try:
        if key != proxy:
            a = dict(_cached_price_series(ticker, period="6mo") or [])
            b = dict(_cached_price_series(proxy, period="6mo") or [])
            common = sorted(set(a) & set(b))
            if len(common) > 30:
                pa = [a[t] for t in common]; pb = [b[t] for t in common]
                ra = [(pa[i] / pa[i-1] - 1) for i in range(1, len(pa)) if pa[i-1] > 0]
                rb = [(pb[i] / pb[i-1] - 1) for i in range(1, len(pb)) if pb[i-1] > 0]
                n = min(len(ra), len(rb))
                if n > 20:
                    import numpy as np
                    rho = float(np.corrcoef(ra[-n:], rb[-n:])[0, 1])
                    if rho == rho:    # no NaN
                        lvl = "alta" if rho >= 0.7 else ("media" if rho >= 0.45 else "baja")
                        note = (f"ρ={round(rho,2)} con el complejo IA/semis (SMH): " + (
                            "este nombre ES la apuesta de IA — si ya tienes NVDA/AMD/PLTR/GOOGL u otros del clúster, tu "
                            "diversificación efectiva ≈ 1 posición; dimensiona como si SUMARAS a la misma posición, no a una nueva."
                            if rho >= 0.7 else
                            "correlación moderada con IA/semis — diversifica algo, pero vigila el solapamiento del libro."
                            if rho >= 0.45 else
                            "baja correlación con IA/semis — aporta diversificación real al libro."))
                        out = {"proxy": proxy, "rho": round(rho, 2), "level": lvl, "note": note}
    except Exception:
        out = None
    _AICORR_CACHE[key] = (now, out)
    return out
















# ════════════════════════ HISTORICAL COLLECTOR + BACKTEST ════════════════════════


def _store_signal_snapshot(row):
    """INSERT OR REPLACE one snapshot row (shared by the live collector and the historical backfill)."""
    conn = _db()
    conn.execute("""INSERT OR REPLACE INTO signal_snapshots
        (ticker,snap_date,spot,confl_verdict,confl_direction,confl_score,conv_bias,conv_strength,
         net_premium,dark_bias,call_wall,put_wall,gamma_flip,targets_json,created_at)
        VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
        (row["ticker"], row["snap_date"], row["spot"], row.get("confl_verdict"), row.get("confl_direction"),
         row.get("confl_score"), row.get("conv_bias"), row.get("conv_strength"), row.get("net_premium"),
         row.get("dark_bias"), row.get("call_wall"), row.get("put_wall"), row.get("gamma_flip"),
         row.get("targets_json") or "[]", row.get("created_at")))
    conn.commit(); conn.close()


def _backtest_eval(snaps, closes, highs, lows, dates, fwd_window=10):
    """Pure backtest evaluator (no I/O) so it can be unit-tested.
    - Confluence DIRECTION accuracy over `fwd_window` trading days forward.
    - Target HIT-RATE per horizon (price touches the level within horizon_days calendar days)."""
    def _fwd(d0, n):
        return [d for d in dates if d > d0][:n]
    dir_total = dir_correct = 0
    by_verdict = {}
    for s in snaps:
        direction = s.get("confl_direction")
        if direction not in ("alcista", "bajista"):
            continue
        fwd = _fwd(s["snap_date"], fwd_window)
        if len(fwd) < fwd_window:
            continue
        p0 = closes.get(s["snap_date"]) or _safe_num(s.get("spot"))
        p1 = closes.get(fwd[-1])
        if not p0 or not p1:
            continue
        ret = (p1 - p0) / p0
        ok = (ret > 0 and direction == "alcista") or (ret < 0 and direction == "bajista")
        dir_total += 1; dir_correct += 1 if ok else 0
        bv = by_verdict.setdefault(s.get("confl_verdict") or "?", {"total": 0, "correct": 0})
        bv["total"] += 1; bv["correct"] += 1 if ok else 0
    hz_stats = {}
    last_date = dates[-1] if dates else None
    for s in snaps:
        try:
            tgs = json.loads(s.get("targets_json") or "[]")
        except Exception:
            tgs = []
        for tg in tgs:
            hz = int(_safe_num(tg.get("hz")) or 0); lvl = _safe_num(tg.get("level"))
            if hz <= 0 or lvl <= 0:
                continue
            try:
                cutoff = (datetime.strptime(s["snap_date"], "%Y-%m-%d") + timedelta(days=hz)).strftime("%Y-%m-%d")
            except Exception:
                continue
            if not last_date or cutoff > last_date:        # horizon not elapsed yet → skip
                continue
            window = [d for d in dates if s["snap_date"] < d <= cutoff]
            if not window:
                continue
            p0 = closes.get(s["snap_date"]) or _safe_num(s.get("spot"))
            if not p0:
                continue
            above = lvl >= p0
            hit = any((above and highs.get(d, 0) >= lvl) or ((not above) and lows.get(d, 1e18) <= lvl)
                      for d in window)
            st = hz_stats.setdefault(hz, {"total": 0, "hit": 0})
            st["total"] += 1; st["hit"] += 1 if hit else 0
    dir_acc = round(dir_correct / dir_total * 100, 1) if dir_total else None
    verdict_acc = {v: {"total": x["total"], "accuracy_pct": round(x["correct"] / x["total"] * 100, 1)}
                   for v, x in by_verdict.items() if x["total"]}
    hz_out = {str(h): {"total": st["total"], "hit_rate_pct": round(st["hit"] / st["total"] * 100, 1)}
              for h, st in sorted(hz_stats.items()) if st["total"]}
    return {"confluence_direction": {"evaluated": dir_total, "fwd_days": fwd_window,
                                     "accuracy_pct": dir_acc, "by_verdict": verdict_acc},
            "target_hit_rate": hz_out}








@app.get("/api/signal-history")
def signal_history_endpoint(ticker: str):
    """List stored snapshots for a ticker (status panel)."""
    ticker = ticker.upper().strip()
    try:
        conn = _db()
        rows = conn.execute(
            "SELECT snap_date,spot,confl_verdict,confl_direction,confl_score,conv_bias FROM signal_snapshots "
            "WHERE ticker=? ORDER BY snap_date DESC LIMIT 60", (ticker,)).fetchall()
        conn.close()
        return _json_safe({"ok": True, "ticker": ticker, "count": len(rows), "rows": [dict(r) for r in rows]})
    except Exception as e:
        return {"ok": False, "error": _error_publico(e, "/api/signal-history")}


# ──────────────── HISTORICAL BACKFILL (reconstruct past confluence via Quant Data sessionDate) ────────────────

_BACKFILL_STATE = {}







_BACKTEST_CACHE = {}

def _backtest_cached(ticker, ttl=1800):
    ent = _BACKTEST_CACHE.get(ticker.upper())
    if ent and time.time() - ent[0] < ttl:
        return ent[1]
    bt = _backtest_signals(ticker)
    _BACKTEST_CACHE[ticker.upper()] = (time.time(), bt)
    return bt

def _calibration_prompt_block(ticker):
    """Feed the agent its OWN historical track record for this ticker so it calibrates confidence
    to empirical accuracy, not theory. This is the feedback loop that makes it improve over time."""
    try:
        bt = _backtest_cached(ticker)
    except Exception:
        return ""
    if not bt or not bt.get("ok") or not bt.get("n_snapshots"):
        return ""
    cd = bt.get("confluence_direction") or {}
    n_dir = cd.get("evaluated") or 0
    if not n_dir:
        return ""
    byv = cd.get("by_verdict") or {}
    parts = []
    for v, x in byv.items():
        tag = "" if x["total"] >= _BT_MIN_DIR_N else " ⚠n-baja"
        parts.append(f"{v} {x['accuracy_pct']}% (n={x['total']}{tag})")
    hr = bt.get("target_hit_rate") or {}
    hrp = [f"{h}d {x['hit_rate_pct']}% (n={x['total']})" for h, x in hr.items()]
    dir_lo = None
    if cd.get("accuracy_pct") is not None:
        dir_lo = round(_wilson_lower(round(cd["accuracy_pct"] / 100.0 * n_dir), n_dir) * 100, 0)
    rng = bt.get("date_range") or ["", ""]
    reliable = n_dir >= _BT_MIN_DIR_N
    return (
        f"\nCALIBRACIÓN HISTÓRICA (backtest propio de {ticker.upper()}, {bt['n_snapshots']} snapshots "
        f"{rng[0]}→{rng[1]}): acierto direccional de la confluencia a {cd.get('fwd_days')} días = "
        f"{cd.get('accuracy_pct')}% sobre {n_dir} señales"
        + (f" (piso de confianza {dir_lo:.0f}% al 68%)" if dir_lo is not None else "")
        + f". Por veredicto: {', '.join(parts) if parts else 'sin desglose'}."
        + (f" Hit-rate de targets: {', '.join(hrp)}." if hrp else "")
        + (" USO: CALIBRA tu confianza a estos números reales de ESTE ticker, pero trata el PISO (no el % "
           "crudo) como tu probabilidad real, y NO subas convicción con veredictos marcados ⚠n-baja (ruido)."
           if reliable else
           f" ⚠ MUESTRA INSUFICIENTE (n={n_dir} < {_BT_MIN_DIR_N}): el track record todavía NO es "
           "estadísticamente fiable. NO ajustes tu convicción por estos números aún; trátalos como referencia "
           "débil y apóyate en fundamentales/flujo. Captura más snapshots para que la calibración sea confiable."))








@app.get("/api/self-test")
def self_test_endpoint(ticker: str):
    """Auto-diagnóstico EN VIVO: corre las rutas de datos clave para un ticker y marca en verde/ámbar/rojo
    lo que se vea raro (sin spot, IV NaN, target con movimiento absurdo, walls invertidos, max pain fuera
    de rango, libro vacío). Pensado para validar contra datos reales antes de operar."""
    ticker = ticker.upper().strip()
    checks = []

    def add(name, status, detail, value=None):
        checks.append({"name": name, "status": status, "detail": detail, "value": _json_safe(value)})

    # Las comprobaciones miraban GEX, walls de gamma, premium neto y targets
    # de horizonte: todo eso venía de Quant Data y de la cadena de opciones,
    # que salieron del proyecto. Un auto-diagnóstico que prueba fuentes
    # inexistentes no diagnostica nada. Ahora prueba las CUATRO que quedan.
    spot = None
    try:
        fi = vertex_market.Ticker(ticker).fast_info
        spot = _safe_num(fi.get("last_price"))
        if spot and spot > 0:
            add("Precio (FMP)", "ok", f"spot ${spot:.2f}", spot)
        else:
            add("Precio (FMP)", "fail", "FMP no devolvió cotización")
    except Exception as e:
        add("Precio (FMP)", "fail", _error_publico(e, "self-test precio"))

    try:
        h = vertex_market.Ticker(ticker).history(period="1y")
        if h is not None and not h.empty:
            add("Historia diaria (FMP)", "ok",
                f"{len(h)} sesiones · última {h.index[-1].date()}", len(h))
        else:
            add("Historia diaria (FMP)", "fail", "sin velas diarias")
    except Exception as e:
        add("Historia diaria (FMP)", "fail", _error_publico(e, "self-test historia"))

    try:
        info = vertex_market.Ticker(ticker).info
        faltan = [k for k in ("longName", "sector", "marketCap") if not info.get(k)]
        add("Ficha de empresa (FMP)", "ok" if not faltan else "warn",
            info.get("longName") or ticker if not faltan
            else f"sin {', '.join(faltan)}")
    except Exception as e:
        add("Ficha de empresa (FMP)", "fail", _error_publico(e, "self-test ficha"))

    try:
        n = len(vertex_market.Ticker(ticker).news or [])
        add("Noticias (FMP)", "ok" if n else "warn", f"{n} titulares", n)
    except Exception as e:
        add("Noticias (FMP)", "fail", _error_publico(e, "self-test noticias"))

    try:
        it = vertex_market.Ticker(ticker).insider_transactions
        n = 0 if it is None else len(it)
        add("Insiders (FMP/EDGAR)", "ok" if n else "warn",
            f"{n} operaciones" if n else "sin Form 4 recientes", n)
    except Exception as e:
        add("Insiders (FMP/EDGAR)", "fail", _error_publico(e, "self-test insiders"))

    try:
        cal = vertex_market.Ticker(ticker).calendar or {}
        fechas = cal.get("Earnings Date") or []
        add("Próximos resultados (FMP)", "ok" if fechas else "warn",
            str(fechas[0]) if fechas else "sin fecha publicada")
    except Exception as e:
        add("Próximos resultados (FMP)", "fail", _error_publico(e, "self-test earnings"))

    summ = {"ok": sum(1 for c in checks if c["status"] == "ok"),
            "warn": sum(1 for c in checks if c["status"] == "warn"),
            "fail": sum(1 for c in checks if c["status"] == "fail")}
    return _json_safe({"ok": True, "ticker": ticker, "spot": spot, "summary": summ, "checks": checks})


def _integrity_checks(g, fair_value=None, targets=None):
    """Cedazo de integridad sobre los números YA computados (no re-pega a las APIs): valida que el gamma
    flip esté cerca del dinero, que las walls encierren el spot, que el max pain y el fair value caigan en
    banda razonable, que los P/C tengan sentido y que la fuente no sea un respaldo silencioso. Devuelve una
    lista verde/ámbar/rojo + resumen para pintar una tira de confianza en el reporte y en Proyecciones.
    Convierte 'número silenciosamente malo' (como el flip $3.11) en 'bandera visible'."""
    out = []
    def add(name, status, detail, value=None):
        out.append({"name": name, "status": status, "detail": detail, "value": _json_safe(value)})
    if not isinstance(g, dict):
        return {"checks": [], "ok": 0, "warn": 0, "fail": 1, "status": "fail"}
    spot = _safe_num(g.get("spot"))
    src = (g.get("source") or "")
    # Fuente de datos
    if "yfinance" in src.lower() or "respaldo" in src.lower():
        add("Fuente de datos", "warn", f"GEX por respaldo ({src}) — Quant Data no respondió esta vez")
    else:
        add("Fuente de datos", "ok", src or "Quant Data")
    # Spot
    if spot and spot > 0:
        add("Spot", "ok", f"${spot:,.2f}", spot)
    else:
        add("Spot", "fail", "sin precio de referencia")
        return {"checks": out, "ok": 0, "warn": 0, "fail": 1, "status": "fail"}
    # Gamma flip cerca del dinero
    gf = _safe_num(g.get("gamma_flip"))
    if g.get("gamma_flip") is None:
        add("Gamma flip", "warn", "sin cruce en rango (estructura sin gamma flip)")
    else:
        r = gf / spot
        if 0.85 <= r <= 1.15:
            add("Gamma flip", "ok", f"${gf:,.2f} ({(r-1)*100:+.1f}% del spot)", gf)
        elif 0.75 <= r <= 1.25:
            add("Gamma flip", "warn", f"${gf:,.2f} algo lejos del spot ({(r-1)*100:+.1f}%)", gf)
        else:
            add("Gamma flip", "fail", f"${gf:,.2f} IMPOSIBLE para spot ${spot:,.2f}", gf)
    # Walls encierran el spot
    cw, pw = _safe_num(g.get("call_wall")), _safe_num(g.get("put_wall"))
    if cw and pw:
        if cw >= spot >= pw:
            add("Walls (call/put)", "ok", f"call ${cw:,.0f} encima · put ${pw:,.0f} debajo")
        elif cw < pw:
            add("Walls (call/put)", "fail", f"INVERTIDAS: call ${cw:,.0f} < put ${pw:,.0f}")
        else:
            add("Walls (call/put)", "warn", f"no encierran el spot (call ${cw:,.0f} · put ${pw:,.0f})")
    else:
        add("Walls (call/put)", "warn", "incompletas esta sesión")
    # Max pain en banda
    mp = _safe_num(g.get("max_pain"))
    if mp:
        rm = mp / spot
        add("Max pain", "ok" if 0.7 <= rm <= 1.3 else "warn",
            f"${mp:,.0f} ({(rm-1)*100:+.1f}% del spot)" + ("" if 0.7 <= rm <= 1.3 else " — fuera de banda"), mp)
    # Net GEX (signo/régimen)
    ng = g.get("net_gex")
    if ng is not None:
        add("Net GEX", "ok", f"{_safe_num(ng):,.0f} · {'GEX+ (anclado)' if _safe_num(ng) >= 0 else 'GEX− (amplificado)'}")
    # P/C en rango plausible
    for lbl, key in (("P/C volumen", "pcr_vol"), ("P/C prima", "pcr_premium")):
        v = _safe_num(g.get(key))
        if g.get(key) is not None:
            add(lbl, "ok" if 0.1 <= v <= 10 else "warn",
                f"{v:.2f}" + ("" if 0.1 <= v <= 10 else " — valor atípico"), v)
    # Fair value en banda del precio
    fv = _safe_num(fair_value)
    if fair_value is not None and fv > 0:
        rf = fv / spot
        add("Fair value 12m", "ok" if 0.5 <= rf <= 2.0 else "warn",
            f"${fv:,.2f} ({(rf-1)*100:+.1f}% vs spot)" + ("" if 0.5 <= rf <= 2.0 else " — fuera de banda razonable"), fv)
    # Targets en banda
    if targets:
        try:
            bad = [t for t in targets if (_safe_num(t) > 0 and not (0.5 * spot <= _safe_num(t) <= 2.0 * spot))]
            add("Targets", "ok" if not bad else "warn",
                f"{len(targets)} niveles, todos en banda" if not bad else f"{len(bad)} target(s) fuera de banda 0.5×–2×")
        except Exception:
            pass
    n_fail = sum(1 for c in out if c["status"] == "fail")
    n_warn = sum(1 for c in out if c["status"] == "warn")
    n_ok = sum(1 for c in out if c["status"] == "ok")
    status = "fail" if n_fail else ("warn" if n_warn else "ok")
    return {"checks": out, "ok": n_ok, "warn": n_warn, "fail": n_fail, "status": status}




# ── FINNHUB DATA PROVIDER ─────────────────────────────────────────────────────
# Best "basic" tier: 60 calls/min free — delayed (~15-min) US quotes, fundamentals,
# company news, insider sentiment, congressional trading. Paste your free key below
# (or set the FINNHUB_API_KEY env var). Everything degrades gracefully if unset.
FINNHUB_API_KEY = os.environ.get("FINNHUB_API_KEY", "")  # <-- pega tu API key gratis de finnhub.io


def finnhub_get(path, params=None):
    if not FINNHUB_API_KEY:
        return None
    try:
        p = dict(params or {}); p["token"] = FINNHUB_API_KEY
        r = requests.get(f"https://finnhub.io/api/v1{path}", params=p, timeout=12)
        if r.status_code != 200:
            return None
        return r.json()
    except Exception:
        return None


def finnhub_quote(symbol):
    """Delayed (~15-min) quote. Returns normalized dict or None."""
    d = finnhub_get("/quote", {"symbol": symbol})
    if not d or not d.get("c"):
        return None
    c = float(d.get("c")); pc = float(d.get("pc") or 0)
    chg = (c - pc) if pc else 0.0
    return {
        "price": round(c, 2),
        "change": round(chg, 2),
        "change_pct": round(chg / pc * 100, 2) if pc else None,
        "high": d.get("h"), "low": d.get("l"), "open": d.get("o"), "prev_close": round(pc, 2),
        "ts": d.get("t"),
    }


def _live_spot(ticker):
    """#5 — Spot con respaldo: yfinance fast_info → Finnhub. Evita que un fallo de yfinance
    tumbe watchlist/analyze. Devuelve (precio|None, fuente)."""
    tk = str(ticker).upper().strip()
    try:
        fi = vertex_market.Ticker(tk).fast_info
        p = getattr(fi, "last_price", None) or getattr(fi, "regular_market_price", None)
        if p and float(p) > 0:
            return round(float(p), 2), "yfinance"
    except Exception:
        pass
    try:
        q = finnhub_quote(tk)
        if q and q.get("price"):
            return round(float(q["price"]), 2), "finnhub"
    except Exception:
        pass
    return None, None


_SPOT_RESOLVE_CACHE = {}
def _resolve_spot(ticker, ttl=20):
    """Fuente ÚNICA de precio para todo el sistema: yfinance/Finnhub → serie cacheada,
    con etiqueta de FUENTE y HORA.
    Devuelve {price, source, as_of}. Cache corto para que todos los paneles vean el MISMO spot y no haya
    discrepancias sutiles (GEX a un precio, gráfica a otro). Degradación limpia si una fuente cae."""
    tk = str(ticker).upper().strip()
    now = time.time()
    hit = _SPOT_RESOLVE_CACHE.get(tk)
    if hit and now - hit[0] < ttl:
        return hit[1]
    price, source = _live_spot(tk)                 # yfinance fast_info → Finnhub
    if not price:
        try:
            s = _cached_price_series(tk, period="5d") or []
            if s:
                price, source = round(float(s[-1][1]), 2), "serie diaria (respaldo)"
        except Exception:
            pass
    out = {"price": price, "source": source,
           "as_of": (datetime.now().strftime('%m/%d/%Y, %I:%M:%S %p') if price else None)}
    _SPOT_RESOLVE_CACHE[tk] = (now, out)
    return out


def finnhub_metrics(symbol):
    d = finnhub_get("/stock/metric", {"symbol": symbol, "metric": "all"})
    if not d or "metric" not in d:
        return None
    m = d["metric"] or {}
    keys = {
        "peTTM": "P/E", "psTTM": "P/S", "pbAnnual": "P/B",
        "52WeekHigh": "52WHigh", "52WeekLow": "52WLow", "beta": "Beta",
        "grossMarginTTM": "GrossMargin%", "netProfitMarginTTM": "NetMargin%",
        "roeTTM": "ROE%", "revenueGrowthTTMYoy": "RevGrowthYoY%",
    }
    out = {}
    for k, lbl in keys.items():
        v = m.get(k)
        if isinstance(v, (int, float)):
            out[lbl] = round(float(v), 2)
    return out or None


def finnhub_company_news(symbol, days=7, limit=5):
    to = datetime.now().strftime("%Y-%m-%d")
    frm = (datetime.now() - timedelta(days=days)).strftime("%Y-%m-%d")
    d = finnhub_get("/company-news", {"symbol": symbol, "from": frm, "to": to})
    if not isinstance(d, list):
        return []
    out = []
    for it in d[:limit]:
        hl = it.get("headline", "")
        if hl:
            out.append({"headline": hl, "source": it.get("source", ""),
                        "summary": (it.get("summary", "") or "")[:200]})
    return out


def finnhub_insider_sentiment(symbol):
    frm = (datetime.now() - timedelta(days=120)).strftime("%Y-%m-%d")
    to = datetime.now().strftime("%Y-%m-%d")
    d = finnhub_get("/stock/insider-sentiment", {"symbol": symbol, "from": frm, "to": to})
    if not d or not d.get("data"):
        return None
    rows = d["data"]
    if not rows:
        return None
    mspr = sum(float(r.get("mspr", 0) or 0) for r in rows) / len(rows)
    net = sum(int(r.get("change", 0) or 0) for r in rows)
    return {"avg_mspr": round(mspr, 1), "net_shares": net, "months": len(rows)}


def finnhub_congressional(symbol):
    frm = (datetime.now() - timedelta(days=180)).strftime("%Y-%m-%d")
    to = datetime.now().strftime("%Y-%m-%d")
    d = finnhub_get("/stock/congressional-trading", {"symbol": symbol, "from": frm, "to": to})
    if not d or not d.get("data"):
        return None
    rows = d["data"]
    buys = sum(1 for r in rows if "purchase" in (r.get("transactionType", "") or "").lower())
    sells = sum(1 for r in rows if "sale" in (r.get("transactionType", "") or "").lower())
    return {"transactions": len(rows), "recent_buys": buys, "recent_sells": sells}


def format_finnhub_context(symbol):
    """Compact text block (fundamentals + news + insider + congress) for the AI prompt."""
    if not FINNHUB_API_KEY:
        return ""
    parts = []
    q = finnhub_quote(symbol)
    if q:
        sign = "+" if (q["change"] or 0) >= 0 else ""
        parts.append(f"Quote 15-min ${q['price']} ({sign}{q['change']}, {q['change_pct']}%)")
    mt = finnhub_metrics(symbol)
    if mt:
        parts.append("Fundamentales: " + ", ".join(f"{k} {v}" for k, v in list(mt.items())[:8]))
    ins = finnhub_insider_sentiment(symbol)
    if ins and ins.get("avg_mspr") is not None:
        bias = "compra" if ins["avg_mspr"] > 0 else "venta"
        parts.append(f"Insider sentiment MSPR {ins['avg_mspr']} (neto {ins['net_shares']:+} acc, sesgo {bias})")
    cong = finnhub_congressional(symbol)
    if cong:
        parts.append(f"Congreso 6m: {cong['transactions']} trades ({cong['recent_buys']} compras / {cong['recent_sells']} ventas)")
    news = finnhub_company_news(symbol, days=7, limit=4)
    if news:
        parts.append("News 7d: " + " | ".join(n["headline"][:80] for n in news))
    return " || ".join(parts) if parts else ""


@app.get("/api/finnhub-quote")
def get_finnhub_quote(symbol: str):
    """Delayed (~15-min) quote from Finnhub. Returns {ok, ...} or graceful error."""
    symbol = symbol.upper().strip()
    if not FINNHUB_API_KEY:
        return {"ok": False, "error": "FINNHUB_API_KEY no configurada."}
    q = finnhub_quote(symbol)
    if not q:
        return {"ok": False, "error": f"Sin quote para {symbol}."}
    return {"ok": True, "symbol": symbol, **q,
            "generated_at": datetime.now().strftime('%m/%d/%Y, %I:%M:%S %p')}


def _reconcile_targets(inst, ht, spot):
    """#5 — reconcilia los DOS motores de targets: σ/DCF (agente) vs gamma/flujo (Proyecciones).
    Por horizonte compara la base del agente con el nivel de gamma/flujo, marca divergencia y consenso."""
    if not ht or not ht.get("targets"):
        return None
    gmap = {int(x.get("horizon_days")): x for x in ht["targets"] if x.get("level") and x.get("horizon_days")}
    pairs = [("7d", 7), ("30d", 30), ("3m", 90), ("6m", 120), ("12m", 365)]
    out = []
    for ik, hd in pairs:
        a = inst.get(ik) or {}
        base = _safe_num(a.get("base"))
        g = gmap.get(hd)
        if base <= 0 or not g:
            continue
        glvl = _safe_num(g.get("level"))
        if glvl <= 0:
            continue
        div = (glvl - base) / base * 100.0
        out.append({"horizon": ik, "agent_base": round(base, 2), "gamma_level": round(glvl, 2),
                    "gamma_dir": g.get("direction"), "gamma_conf": g.get("confidence"),
                    "divergence_pct": round(div, 1), "agree": bool(abs(div) <= 3.0),
                    "consensus": round((base + glvl) / 2, 2)})
    return out or None


def _key_signals_summary(conv, confl, regime, net_premium):
    """#6 — punchline de las señales de MAYOR peso al INICIO del prompt, para que no se diluyan en el
    contexto largo. El LLM ve primero lo que más importa."""
    lines = []
    if conv and conv.get("strength_pct") is not None:
        lines.append(f"• Convicción institucional COMPUTADA (ΔOI real): sesgo {conv.get('bias')} · "
                     f"dominancia {conv.get('strength_pct'):.0f}% · {conv.get('qualifying')} confirmaciones "
                     f"(${conv.get('bull_premium',0):,.0f} calls vs ${conv.get('bear_premium',0):,.0f} puts). "
                     "ESTA es tu señal de mayor peso (25%) — ánclala, no la adivines.")
    else:
        lines.append("• Convicción institucional: sin flujo calificado hoy → puntúa el flujo con cautela.")
    if confl and confl.get("verdict"):
        lines.append(f"• Confluencia (convicción + GEX + dark pool): {confl.get('verdict')}"
                     + (f" ({confl.get('agree_count')}/3 de acuerdo)" if confl.get('agree_count') is not None else "") + ".")
    if net_premium and net_premium.get("bias"):
        lines.append(f"• Premium neto de opciones: {net_premium.get('bias')} (${net_premium.get('net_premium',0):,.0f}).")
    if regime and regime.get("label"):
        lines.append(f"• Régimen de mercado: {regime['label']}"
                     + (f" · VIX {regime.get('vix')}" if regime.get('vix') is not None else "") + ".")
    return "SEÑALES CLAVE (PRIORIZA ESTAS — son el núcleo de la decisión):\n" + "\n".join(lines) if lines else ""


def _agent_coherence_checks(aj, spot):
    """#8 — gate de coherencia sobre la SALIDA del LLM (igual que self-test, pero para la lógica del
    reporte): marca contradicciones internas para que no operes sobre un análisis inconsistente."""
    flags = []
    rec = (aj.get("recommendation") or "").upper()
    # Convicción = el puntaje de los agentes de Victor (0-100). El motor ponderado de Vertex
    # (conviction_weighted) fue eliminado, así que la coherencia se chequea contra su score.
    cw = _safe_num(aj.get("conviccion_score"))
    up = aj.get("upside_pct")
    up = _safe_num(up) if up is not None else None
    buyish = any(w in rec for w in ("FAVORABLE", "BUY", "COMPR", "ACUMUL"))
    sellish = any(w in rec for w in ("SELL", "VEND", "REDUC"))
    if buyish and cw and cw < 45:
        flags.append({"check": "Recomendación vs convicción", "status": "warn",
                      "detail": f"{rec} con puntaje de los agentes bajo ({cw:.0f}/100)"})
    if sellish and cw and cw > 60:
        flags.append({"check": "Recomendación vs convicción", "status": "warn",
                      "detail": f"{rec} con convicción alta ({cw:.0f}/100)"})
    if buyish and up is not None and up < 0:
        flags.append({"check": "Recomendación vs fair value", "status": "warn",
                      "detail": f"{rec} pero el fair value implica downside ({up:+.1f}%)"})
    if sellish and up is not None and up > 5:
        flags.append({"check": "Recomendación vs fair value", "status": "warn",
                      "detail": f"{rec} pero el fair value implica upside ({up:+.1f}%)"})
    fa = aj.get("flow_anchor") or {}
    if buyish and fa.get("bias") == "bajista":
        flags.append({"check": "Recomendación vs flujo", "status": "warn",
                      "detail": "Compra con flujo institucional COMPUTADO bajista"})
    if sellish and fa.get("bias") == "alcista":
        flags.append({"check": "Recomendación vs flujo", "status": "warn",
                      "detail": "Venta con flujo institucional COMPUTADO alcista"})
    # Coherencia contra los AGENTES DE VICTOR (antes contra las 7 señales del LLM, eliminadas):
    # una compra sin ningún agente por encima de 4.5/10 es una contradicción interna.
    cats = ((aj.get("wbj") or {}).get("categories_10")) or {}
    scores = [_safe_num(v) for v in cats.values() if v is not None]
    if buyish and scores and max(scores) < 4.5:
        flags.append({"check": "Agentes vs recomendación", "status": "warn",
                      "detail": "Compra pero ningún agente de Victor supera 4.5/10"})
    return flags


# ═════════════════════════════════════════════════════════════════════════════
# FRAMEWORK WBJ — "Warren Buffett Jr" / Ruta 2030 Wall Street Agent System v2.0.0
# 6 especialistas independientes, 100 puntos, evidencia obligatoria.
# Regla innegociable: sin evidencia no hay número; sin número no hay score;
# sin fórmula no hay conclusión. Sin datos → NOT_SCORABLE (nunca 5/10 de relleno).
# Score y confianza son cosas separadas. Fuente: /Cerebro (base de conocimiento).
# ═════════════════════════════════════════════════════════════════════════════

# Cada dimensión ya está expresada en PUNTOS DE CATEGORÍA (suman el máximo de la
# categoría), así: category_points = Σ dim_max * score/10 sobre dims con evidencia.
WBJ_CATEGORIES = {
    "business":  {"max": 20, "label": "Business", "dims": [
        ("moat_pricing_power",            "Moat y pricing power", 5),
        ("competitive_position",          "Posición competitiva", 4),
        ("management_capital_allocation", "Management y asignación de capital", 4),
        ("business_durability",           "Durabilidad del negocio", 4),
        ("customer_economics",            "Economía del cliente", 3)]},
    "financial": {"max": 15, "label": "Financial", "dims": [
        ("revenue_quality_growth",           "Calidad y crecimiento de ingresos", 3),
        ("eps_fcf",                          "EPS y free cash flow", 3),
        ("margins",                          "Márgenes", 3),
        ("balance_liquidity",                "Balance y liquidez", 3),
        ("cash_conversion_capital_efficiency","Conversión de caja y eficiencia de capital", 3)]},
    "market":    {"max": 20, "label": "Market & Growth", "dims": [
        ("tam_tailwind",       "TAM y viento de cola de la industria", 5),
        ("revisions",          "Revisiones de earnings/ingresos", 4),
        ("catalysts",          "Catalizadores de producto/negocio", 4),
        ("growth_runway",      "Pista de crecimiento y captura de share", 4),
        ("operating_leverage", "Apalancamiento operativo y confirmación de mercado", 3)]},
    "technical": {"max": 20, "label": "Technical & Momentum", "dims": [
        ("primary_trend",       "Tendencia primaria de precio", 4),
        ("relative_strength",   "Fuerza relativa", 4),
        ("volume_demand",       "Volumen y demanda institucional", 3),
        ("earnings_gap",        "Comportamiento en gaps de earnings", 3),
        ("breakout_base",       "Calidad de base y breakout", 3),
        ("breadth_volatility",  "Amplitud sectorial y calidad de volatilidad", 3)]},
    "risk":      {"max": 15, "label": "Risk & Resilience", "dims": [
        ("financing_balance_sheet",   "Riesgo de financiamiento y balance", 3),
        ("concentration",             "Riesgo de competencia y concentración", 3),
        ("execution_earnings_quality","Riesgo de ejecución y calidad de earnings", 3),
        ("regulatory_legal_macro",    "Riesgo regulatorio, legal y macro", 2),
        ("valuation_compression",     "Riesgo de compresión de múltiplo", 2),
        ("volatility_drawdown",       "Riesgo de volatilidad y drawdown", 2)]},
    "valuation": {"max": 10, "label": "Valuation", "dims": [
        ("growth_adjusted_multiples","Múltiplos ajustados por crecimiento", 3),
        ("historical_peer",          "Comparación histórica y con pares", 2),
        ("cashflow_earnings_yield",  "Yield de caja y earnings", 2),
        ("fair_value_scenarios",     "Valor justo por escenarios", 2),
        ("margin_of_safety",         "Margen de seguridad", 1)]},
}
WBJ_ORDER = ["business", "financial", "market", "technical", "risk", "valuation"]


class WBJDim(BaseModel):
    score: float = Field(..., description="Score económico 0-10 (10 = máximamente favorable). Usa -1 SOLO si NOT_SCORABLE: no hay evidencia/número para puntuar (jamás rellenes con 5).")
    confidence: int = Field(..., description="Confianza 0-100 en la EVIDENCIA de esta dimensión (cobertura, calidad de fuente, frescura). Separada del score: score alto con evidencia vieja/escasa lleva confianza baja.")
    nota: str = Field(..., description="Una línea citando el dato/número concreto que sustenta el score (p.ej. 'ROIC 18% vs WACC 9% en 5/5 años'). Sin número → declara NOT_SCORABLE.")

class WBJBusiness(BaseModel):
    moat_pricing_power: WBJDim; competitive_position: WBJDim
    management_capital_allocation: WBJDim; business_durability: WBJDim; customer_economics: WBJDim
class WBJFinancial(BaseModel):
    revenue_quality_growth: WBJDim; eps_fcf: WBJDim; margins: WBJDim
    balance_liquidity: WBJDim; cash_conversion_capital_efficiency: WBJDim
class WBJMarket(BaseModel):
    tam_tailwind: WBJDim; revisions: WBJDim; catalysts: WBJDim
    growth_runway: WBJDim; operating_leverage: WBJDim
class WBJTechnical(BaseModel):
    primary_trend: WBJDim; relative_strength: WBJDim; volume_demand: WBJDim
    earnings_gap: WBJDim; breakout_base: WBJDim; breadth_volatility: WBJDim
class WBJRisk(BaseModel):
    financing_balance_sheet: WBJDim; concentration: WBJDim; execution_earnings_quality: WBJDim
    regulatory_legal_macro: WBJDim; valuation_compression: WBJDim; volatility_drawdown: WBJDim
class WBJValuation(BaseModel):
    growth_adjusted_multiples: WBJDim; historical_peer: WBJDim; cashflow_earnings_yield: WBJDim
    fair_value_scenarios: WBJDim; margin_of_safety: WBJDim

class WBJScorecard(BaseModel):
    business: WBJBusiness; financial: WBJFinancial; market: WBJMarket
    technical: WBJTechnical; risk: WBJRisk; valuation: WBJValuation

class WBJScenario(BaseModel):
    scenario: str = Field(..., description="'Bear', 'Base' o 'Bull'.")
    value: float = Field(..., description="Valor intrínseco por acción del escenario (número, no un rango).")
    assumptions: str = Field(..., description="Supuestos declarados: crecimiento y margen asumidos, método (DCF/múltiplo). Sin supuestos, el número no significa nada.")

class WBJLevel(BaseModel):
    tipo: str = Field(..., description="Clase de nivel (p.ej. 'Soporte confirmado', 'Resistencia', 'SMA200', 'Bull intrínseco', 'Reverse-DCF implícito').")
    lente: str = Field(..., description="'Técnico' o 'Valuación'. Nunca se promedian entre sí.")
    valor: float = Field(..., description="Precio del nivel/zona.")
    nota: str = Field(..., description="Confirmación/invalidación o supuesto. Lenguaje de referencia, nunca 'target garantizado'.")

# M-09: CLAUDE.md ("Límites del sistema") prohíbe convertir el análisis en una
# instrucción de compra/venta. El campo emitía literalmente BUY / AVOID — una
# orden, no una clasificación. Ahora emite CLASES DE RESEARCH.
#
# `_RECO_LEGACY` traduce los valores viejos al leer `vertex.db`: el track record
# compara la dirección de reportes anteriores, así que renombrar sin puente
# habría invalidado todo el histórico acumulado.
RESEARCH_FAVORABLE   = "FAVORABLE"
RESEARCH_CONDICIONAL = "CONDICIONAL"
RESEARCH_ESPECULATIVO = "ESPECULATIVO"
RESEARCH_DESFAVORABLE = "DESFAVORABLE"

_RECO_LEGACY = {"BUY": RESEARCH_FAVORABLE, "HOLD": RESEARCH_CONDICIONAL,
                "SPECULATIVE": RESEARCH_ESPECULATIVO, "AVOID": RESEARCH_DESFAVORABLE}


def _reco_norm(v):
    """Clase de research de un valor guardado, sea nuevo o del esquema anterior."""
    u = (v or "").upper().strip()
    return _RECO_LEGACY.get(u, u)


# Perfiles de los gates de Victor → (clase de research, texto en español).
# Cualquier perfil fuera de este mapa cae a DESFAVORABLE: ante la duda, el
# research no favorece entrar.
_WBJ_PROFILE_TO_RECO = {
    "Momentum Candidate":  (RESEARCH_FAVORABLE, "Favorable a invertir"),
    "Quality Opportunity": (RESEARCH_FAVORABLE, "Favorable a invertir"),
    "Value Opportunity":   (RESEARCH_FAVORABLE, "Favorable a invertir"),
    "Conditional / Watch": (RESEARCH_CONDICIONAL, "Condicional — esperar confirmación"),
    "Speculative":         (RESEARCH_ESPECULATIVO, "Especulativa — solo tamaño de riesgo"),
}


def _wbj_reco_from_profile(profile):
    """Perfil de Victor → (recomendación, clasificación). Avoid/Wait y Weak/Wait → AVOID."""
    return _WBJ_PROFILE_TO_RECO.get(profile, (RESEARCH_DESFAVORABLE, "Evitar / esperar"))


def _wbj_band(raw: float) -> str:
    if raw >= 90:   return "Elite raw score"
    if raw >= 80:   return "Strong raw score"
    if raw >= 70:   return "Conditional raw score"
    if raw >= 60:   return "Mixed / wait"
    if raw >= 50:   return "Weak"
    return "Avoid on raw score"


def _wbj_gates(comp: dict) -> dict:
    """Aplica overrides obligatorios y gates de perfil (Cerebro/00_main_agent/SCORING_AND_GATES).
    Devuelve el perfil final + gates pasados/fallidos + overrides activados."""
    c = comp["categories"]
    def P(k):  return c.get(k, {}).get("points", 0.0)
    def CV(k): return c.get(k, {}).get("coverage", 0.0)
    def CF(k): return c.get(k, {}).get("confidence", 0.0)
    raw = comp["raw_total"]; tconf = comp["total_confidence"]
    biz, fin, mkt, tec, rsk, val = P("business"), P("financial"), P("market"), P("technical"), P("risk"), P("valuation")
    tech_conf = CF("technical")

    def F(k):  return c.get(k, {}).get("mandatory_flags", []) or []
    biz_flags, fin_flags = F("business"), F("financial")
    # Override 2 de Victor (aggregate/overrides.py): ROIC<WACC (business VALUE_DESTRUCTION
    # y/o financial OVERRIDE_2_ROIC_BELOW_WACC) → NO_ELITE_QUALITY (no Quality Opportunity).
    value_destruction = ("VALUE_DESTRUCTION" in biz_flags) or ("OVERRIDE_2_ROIC_BELOW_WACC" in fin_flags)

    # A-05: overrides 1, 3 y 7 faltaban aquí. Estaban implementados en el engine
    # (`aggregate/overrides.py`) pero en el camino web solo existían como PROSA
    # en el prompt del LLM — es decir, se le *pedía* al modelo que los mencionara,
    # sin ningún chequeo determinista. Eso viola la regla innegociable de
    # CLAUDE.md: "sin fórmula, no hay conclusión". Se leen de los mismos
    # mandatory_flags que emiten los especialistas, una sola fuente por condición.
    rsk_flags, val_flags = F("risk"), F("valuation")
    capital_dependence = "OVERRIDE_1_LOSS_NEGATIVE_FCF_EXTERNAL_DEPENDENCE" in fin_flags
    solvency_warning = ("SOLVENCY_WARNING" in rsk_flags) or ("SOLVENCY_WARNING" in fin_flags)
    data_conflict = [f for f in (val_flags + fin_flags) if f.startswith("OVERRIDE_7_")]

    overrides = []
    if capital_dependence:
        overrides.append("Override 1 (dependencia de capital): pérdida neta + FCF negativo + "
                         "dependencia de capital externo → el perfil final se limita a "
                         "Avoid/Speculative.")
    if solvency_warning:
        overrides.append("⚠ Override 3 (SOLVENCIA): cobertura de intereses por debajo de 1.5x. "
                         "Esta advertencia debe aparecer de forma prominente en el reporte.")
    if data_conflict:
        overrides.append("Override 7 (conflicto de datos sin resolver): " + ", ".join(data_conflict) +
                         " → no se publica valor por acción hasta reconciliar la fuente.")
    if rsk <= 4:
        overrides.append("Risk override: Risk ≤4/15 limita el perfil a Speculative.")
    if val <= 4 and tec <= 8:
        overrides.append("Premium breakdown override: Valuation ≤4/10 y Technical ≤8/20 → Wait/Avoid.")
    if value_destruction:
        overrides.append("Override 2 (ROIC<WACC / VALUE_DESTRUCTION): no puede clasificar como "
                         "Quality Opportunity ni Elite (destrucción de valor).")
    if "CONCENTRATION_RED_FLAG" in biz_flags:
        overrides.append("Concentration red flag: un cliente concentra demasiado ingreso "
                         "(Business ya capó la dimensión Durability).")
    if "DILUTION_RED_FLAG" in biz_flags:
        overrides.append("Dilution red flag: dilución material de acciones en circulación.")
    core_incomplete = [k for k in WBJ_ORDER if CV(k) < 0.70]
    if core_incomplete:
        overrides.append("Coverage override: categoría(s) con cobertura <70% no pueden pasar un gate de perfil: "
                         + ", ".join(WBJ_CATEGORIES[k]["label"] for k in core_incomplete) + ".")

    # Gates de perfil (todas las condiciones deben cumplirse) — se registran numéricamente
    gates = {}
    gates["Momentum Candidate"] = [
        ("Raw ≥ 78", raw >= 78), ("Technical ≥ 17/20", tec >= 17),
        ("Market ≥ 16/20", mkt >= 16), ("Business+Financial ≥ 28/35", (biz + fin) >= 28),
        ("Risk ≥ 8/15", rsk >= 8), ("Confianza técnica ≥ 70", tech_conf >= 70)]
    gates["Quality Opportunity"] = [
        ("Raw ≥ 80", raw >= 80), ("Business ≥ 16/20", biz >= 16), ("Financial ≥ 11/15", fin >= 11),
        ("Risk ≥ 10/15", rsk >= 10), ("Valuation ≥ 5/10", val >= 5), ("Technical ≥ 12/20", tec >= 12)]
    gates["Value Opportunity"] = [
        ("Raw ≥ 75", raw >= 75), ("Valuation ≥ 8/10", val >= 8), ("Business ≥ 13/20", biz >= 13),
        ("Risk ≥ 10/15", rsk >= 10), ("Technical ≥ 9/20", tec >= 9)]

    gate_eligible = not core_incomplete            # coverage override bloquea gates mayores
    passed, failed = [], []
    profile = None
    for name in ("Momentum Candidate", "Quality Opportunity", "Value Opportunity"):
        conds = gates[name]
        ok = all(v for _, v in conds)
        # Override 2: la destrucción de valor bloquea específicamente Quality Opportunity.
        _blocked = (name == "Quality Opportunity" and value_destruction)
        (passed if (ok and not _blocked) else failed).append({"gate": name, "conditions": (
            [{"cond": lbl, "pass": bool(v)} for lbl, v in conds]
            + ([{"cond": "Sin destrucción de valor (Override 2)", "pass": False}] if _blocked else []))})
        if ok and not _blocked and gate_eligible and profile is None:
            profile = name

    # Speculative / Avoid / Conditional
    spec_reasons = []
    if rsk <= 4: spec_reasons.append("Risk ≤4/15")
    if tconf < 60: spec_reasons.append("confianza total <60")
    if core_incomplete: spec_reasons.append("categoría crítica incompleta")
    # A-05: el override 1 CAPA el perfil, no solo se muestra. `apply_gates` del
    # engine lo enruta a Speculative (a Avoid solo se llega por raw<50, que ya
    # se evalúa abajo) — así se reproduce el tope "Avoid/Speculative" del doc.
    if capital_dependence: spec_reasons.append("dependencia de capital externo (override 1)")

    if raw < 50 or (val <= 4 and tec <= 8):
        profile = "Avoid / Wait"
    elif profile is None and spec_reasons:
        profile = "Speculative"
    elif profile is None and raw >= 60:
        profile = "Conditional / Watch"
    elif profile is None:
        profile = "Avoid / Wait"

    # Cap a Speculative aunque un gate haya pasado, igual que apply_gates de Victor: además de
    # Risk ≤4/15, la confianza total <60 FUERZA Speculative (apply_gates devuelve Speculative
    # antes de resolver los gates cuando conf_total<60). Antes el backup solo capaba por Risk.
    # A-05: la dependencia de capital entra en el mismo cap.
    if profile in ("Momentum Candidate", "Quality Opportunity", "Value Opportunity") and (
            rsk <= 4 or tconf < 60 or capital_dependence):
        profile = "Speculative"

    # Clasificación de research + recomendación de compatibilidad (persistencia/histórico)
    if profile in ("Momentum Candidate", "Quality Opportunity", "Value Opportunity"):
        classification, rec = "Favorable a invertir", RESEARCH_FAVORABLE
    elif profile == "Conditional / Watch":
        classification, rec = "Condicional — esperar confirmación", RESEARCH_CONDICIONAL
    elif profile == "Speculative":
        classification, rec = "Especulativa — solo tamaño de riesgo", RESEARCH_ESPECULATIVO
    else:
        classification, rec = "Evitar / esperar", RESEARCH_DESFAVORABLE

    _band = _wbj_band(raw)
    if value_destruction and _band == "Elite raw score":
        _band = "Strong raw score (Elite bloqueado: ROIC<WACC, override 2)"
    return {"profile": profile, "band": _band, "classification": classification,
            "recommendation": rec, "passed_gates": passed, "failed_gates": failed,
            "overrides": overrides, "spec_reasons": spec_reasons,
            "gate_eligible": gate_eligible,
            # A-05: banderas explícitas para que el reporte pueda destacarlas.
            # El override 3 exige aparecer "de forma prominente", así que el
            # frontend necesita poder distinguirlo, no solo leer una lista.
            "solvency_warning": bool(solvency_warning),
            "capital_dependence": bool(capital_dependence),
            "data_conflict": data_conflict,
            # M-14: la banda "Elite" no sobrevive a la destrucción de valor.
            "band_note": ("Elite bloqueado por override 2 (ROIC<WACC)"
                          if (value_destruction and raw >= 90) else None)}


# Metodología WBJ resumida que se inyecta en el prompt (fuente: /Cerebro).
_WBJ_METHODOLOGY = """
FRAMEWORK DE ANÁLISIS WBJ (Ruta 2030 Wall Street Agent System v2.0.0 — base de conocimiento en /Cerebro).
Actúas como 6 especialistas INDEPENDIENTES; ninguno ve el score del otro. Rellena 'scorecard' puntuando
cada dimensión de 0 a 10 (10 = máximamente favorable) con su 'confidence' (0-100, calidad de la EVIDENCIA)
y una 'nota' que CITE EL NÚMERO concreto.

REGLA INNEGOCIABLE: sin evidencia no hay número; sin número no hay score. Si NO tienes datos suficientes
para una dimensión, pon score = -1 (NOT_SCORABLE) y explica por qué en la nota. NUNCA rellenes con 5/10.
Score y confianza son SEPARADOS: un score alto con evidencia vieja/escasa debe llevar confianza baja.

Máximos por categoría y dimensiones (cada dimensión ya está en puntos de categoría):
1) BUSINESS (20): Moat y pricing power (5) · Posición competitiva (4) · Management y asignación de capital (4)
   · Durabilidad del negocio (4) · Economía del cliente (3). No declares moat por marca; exige efectos medibles
   (ROIC-WACC persistente, estabilidad de márgenes, retención/pricing). No uses el precio de la acción aquí.
2) FINANCIAL (15): Calidad y crecimiento de ingresos (3) · EPS y FCF (3) · Márgenes (3) · Balance y liquidez (3)
   · Conversión de caja y eficiencia de capital (3). Usa números reportados; guidance es solo contexto.
   Interest coverage <1.5x = alerta de solvencia. ROIC<WACC impide veredicto 'excelente'.
3) MARKET & GROWTH (20): TAM y viento de cola (5) · Revisiones de earnings/ingresos (4) · Catalizadores (4)
   · Pista de crecimiento y captura de share (4) · Apalancamiento operativo y confirmación (3). No confundas TAM
   con ingresos; catalizador solo narrativo se limita a 3.
4) TECHNICAL & MOMENTUM (20): Tendencia primaria (4) · Fuerza relativa (4) · Volumen y demanda institucional (3)
   · Comportamiento en gaps de earnings (3) · Base y breakout (3) · Amplitud y volatilidad (3). Un chart fuerte
   NO compensa un negocio o solvencia débiles.
5) RISK & RESILIENCE (15): Financiamiento/balance (3) · Competencia/concentración (3) · Ejecución/calidad de
   earnings (3) · Regulatorio/legal/macro (2) · Compresión de múltiplo (2) · Volatilidad/drawdown (2).
   MÁS PUNTOS = MENOR RIESGO. No infieras bajo riesgo por un precio alto. No ocultes coverage <1.5x.
6) VALUATION (10): Múltiplos ajustados por crecimiento (3) · Histórico y pares (2) · Yield de caja/earnings (2)
   · Valor justo por escenarios (2) · Margen de seguridad (1). Nunca un único punto: da Bear/Base/Bull con
   supuestos. Terminal growth < tasa de descuento. Un múltiplo bajo NO es barato sin controlar calidad/riesgo.

Además: escribe las 7 frases del resumen ejecutivo, escenarios de valuación (Bear/Base/Bull con supuestos),
reverse DCF (qué exige el precio hoy), exactamente 3 thesis killers, disparadores de monitoreo, niveles
importantes (marca lente Técnico/Valuación; NUNCA promedies un nivel técnico con un valor intrínseco), y las
probabilidades calibradas. El fair_value debe ser el escenario Base. Lenguaje de referencia: 'zona',
'confirmación', 'invalidación', 'escenario' — nunca 'target garantizado' ni órdenes de compra/venta.
"""




# ─────────────────────────────────────────────────────────────────────────────
# CAPAS ADITIVAS WBJ — NO alteran ningún número. Calculan datos deterministas
# (con la matemática de Victor) y el LLM SOLO los explica en un 2º pase.
# ─────────────────────────────────────────────────────────────────────────────

def _load_investor_profile():
    """Lee el perfil del inversionista de la sesión.

    Devuelve `(nombre, texto)` o `(None, "")` si no hay ninguno. Solo contexto
    para la explicación; nunca cambia el scoring.

    **Lo único que cambió al haber cuentas.** Antes leía siempre `Kevin.md`, que
    con un solo usuario era correcto: era el perfil de todo el mundo. Con varias
    cuentas eso significaba contarle a cada persona su análisis con el capital y
    la tolerancia de Kevin.

    Ahora resuelve el `.md` del usuario de la sesión —lo deja el middleware en
    `_USUARIO_CTX`— y cae a `Kevin.md` cuando no hay sesión (scripts, cron,
    preflight). La firma, el valor de retorno y sus dos llamadores siguen
    exactamente igual: Analyze y Explore no saben que esto cambió, solo reciben
    el perfil correcto.
    """
    # `_PERFIL_DIR`, no una ruta calculada aparte. Estuvo calculada aquí y era
    # exactamente el fallo que se documentaba en el test: dos funciones
    # resolviendo el mismo directorio por su cuenta acaban en directorios
    # distintos, el editor escribe en uno y el agente lee del otro, y nadie se
    # entera porque el archivo viejo sigue existiendo.
    base = _PERFIL_DIR
    u = _usuario_actual()
    if u is not None:
        propio = _CU.ruta_md_de(base, u)
        # El `.md` es CACHÉ: la fuente de verdad es la fila del usuario. Si el
        # archivo no está —disco nuevo, respaldo viejo, alguien lo borró— se
        # regenera desde la base con el MISMO escritor que lo creó, en vez de
        # seguir de largo. Seguir de largo caía a `Kevin.md` sin decir nada, y
        # el reporte hablaba del capital de otra persona: el fallo silencioso
        # que este archivo entero existe para evitar.
        if not os.path.exists(propio):
            try:
                conn = _db()
                try:
                    _CU.guardar_perfil(conn, base, u, _CU.leer_perfil(conn, u["id"]))
                finally:
                    conn.close()
            except Exception:                     # noqa: BLE001
                pass                              # un análisis no se cae por esto
        if os.path.exists(propio):
            try:
                with open(propio, "r", encoding="utf-8") as f:
                    return (u.get("nombre") or "inversionista"), f.read().strip()
            except Exception:
                pass
    for fn in ("Kevin.md", "Mi Perfil.md", "MiPerfil.md", "Perfil.md"):
        p = os.path.join(base, fn)
        if os.path.exists(p):
            try:
                with open(p, "r", encoding="utf-8") as f:
                    return fn.replace(".md", ""), f.read().strip()
            except Exception:
                pass
    return None, ""


_US_EXCHANGES = {"NMS", "NGM", "NCM", "NIM", "NYQ", "NYS", "ASE", "PCX", "BATS",
                 "AMEX", "NASDAQ", "NYSE", "NASDAQGS", "NASDAQGM", "NASDAQCM"}


def _fmt_usd_corto(v):
    """Dólares con separador de miles, sin decimales.

    El perfil habla de restricciones —capital, tope por posición— y ahí
    «$1.0K» y «$1,049» son la diferencia entre que algo te quepa o no. Se
    escribe entero.
    """
    try:
        return f"${float(v or 0):,.0f}"
    except (TypeError, ValueError):
        return "$0"


#: Los mercados que el cuestionario ofrece, y cómo se comprueba cada uno contra
#: los datos del proveedor. Sin esto, «¿en qué mercados inviertes?» sería una
#: pregunta decorativa: la respuesta no podría contrastarse con nada.
_MERCADOS_CHEQUEO = {
    "EE.UU.": lambda ex, pais: ex in _US_EXCHANGES or pais == "United States",
    "Europa": lambda ex, pais: pais in {"Germany", "France", "Spain", "Italy",
                                        "Netherlands", "Switzerland", "Sweden",
                                        "United Kingdom", "Ireland", "Belgium",
                                        "Denmark", "Norway", "Finland", "Portugal",
                                        "Austria", "Poland"},
    "Latinoamérica": lambda ex, pais: pais in {"Brazil", "Mexico", "Chile", "Colombia",
                                               "Argentina", "Peru", "Uruguay", "Panama"},
    "Asia": lambda ex, pais: pais in {"China", "Japan", "India", "South Korea", "Taiwan",
                                      "Hong Kong", "Singapore", "Indonesia", "Thailand",
                                      "Malaysia", "Vietnam", "Philippines", "Israel"},
}


def _wbj_profile_fit(info, recommendation):
    """Filtro por perfil del ORQUESTADOR (CLAUDE.md paso 6): cruza la recomendación
    de research con el perfil de QUIEN pidió el análisis.

    **NO cambia el scoring** — es una CLASIFICACIÓN de fit, y esa regla no se
    toca. Lo que cambió es de dónde salen los hechos con los que se clasifica.

    Antes estaban ESCRITOS A MANO: «Kevin invierte solo en EE.UU.», «~$1.000
    USD», «agresivo / especulativo», «1-3 años». Con un solo usuario eso era
    correcto — era el perfil de todo el mundo. Con cuentas, le contaba a cada
    persona el perfil de Kevin, incluida la comprobación de universo: a alguien
    que hubiera marcado Europa se le decía que una acción alemana estaba «fuera
    de su universo». Ahora todo sale de `_perfil_leer()`.

    Devuelve `None` si no hay perfil legible.
    """
    perfil = _perfil_leer()
    if not perfil:
        return None
    nombre, _texto = _load_investor_profile()

    # ── Universo: chequeo determinista contra los mercados que TÚ marcaste ──
    _exch = (info.get("exchange") or "").upper()
    _country = info.get("country") or ""
    mercados = list(perfil.get("mercados") or [])
    # Sin mercados marcados no se puede afirmar que algo esté fuera: se deja
    # pasar y se dice. Inventar un universo sería peor que no tenerlo.
    universe_ok = (not mercados) or any(
        _MERCADOS_CHEQUEO[m](_exch, _country) for m in mercados
        if m in _MERCADOS_CHEQUEO)

    tol = _CU.TOLERANCIAS.get(perfil.get("tolerancia"), _CU.TOLERANCIAS["agresivo"])
    _lista = lambda k, v="sin especificar": ", ".join(perfil.get(k) or []) or v  # noqa: E731
    rec = _reco_norm(recommendation)
    if not universe_ok:
        fit = "fuera-de-universo"
        reason = (f"Tu perfil invierte en {_lista('mercados')}; este valor no cotiza ahí"
                  + (f" ({_country})" if _country else "") + ".")
    elif rec == RESEARCH_FAVORABLE:
        fit, reason = "apto", "Clasificación favorable, dentro de tu universo y tolerancia."
    elif rec == RESEARCH_ESPECULATIVO:
        # El aviso de riesgo de ruina se calibra al capital REAL. Con $1.000 y
        # opciones es urgente; con $250.000 es una nota al pie, y repetirlo con
        # las mismas palabras lo convertiría en ruido que se deja de leer.
        _apretado = perfil.get("capital", 0) and perfil["capital"] < 10_000
        fit = "apto-especulativo"
        reason = (f"Especulativa, pero dentro de tu tolerancia {tol['label'].lower()}"
                  + (" — cuida el sizing: con "
                     f"{_fmt_usd_corto(perfil['capital'])} y opciones, el riesgo de "
                     "ruina es real." if _apretado else " — dimensiona con tu tope por posición."))
    elif rec == RESEARCH_CONDICIONAL:
        fit, reason = "condicional", "Condicional — esperar confirmación antes de dimensionar."
    else:
        fit, reason = "evitar", "El research no favorece invertir ahora."

    pos = perfil.get("max_posicion_pct") or [20, 30]
    return {
        "profile_name": nombre or perfil.get("nombre") or "inversionista",
        # Si el perfil es el de referencia, se DICE. El lector tiene derecho a
        # saber que estos hechos no los declaró nadie.
        "es_por_defecto": perfil.get("modo") == "default",
        "universe_ok": bool(universe_ok),
        "universo": _lista("mercados"),
        "tolerancia": tol["label"].lower(),
        "horizonte": perfil.get("horizonte") or "sin especificar",
        "instrumentos": _lista("instrumentos")
                        + (f"; sin {_lista('excluir', '')}" if perfil.get("excluir") else ""),
        "capital": _fmt_usd_corto(perfil.get("capital") or 0),
        "riesgo_por_operacion": _fmt_usd_corto(perfil.get("riesgo_por_trade") or 0),
        "max_posicion_pct": f"{pos[0]}% – {pos[1]}%",
        "sizing_note": (
            f"Tope por posición {pos[0]}%–{pos[1]}% de "
            f"{_fmt_usd_corto(perfil.get('capital') or 0)} "
            f"({_fmt_usd_corto((perfil.get('capital') or 0) * pos[0] / 100)}–"
            f"{_fmt_usd_corto((perfil.get('capital') or 0) * pos[1] / 100)} por posición). "
            + ("Capital pequeño + opciones → cuida el riesgo de ruina; prioriza "
               "probabilidad de éxito y puntos de entrada/salida."
               if (perfil.get("capital") or 0) < 10_000 else
               "Prioriza probabilidad de éxito y puntos de entrada/salida (timing).")),
        "fit": fit, "fit_reason": reason,
        "disclaimer": "Clasificación de research; nunca una orden automática. La ejecución es manual y tuya."}


def _wbj_fmp_important_insiders(ticker):
    """Insiders con transacciones >$1M desde FMP (Form 4: securitiesTransacted × price).
    Se usa SOLO si hay FMP_API_KEY; si no, el reporte cae a los datos de yfinance.
    Devuelve lista de transacciones importantes o None si no hay FMP/datos."""
    if not os.environ.get("FMP_API_KEY"):
        return None
    try:
        import sys
        if _WBJ_ENGINE_PATH not in sys.path:
            sys.path.insert(0, _WBJ_ENGINE_PATH)
        from wbj.config import load_settings
        from wbj.providers.cache import Cache
        from wbj.providers.fmp import FMPProvider
        _s = _engine_settings()
        _fmp = FMPProvider(_s, Cache(_s.cache_dir))
        rows = _fmp.insider_trades(ticker) or []
        if not isinstance(rows, list):
            return None
        # A-04: CLAUDE.md item 5 dice "las que EXCEDAN $1M USD **en total**".
        # Antes se umbralizaba cada Form 4 por separado, así que un insider que
        # vendía 6 veces $300k ($1.8M) desaparecía del reporte — justo el patrón
        # de venta escalonada que más importa detectar. Ahora se AGRUPA por
        # persona y dirección, y se umbraliza el total (igual que hace el engine
        # en `wbj.report._insiders`, que tiene test propio).
        _grupos = {}
        for r in rows:
            if not isinstance(r, dict):
                continue
            try:
                _sh = float(r.get("securitiesTransacted") or 0)
                _px = float(r.get("price") or 0)
            except (TypeError, ValueError):
                continue
            if _sh <= 0 or _px <= 0:          # un Form 3 o una concesión sin precio
                continue                       # no llevan valor que umbralizar
            _tt = str(r.get("transactionType") or "")
            _dir = "buy" if _tt.upper().startswith("P") else \
                   "sell" if _tt.upper().startswith("S") else ""
            if not _dir:
                continue
            _nombre = str(r.get("reportingName") or "").strip()
            _k = (_nombre.upper(), _dir)
            g = _grupos.setdefault(_k, {"insider": _nombre, "direccion": _dir, "shares": 0.0,
                                        "value": 0.0, "n": 0, "date": "", "transaction": _tt})
            g["shares"] += _sh
            g["value"] += _sh * _px
            g["n"] += 1
            _f = str(r.get("transactionDate") or r.get("filingDate") or "")[:10]
            if _f > g["date"]:                # la más reciente del grupo
                g["date"] = _f
        important = []
        for g in _grupos.values():
            if g["value"] <= 1_000_000:       # el umbral se aplica al TOTAL
                continue
            important.append({
                "insider": g["insider"], "transaction": g["transaction"],
                "shares": int(g["shares"]), "n_operaciones": g["n"],
                "price": round(g["value"] / g["shares"], 2) if g["shares"] else 0.0,
                "value": round(g["value"], 2), "date": g["date"],
                "is_buy": g["direccion"] == "buy", "is_sell": g["direccion"] == "sell",
                "source": "FMP Form 4 (agregado por insider)"})
        important.sort(key=lambda x: x["value"], reverse=True)
        # Flujo NETO de insiders con la función de Victor (brief.py `_insiders_flow`): compras vs
        # ventas en dólares sobre TODO el feed, no solo lo que excede $1M. Él la describe como
        # "a coarser, fuller-picture lens than the >$1M highlights" — las dos vistas conviven.
        _flow = None
        try:
            from wbj.brief import _insiders_flow as _victor_flow
            _flow = _victor_flow(rows)
        except Exception:
            _flow = None
        return {"important": important, "flow": _flow}
    except Exception as _e:
        print(f"[analyze] insiders FMP omitidos (fallback yfinance): {str(_e)[:120]}")
        return None


def _wbj_mandatory_report(insiders, recommendation, next_earnings_date=None, fmp_important=None):
    """Contenido OBLIGATORIO del reporte final (CLAUDE.md): insiders con transacciones que
    EXCEDEN $1M USD (Forms 4), inversionistas institucionales (13F) y —si la clasificación es
    'evitar'— cuándo revisitar. Determinista; NO cambia el scoring."""
    out = {}
    insiders = insiders if isinstance(insiders, dict) else {}
    # `fmp_important` llega como {"important": [...], "flow": {...}} (o una lista, por
    # compatibilidad con reportes viejos guardados antes de que se añadiera el flujo).
    _flow = None
    if isinstance(fmp_important, dict):
        _flow = fmp_important.get("flow")
        fmp_important = fmp_important.get("important")
    # Fuente PRIMARIA: FMP (Form 4 con valores confiables) si hay key; si no, yfinance.
    if fmp_important is not None:
        important = fmp_important
        _src = "FMP (Form 4)"
        _net = round(sum((t.get("value") or 0) * (1 if t.get("is_buy") else -1 if t.get("is_sell") else 0)
                         for t in important), 2) if important else 0.0
        _signal = "BULLISH" if _net > 0 else ("BEARISH" if _net < 0 else "NEUTRAL")
    else:
        txns = insiders.get("transactions") or []
        important = []
        for t in txns:
            if isinstance(t, dict) and t.get("value") is not None:
                try:
                    if abs(float(t["value"])) > 1_000_000:
                        important.append(t)
                except (TypeError, ValueError):
                    pass
        _sum = insiders.get("summary") or {}
        _net = _sum.get("net_value")
        _signal = _sum.get("signal")
        _src = "yfinance"
    out["insiders_over_1m"] = {
        "threshold_usd": 1_000_000, "count": len(important),
        "transactions": important, "net_value": _net,
        "net_exceeds_1m": bool(_net is not None and abs(float(_net)) > 1_000_000),
        "signal": _signal, "source": _src,
        "note": "Solo se listan insiders con transacciones que exceden $1M USD (Forms 4, SEC)."}
    if _flow:
        # Vista COMPLEMENTARIA de Victor: compras vs ventas en dólares sobre TODO el feed
        # (los regalos/adjudicaciones a precio 0 no suman). Más gruesa pero más completa.
        out["insiders_flow"] = _flow
    # Inversionistas reconocidos (13F) — CLAUDE.md, punto 4.
    #
    # Había DOS caminos y este leía el vacío. `insiders["institutional"]` se
    # llenaba del `institutional_holders` estilo yfinance, que hoy devuelve
    # None: FMP responde 402 en las SEIS rutas de `institutional-ownership`
    # con este plan. Mientras tanto `insiders["edgar"]["holders_5pct"]` sí
    # traía los diez de verdad —BlackRock, Vanguard, State Street con
    # acciones y dólares— desde el conjunto trimestral 13F de la SEC.
    #
    # El reporte decía "0 tenedores" con los datos ya en memoria, en la
    # misma estructura, una clave más abajo.
    _inst = (insiders.get("institutional") or [])[:10]
    if not _inst:
        _edg = (insiders.get("edgar") or {}).get("holders_5pct") or []
        # Se normaliza el nombre del campo: el camino de EDGAR usa `name`
        # y la interfaz espera `holder`.
        _inst = [{"holder": h.get("name") or h.get("holder"),
                  "shares": h.get("shares"), "value": h.get("value"),
                  "pct_held": h.get("pct_held"),
                  "date": h.get("period") or h.get("date"),
                  "source": h.get("source_locator")}
                 for h in _edg[:10] if isinstance(h, dict)]
    out["institutional_13f"] = _inst
    if _reco_norm(recommendation) == RESEARCH_DESFAVORABLE:
        out["revisit"] = {   # CLAUDE.md: si 'evitar', fecha/evento concreto para revisitar
            "trigger": "Próximo reporte de resultados (10-Q/10-K) o cambio material en la tesis.",
            "date": next_earnings_date}
    return out


# Roles clave del management (para el historial obligatorio del reporte, CLAUDE.md #4).
_WBJ_KEY_ROLES = ("chief executive", "ceo", "founder", "co-founder", "cofounder",
                  "chairman", "chief financial", "cfo", "president", "chief operating", "coo")


def _wbj_management_roster(info):
    """Roster FACTUAL del management desde yfinance `companyOfficers` (FMP no expone
    ejecutivos → se usa yfinance, que siempre trae este campo). Determinista, sin LLM:
    nombre, cargo, edad, año de nacimiento, compensación total. Marca los roles CLAVE
    (CEO/CFO/fundador/chairman/presidente/COO). NO cambia el scoring."""
    officers = info.get("companyOfficers") or []
    roster = []
    for o in officers:
        if not isinstance(o, dict):
            continue
        name = (o.get("name") or "").strip()
        title = (o.get("title") or "").strip()
        if not name:
            continue
        tl = title.lower()
        roster.append({
            "name": name, "title": title,
            "is_key": any(k in tl for k in _WBJ_KEY_ROLES),
            "age": o.get("age"), "year_born": o.get("yearBorn"),
            "total_pay": o.get("totalPay"), "fiscal_year": o.get("fiscalYear"),
        })
    # Los roles clave primero (más relevantes para el historial), luego por compensación.
    roster.sort(key=lambda r: (not r["is_key"], -(r.get("total_pay") or 0)))
    return roster


def _wbj_management_track_record(info, settings=None):
    """Contenido OBLIGATORIO del reporte (CLAUDE.md #4): "si el management tiene historial
    en otras empresas exitosas". Dos capas SEPARADAS:
      (a) roster FACTUAL (yfinance companyOfficers) — determinista, siempre disponible.
      (b) historial en otras empresas — evaluación cualitativa OPCIONAL vía Claude, GROUNDED:
          solo trayectorias VERIFICABLES y conocidas; si no hay certeza -> 'no_verificable'.
          Respeta 'No inventes nada': el default es no_verificable, nunca fabrica.
    Nunca cambia el scoring."""
    roster = _wbj_management_roster(info)
    out = {"roster": roster, "source": "yfinance companyOfficers",
           "note": "Roster factual. FMP no expone ejecutivos; yfinance sí (companyOfficers)."}
    if not roster:
        out["track_record"] = {"status": "sin_datos",
                               "note": "yfinance no devolvió ejecutivos para este ticker."}
        return out
    # (b) Historial en otras empresas — SOLO si hay key de Claude; grounded y conservador.
    try:
        if settings is None:
            import sys
            if _WBJ_ENGINE_PATH not in sys.path:
                sys.path.insert(0, _WBJ_ENGINE_PATH)
            from wbj.config import load_settings
            settings = _engine_settings()
        key = getattr(settings, "anthropic_api_key", None)
    except Exception:
        key = os.environ.get("ANTHROPIC_API_KEY")
        settings = None
    if not key:
        out["track_record"] = {"status": "no_evaluado",
                               "note": "Sin ANTHROPIC_API_KEY: el historial cualitativo queda sin evaluar (el roster factual sí está)."}
        return out
    keyexecs = [r for r in roster if r["is_key"]][:6] or roster[:6]
    company = info.get("longName") or info.get("shortName") or ""
    try:
        import anthropic, json as _json, re as _re
        _client = anthropic.Anthropic(api_key=key)
        _sys = ("Eres un analista de research. Evalúas SOLO trayectorias PÚBLICAS y VERIFICABLES "
                "de ejecutivos: cargos previos de alto nivel (CEO/CFO/fundador) en OTRAS empresas "
                "reconocidas y su resultado (éxito/fracaso). Regla dura: si NO estás seguro de un "
                "dato, usa 'no_verificable' y deja prior_companies vacío. NUNCA inventes empresas, "
                "cargos ni resultados. Responde ÚNICAMENTE con JSON válido, sin texto alrededor.")
        _roster_txt = "\n".join(f"- {r['name']} — {r['title']}" for r in keyexecs)
        _schema = ('{"executives": [{"name": string, "current_title": string, '
                   '"prior_companies": [{"company": string, "role": string, "outcome": string}], '
                   '"assessment": "verificable"|"no_verificable", "note": string}], '
                   '"overall": string}')
        _msg = _claude_crea(_client,
            model=getattr(settings, "judge_model", "claude-opus-5") if settings else "claude-opus-5",
            max_tokens=1024, system=_sys,
            messages=[{"role": "user", "content":
                       f"Empresa: {company}. Ejecutivos clave:\n{_roster_txt}\n\n"
                       "¿Alguno tiene historial de alto nivel en OTRAS empresas exitosas? "
                       "Devuelve este JSON (usa 'no_verificable' si no estás seguro):\n" + _schema}],
        )
        _raw = "".join(getattr(b, "text", "") for b in _msg.content)
        _m = _re.search(r"\{.*\}", _raw, _re.DOTALL)
        if _m:
            _d = _json.loads(_m.group(0))
            out["track_record"] = {"status": "evaluado", "source": "Claude (grounded)",
                                   "executives": _d.get("executives") or [],
                                   "overall": _d.get("overall") or "",
                                   "note": "Evaluación cualitativa asistida por LLM; solo cuenta lo marcado 'verificable'."}
        else:
            out["track_record"] = {"status": "no_evaluado", "note": "Respuesta del LLM no parseable."}
    except Exception as _e:
        print(f"[analyze] historial del management omitido: {str(_e)[:120]}")
        out["track_record"] = {"status": "no_evaluado", "note": "El historial cualitativo no se pudo evaluar (el roster factual sí está)."}
    return out




def _wbj_reexecution_triggers(ticker, prior_report, cik=None):
    """Disparadores de RE-EJECUCIÓN (CLAUDE.md, sección "Re-ejecución"): decide si la
    tesis PREVIA guardada quedó obsoleta y este análisis la reemplaza. Determinista, sin
    LLM, sin inventar. Dos fuentes de evidencia:
      1) Data vencida — usa la función EXACTA de Victor `staleness_state` (umbrales de
         DATA_POLICY.md: market 3d, consenso 7d, fundamentales 120d) sobre la EDAD del
         reporte previo.
      2) Filings SEC nuevos desde el reporte previo (EDGAR): 10-K/10-Q (nuevo periodo,
         earnings) y 8-K (evento material: financiamiento/adquisición/legal).
    Sin reporte previo → nada que revisitar. Si EDGAR no responde, igual devuelve los
    disparadores por antigüedad (no dependen de red). No cambia ningún número."""
    if not prior_report:
        return {"status": "sin_analisis_previo", "recalc_required": False, "triggers": [],
                "note": "Primer análisis de este ticker; no hay tesis previa que revisitar."}
    _pdate = (prior_report.get("created_at") or "")[:10]
    try:
        _age = (datetime.now() - datetime.strptime(_pdate, "%Y-%m-%d")).days
    except Exception:
        return {"status": "fecha_previa_invalida", "recalc_required": False, "triggers": [],
                "prior_report_date": _pdate or None}
    triggers = []
    # 1) DATA VENCIDA — umbrales exactos de Victor (staleness_state).
    try:
        import sys
        if _WBJ_ENGINE_PATH not in sys.path:
            sys.path.insert(0, _WBJ_ENGINE_PATH)
        from wbj.packet.staleness import staleness_state, THRESHOLDS_DAYS
        for _dt, _lbl in (("quarterly_fundamentals", "fundamentales trimestrales"),
                          ("consensus", "consenso de estimados"),
                          ("daily_market", "data de mercado diaria")):
            if staleness_state(_dt, _age) == "STALE":
                triggers.append({"tipo": "DATA_VENCIDA",
                                 "detalle": f"El análisis previo tiene {_age}d; {_lbl} vence a los "
                                            f"{THRESHOLDS_DAYS[_dt]}d (DATA_POLICY.md) → recalcular.",
                                 "fecha": _pdate})
    except Exception as _se:
        print(f"[analyze] staleness omitido: {str(_se)[:100]}")
    # 2) FILINGS SEC NUEVOS desde el reporte previo (10-K/10-Q/8-K).
    try:
        cik = cik or _get_sec_cik(ticker)
        if cik:
            import httpx
            from wbj.providers.edgar import EDGAR_USER_AGENT
            _r = httpx.get(f"https://data.sec.gov/submissions/CIK{int(cik):010d}.json",
                           headers={"User-Agent": EDGAR_USER_AGENT}, timeout=20.0)
            _r.raise_for_status()
            _rec = (_r.json().get("filings", {}) or {}).get("recent", {}) or {}
            _forms = _rec.get("form", []); _fdates = _rec.get("filingDate", [])
            _relevant = {"10-K": "nuevo reporte anual (10-K)",
                         "10-Q": "nuevo reporte trimestral (10-Q / earnings)",
                         "8-K": "evento material (8-K): financiamiento / adquisición / legal"}
            _seen = set()
            for _f, _fd in zip(_forms, _fdates):
                if _f in _relevant and _fd > _pdate and _f not in _seen:
                    _seen.add(_f)
                    triggers.append({"tipo": "FILING_SEC", "detalle": _relevant[_f], "fecha": _fd})
    except Exception as _fe:
        print(f"[analyze] filings SEC para re-ejecución omitidos: {str(_fe)[:100]}")
    return {"status": "evaluado", "recalc_required": len(triggers) > 0,
            "prior_report_date": _pdate, "prior_report_age_days": _age,
            "triggers": triggers,
            "note": ("La tesis previa quedó obsoleta; este análisis la reemplaza."
                     if triggers else
                     "Sin disparadores desde el análisis previo: la tesis anterior sigue vigente.")}


def _wbj_levels_ctx(victor_levels):
    """Formatea los niveles Y las confluencias REALES de Victor (synthesize_levels →
    _find_confluences, tolerancia exacta confluence_tolerance) para el prompt de la
    explicación. Así el campo 'niveles_y_confluencia' se explica sobre el cálculo
    determinista de Victor y NO sobre una adivinanza del LLM. No cambia ningún número."""
    vl = victor_levels or {}
    levels = vl.get("levels") or []
    confs = vl.get("confluences") or []
    lines = []
    for lv in levels[:12]:
        if not isinstance(lv, dict):
            continue
        _lo, _hi, _v = lv.get("zone_low"), lv.get("zone_high"), lv.get("value")
        if _lo is not None and _hi is not None:
            _px = f"${_lo}–${_hi}"
        elif _v is not None:
            _px = f"${_v}"
        else:
            _px = "s/precio"
        _d = lv.get("distance_percent")
        _dtxt = f", {_d:+.1f}% del spot" if isinstance(_d, (int, float)) else ""
        lines.append(f"  · [{lv.get('level_class', '?')}] {lv.get('label', '')}: {_px}"
                     f"{_dtxt}{(' — ' + lv['status']) if lv.get('status') else ''}")
    ltxt = "\n".join(lines) if lines else "  (sin niveles)"
    if confs:
        ctxt = "\n".join(
            f"  · CONFLUENCIA {'+'.join(c.get('level_labels') or [])} en ${c.get('low')}–${c.get('high')}"
            f" — {c.get('note', '')}"
            for c in confs if isinstance(c, dict))
    else:
        ctxt = ("  (sin confluencias: ningún nivel técnico y de valuación se solapan dentro de la "
                "tolerancia de Victor — NO se promedian)")
    return f"{ltxt}\nZONAS DE CONFLUENCIA (técnico∩valuación, NUNCA promediar):\n{ctxt}"


def _wbj_read_thesis_md(ticker):
    """Lee Memoria/tesis/<TICKER>.md (tesis previa) para el prompt. '' si no hay."""
    p = os.path.join(os.path.dirname(os.path.abspath(__file__)), "Memoria", "tesis", f"{ticker.upper()}.md")
    if os.path.exists(p):
        try:
            with open(p, "r", encoding="utf-8") as f:
                return f.read().strip()
        except Exception:
            return ""
    return ""


def _wbj_sin_encabezado(texto):
    """El historial de un archivo de tesis, sin su título `# Tesis — X`.

    El defecto que arregla: se anteponía el texto ANTERIOR COMPLETO —título
    incluido— debajo de un título nuevo, así que `# Tesis — NVDA` se
    multiplicaba una vez por corrida. `NVDA.md` llegó a tener el mismo
    encabezado repetido con bloques idénticos debajo.
    """
    cuerpo = (texto or "").lstrip()
    if cuerpo.startswith("# "):
        _, _, cuerpo = cuerpo.partition("\n")
    return cuerpo.strip()


def _wbj_firma_tesis(profile, raw, fair_value, t12):
    """Lo que hace que dos análisis sean el MISMO resultado."""
    return (f"{profile}|{raw}|{fair_value}|"
            f"{t12.get('bull')}|{t12.get('base')}|{t12.get('bear')}")


def _wbj_write_thesis_md(ticker, price, profile, raw, fair_value, targets, thesis, invalidation):
    """Escribe/actualiza Memoria/tesis/<TICKER>.md (protocolo de memoria del CLAUDE.md).

    Corrige encima; nunca borra una tesis vieja: el historial es la señal de
    aprendizaje. Pero repetir el MISMO resultado tampoco es historial —
    veinte bloques idénticos no dicen que la tesis se sostuvo, sólo que se
    apretó el botón veinte veces. Si el análisis nuevo coincide con el más
    reciente (perfil, score, fair value y los tres targets), se actualiza la
    fecha de revisión de ese bloque en lugar de apilar un duplicado; el
    bloque conserva la fecha en que la conclusión apareció por primera vez.
    Un cambio en cualquiera de esos campos sí abre un bloque nuevo.

    Best-effort: fallar aquí nunca puede tumbar el análisis.
    """
    try:
        base = os.path.join(os.path.dirname(os.path.abspath(__file__)), "Memoria", "tesis")
        os.makedirs(base, exist_ok=True)
        p = os.path.join(base, f"{ticker.upper()}.md")
        fecha = datetime.now().strftime("%Y-%m-%d %H:%M")
        t12 = (targets or {}).get("12m", {}) or {}
        firma = _wbj_firma_tesis(profile, raw, fair_value, t12)

        previo = ""
        if os.path.exists(p):
            with open(p, "r", encoding="utf-8") as f:
                previo = _wbj_sin_encabezado(f.read())

        bloques = [b for b in re.split(r"\n(?=## )", previo) if b.strip()]
        reciente = bloques[0] if bloques else ""
        m = re.search(r"<!-- firma: (.*?) \| desde: (.*?) -->", reciente)

        if m and m.group(1) == firma:
            # Mismo resultado: se sella la revisión, no se duplica el bloque.
            desde = m.group(2)
            bloques[0] = re.sub(
                r"^## .*$",
                f"## {desde} — perfil {profile} · raw {raw}/100  "
                f"*(sin cambios; revisado {fecha})*",
                reciente, count=1, flags=re.M)
        else:
            entry = (f"## {fecha} — perfil {profile} · raw {raw}/100\n"
                     f"<!-- firma: {firma} | desde: {fecha} -->\n"
                     f"- Precio al análisis: ${price} · Fair value (base): ${fair_value}\n"
                     f"- Targets 12M: Bull ${t12.get('bull')} / Base ${t12.get('base')} / Bear ${t12.get('bear')}\n"
                     f"- Tesis: {(thesis or '').strip()[:600]}\n"
                     f"- Invalidación: {invalidation}\n")
            bloques.insert(0, entry)

        with open(p, "w", encoding="utf-8") as f:
            f.write(f"# Tesis — {ticker.upper()}\n\n"
                    + "\n\n".join(b.strip() for b in bloques) + "\n")

        _wbj_actualizar_indice_memoria(ticker, fecha, profile, raw, fair_value)
    except Exception as e:
        print(f"[Memoria] no se pudo escribir tesis {ticker}: {e}")


#: Dónde viven las líneas por ticker dentro de `Memoria/MEMORIA.md`.
_MEMORIA_SECCION = "## Tesis activas"


def _wbj_actualizar_indice_memoria(ticker, fecha, profile, raw, fair_value):
    """UNA línea por ticker en `Memoria/MEMORIA.md`, no una por corrida.

    El índice se abría en modo `"a"`, así que cada análisis añadía una línea
    aunque el resultado fuese idéntico: NVDA aparecía 25 veces con el mismo
    texto y el índice dejaba de servir para lo único que existe — mirar de
    un vistazo qué se dijo de cada empresa. El propio MEMORIA.md lo pide
    así: "el agente agrega una línea por ticker analizado".

    La línea se REEMPLAZA y el listado queda ordenado. El historial no se
    pierde: vive en `tesis/<TICKER>.md`, que es su sitio.
    """
    idx = os.path.join(os.path.dirname(os.path.abspath(__file__)), "Memoria", "MEMORIA.md")
    linea = f"- [{ticker.upper()}](tesis/{ticker.upper()}.md) · {fecha} · {profile} · raw {raw}/100 · FV ${fair_value}"
    try:
        with open(idx, "r", encoding="utf-8") as f:
            texto = f.read()
    except OSError:
        texto = f"# Memoria del Agente — Warren Buffett Jr\n\n{_MEMORIA_SECCION}\n\n"

    cabeza, sep, cola = texto.partition(_MEMORIA_SECCION)
    if not sep:                                    # sin la sección: se crea
        cabeza, cola = texto.rstrip() + "\n\n", "\n"

    lineas = [l for l in cola.splitlines() if re.match(r"- \[?[A-Z][A-Z.\-]*\]?[ (·]", l)]
    otras = [l for l in cola.splitlines()
             if l.strip() and l not in lineas and not l.startswith("- ")]
    lineas = [l for l in lineas
              if not re.match(rf"- \[?{re.escape(ticker.upper())}\]?[ (·]", l)]
    lineas.append(linea)
    lineas.sort(key=lambda l: re.sub(r"^- \[?", "", l))

    with open(idx, "w", encoding="utf-8") as f:
        f.write(cabeza + _MEMORIA_SECCION + "\n\n"
                + "\n".join(otras + ([""] if otras else []) + lineas) + "\n")


def _wbj_write_prediccion(ticker, report_id, price, fair_value, profile, raw, targets, recommendation):
    """Guarda Reportes/<TICKER>/<fecha>/prediccion.json (para el track record).
    Nunca se edita luego. Best-effort."""
    try:
        fecha = datetime.now().strftime("%Y-%m-%d")
        base = os.path.join(os.path.dirname(os.path.abspath(__file__)), "Reportes", ticker.upper(), fecha)
        os.makedirs(base, exist_ok=True)
        payload = {"report_id": report_id, "ticker": ticker.upper(), "fecha": fecha,
                   "price_at_analysis": price, "fair_value": fair_value, "profile": profile,
                   "raw_total": raw, "recommendation": recommendation,
                   "targets_12m": (targets or {}).get("12m", {}), "framework": "WBJ v2.0.0"}
        with open(os.path.join(base, "prediccion.json"), "w", encoding="utf-8") as f:
            json.dump(payload, f, ensure_ascii=False, indent=2)
    except Exception as e:
        print(f"[Predicción] no se pudo escribir {ticker}: {e}")


class WBJExplanation(BaseModel):
    resumen_simple: str = Field(..., description="En 3-5 frases y en lenguaje MUY simple: qué es esta empresa como inversión y qué dice el veredicto. Para alguien sin conocimientos financieros.")
    por_categoria: str = Field(..., description="Explica en palabras qué significa el puntaje de CADA una de las 6 categorías (business, financial, market, technical, risk, valuation) y por qué está alto o bajo, citando las notas. Detallado pero simple.")
    gates_y_perfil: str = Field(..., description="Explica qué significa el perfil asignado (Momentum/Quality/Value/Conditional/Speculative/Avoid), qué gates pasaron o fallaron y qué implican, en palabras llanas.")
    overrides_y_coherencia: str = Field(..., description="Explica qué significan los overrides activados y los flags de coherencia (contradicciones) listados, y qué debería vigilar el inversionista.")
    niveles_y_confluencia: str = Field(..., description="Explica los niveles importantes (técnicos vs valuación) y las zonas de confluencia detectadas: qué son, por qué importan y cómo leerlas. Recuerda que no se promedian.")
    ajuste_a_mi_perfil: str = Field(..., description="Explica cómo encaja (o no) esta inversión con MI perfil (horizonte 1-3a + opciones corto plazo + ingresos, agresivo/especulativo, acciones/ETF/opciones, ~$1,000), incluido el riesgo de sizing con capital pequeño.")
    calibracion: str = Field(..., description="Explica en palabras qué dice mi track record/calibración histórica (si hay) y cómo tomar la confianza del veredicto. Si no hay historial, dilo.")
    conclusion: str = Field(..., description="La conclusión final en 1-2 frases, honesta y sin promesas de retorno.")


def _wbj_explain_context(ticker, nombre_largo, precio, analisis_json):
    """Arma el contexto del 2º pase: los números YA congelados + TU perfil.

    Estaba escrito en línea dentro de `/api/analyze`, y por eso la explicación
    solo podía generarse allí — pagando sus ~18 s dentro del camino crítico o
    no generándose nunca. Extraído, el mismo contexto se puede reconstruir
    después desde el reporte guardado, que es lo que hace
    `/api/wbj-explicacion`.

    El bloque `=== MI PERFIL ===` es el ÚNICO sitio donde entra el texto libre
    del cuestionario. De ahí sale que el capital, el horizonte y lo que
    escribiste cambien el TONO de la explicación — nunca los números.
    """
    _pname, _ptext = _load_investor_profile()
    _wj = analisis_json.get("wbj") or {}
    _cts = _wj.get("categories") or {}
    _catl = []
    for _ck in WBJ_ORDER:
        _cc2 = _cts.get(_ck) or {}
        _catl.append(f"- {_cc2.get('label', _ck)}: {_cc2.get('score10')}/10 "
                     f"({_cc2.get('points')}/{_cc2.get('max')} pts, "
                     f"cob {int((_cc2.get('coverage') or 0) * 100)}%, {_cc2.get('status')})")
    _pf = analisis_json.get("profile_fit") or {}
    # MEMORIA (CLAUDE.md): lee la tesis PREVIA para dar coherencia entre sesiones.
    _prior_thesis = _wbj_read_thesis_md(ticker)
    _prior_ctx = (f"\n=== TESIS PREVIA (Memoria) ===\n{_prior_thesis[:900]}\n"
                  "Si esta llamada contradice la tesis previa, la explicación debe señalarlo.\n"
                  if _prior_thesis else "")
    # Re-ejecución: dile al LLM qué disparó reemplazar la tesis previa (si algo).
    _rex = analisis_json.get("re_execution") or {}
    if _rex.get("triggers"):
        _prior_ctx += ("RE-EJECUCIÓN: la tesis previa quedó obsoleta por: "
                       + "; ".join(t.get("detalle", "") for t in _rex["triggers"]) + "\n")
    # Los hechos del perfil van EXPLÍCITOS además del `.md`: son los que el
    # LLM tiene que usar para calibrar el tamaño de lo que describe, y
    # enterrados en la prosa del archivo se pierden.
    _perfil_duro = ""
    if _pf:
        _perfil_duro = (f"HECHOS DE TU PERFIL: capital {_pf.get('capital')} · "
                        f"riesgo por operación {_pf.get('riesgo_por_operacion')} · "
                        f"tope por posición {_pf.get('max_posicion_pct')} · "
                        f"horizonte {_pf.get('horizonte')} · universo {_pf.get('universo')}\n")
        if _pf.get("es_por_defecto"):
            _perfil_duro += ("AVISO: esta persona NO ha personalizado su perfil. Son valores de "
                             "referencia, no suyos — no le hables como si los hubiera declarado.\n")
    # La memoria: qué dijo el agente de ESTE ticker la última vez y qué ha hecho
    # el precio desde entonces. Va al prompt porque una memoria que no cambia
    # ninguna decisión no es memoria, es un archivo.
    try:
        import vertex_almacen as _AL2
        import vertex_memoria as _MEM
        _memoria_ctx = _MEM.contexto_para_el_agente(
            analisis_json.get("ticker") or "", _AL2.almacen,
            precio_hoy=analisis_json.get("precio_actual"))
        if _memoria_ctx:
            _memoria_ctx = "\n" + _memoria_ctx + "\n"
    except Exception:                              # noqa: BLE001
        _memoria_ctx = ""
    return (
        f"TICKER: {ticker} — {nombre_largo} | precio ${precio}\n"
        f"PERFIL/BANDA: {_wj.get('profile')} — {_wj.get('band')}\n"
        f"RAW TOTAL: {_wj.get('raw_total')}/100 | CONFIANZA TOTAL: {_wj.get('total_confidence')}\n"
        "CATEGORÍAS (score de Victor, no cambiar):\n" + "\n".join(_catl) + "\n"
        f"GATES PASADOS: {[g.get('gate') for g in _wj.get('passed_gates', []) if isinstance(g, dict)]}\n"
        f"GATES FALLIDOS: {[g.get('gate') for g in _wj.get('failed_gates', []) if isinstance(g, dict)]}\n"
        f"OVERRIDES ACTIVOS: {_wj.get('overrides')}\n"
        f"WARNINGS: {_wj.get('warnings')}\n"
        f"CONTRADICCIONES: {[c.get('combination') for c in (_wj.get('contradictions') or [])]}\n"
        f"TARGETS 12m: bull ${analisis_json.get('target_bull_12m')} / base "
        f"${analisis_json.get('target_base_12m')} / bear ${analisis_json.get('target_bear_12m')} "
        f"| fair value ${analisis_json.get('fair_value')}\n"
        f"NIVELES (synthesize_levels de Victor):\n"
        f"{_wbj_levels_ctx(analisis_json.get('victor_levels'))}\n"
        f"FIT DE PERFIL (determinista): {_pf.get('fit')} — {_pf.get('fit_reason')}\n"
        + _perfil_duro + _prior_ctx + _memoria_ctx +
        f"\n=== MI PERFIL ({_pname or 'inversionista'}) ===\n{_ptext}")


def _wbj_explain(context_text, temp=0.3):
    """2º PASE: el LLM SOLO explica el paquete ya calculado en palabras simples.
    Recibe los números FINALES (matemática de Victor) y NO los cambia. Respaldo de
    proveedor igual que el análisis. Devuelve (dict, fuente) o (None, None) si falla."""
    # La instrucción de idioma va AL FINAL, después del contexto: el esquema de
    # respuesta (`WBJExplanation`) trae «en español» escrito en la descripción
    # de un campo, y lo último que lee el modelo es lo que manda.
    prompt = (
        "Eres un divulgador financiero. Abajo tienes un análisis WBJ YA CALCULADO con la "
        "metodología de Victor (los números son FINALES y correctos). Tu ÚNICO trabajo es "
        "EXPLICARLO de forma simple, clara y detallada para el inversionista de 'MI PERFIL'. "
        "NO recalcules, NO cambies, NO reduzcas ni 'corrijas' ningún número, score, gate ni nivel. "
        "Si algo no tiene datos (NOT_SCORABLE), explícalo con honestidad. No prometas retornos ni "
        "des órdenes de compra/venta.\n\n" + context_text)
    # Por qué falló CADA proveedor, en orden. Antes sólo se propagaba el
    # último error de la cadena, así que un 429 de cuota en Gemini —el
    # proveedor PRINCIPAL— se reportaba como "Grok no configurado
    # (XAI_API_KEY vacío)". El mensaje señalaba una variable de entorno que
    # falta a propósito y escondía la causa real, que es de facturación.
    # (Grok salió del proyecto: Victor no lo usa en ninguna parte.)
    fallos: list[str] = []
    for attempt in range(2):
        try:
            r = _gemini_genera(
                model="gemini-2.5-flash", contents=prompt,
                config=types.GenerateContentConfig(
                    response_mime_type="application/json", response_schema=WBJExplanation, temperature=temp))
            return json.loads(r.text), "gemini"
        except Exception as e:
            if _is_quota_error(e) and attempt == 0:
                time.sleep(_retry_delay_secs(e)); continue
            fallos.append(f"gemini: {type(e).__name__} {str(e)[:90]}")
            break
    try:
        keys = list(getattr(WBJExplanation, "model_fields", None) or getattr(WBJExplanation, "__fields__", {}) or [])
    except Exception:
        keys = []
    for fn, src in ((_openai_json, "openai (ChatGPT)"),):
        try:
            return fn(prompt, keys, temp), src
        except Exception as e:
            fallos.append(f"{src}: {type(e).__name__} {str(e)[:90]}")
    if fallos:
        # El PRIMERO es el que importa: es el proveedor principal.
        print("[analyze] narrativa: ningún proveedor respondió — " + " | ".join(fallos))
    return None, None


# ── ENGINE DETERMINISTA DE VICTOR (sin LLM) para los scores de las 6 categorías ──








def _norm_shares(vals, ticker=""):
    """Normaliza una lista de participaciones a fracciones 0-1.

    Si algún valor viene >1 (y ≤100) la fuente reportó porcentajes: se divide
    toda la lista entre 100. Los que queden fuera de [0,1] se descartan — nunca
    se recortan a la fuerza, porque un valor imposible es un error de la fuente,
    no un dato que valga la pena salvar.
    """
    out = []
    for x in (vals or []):
        try:
            v = float(x)
        except (TypeError, ValueError):
            continue
        if v >= 0:
            out.append(v)
    if not out:
        return None
    if max(out) > 1.0 and max(out) <= 100.0:
        out = [v / 100.0 for v in out]
    out = [v for v in out if 0.0 <= v <= 1.0]
    return out or None


def _fmp_segment_shares(ticker, settings=None):
    """`segment_shares` desde la segmentación de ingresos por producto de FMP.

    Fuente REPORTADA (clase R): FMP publica el desglose por segmento tal como la
    empresa lo divulga en el filing. Es determinista y auditable — nada que ver
    con pedirle a un LLM que lea prosa.

    Cualquier fallo (sin key, endpoint fuera del plan → 402/403, forma
    inesperada) devuelve None: la métrica queda N/S. Jamás se estima.
    """
    key = (os.environ.get("FMP_API_KEY") or getattr(settings, "fmp_api_key", None) or "").strip()
    if not key:
        return None
    try:
        import httpx
        r = httpx.get("https://financialmodelingprep.com/stable/revenue-product-segmentation",
                      params={"symbol": ticker.upper(), "apikey": key}, timeout=20.0)
        if r.status_code != 200:
            # 402/403 = el endpoint no está en el plan. Es información útil, no un error.
            print(f"[engine] {ticker}: segmentación de ingresos no disponible en tu plan FMP "
                  f"(HTTP {r.status_code}) → segment_shares N/S")
            return None
        rows = r.json()
    except Exception as e:
        print(f"[engine] {ticker}: segmentación de ingresos falló: {str(e)[:110]} → N/S")
        return None
    if not isinstance(rows, list) or not rows:
        return None
    # El periodo más reciente. FMP devuelve {date, symbol, data:{segmento: monto}}
    # o el mapa plano según versión; se aceptan ambos sin inventar.
    latest = rows[0] if isinstance(rows[0], dict) else None
    if not isinstance(latest, dict):
        return None
    data = latest.get("data") if isinstance(latest.get("data"), dict) else {
        k: v for k, v in latest.items()
        if k not in ("date", "symbol", "fiscalYear", "period", "reportedCurrency")
        and isinstance(v, (int, float))
    }
    amounts = [float(v) for v in (data or {}).values() if isinstance(v, (int, float)) and v >= 0]
    total = sum(amounts)
    if not amounts or total <= 0:
        return None
    shares = _norm_shares([v / total for v in amounts], ticker)
    if shares:
        print(f"[engine] {ticker}: segment_shares desde FMP (reportado) — {len(shares)} segmentos")
    return shares


def _extraction_model(settings=None):
    """Modelo para EXTRAER texto largo (10-K), separado del que EMITE JUICIO.

    Las dos llamadas al LLM tienen perfiles opuestos: la extracción manda ~125k tokens
    de 10-K pero solo busca números (volumen, poco criterio), mientras el judge manda
    ~3k tokens pero clasifica el moat y los thesis-killers (poco volumen, mucho
    criterio) — y su respuesta SÍ mueve puntos. Poner el modelo caro en la extracción
    es pagar 5x donde menos rinde, así que aquí se usa uno barato por defecto y el
    judge conserva el suyo (wbj.config.judge_model).
    """
    m = os.environ.get("EXTRACTION_MODEL")
    if m:
        return m.strip()
    # Si alguien fijó JUDGE_MODEL a un modelo barato, se respeta esa intención.
    jm = (getattr(settings, "judge_model", "") or os.environ.get("JUDGE_MODEL") or "").strip()
    if "haiku" in jm.lower():
        return jm
    return "claude-haiku-4-5"


def _wbj_qual_from_10k_llm(ticker, cik, settings, revenue_hint=None, skip=()):
    """Extrae del 10-K real (SEC EDGAR) los inputs CUALITATIVOS que el especialista
    Business de Victor lee por `overlay` y que NO están en FMP ni son slots del judge:
    recurring_revenue, largest_customer_share, customer_shares, retention (NRR/GRR),
    churn, customer_economics (arpu/ltv/cac/payback) y guidance_history.

    Fiel al sub-agente `business-analysis` de Victor: Claude LEE el filing y devuelve
    SOLO lo que la empresa divulga explícitamente; si no está, null → la métrica queda
    N/S ('sin evidencia, no hay número'). Nunca inventa. Cualquier fallo → {} (el
    análisis sigue igual). Devuelve el dict de overlay ya en la forma que espera Victor."""
    key = getattr(settings, "anthropic_api_key", None)
    if not key or not cik:
        return {}
    try:
        import httpx, json as _json, re as _re
        from wbj.providers.edgar import EDGAR_USER_AGENT
        _hdr = {"User-Agent": EDGAR_USER_AGENT}
        # 1) localizar el último 10-K (accession + documento primario) en submissions
        _sub = httpx.get(f"https://data.sec.gov/submissions/CIK{int(cik):010d}.json",
                         headers=_hdr, timeout=20.0)
        _sub.raise_for_status()
        _rec = (_sub.json().get("filings", {}) or {}).get("recent", {}) or {}
        _forms = _rec.get("form", []); _accs = _rec.get("accessionNumber", []); _docs = _rec.get("primaryDocument", [])
        _idx = next((i for i, f in enumerate(_forms) if f == "10-K"), None)
        if _idx is None:
            return {}
        _acc = _accs[_idx].replace("-", ""); _doc = _docs[_idx]
        # 2) bajar el documento y limpiarlo a texto plano
        _u = f"https://www.sec.gov/Archives/edgar/data/{int(cik)}/{_acc}/{_doc}"
        _r = httpx.get(_u, headers=_hdr, timeout=30.0); _r.raise_for_status()
        _txt = _re.sub(r"<[^>]+>", " ", _r.text)
        _txt = _re.sub(r"&#\d+;|&[a-z]+;", " ", _txt)
        # cap de contexto: 10-K reales miden 200k-500k caracteres. 180k truncaba MD&A/notas
        # (donde viven segmentos, ingreso recurrente, unit economics) → datos omitidos. Subimos
        # a 500k (~125k tokens, dentro del contexto de Claude) para NO omitir esas secciones.
        _txt = _re.sub(r"\s+", " ", _txt).strip()[:500000]
        # 3) extracción con Claude — SOLO lo divulgado, si no null
        import anthropic
        _client = anthropic.Anthropic(api_key=key)
        _sys = ("Eres un analista que extrae SOLO datos DIVULGADOS explícitamente en un 10-K. "
                "Si un dato no aparece divulgado, devuelve null. NUNCA estimes ni inventes. "
                "Muchas empresas (no-suscripción) no reportan NRR/churn/LTV/CAC: en ese caso null. "
                "Responde ÚNICAMENTE con un objeto JSON válido, sin texto alrededor.")
        _schema = (
            '{"recurring_revenue_pct": number|null,  // fracción 0-1 del ingreso total que es recurrente/suscripción (PREFERIDO, inequívoco)\n'
            ' "recurring_revenue_usd": number|null,  // ingreso recurrente/suscripción anual en USD absolutos (respeta la escala: dólares, no millones)\n'
            ' "largest_customer_share": number|null, // 0-1; SOLO si divulga un % ESPECÍFICO del mayor cliente. Si solo dice "ningún cliente supera X%" sin dar el número exacto -> null (PROHIBIDO imputar)\n'
            ' "customer_shares": [number]|null,      // 0-1 por cliente si divulga varios\n'
            ' "segment_shares": [number]|null,       // 0-1 fracción de ingresos por segmento de negocio\n'
            ' // Cohorte de retención de ingresos (ARR bridge), en USD del MISMO periodo:\n'
            ' "retention_begin": number|null,        // ARR/ingreso recurrente al inicio\n'
            ' "retention_expansion": number|null,    // expansión/upsell del periodo\n'
            ' "retention_contraction": number|null,  // contracción/downgrade del periodo\n'
            ' "retention_churn": number|null,        // ingreso perdido por bajas del periodo\n'
            ' // Churn de logos (CONTEO de clientes):\n'
            ' "customers_lost": number|null,         // clientes perdidos en el periodo\n'
            ' "customers_begin": number|null,        // clientes al inicio del periodo\n'
            ' // Unit economics (si los divulga):\n'
            ' "arpu": number|null,                   // ingreso anual promedio por cliente (USD)\n'
            ' "monthly_arpu": number|null,           // ingreso MENSUAL promedio por cliente (USD)\n'
            ' "gross_margin": number|null,           // margen bruto por cliente 0-1\n'
            ' "customer_life_years": number|null,    // vida media del cliente en años\n'
            ' "cac_spend": number|null,              // gasto total de adquisición (S&M) USD\n'
            ' "new_customers": number|null,          // clientes nuevos adquiridos en el periodo\n'
            ' "guidance_history": [{"actual": number, "guidance_midpoint": number}]|null}'
        )
        _msg = _claude_crea(_client,
            model=_extraction_model(settings),      # modelo BARATO: aquí manda el volumen, no el criterio
            max_tokens=1024, system=_sys,
            messages=[{"role": "user", "content":
                       f"Empresa {ticker}. Del siguiente 10-K, extrae este JSON (null si no está divulgado):\n"
                       f"{_schema}\n\n=== 10-K ===\n{_txt}"}],
        )
        _raw = "".join(getattr(b, "text", "") for b in _msg.content)
        _m = _re.search(r"\{.*\}", _raw, _re.DOTALL)
        if not _m:
            return {}
        _d = _json.loads(_m.group(0))
    except Exception as _e:
        print(f"[engine] extracción cualitativa del 10-K omitida: {str(_e)[:140]}")
        return {}
    # 4) mapear a la forma EXACTA que Victor espera por overlay (solo lo no-null)
    _ov = {}
    def _num(x):
        try:
            return float(x) if x is not None else None
        except (TypeError, ValueError):
            return None
    # recurring_revenue: preferir el PORCENTAJE (inequívoco) → absoluto = pct × ingreso.
    # El absoluto directo se acepta solo si pasa una cota anti-error-de-unidad (millones vs dólares).
    _rrp = _num(_d.get("recurring_revenue_pct"))
    _rr = _num(_d.get("recurring_revenue_usd"))
    _rev_h = _num(revenue_hint)
    if _rrp is not None and 0.0 < _rrp <= 1.0 and _rev_h and _rev_h > 0:
        _ov["recurring_revenue"] = _rrp * _rev_h
    elif _rr is not None and _rr > 0:
        # banda de sanidad de dos lados: recurrente entre 0.1% y 120% del ingreso. Fuera de eso
        # es casi seguro un error de escala del LLM (millones/miles) → N/S (más honesto que un ~0 falso).
        if (not _rev_h) or (_rev_h * 0.001 <= _rr <= _rev_h * 1.2):
            _ov["recurring_revenue"] = _rr
        else:
            print(f"[engine] {ticker}: recurring_revenue absoluto descartado por escala improbable "
                  f"({_rr:.3g} vs ingreso ~{_rev_h:.3g}) → N/S")
    _lcs = _num(_d.get("largest_customer_share"))
    if _lcs is not None and 0.0 <= _lcs <= 1.0:
        _ov["largest_customer_share"] = _lcs
    # customer/segment shares: mismo riesgo de escala que gross_margin. Si el 10-K dice
    # "cliente A = 40%", el LLM puede devolver 40 en vez de 0.40. Si algún valor >1 (y ≤100),
    # se interpreta como porcentaje y se divide toda la lista entre 100 (antes se descartaban).
    def _norm_shares(_lst):
        _vals = [v for v in (_num(x) for x in _lst) if v is not None and v >= 0]
        if not _vals:
            return None
        if max(_vals) > 1.0 and max(_vals) <= 100.0:
            _vals = [v / 100.0 for v in _vals]
        _vals = [v for v in _vals if 0.0 <= v <= 1.0]
        return _vals or None
    _cs = _d.get("customer_shares")
    if isinstance(_cs, list):
        _csv = _norm_shares(_cs)
        if _csv:
            _ov["customer_shares"] = _csv
    _ss = _d.get("segment_shares")
    if isinstance(_ss, list):
        _ssv = _norm_shares(_ss)
        if _ssv:
            _ov["segment_shares"] = _ssv
    # retention: Victor exige la cohorte cruda {begin, expansion, contraction, churn}
    # (NO nrr/grr; él los calcula). Solo se pasa si los 4 componentes están divulgados.
    _rb, _rx = _num(_d.get("retention_begin")), _num(_d.get("retention_expansion"))
    _rcn, _rch = _num(_d.get("retention_contraction")), _num(_d.get("retention_churn"))
    if None not in (_rb, _rx, _rcn, _rch) and _rb > 0:
        # La fórmula NRR RESTA contraction y churn → son MAGNITUDES. abs() por si el LLM
        # las devuelve con signo (un contraction=-40 inflaría el NRR).
        _ex, _ct, _cn = abs(_rx), abs(_rcn), abs(_rch)
        _grr = (_rb - _ct - _cn) / _rb          # retención bruta
        _nrr = (_rb + _ex - _ct - _cn) / _rb    # retención neta
        # rechaza cohortes IMPOSIBLES: no se puede perder más que la base (GRR<0) ni retener
        # >250% (NRR>2.5). Un dato así es error de extracción → N/S (no un score real).
        if _grr >= 0.0 and _nrr <= 2.5:
            _ov["retention"] = {"begin": _rb, "expansion": _ex, "contraction": _ct, "churn": _cn}
        else:
            print(f"[engine] {ticker}: cohorte de retención implausible (NRR={_nrr:.2f}, GRR={_grr:.2f}) → N/S")
    # churn de logos: Victor exige {lost, begin_customers} (conteo de clientes).
    # No se pueden perder más clientes que los del inicio (churn>100% es imposible) → N/S.
    _cl, _cb = _num(_d.get("customers_lost")), _num(_d.get("customers_begin"))
    if None not in (_cl, _cb) and _cb > 0 and 0.0 <= abs(_cl) <= _cb:
        _ov["churn"] = {"lost": abs(_cl), "begin_customers": _cb}
    elif None not in (_cl, _cb) and _cb > 0 and abs(_cl) > _cb:
        print(f"[engine] {ticker}: churn de logos implausible (lost {abs(_cl):.0f} > base {_cb:.0f}) → N/S")
    # customer_economics: claves EXACTAS que Victor consume para LTV/CAC/payback
    _ce = {}
    for _src, _dst in (("arpu", "arpu"), ("monthly_arpu", "monthly_arpu"), ("gross_margin", "gross_margin"),
                       ("customer_life_years", "customer_life_years"), ("cac_spend", "cac_spend"),
                       ("new_customers", "new_customers")):
        _val = _num(_d.get(_src))
        if _val is None:
            continue
        if _dst == "gross_margin":
            # Victor multiplica gross_margin como FRACCIÓN 0-1 (LTV=arpu·gm·vida). Si el 10-K lo
            # divulga como "80%" y el LLM devuelve 80 → normalizamos a 0.80 y acotamos [0,1].
            if _val > 1.0:
                _val = _val / 100.0
            _val = min(max(_val, 0.0), 1.0)
        _ce[_dst] = _val
    if _ce:
        _ov["customer_economics"] = _ce
    _gh = _d.get("guidance_history")
    if isinstance(_gh, list):
        _ghv = [{"actual": _num(g.get("actual")), "guidance_midpoint": _num(g.get("guidance_midpoint"))}
                for g in _gh if isinstance(g, dict) and _num(g.get("actual")) is not None
                and _num(g.get("guidance_midpoint")) not in (None, 0)]
        if _ghv:
            _ov["guidance_history"] = _ghv
    # `skip`: lo que la capa determinista ya resolvio no se devuelve. Un numero
    # sacado del filing por codigo no puede ser pisado por uno sacado por un LLM.
    for _k in (skip or ()):
        _ov.pop(_k, None)
    if _ov:
        print(f"[engine] {ticker}: 10-K → inputs cualitativos divulgados: {sorted(_ov.keys())}")
    return _ov


def _edgar_companyfacts_for(cik, settings=None):
    """XBRL companyfacts via el EdgarProvider de Victor (cacheado, sin API key).

    Punto unico de entrada a SEC para la capa determinista: aisla la red para que
    los tests puedan inyectar un payload y ejercitar la logica de verdad.
    Cualquier fallo devuelve None — el analisis sigue sin este input.
    """
    try:
        if _WBJ_ENGINE_PATH not in sys.path:
            sys.path.insert(0, _WBJ_ENGINE_PATH)
        from wbj.config import load_settings
        from wbj.providers.cache import Cache
        from wbj.providers.edgar import EdgarProvider
        st = settings or _engine_settings()
        return EdgarProvider(st, Cache(st.cache_dir)).companyfacts(int(cik))
    except Exception as e:
        print(f"[engine] companyfacts no disponible: {str(e)[:110]}")
        return None


def _xbrl_recurring_revenue(cik, revenue_hint, settings=None):
    """`recurring_revenue` desde XBRL — PROXY REGISTRADO, no un dato reportado.

    MISSING_DATA_POLICY.md paso 4: "Is a proxy explicitly registered? If yes, use
    it with a proxy flag and lower model-fit confidence." Este es ese caso, y se
    declara como tal en la procedencia del reporte.

    El proxy: pasivo por contrato con clientes (ASC 606) —
    `ContractWithCustomerLiabilityCurrent` + `...Noncurrent`, con respaldo en los
    tags `DeferredRevenue*` anteriores a ASC 606. Solo existe cuando el cliente
    pago por adelantado un servicio que se entrega a lo largo del tiempo, que es
    justo la forma economica del ingreso por suscripcion.

    Limite honesto: mide el saldo diferido, NO el ingreso recurrente anual. Por eso
    es proxy y no clase R. Si el saldo supera al ingreso total, el supuesto no se
    sostiene y se devuelve None en vez de un numero que parezca bueno.
    """
    if not cik or not revenue_hint or revenue_hint <= 0:
        return None
    facts = _edgar_companyfacts_for(cik, settings)
    if not isinstance(facts, dict):
        return None

    def _latest(tag):
        """Ultimo valor anual (form 10-K) del tag, en USD."""
        units = (((facts.get("facts") or {}).get("us-gaap") or {}).get(tag) or {}).get("units") or {}
        rows = [r for r in (units.get("USD") or [])
                if isinstance(r.get("val"), (int, float)) and r.get("form") == "10-K" and r.get("end")]
        if not rows:
            return None
        return float(max(rows, key=lambda r: r["end"])["val"])

    for pair in (("ContractWithCustomerLiabilityCurrent", "ContractWithCustomerLiabilityNoncurrent"),
                 ("DeferredRevenueCurrent", "DeferredRevenueNoncurrent")):
        cur, non = _latest(pair[0]), _latest(pair[1])
        if cur is None and non is None:
            continue
        total = (cur or 0.0) + (non or 0.0)
        if total <= 0:
            continue
        if total > revenue_hint:
            # Saldo diferido > ingreso anual: el proxy deja de tener sentido economico.
            print(f"[engine] proxy de ingreso recurrente descartado: diferido {total:,.0f} "
                  f"> ingreso {revenue_hint:,.0f} → N/S")
            return None
        return total, pair[0].replace("Current", "")
    return None



def _wbj_extract_business_qual(ticker, cik, settings, revenue_hint=None):
    """Inputs cualitativos del negocio que Victor lee por `overlay`.

    Sigue el arbol de MISSING_DATA_POLICY.md en orden, sin saltarse escalones:

      capa 1 (reportado / calculado / proxy registrado) — Python puro, gratis,
              siempre corre. Es lo que Victor hace: leer el filing con codigo.
      capa 2 (residuo) — el 10-K por LLM, SOLO para los campos que ninguna fuente
              estructurada puede dar, y solo si hay key/credito.
      capa 3 — lo que sigue faltando queda NOT_SCORABLE.

    La capa 1 SIEMPRE gana: si el proxy de XBRL resolvio `recurring_revenue`, a la
    capa 2 ni se le pregunta por el, para que un LLM no pueda pisar un numero que
    salio del filing.

    Por que cada campo cae donde cae:
    - `segment_shares`   -> capa 1: FMP publica el desglose reportado (clase R).
    - `recurring_revenue`-> capa 1: proxy registrado sobre el pasivo por contrato
                            (ASC 606). Declarado como proxy en la procedencia.
    - `largest_customer_share` / `customer_shares` -> capa 2. En XBRL viven detras
                            del eje MajorCustomersAxis, y `companyfacts` devuelve
                            los hechos SIN ejes: un ConcentrationRiskPercentage1
                            suelto no dice si es de un cliente, un proveedor o una
                            geografia. Tomarlo igual seria inferir concentracion no
                            divulgada, prohibido por MISSING_DATA_POLICY.md:13-15 y
                            BUS-CONC-003. El LLM si lee el eje en la prosa, y solo
                            devuelve el dato si la empresa da un % especifico.
    - retencion (NRR/GRR), churn de logos, ARPU, CAC, vida del cliente, guidance
                         -> capa 2: no existen como tags XBRL, viven en el MD&A.
                            LTV/CAC ademas exige CONTEOS de clientes que ninguna
                            taxonomia etiqueta.
    """
    ov, prov = {}, {}

    # ── capa 1: determinista ──────────────────────────────────────────────────
    seg = _fmp_segment_shares(ticker, settings)
    if seg:
        ov["segment_shares"] = seg
        prov["segment_shares"] = {"source": "FMP revenue-product-segmentation",
                                  "evidence_class": "R", "proxy": False}

    rec = _xbrl_recurring_revenue(cik, revenue_hint, settings)
    if rec:
        ov["recurring_revenue"], _tag = rec
        prov["recurring_revenue"] = {"source": f"XBRL us-gaap:{_tag}(Current+Noncurrent)",
                                     "evidence_class": "A", "proxy": True,
                                     "note": "Saldo diferido como proxy del ingreso recurrente; "
                                             "confianza reducida (MISSING_DATA_POLICY paso 4)."}

    if ov:
        print(f"[engine] {ticker}: capa determinista → {sorted(ov.keys())}")

    # ── capa 2: el residuo, por LLM ───────────────────────────────────────────
    try:
        llm = _wbj_qual_from_10k_llm(ticker, cik, settings, revenue_hint=revenue_hint,
                                     skip=tuple(ov.keys())) or {}
    except Exception as e:
        print(f"[engine] {ticker}: capa LLM del 10-K omitida: {str(e)[:120]}")
        llm = {}
    for k, v in llm.items():
        if k in ov:
            continue                      # la capa determinista manda, siempre
        ov[k] = v
        prov[k] = {"source": "10-K (extraccion por LLM)", "evidence_class": "R", "proxy": False,
                   "note": "Solo lo divulgado explicitamente en el filing; si no aparece, null."}
    if llm:
        print(f"[engine] {ticker}: capa LLM → {sorted(k for k in llm if k not in ('__prov__',))}")

    if not ov:
        print(f"[engine] {ticker}: sin inputs cualitativos disponibles → todos N/S")
    ov["__provenance__"] = prov
    return ov


#: Scorecards del motor ya calculados, por (ticker, reloj congelado del packet).
#: El motor es DETERMINISTA y el packet esta anclado a una sesion YA CERRADA
#: (`market_timestamp`, ver V-05), asi que para el mismo ticker y la misma
#: sesion el resultado es identico bit a bit. Recalcularlo costaba ~40 s de
#: pandas en cada llamada. Se invalida solo: al abrir una sesion nueva cambia
#: la clave.
_ENGINE_CACHE: dict[tuple, dict] = {}
_ENGINE_CACHE_MAX = 64

#: Lo mismo para el pase estructurado del LLM, que describe esos
#: numeros ya congelados. Se guarda una COPIA porque el llamador
#: reescribe campos (fair_value, targets) sobre el dict.
_LLM_CACHE: dict[tuple, tuple] = {}
_LLM_CACHE_MAX = 64


def _engine_cache_get(ticker: str, reloj: str):
    return _ENGINE_CACHE.get((ticker.upper(), reloj))


def _engine_cache_put(ticker: str, reloj: str, valor: dict) -> None:
    if len(_ENGINE_CACHE) >= _ENGINE_CACHE_MAX:
        # FIFO: la entrada mas vieja se va. Son sesiones cerradas, no hay
        # nada que "expirar" salvo el tamaño.
        _ENGINE_CACHE.pop(next(iter(_ENGINE_CACHE)), None)
    _ENGINE_CACHE[(ticker.upper(), reloj)] = valor


def _engine_scorecard(ticker, info, price):
    """Scorecard de 6 categorías con el ENGINE REAL de Victor (código determinista,
    sin LLM), EXACTAMENTE como él lo tiene:

      build_packet(ticker, Providers(fmp,edgar,finnhub,fred), now) → Packet
        → los 6 especialistas reales de Victor (wbj/specialists/*.run(packet))
        → aggregate (raw_total, confianza) + targets.price_targets

    Si el paquete completo (FMP) no está disponible, cae al camino RÁPIDO de Victor
    (wbj.quick.quick_scorecard sobre el packet EDGAR: Business/Financial/Risk reales,
    las demás N/S — 'sin evidencia no hay número'). Devuelve el dict del engine o
    None si nada pudo calcularse (→ fallback LLM)."""
    import sys
    if _WBJ_ENGINE_PATH not in sys.path:
        sys.path.insert(0, _WBJ_ENGINE_PATH)
    try:
        from datetime import datetime, timezone
        from wbj.config import load_settings
        from wbj.providers.cache import Cache
        from wbj.providers.edgar import EdgarProvider
        from wbj.providers.fmp import FMPProvider
        from wbj.providers.finnhub import FinnhubProvider
        from wbj.providers.fred import FredProvider
        from wbj.packet.builder import Providers, build_packet
        import wbj.specialists.business as _biz
        import wbj.specialists.financial as _fin
        import wbj.specialists.market as _mkt
        import wbj.specialists.risk as _rsk
        import wbj.specialists.technical as _tec
        import wbj.specialists.valuation as _val
        from wbj.cli import _build_packet
        from wbj.targets import price_targets
    except Exception as e:
        print(f"[engine] no disponible (deps/import): {str(e)[:160]}")
        return None

    # settings + inyección de claves desde el entorno (Render) si el .env no las tomó
    try:
        settings = _engine_settings()
    except Exception as e:
        print(f"[engine] load_settings falló: {str(e)[:120]}"); return None
    cache = Cache(settings.cache_dir)

    _LABEL = {k: WBJ_CATEGORIES[k]["label"] for k in WBJ_ORDER}
    _MODS = [("business", _biz), ("financial", _fin), ("market", _mkt),
             ("technical", _tec), ("risk", _rsk), ("valuation", _val)]

    categories = {}; raw_total = 0.0; conf_num = 0.0; incomplete = []
    used_specialists = False
    _victor_gates = None; _victor_contradictions = None; _victor_levels = None   # aggregate REAL de Victor (principal)
    _qual_prov = {}                 # linaje de los inputs cualitativos (fuente/clase/proxy)
    _victor_final_objs = None   # objetos crudos para el reporte final conforme a schema (apéndice de auditoría)
    _outputs_ai = None          # outputs fusionados CON judge (versión del panel "Juicio AI", NO el score principal)
    _ai_judgment = None         # scorecard con judge ya armado (categories/raw_total/perfil/recomendación) para el panel
    _quick_evidence = None      # cobertura de evidencia (0-100) del camino rápido de Victor, si se usa
    _pk_staleness = None        # frescura por tipo de dato del packet ACTUAL (DATA_POLICY.md)
    _fmp_annual = None          # fundamentales anuales FMP (los MISMOS que usan los especialistas) para el gráfico de ventas

    # ── CAMINO PRINCIPAL: los 6 especialistas REALES de Victor sobre el Packet completo ──
    try:
        prov = Providers(fmp=FMPProvider(settings, cache), edgar=EdgarProvider(settings, cache),
                         finnhub=FinnhubProvider(settings, cache), fred=FredProvider(settings, cache))
        pk = build_packet(ticker, prov, datetime.now(timezone.utc))
        # El reloj congelado del packet identifica la sesion. Mismo ticker +
        # misma sesion cerrada => mismo resultado; devolverlo tal cual ahorra
        # los ~40 s de los seis especialistas.
        _reloj = str(getattr(getattr(pk, "analysis", None), "market_timestamp", "") or "")
        if _reloj:
            _hit = _engine_cache_get(ticker, _reloj)
            if _hit is not None:
                print(f"[engine] {ticker}: scorecard servido de cache ({_reloj})")
                return _hit
        try:
            _fmp_annual = (getattr(pk, "fundamentals", {}) or {}).get("annual") or None
        except Exception:
            _fmp_annual = None
        # DATA_POLICY.md: la frescura del packet ACTUAL (FRESH/STALE por tipo de dato) afecta la
        # confianza y puede pedir RECALC. Victor ya la computa en pk.staleness; la surfaceamos.
        try:
            _pk_staleness = {str(k): str(v) for k, v in (getattr(pk, "staleness", {}) or {}).items()}
        except Exception:
            _pk_staleness = None

        # ── ADAPTADOR DE INDUSTRIA: el builder de Victor fija 'default_nonfinancial' para TODO.
        #    Para bancos/aseguradoras/REITs las fórmulas de ROIC/moat no aplican; si se deja el
        #    default, Business puntúa con confianza ALTA (model_fit 90) y SIN la advertencia de
        #    Victor. Fijamos el adaptador según el sector real (dato, no lógica): así se dispara
        #    su propia advertencia (business.py L975) y baja la confianza a 40 (L1215). ──
        #    IMPORTANTE: usar la INDUSTRIA granular, no el sector. El sector 'Financial Services'
        #    incluye a Visa/Mastercard/Moody's/exchanges (modelos NO-financieros SÍ aplican); solo
        #    los negocios de balance (bancos/aseguradoras/REITs/hipotecas) rompen los modelos y
        #    Victor los marca como adaptador no soportado (Valuation → N/S, sin WACC).
        try:
            _industry = (info.get("industry") or "").strip().lower()
            _adapter = None
            if ("bank" in _industry) or ("insurance" in _industry) or ("mortgage" in _industry):
                _adapter = "financials"
            elif "reit" in _industry:
                _adapter = "reits"
            if _adapter:
                pk = pk.model_copy(update={
                    "analysis": pk.analysis.model_copy(update={"industry_adapter": _adapter})})
                print(f"[engine] {ticker}: industria '{info.get('industry')}' → industry_adapter='{_adapter}' "
                      f"(negocio de balance: Valuation N/S y Business con advertencia, como define Victor)")
        except Exception as _ae:
            print(f"[engine] ajuste de adaptador de industria omitido: {str(_ae)[:120]}")

        # ── SERIE SECTORIAL REAL (ETF SPDR por sector GICS): el builder de Victor fija
        #    market_data.sector = benchmark (SPY) como PROXY documentado, y comenta que el mapa
        #    per-sector ETF (XLK/XLF/XLE…) es la mejora pendiente. Con SPY==sector, MKT-RSG-024
        #    compara SPY vs SPY = 0 (score neutro degenerado) y TECH-RSS-012 (stock vs sector) queda
        #    IDÉNTICO a TECH-RS-011 (stock vs mercado). Inyectamos el ETF sectorial real del sector
        #    de la empresa, construido EXACTAMENTE como Victor arma benchmark_rows (FMP newest-first,
        #    filtrado a las fechas del stock, mismo _ohlcv_row) → RSG/RSS pasan a ser sector-vs-mercado
        #    y stock-vs-sector REALES. Solo cambia el instrumento (SPY→ETF); no se toca su engine. ──
        _SECTOR_ETF = {
            "technology": "XLK", "information technology": "XLK",
            "financial services": "XLF", "financials": "XLF", "financial": "XLF",
            "energy": "XLE", "healthcare": "XLV", "health care": "XLV",
            "industrials": "XLI", "industrial": "XLI",
            "consumer defensive": "XLP", "consumer staples": "XLP",
            "consumer cyclical": "XLY", "consumer discretionary": "XLY",
            "utilities": "XLU", "basic materials": "XLB", "materials": "XLB",
            "real estate": "XLRE", "communication services": "XLC", "communications": "XLC",
        }
        try:
            _sec_name = (info.get("sector") or "").strip().lower()
            _etf = _SECTOR_ETF.get(_sec_name)
            if _etf:                                   # solo si mapea a un ETF sectorial real (≠ SPY)
                from wbj.schemas.packet import OHLCVRow as _OHLCVRow
                _stock_dates = {r.date for r in (pk.market_data.daily or [])}
                _raw_etf = prov.fmp.ohlcv_daily(_etf, today=datetime.now(timezone.utc).date()) or []
                _sec_rows = [_OHLCVRow(date=_b["date"], open=_b["open"], high=_b["high"], low=_b["low"],
                                       close=_b["close"], adj_close=_b.get("adjClose", _b["close"]),
                                       volume=_b["volume"])
                             for _b in _raw_etf
                             if isinstance(_b, dict) and _b.get("date") in _stock_dates
                             and _b.get("close") is not None]
                if len(_sec_rows) >= 64:               # RSG/RSS exigen >63 sesiones de solape
                    pk = pk.model_copy(update={
                        "market_data": pk.market_data.model_copy(update={"sector": _sec_rows})})
                    print(f"[engine] {ticker}: sector '{info.get('sector')}' → ETF {_etf} inyectado como "
                          f"serie sectorial REAL ({len(_sec_rows)} sesiones); RSG/RSS ya no comparan SPY vs SPY")
        except Exception as _se:
            print(f"[engine] inyección de ETF sectorial omitida: {str(_se)[:120]}")

        # ── HANDOFF de Victor: Valuation computa el WACC (VAL-WACC-007) y Business/Financial
        #    lo CONSUMEN vía overlay["wacc"]. Sin este traspaso, todo ROIC/spread/EVA/moat de
        #    Business degrada a MISSING (business ≈ 0). Es el mecanismo que su metodología define
        #    ("mirroring financial.py's overlay['wacc'] precedent" + HANDOFF_CONTRACT). ──
        # La BASE es el overlay canónico del motor — el mismo que usan
        # `run_aggregate`, `wbj report` y la CLI. Antes esto arrancaba en `{}`
        # y la ruta construía sus propias 16 claves a mano, mientras el motor
        # construye 42: la web ni siquiera leía `Entradas/<TICKER>.json`, así
        # que el TAM declarado, la clasificación de moat y la concentración de
        # clientes existían en disco y no llegaban a los especialistas.
        #
        # Por eso el mismo ticker daba dos números el mismo día (motor 44.70,
        # web 39.84 sobre NVDA): −3.94 en Risk y −0.92 en Business, no por una
        # regla distinta sino por hambre de datos. `CLAUDE.md` dice "dos capas,
        # una sola matemática"; esto es lo que lo hacía falso.
        #
        # Las asignaciones propias de la ruta vienen DESPUÉS y siguen ganando:
        # aporta seis claves que el motor no construye (beta, risk_free_rate,
        # equity_issuance, earnings_dates, peer_multiples, sector_breadth), y
        # así se suman en vez de competir.
        try:
            from wbj.overlay.from_packet import build_overlay as _build_overlay
            _overlay = _build_overlay(pk, settings) or {}
        except Exception as _eov:
            print(f"[engine] overlay canónico no disponible: {str(_eov)[:120]}")
            _overlay = {}
        # MISSING_DATA_POLICY.md paso 4: un input de FUENTE PROXY (respaldo) se usa "con un proxy
        # flag y menor confianza". Registramos aquí cada respaldo inyectado para SURFACEARLO
        # (beta y WACC están en la lista de imputación prohibida: nunca se INVENTAN, pero sí se
        # pueden tomar de una fuente secundaria real DECLARÁNDOLO).
        _proxy_inputs = {}
        # Beta SOLO como respaldo si el packet (FMP profile) no lo trae. Victor resuelve
        # beta = overlay.get("beta", packet.capital_structure["beta"]): si inyectáramos SIEMPRE
        # el de yfinance pisaríamos el beta del packet (fuente congelada del análisis) — una
        # inversión de la jerarquía de fuentes. Solo lo aportamos cuando el packet no tiene beta.
        try:
            _pkt_beta = (getattr(pk, "capital_structure", {}) or {}).get("beta")
            if _pkt_beta is None and info.get("beta") is not None:
                _overlay["beta"] = float(info["beta"])
                _proxy_inputs["beta"] = "yfinance (packet sin beta de FMP) — fuente secundaria, menor confianza"
        except Exception:
            pass
        # Risk-free rate: respaldo si el packet (FRED) no lo trae. FRED es OPCIONAL; sin rf,
        # Victor no computa Ke → sin WACC → business/financial colapsan. Victor lee
        # overlay.get("risk_free_rate", packet.estimates["risk_free_rate"]) — mismo punto de
        # inyección que el WACC/beta. Fuente REAL (no inventada): rendimiento del Treasury 10Y
        # (yfinance ^TNX), con normalización de escala y banda de sanidad.
        try:
            _pkt_rf = (getattr(pk, "estimates", {}) or {}).get("risk_free_rate")
            if _pkt_rf is None:
                _rfv = None
                try:
                    _rfv = float(vertex_market.Ticker("^TNX").fast_info.get("lastPrice"))
                except Exception:
                    _htnx = vertex_market.Ticker("^TNX").history(period="5d")
                    if _htnx is not None and not _htnx.empty:
                        _rfv = float(_htnx["Close"].dropna().iloc[-1])
                if _rfv is not None:
                    _rf_dec = _rfv / 100.0 if _rfv > 1.0 else _rfv   # ^TNX cotiza en % (4.25 → 0.0425)
                    if 0.005 <= _rf_dec <= 0.10:                     # banda de sanidad; fuera → no inyectar
                        _overlay["risk_free_rate"] = _rf_dec
                        _proxy_inputs["risk_free_rate"] = f"^TNX 10Y (packet sin FRED) = {_rf_dec:.4f} — fuente secundaria"
                        print(f"[engine] {ticker}: risk_free_rate de respaldo (^TNX 10Y) = {_rf_dec:.4f}")
        except Exception:
            pass
        # Interest expense → overlay. FIN-BS-020 (financial) y RSK-ICOV-011/RSK-FCC-012 (risk)
        # leen interest_expense SOLO por overlay ("overlay-only… pending a future code change"),
        # pero el builder de Victor YA lo mapea a packet.fundamentals como "the natural source for a
        # future overlay-from-packet default". Sin este puente la cobertura de intereses NUNCA se
        # computa y el Override 3 (advertencia de solvencia, MAIN-006) jamás dispara. Lo puenteamos.
        try:
            _annual_f = (getattr(pk, "fundamentals", {}) or {}).get("annual") or []
            if _annual_f:
                # OJO: packet.fundamentals["annual"] se guarda NEWEST-FIRST (convención FMP); el
                # especialista lo invierte (_annual_rows) y usa annual[-1]=reciente. Aquí leemos el
                # packet CRUDO, así que el reciente es [0] (NO [-1], que sería el año más viejo).
                _ie = _annual_f[0].get("interest_expense")
                if _ie is not None and abs(float(_ie)) > 0:
                    _overlay["interest_expense"] = abs(float(_ie))   # magnitud del gasto (la fórmula usa EBIT/|int|)
        except Exception:
            pass
        # equity_issuance → overlay (FIN-CF-016 → Override 1 capital-dependence). DATASET.md lo lista
        # como campo requerido del cash flow ("debt/equity issuance"), pero el builder NO mapea
        # commonStockIssued a packet.fundamentals. Lo tomamos de FMP (fila anual más reciente). SEGURO:
        # externally_dependent solo alimenta el Override 1, que exige net_income<0 Y fcf<0 — la emisión
        # por opciones de una empresa rentable NO puede causar un falso positivo.
        try:
            _cf = prov.fmp.cashflow_annual(ticker, limit=1) or []
            _cfr = _cf[0] if isinstance(_cf, list) and _cf else None
            if isinstance(_cfr, dict):
                _eq = _cfr.get("commonStockIssued")
                if _eq is not None:
                    _overlay["equity_issuance"] = max(0.0, float(_eq))   # solo el lado de emisión (>0)
        except Exception:
            pass
        # MKT-SURP-014 (earnings surprise) del agente MARKET: lee estimates.actual/
        # pre_release_consensus por overlay. FMP earnings_calendar trae eps (actual) y epsEstimated
        # (consenso pre-release, congelado antes del reporte → snapshot_before_release=True honesto).
        # Puenteamos SOLO la sorpresa; las otras revisiones (breadth/magnitude/dispersion) necesitan
        # datos que ninguna API gratuita da → quedan N/S (dict parcial, lectura .get() segura).
        try:
            _ecal = prov.fmp.earnings_calendar(ticker) or []
            _todayd = datetime.now(timezone.utc).date().isoformat()
            _reported = [e for e in _ecal if isinstance(e, dict)
                         and e.get("eps") is not None and e.get("epsEstimated") is not None
                         and str(e.get("date", ""))[:10] <= _todayd]      # solo trimestres YA reportados
            _est_ov = {}
            if _reported:
                _le = max(_reported, key=lambda e: str(e.get("date", "")))   # el reportado más reciente
                _est_ov.update({
                    "actual": float(_le["eps"]),
                    "pre_release_consensus": float(_le["epsEstimated"]),
                    "snapshot_before_release": True})
            # MKT-REVMAG-012: magnitud de revisión = consenso de HOY vs el de nuestro snapshot
            # anterior. Ninguna API da el consenso histórico, así que lo acumulamos nosotros
            # (tabla consensus_snapshots). El primer análisis de un ticker no tiene contra qué
            # comparar → no se inyecta y la métrica queda N/S, que es lo honesto.
            try:
                _est_rows = (getattr(pk, "estimates", {}) or {}).get("fmp_analyst_estimates") or []
                _e0 = _next_year_estimate(_est_rows) or {}
                _eps_now = _est_field(_e0, "epsAvg", "estimatedEpsAvg")
                _rev_now = _est_field(_e0, "revenueAvg", "estimatedRevenueAvg")
                _n_an = _est_field(_e0, "numAnalystsRevenue", "numberAnalystEstimatedRevenue")
                _prior_c = consensus_snapshot(
                    ticker, str(_e0.get("date", ""))[:10] or None,
                    float(_eps_now) if _eps_now is not None else None,
                    float(_rev_now) if _rev_now is not None else None,
                    int(_n_an) if _n_an is not None else None)
                # Solo comparable si es el MISMO año fiscal (si no, el cambio sería de periodo, no revisión)
                if (_prior_c and _eps_now is not None and _prior_c.get("eps_avg")
                        and _prior_c.get("fiscal_date") == (str(_e0.get("date", ""))[:10] or None)):
                    _est_ov["current_consensus"] = float(_eps_now)
                    _est_ov["prior_consensus"] = float(_prior_c["eps_avg"])
                    print(f"[engine] {ticker}: revisión de consenso EPS "
                          f"{_prior_c['eps_avg']:.2f} → {float(_eps_now):.2f}")
            except Exception as _ce:
                print(f"[engine] snapshot de consenso omitido: {str(_ce)[:110]}")
            if _est_ov:
                _overlay["estimates"] = _est_ov
        except Exception:
            pass
        # eps_growth_pct para VALUATION (VAL-PEG-028): crecimiento de EPS de consenso.
        # ESPEJO EXACTO de cómo Victor computa consensus_growth de revenue dentro de
        # valuation.py (fmp_est[0].estimatedRevenueAvg / revenue0 - 1), aplicado a EPS
        # (estimatedEpsAvg / eps0 - 1) con la MISMA fuente (packet.estimates.fmp_analyst_estimates)
        # y el MISMO índice [0]=próximo año. Solo se puentea si el crecimiento es POSITIVO:
        # FORMULAS.md dice que PEG "not meaningful for negative earnings or unstable growth", y un
        # eps_growth<0 produciría un PEG negativo que el anchor puntuaría erróneamente como "barato".
        try:
            _fest = (getattr(pk, "estimates", {}) or {}).get("fmp_analyst_estimates") or []
            _af_pk = (getattr(pk, "fundamentals", {}) or {}).get("annual") or []
            _eps0 = _af_pk[0].get("eps") if _af_pk and isinstance(_af_pk[0], dict) else None
            _ny = _next_year_estimate(_fest)
            if _ny and _eps0 not in (None, 0):
                _eps_next = _est_field(_ny, "epsAvg", "estimatedEpsAvg")
                if _eps_next is not None:
                    _g_eps = float(_eps_next) / float(_eps0) - 1.0
                    if _g_eps > 0:
                        _overlay["eps_growth_pct"] = _g_eps
        except Exception:
            pass
        # historical_multiples para VALUATION (VAL-ZHIST-035 → dimensión Historical/peer, 2 pts):
        # serie de P/E trailing histórico. "Igual que Victor": P/E = price/eps (la MISMA definición
        # que usa valuation.py para el trailing_pe actual, L589). Tomamos el cierre de FMP en cada
        # cierre fiscal (fundamentals.annual[i].date) sobre el eps de ese año (>0, mismo guard que el
        # P/E actual). hist_zscore compara el P/E de hoy contra la MEDIANA robusta de esta historia.
        # Sin esto la dimensión Historical/peer queda NOT_SCORABLE. DEBE construirse antes de _vo.
        try:
            _px_hist = prov.fmp.ohlcv_daily(ticker, years=6, today=datetime.now(timezone.utc).date()) or []
            # cierres en orden ASCENDENTE por fecha (FMP los da newest-first)
            _closes = sorted(((str(_b.get("date"))[:10], _b.get("close")) for _b in _px_hist
                              if isinstance(_b, dict) and _b.get("date") and _b.get("close") is not None),
                             key=lambda _x: _x[0])
            _pe_hist = []
            for _yr in ((getattr(pk, "fundamentals", {}) or {}).get("annual") or []):
                if not isinstance(_yr, dict):
                    continue
                _fd = str(_yr.get("date") or "")[:10]
                _epsy = _yr.get("eps")
                if not _fd or _epsy is None:
                    continue
                try:
                    _epsy = float(_epsy)
                except (TypeError, ValueError):
                    continue
                if _epsy <= 0:                 # P/E no significativo con EPS<=0 (igual que el trailing_pe actual)
                    continue
                # último cierre EN o ANTES del cierre fiscal = el precio "as-of" ese fin de año
                _cl = next((_c for (_d, _c) in reversed(_closes) if _d <= _fd), None)
                if _cl is not None and float(_cl) > 0:
                    _pe_hist.append(float(_cl) / _epsy)
            if len(_pe_hist) >= 3:             # mediana/MAD robustos requieren varios puntos
                _overlay["historical_multiples"] = _pe_hist
        except Exception:
            pass
        # peer_multiples para VALUATION (VAL-REL-034 → ensemble VAL-ENSEMBLE-044): lista de P/S
        # (Price/Sales) de los pares. "Igual que Victor": su valuation.py hace
        #   relative_value = median(peer_multiples) * revenue0 / diluted_shares
        # → peer_multiples = mktCap_par / revenue_par (P/S en base equity). Fuente: FMP profile
        # (mktCap) + income_annual (revenue) del MISMO stock-peers que usa el bridge peer_roic
        # (income queda cacheado y lo reusa peer_roic → sin doble llamada de red). Alimenta el
        # modelo RELATIVE del ensemble (peso 0.5) → dispersión/confianza. DEBE ir antes de _vo.
        try:
            _pm_raw = prov.fmp.peers(ticker) or []
            _pm_list = (_pm_raw[0].get("peersList") if isinstance(_pm_raw, list) and _pm_raw
                        and isinstance(_pm_raw[0], dict) else _pm_raw) or []
            _pmults = []
            for _ppt in list(_pm_list)[:_FMP_MAX_PEERS]:
                try:
                    if not _ppt or str(_ppt).upper() == ticker.upper():
                        continue
                    _prof = prov.fmp.profile(_ppt)
                    _p0 = (_prof[0] if isinstance(_prof, list) and _prof
                           else _prof if isinstance(_prof, dict) else {}) or {}
                    _mc = _p0.get("mktCap") or _p0.get("marketCap")
                    _pinc = prov.fmp.income_annual(_ppt, limit=1) or []
                    _prev = (_pinc[0].get("revenue") if isinstance(_pinc, list) and _pinc
                             and isinstance(_pinc[0], dict) else None)
                    if _mc is not None and _prev not in (None, 0):
                        _ps = float(_mc) / float(_prev)
                        if _ps > 0:
                            _pmults.append(_ps)
                except Exception:
                    continue
            if len(_pmults) >= 8:              # umbral MIN_PEERS de Victor
                _overlay["peer_multiples"] = _pmults
            # ── MKT-SECB-023 (market) + TECH-BREAD-039 (technical): PANEL DE CONSTITUYENTES.
            # El Packet solo trae el ÍNDICE del sector, no sus miembros, así que Victor dice
            # explícitamente que esto solo puede venir del overlay. Usamos los stock-peers de FMP
            # como panel del sector (proxy DECLARADO: son los comparables del mismo sector, no la
            # lista completa del índice) y contamos cuántos cotizan sobre su SMA50/SMA200 con el
            # mismo ohlcv_daily del packet. Sin miembros válidos → no se inyecta (queda N/S).
            try:
                # Cierres del BENCHMARK del packet (ascendente) — base de la fuerza relativa:
                # RS_n = ROC_n(acción) − ROC_n(benchmark), la misma definición de indicators.py.
                _bench_cl = [float(getattr(_r, "close")) for _r in
                             sorted((getattr(getattr(pk, "market_data", None), "benchmark", None) or []),
                                    key=lambda _r: str(getattr(_r, "date", "")))
                             if getattr(_r, "close", None) is not None]
                def _roc(_series, _n):
                    if len(_series) <= _n or _series[-1 - _n] <= 0:
                        return None
                    return _series[-1] / _series[-1 - _n] - 1.0
                _RSW = {"RS21": 21, "RS63": 63, "RS126": 126, "RS252": 252}
                _b50 = _b200 = _bval = 0
                _rs_rows = []
                for _bpt in list(_pm_list)[:_FMP_MAX_BREADTH]:
                    if not _bpt or str(_bpt).upper() == ticker.upper():
                        continue
                    _bars = prov.fmp.ohlcv_daily(_bpt, years=2, today=datetime.now(timezone.utc).date()) or []   # cacheado 1d por el provider
                    # FMP entrega newest-first → ordenar ASCENDENTE por fecha antes de las medias
                    _cl = [float(_c) for _, _c in
                           sorted(((str(b.get("date"))[:10], b.get("close")) for b in _bars
                                   if isinstance(b, dict) and b.get("date") and b.get("close") is not None),
                                  key=lambda _x: _x[0])]
                    if len(_cl) < 200:
                        continue                      # sin 200 sesiones no se puede decidir la SMA200
                    _last = _cl[-1]
                    _sma50 = sum(_cl[-50:]) / 50.0
                    _sma200 = sum(_cl[-200:]) / 200.0
                    _bval += 1
                    if _last > _sma50:
                        _b50 += 1
                    if _last > _sma200:
                        _b200 += 1
                    # TECH-RSC-013: fila del universo con la RS del par en las 4 ventanas.
                    # Solo se incluye si TODAS son computables — una fila a medias sesgaría el percentil.
                    if _bench_cl:
                        _row = {}
                        for _k, _n in _RSW.items():
                            _rp, _rb = _roc(_cl, _n), _roc(_bench_cl, _n)
                            if _rp is None or _rb is None:
                                _row = None
                                break
                            _row[_k] = _rp - _rb
                        if _row:
                            _rs_rows.append(_row)
                # TECH-RSC-013 (percentil compuesto de fuerza relativa): el engine necesita el
                # universo PUNTO-EN-EL-TIEMPO, que el Packet no trae. Se arma con los mismos peers.
                if len(_rs_rows) >= 5:               # con menos de 5, el percentil no dice nada
                    _overlay["rs_universe"] = _rs_rows
                    _proxy_inputs["rs_universe"] = (
                        f"Universo de fuerza relativa = {len(_rs_rows)} stock-peers de FMP "
                        "(proxy declarado: comparables del sector, no el universo completo del mercado)")
                    print(f"[engine] {ticker}: universo RS con {len(_rs_rows)} pares (21/63/126/252d)")
                if _bval > 0:
                    _overlay["sector_breadth"] = {"above_50dma": _b50, "above_200dma": _b200,
                                                  "valid_members": _bval}
                    _proxy_inputs["sector_breadth"] = (
                        f"Panel del sector = {_bval} stock-peers de FMP (proxy declarado: comparables "
                        "del mismo sector, no la lista completa del índice sectorial)")
                    print(f"[engine] {ticker}: breadth del sector = {_b50}/{_bval} sobre SMA50, "
                          f"{_b200}/{_bval} sobre SMA200")
            except Exception as _be:
                print(f"[engine] sector breadth omitido: {str(_be)[:120]}")
        except Exception:
            pass
        # TECH-GAP-020/GHOLD-021 (dimensión earnings-gap) + anchors de AVWAP del agente TECHNICAL:
        # leen overlay["earnings_dates"] = fechas de la SESIÓN del gap, que deben EXISTIR en el OHLCV
        # del packet. El engine NO infiere amc/bmo; espera la sesión ya resuelta. Resolución
        # CONSERVADORA contra las fechas del packet: bmo→misma sesión, amc→sesión siguiente,
        # tiempo desconocido→se OMITE (nunca adivinar → jamás un gap mal mapeado).
        try:
            _pk_days = sorted(str(getattr(r, "date", ""))[:10]
                              for r in (getattr(getattr(pk, "market_data", None), "daily", None) or []))
            _pk_dayset = set(_pk_days)
            _gap_sessions = []
            for e in (_ecal or []):
                if not (isinstance(e, dict) and e.get("eps") is not None):
                    continue                                   # solo eventos YA reportados
                _d = str(e.get("date", ""))[:10]
                if not _d or _d > _todayd:
                    continue
                _t = str(e.get("time") or "").lower()
                if _t == "bmo":
                    _gs = _d if _d in _pk_dayset else None      # gap en la misma sesión
                elif _t == "amc":
                    _gs = next((x for x in _pk_days if x > _d), None)   # gap en la sesión siguiente
                else:
                    _gs = None                                  # tiempo desconocido → omitir
                if _gs and _gs in _pk_dayset:
                    _gap_sessions.append(_gs)
            _gap_sessions = sorted(set(_gap_sessions))
            if _gap_sessions:
                _overlay["earnings_dates"] = _gap_sessions      # el engine exige >=4 para puntuar el gap
        except Exception:
            pass

        # ── RSK-CYC-034 (sensibilidad macro): beta OLS del cambio de una métrica de la EMPRESA
        # contra el cambio de un FACTOR MACRO. Victor exige series ALINEADAS y >=6 observaciones.
        # Empresa: crecimiento YoY de ingresos TRIMESTRALES del packet (el builder trae ~21 tri).
        # Macro: producción industrial (FRED INDPRO), el factor cíclico estándar, en su variación
        # YoY tomada en el trimestre MÁS CERCANO ANTERIOR a cada cierre fiscal — nunca posterior,
        # para no mirar al futuro. Si no se alinean >=6 pares, no se inyecta nada (queda N/S).
        try:
            _q = (getattr(pk, "fundamentals", {}) or {}).get("quarterly") or []
            _qrows = sorted(({"d": str(r.get("date"))[:10], "rev": r.get("revenue")}
                             for r in _q if isinstance(r, dict) and r.get("date") and r.get("revenue")),
                            key=lambda _x: _x["d"])
            _co_growth = []           # (fecha, crecimiento YoY) — 4 trimestres atrás
            for _i in range(4, len(_qrows)):
                _prev = float(_qrows[_i - 4]["rev"]); _cur = float(_qrows[_i]["rev"])
                if _prev > 0:
                    _co_growth.append((_qrows[_i]["d"], _cur / _prev - 1.0))
            if len(_co_growth) >= 6:
                _obs = ((prov.fred.series("INDPRO", limit=180) or {}) or {}).get("observations") or []
                _macro = sorted(((str(o.get("date"))[:10], float(o["value"]))
                                 for o in _obs
                                 if isinstance(o, dict) and o.get("date")
                                 and str(o.get("value", ".")) not in (".", "", "None")),
                                key=lambda _x: _x[0])
                _mdates = [d for d, _ in _macro]
                _mvals = {d: v for d, v in _macro}
                def _macro_yoy(_asof):
                    """INDPRO YoY en la última observación NO POSTERIOR a _asof."""
                    _c = [d for d in _mdates if d <= _asof]
                    if not _c:
                        return None
                    _now_d = _c[-1]
                    _yr_ago = f"{int(_now_d[:4]) - 1}{_now_d[4:]}"
                    _p = [d for d in _mdates if d <= _yr_ago]
                    if not _p or _mvals[_p[-1]] <= 0:
                        return None
                    return _mvals[_now_d] / _mvals[_p[-1]] - 1.0
                _cs, _ms = [], []
                for _d, _g in _co_growth:
                    _mg = _macro_yoy(_d)
                    if _mg is not None:
                        _cs.append(_g); _ms.append(_mg)
                if len(_cs) >= 6:
                    _overlay["company_series"] = _cs
                    _overlay["macro_series"] = _ms
                    _proxy_inputs["macro_series"] = (
                        "Factor macro = producción industrial FRED (INDPRO) en variación YoY, "
                        "alineada al último dato NO POSTERIOR a cada cierre trimestral")
                    print(f"[engine] {ticker}: sensibilidad macro con {len(_cs)} pares "
                          f"(ingresos trimestrales YoY vs INDPRO YoY)")
        except Exception as _mce:
            print(f"[engine] series macro omitidas: {str(_mce)[:120]}")
        # Forense de RISK (AQI-023, DEPI-025, SGAI-026, Beneish M-score RSK-MSCR-029, Altman Z
        # RSK-ALT-030): leen ppe/depreciation/sga (+prior) y retained_earnings por overlay (como
        # interest_expense). El builder de Victor YA los mapea a packet.fundamentals; los leemos de
        # la fila reciente [0] y previa [1] (packet newest-first) — los MISMOS años que usa risk para
        # revenue/assets → ratios de Beneish consistentes. ppe_net es proxy documentado (net vs gross).
        try:
            _af = (getattr(pk, "fundamentals", {}) or {}).get("annual") or []
            _r0 = _af[0] if len(_af) >= 1 else {}
            _r1 = _af[1] if len(_af) >= 2 else {}
            def _fnum(_row, _key):
                _v = _row.get(_key) if isinstance(_row, dict) else None
                try:
                    return float(_v) if _v is not None else None
                except (TypeError, ValueError):
                    return None
            for _okey, _fk, _row in (("ppe", "ppe_net", _r0), ("ppe_prior", "ppe_net", _r1),
                                     ("depreciation", "depreciation_and_amortization", _r0),
                                     ("depreciation_prior", "depreciation_and_amortization", _r1),
                                     ("sga", "sga", _r0), ("sga_prior", "sga", _r1),
                                     ("retained_earnings", "retained_earnings", _r0)):
                _fv = _fnum(_row, _fk)
                if _fv is not None:
                    _overlay[_okey] = _fv
            if "ppe" in _overlay:
                _proxy_inputs["ppe"] = ("PP&E NETO de FMP como proxy del PP&E bruto que piden Beneish "
                                        "AQI/DEPI (proxy documentado de Victor: ppe_net)")
        except Exception:
            pass
        _vo = None
        try:
            _vo = _val.run(pk, overlay=(_overlay or None))   # valuation primero → su WACC
            _w = getattr(getattr(_vo, "wacc", None), "value", None)
            if _w is not None and float(_w) > 0:
                _overlay["wacc"] = float(_w)
        except Exception as _ve:
            print(f"[engine] handoff de WACC no disponible: {str(_ve)[:120]}")
        if _overlay.get("wacc"):
            print(f"[engine] {ticker}: WACC del handoff = {_overlay['wacc']:.4f} → Business/Financial")
        # margin_of_safety para RISK (RSK-VCOMP): SCORING.md dice "use the valuation-agent
        # packet; do not duplicate valuation score". Tomamos el MISMO VAL-MOS-040 que ya
        # computó valuación (_vo) — no lo recomputamos — para que el componente de valuación
        # dentro de riesgo sea CONSISTENTE con el score de valuación (mismo base_per_share,
        # mismo precio, misma fórmula (value-price)/value). Sin esto RSK-VCOMP queda MISSING.
        try:
            if _vo is not None:
                for _m in (getattr(_vo, "metrics", []) or []):
                    if getattr(_m, "metric_id", "") == "VAL-MOS-040" and getattr(_m, "value", None) is not None:
                        _overlay["margin_of_safety"] = float(_m.value)
                        break
        except Exception:
            pass

        # ── peer_roic para BUSINESS (dimensión Competitive, BUS): ROIC de los pares con
        #    la fórmula EXACTA de Victor (valuation_engine.nopat / invested_capital) sobre
        #    los estados FMP de cada par. Sin esto, media dimensión Competitive queda N/S. ──
        try:
            from wbj.engines import valuation_engine as _ve_roic
            _peers_raw = prov.fmp.peers(ticker) or []
            _plist = (_peers_raw[0].get("peersList") if isinstance(_peers_raw, list) and _peers_raw
                      and isinstance(_peers_raw[0], dict) else _peers_raw) or []
            # Usa las funciones EXACTAS de Victor para que el ROIC de los pares sea
            # IDÉNTICO al que él computa para la empresa (misma tasa efectiva _tax_rate,
            # mismo capital invertido PROMEDIO inicio+fin, misma fórmula roic). Si no,
            # el percentil peer_score compararía cosas distintas.
            from wbj.specialists.business import _tax_rate as _biz_tax_rate, average_invested_capital as _biz_avg_ic
            def _fmp_num(_row, _k):
                _v = _row.get(_k) if isinstance(_row, dict) else None
                try:
                    return float(_v) if _v is not None else None
                except (TypeError, ValueError):
                    return None
            _proics = []
            # Victor's peer_score exige ≥8 pares válidos (SCORING_ENGINE.md); con menos
            # devuelve N/S. Por eso pedimos hasta 15 para asegurar ≥8 tras posibles fallos.
            for _pt in list(_plist)[:_FMP_MAX_PEERS]:
                try:
                    if not _pt or str(_pt).upper() == ticker.upper():
                        continue                      # nunca comparar la empresa contra sí misma
                    _inc = prov.fmp.income_annual(_pt, limit=1) or []
                    _bal = prov.fmp.balance_annual(_pt, limit=2) or []   # año actual + previo (IC promedio)
                    _ir = _inc[0] if isinstance(_inc, list) and _inc else None
                    _cur = _bal[0] if isinstance(_bal, list) and len(_bal) >= 1 else None
                    _prev = _bal[1] if isinstance(_bal, list) and len(_bal) >= 2 else None
                    if not isinstance(_ir, dict) or not isinstance(_cur, dict) or not isinstance(_prev, dict):
                        continue
                    _ebit = _fmp_num(_ir, "operatingIncome")
                    _dc = _fmp_num(_cur, "totalDebt"); _ec = _fmp_num(_cur, "totalStockholdersEquity"); _cc = _fmp_num(_cur, "cashAndCashEquivalents")
                    _dp = _fmp_num(_prev, "totalDebt"); _ep = _fmp_num(_prev, "totalStockholdersEquity"); _cp = _fmp_num(_prev, "cashAndCashEquivalents")
                    if None in (_ebit, _dc, _ec, _dp, _ep):
                        continue
                    # tasa efectiva con el helper de Victor (income_before_tax/income_tax_expense, fallback 0.21)
                    _row_can = {"income_before_tax": _fmp_num(_ir, "incomeBeforeTax"),
                                "income_tax_expense": _fmp_num(_ir, "incomeTaxExpense")}
                    _trate = _biz_tax_rate(_row_can, 0.21)
                    _np = _ve_roic.nopat(_ebit, _trate).value
                    _avg_ic = _biz_avg_ic(_dp, _ep, (_cp or 0.0), _dc, _ec, (_cc or 0.0))   # promedio inicio+fin
                    if _avg_ic.is_null:
                        continue
                    _roic_v = _ve_roic.roic(_np, _avg_ic.value)
                    if _roic_v.is_valid:
                        _proics.append(_roic_v.value)
                except Exception:
                    continue
            if len(_proics) >= 8:                            # umbral de Victor: ≥8 pares o N/S
                _overlay["peer_roic"] = _proics
                print(f"[engine] {ticker}: peer_roic de {len(_proics)} pares → Business (Competitive)")
            elif _proics:
                print(f"[engine] {ticker}: solo {len(_proics)} pares con ROIC (<8) → "
                      f"Competitive cae a reglas absolutas (peer_score N/S, como define Victor)")
        except Exception as _pe:
            print(f"[engine] peer_roic omitido: {str(_pe)[:120]}")

        # ── Inputs CUALITATIVOS del 10-K (fiel al sub-agente business de Victor): una sola
        #    extracción con Claude que devuelve TODO lo divulgado. Se consumen por dimensión.
        #    DURABILITY: recurring_revenue + largest_customer_share (+ customer_shares).
        #    MANAGEMENT: guidance_history (precisión de guía = actual vs punto medio guiado).
        #    CUSTOMER ECONOMICS: retention (cohorte NRR/GRR) + churn (logos) + customer_economics
        #    (LTV/CAC/payback) — solo empresas de suscripción los divulgan; si no, N/S correcto. ──
        _qual = {}
        try:
            _cik_biz = prov.edgar.cik_for(ticker)
            # ingreso anual del packet como cota de sanidad para el recurring_revenue del 10-K
            _rev_hint = None
            try:
                _rv = pk.facts_table.get("revenue")
                _rev_hint = float(_rv.value) if _rv is not None and getattr(_rv, "value", None) else None
            except Exception:
                _rev_hint = None
            _qual = _wbj_extract_business_qual(ticker, _cik_biz, settings, revenue_hint=_rev_hint) or {}
        except Exception as _qe:
            print(f"[engine] extracción cualitativa omitida: {str(_qe)[:120]}")
        for _qk in ("recurring_revenue", "largest_customer_share", "customer_shares", "segment_shares",
                    "guidance_history", "retention", "churn", "customer_economics"):
            if _qual.get(_qk) is not None:
                _overlay[_qk] = _qual[_qk]
        # Procedencia de cada input cualitativo (fuente, clase de evidencia, si es
        # proxy). DATA_POLICY.md exige linaje: sin esto un proxy registrado se leeria
        # igual que un dato reportado. No entra al overlay — es solo para el reporte.
        _qual_prov = _qual.get("__provenance__") or {}

        # ── Fase A: correr los 6 especialistas (con overlay wacc/peer_roic) y recoger sus outputs ──
        #
        # El perfil de QUIEN pidió el análisis, para el especialista de riesgo.
        # Su `PROFILE` se resuelve al importar el módulo y por tanto es el mismo
        # para todo el proceso: sin esto, `profile_fit` le contaría a cada
        # usuario su análisis con el capital y el horizonte de Kevin.
        _rsk.PERFIL_ACTUAL.set(_perfil_para_el_engine())
        _outputs = []                       # [(key, output)] en orden, para el judge y el merge
        for key, mod in _MODS:
            try:
                out = _vo if (key == "valuation" and _vo is not None) else mod.run(pk, overlay=(_overlay or None))
                _outputs.append((key, out))
            except Exception as _es:
                categories[key] = {"key": key, "label": _LABEL[key], "max": WBJ_CATEGORIES[key]["max"],
                    "score10": None, "points": None, "coverage": 0.0, "status": "not_scorable",
                    "confidence": None, "reason": f"no se pudo analizar ({type(_es).__name__})"}
                incomplete.append(key)

        # ── Fase B: JUDGE de Victor — Claude responde lo CUALITATIVO que el código no puede
        #    puntuar (moat, catalizadores, concentración, thesis-killers, tier de TAM).
        #    IMPORTANTE (decisión de producto): el judge NO toca el score PRINCIPAL. El score
        #    principal es 100% determinista ("sin evidencia, no hay número" — regla de Victor):
        #    sale SOLO de los outputs deterministas de los 6 especialistas. La versión CON judge
        #    se calcula APARTE (_outputs_ai) y vive en el panel "Juicio AI" (sc["ai_judgment"]).
        #    Necesita ANTHROPIC_API_KEY; si falla o no hay key, no hay panel Juicio AI (nada más). ──
        if getattr(settings, "anthropic_api_key", None) and _outputs:
            try:
                from wbj.overlay.merge import collect_requests, merge_overlay
                from wbj.judge import answer_judgments
                _outs = [o for _, o in _outputs]
                _reqs = collect_requests(_outs)
                # `outputs` va explícito: sin él el juez solo veía 5 números crudos del
                # packet, y las preguntas del gate cuantitativo (moat) afirman que sus
                # entradas están "computadas arriba" — viven en estos outputs.
                _judgments = answer_judgments(pk, _reqs, settings, outputs=_outs)
                if _judgments:
                    _merged = merge_overlay(_outs, _judgments)
                    _outputs_ai = [(_outputs[i][0], _merged[i]) for i in range(len(_outputs))]
                    print(f"[engine] {ticker}: judge respondió {len(_judgments)}/{len(_reqs)} preguntas "
                          f"cualitativas → panel 'Juicio AI' (el score PRINCIPAL sigue determinista)")
                else:
                    print(f"[engine] {ticker}: judge sin respuestas (sin SDK/key válida o sin preguntas)")
            except Exception as _je:
                print(f"[engine] judge omitido (el análisis determinista es el principal): {str(_je)[:140]}")

        # ── Fase C: construir el scorecard PRINCIPAL desde los outputs DETERMINISTAS (sin judge) ──
        for key, out in _outputs:
            cat = out.category
            cov = out.coverage if out.coverage is not None else 0.0
            s10 = round(cat.score_10, 1) if cat.score_10 is not None else None
            categories[key] = {
                "key": key, "label": _LABEL[key], "max": cat.max_points,
                "score10": s10,
                "points": round(cat.awarded_points, 2) if cat.awarded_points is not None else None,
                "coverage": round(cov, 2), "status": "scored" if s10 is not None else "not_scorable",
                "confidence": round(cat.confidence) if cat.confidence is not None else None,
                # banderas obligatorias que emite el especialista (p.ej. VALUE_DESTRUCTION,
                # CONCENTRATION_RED_FLAG, DILUTION_RED_FLAG) — insumo de los overrides del principal
                "mandatory_flags": list(getattr(out, "mandatory_flags", []) or []),
                "reason": None if s10 is not None else "cobertura insuficiente (sin evidencia, no hay número)"}
            if s10 is not None:
                raw_total += cat.awarded_points or 0.0
            else:
                incomplete.append(key)
            # confianza total (Victor, core.confidence.total_confidence): Σ(max_i × conf_i)/100,
            # acumulando TODA categoría con confianza (no solo las puntuadas) — así una categoría
            # N/S penaliza la confianza total en vez de ignorarse.
            if cat.confidence is not None:
                conf_num += cat.max_points * cat.confidence
        used_specialists = any(c["status"] == "scored" for c in categories.values())
        if used_specialists:
            print(f"[engine] {ticker}: 6 especialistas reales de Victor OK "
                  f"({sum(1 for c in categories.values() if c['status']=='scored')}/6 con datos)")

        # ── AGREGACIÓN REAL de Victor (agente principal): apply_overrides (8 overrides
        #    obligatorios) + apply_gates + contradictions, alimentados con los 6 OUTPUT
        #    OBJECTS reales (no mi _wbj_gates). Solo si los 6 especialistas corrieron. ──
        try:
            _obk = {k: o for k, o in _outputs}
            if all(k in _obk for k in WBJ_ORDER):
                from wbj.aggregate import (AggregateInputs, apply_overrides, CategoryPoints,
                    CategoryConfidences, apply_gates, raw_total as _v_rawtot, contradictions,
                    CategoryScore10s, validate_handoff)
                # HANDOFF_CONTRACT.md: el agente principal valida el traspaso de cada especialista
                # (puntos reproducibles, timestamp, confianza/cobertura, flags, niveles, escenarios).
                _handoff_issues = {}
                for _hk in WBJ_ORDER:
                    _hr = validate_handoff(_obk[_hk])
                    if _hr:
                        _handoff_issues[_hk] = _hr
                if _handoff_issues:
                    print(f"[engine] {ticker}: handoff con observaciones: {_handoff_issues}")
                _ai = AggregateInputs(
                    business=_obk["business"], financial=_obk["financial"], market=_obk["market"],
                    technical=_obk["technical"], risk=_obk["risk"], valuation=_obk["valuation"],
                    facts_table=(getattr(pk, "facts_table", {}) or {}))
                _ovr = apply_overrides(_ai)                       # ← 8 overrides reales de Victor
                def _P(k):
                    _p = _obk[k].category.awarded_points
                    return float(_p) if _p is not None else 0.0
                def _Cf(k):
                    _c = _obk[k].category.confidence
                    return float(_c) if _c is not None else 0.0
                _cp = CategoryPoints(**{k: _P(k) for k in WBJ_ORDER})
                _cc = CategoryConfidences(**{k: _Cf(k) for k in WBJ_ORDER})
                _valflags = list(getattr(_obk["valuation"], "mandatory_flags", []) or [])
                # pre_profit: pérdida neta del último año fiscal (del packet), si está disponible
                _preprofit = False
                try:
                    _niv = (getattr(pk, "facts_table", {}) or {}).get("net_income")
                    if _niv is not None and getattr(_niv, "value", None) is not None:
                        _preprofit = float(_niv.value) < 0
                except Exception:
                    pass
                # runway_unfunded: bullet de Speculative "financing runway <12 meses sin fondeo
                # comprometido". La señal REAL de Victor es risk.cash_runway_months (RSK-RUN-015,
                # ya incluye la liquidez comprometida); <12 → dispara Speculative. Antes iba en
                # False por defecto y el bullet nunca se evaluaba.
                _runway_unfunded = False
                try:
                    _rmths = (getattr(_obk["risk"], "liquidity_and_solvency", {}) or {}).get("cash_runway_months")
                    _runway_unfunded = (_rmths is not None and float(_rmths) < 12.0)
                except Exception:
                    pass
                _pr = apply_gates(_v_rawtot(_cp), _cp, _cc, _ovr,
                                  runway_unfunded=_runway_unfunded,
                                  pre_profit=_preprofit, valuation_mandatory_flags=_valflags)
                _victor_gates = {
                    "profile": _pr.label, "band": _pr.descriptive_band,
                    "raw_score": round(_pr.raw_score, 1), "total_confidence": round(_pr.total_confidence),
                    "passed_gates": list(_pr.passed_gates), "failed_gates": list(_pr.failed_gates),
                    "overrides": list(_pr.overrides), "warnings": list(_pr.warnings),
                    "handoff_issues": _handoff_issues}      # validación del traspaso (HANDOFF_CONTRACT)
                # contradicciones reales (score_10 por categoría)
                def _S10(k):
                    _s = _obk[k].category.score_10
                    return float(_s) if _s is not None else 0.0
                _cs10 = CategoryScore10s(**{k: _S10(k) for k in WBJ_ORDER})
                # Row 6 (CONTRADICTION_RESOLUTION.md "DCF high, reverse DCF demanding") solo se
                # evalúa si se pasa el contexto reverse-DCF. Se arma con los números YA congelados
                # de valuación: upside del escenario Base + crecimiento implícito (reverse DCF) vs
                # el crecimiento de referencia (supuesto del escenario Base, fallback documentado).
                _rdcf_ctx = None
                try:
                    from wbj.aggregate.contradiction import ReverseDCFContext
                    _base_ps = _base_growth = None
                    for _s in (getattr(_obk["valuation"], "scenarios", None) or []):
                        if getattr(_s, "name", "") == "Base":
                            _base_ps = _s.per_share_value
                            _base_growth = (_s.assumptions or {}).get("growth")
                    _implied = getattr(getattr(_obk["valuation"], "reverse_dcf", None), "implied_revenue_cagr", None)
                    if _base_ps and price and _implied is not None and _base_growth is not None:
                        _rdcf_ctx = ReverseDCFContext(
                            base_case_upside_pct=(float(_base_ps) - float(price)) / float(price),
                            reverse_dcf_implied_growth=float(_implied),
                            reference_growth=float(_base_growth))
                except Exception as _rce:
                    print(f"[engine] contexto reverse-DCF (row 6) omitido: {str(_rce)[:100]}")
                _contras = contradictions(_cs10, _v_rawtot(_cp), reverse_dcf=_rdcf_ctx)
                _victor_contradictions = [
                    {"combination": _c.combination, "label": getattr(_c, "label", None),
                     "interpretation": getattr(_c, "interpretation", None)}
                    for _c in _contras]
                # ── SÍNTESIS DE NIVELES DE PRECIO real de Victor (PRICE_LEVEL_SYNTHESIS.md):
                #    12 clases de nivel (soporte/resistencia/MAs/aVWAP/gaps/bandas de valor) +
                #    zonas de confluencia, desde technical.important_levels + valuation.reference_bands. ──
                try:
                    from wbj.aggregate import synthesize_levels
                    _atr = getattr(getattr(_obk["technical"], "indicators", None), "atr14", None)
                    if _atr and _atr > 0 and price:
                        _ls = synthesize_levels(_obk["technical"], _obk["valuation"], float(price), float(_atr))
                        _victor_levels = _ls.model_dump()
                        # Objetos crudos para el reporte final conforme a schema. formula_versions
                        # se recogen de las filas de métrica REALES de los 6 outputs (no inventadas).
                        _fv_formulas = sorted({f"{getattr(m, 'formula_id', '')}@{getattr(m, 'formula_version', '')}"
                                               for _o in (_obk[k] for k in WBJ_ORDER)
                                               for m in (getattr(_o, "metrics", []) or [])
                                               if getattr(m, "formula_id", None)})
                        _victor_final_objs = {
                            "inputs": _ai, "profile": _pr, "contradictions": _contras, "levels": _ls,
                            "packet_hash": getattr(pk, "packet_hash", "") or "",
                            "exchange": getattr(getattr(pk, "security", None), "exchange", "") or "",
                            "currency": getattr(getattr(pk, "security", None), "reporting_currency", "") or "",
                            "formula_versions": _fv_formulas}
                except Exception as _le:
                    print(f"[engine] synthesize_levels omitido: {str(_le)[:120]}")
                print(f"[engine] {ticker}: aggregate REAL de Victor → perfil '{_pr.label}', "
                      f"{len(_ovr)} overrides, {len(_victor_contradictions)} contradicciones, "
                      f"{len((_victor_levels or {}).get('levels', []))} niveles de precio")
        except Exception as _age:
            print(f"[engine] aggregate real de Victor omitido (usa _wbj_gates): {str(_age)[:150]}")
    except Exception as e:
        print(f"[engine] pipeline de especialistas no disponible: {str(e)[:160]}")

    # ── packet EDGAR (dict) para targets + ventas anuales (y fallback rápido) ──
    dict_packet = None
    try:
        dict_packet = _build_packet(ticker)
    except Exception as e:
        print(f"[engine] packet EDGAR (dict) falló: {str(e)[:140]}")

    # ── FALLBACK: camino RÁPIDO de Victor (EDGAR) si los especialistas no dieron nada ──
    if not used_specialists:
        if not dict_packet:
            return None
        try:
            from wbj.quick import quick_scorecard
            qs = quick_scorecard(dict_packet)
            # Victor no computa confianza por categoría en quick; SÍ reporta evidence_points_covered
            # (/100). Ese es su número honesto de "cuánto sabemos" → lo usamos como confianza total
            # en vez de un 50 fijo (que no es de Victor). Baja evidencia → confianza baja → cap a Speculative.
            _quick_evidence = qs.get("evidence_points_covered")
            categories = {}; raw_total = 0.0; conf_num = 0.0; incomplete = []
            for row in qs.get("categories", []):
                k = row.get("key")
                if k not in WBJ_ORDER:
                    continue
                categories[k] = {
                    "key": k, "label": _LABEL.get(k, row.get("label")), "max": row.get("max_points"),
                    "score10": row.get("score10"), "points": row.get("points"),
                    "coverage": row.get("coverage", 0.0), "confidence": None,
                    "status": row.get("status"), "reason": row.get("reason")}
                if row.get("status") == "scored":
                    raw_total += row.get("points") or 0.0
                else:
                    incomplete.append(k)
            # rellena cualquier categoría faltante como N/S (contrato de 6)
            for k in WBJ_ORDER:
                if k not in categories:
                    categories[k] = {"key": k, "label": _LABEL[k], "max": WBJ_CATEGORIES[k]["max"],
                        "score10": None, "points": None, "coverage": 0.0, "status": "not_scorable",
                        "confidence": None, "reason": "motor pendiente sin FMP (N/S)"}
                    incomplete.append(k)
            print(f"[engine] {ticker}: camino RÁPIDO de Victor (EDGAR) — "
                  f"{sum(1 for c in categories.values() if c['status']=='scored')}/6 con datos")
        except Exception as e:
            print(f"[engine] quick_scorecard falló: {str(e)[:160]}")
            return None

    if not categories:
        return None

    # Victor: Σ(category_max_points × confidence) / 100 (÷ el 100 fijo, NO por la suma de
    # máximos puntuados). Una categoría N/S baja la confianza total, como en su metodología.
    # En modo rápido (sin confianza por categoría) usamos evidence_points_covered de Victor.
    if conf_num > 0:
        total_confidence = round(conf_num / 100.0)
    elif _quick_evidence is not None:
        total_confidence = int(round(float(_quick_evidence)))
    else:
        total_confidence = 50
    sc = {"categories": categories, "raw_total": round(raw_total, 1),
          "total_confidence": total_confidence, "incomplete": sorted(set(incomplete))}

    # ── DCF REAL DE VICTOR (su especialista de valuación) ────────────────────────────
    # El "Vertex DCF" que se mostraba era eps × P/E_objetivo — un múltiplo, no un DCF.
    # El DCF de verdad es el FCFF del especialista: valuation.py documenta que
    # "scenarios() IS the FCFF DCF model for the base case", así que el valor por acción
    # del escenario Base ES el DCF, y Bear/Bull son sus bandas con supuestos declarados.
    try:
        if _vo is not None:
            _scn = []
            for _s in (getattr(_vo, "scenarios", None) or []):
                _psv = getattr(_s, "per_share_value", None)
                if _psv is not None:
                    _scn.append({"name": getattr(_s, "name", ""), "per_share_value": round(float(_psv), 2),
                                 "assumptions": dict(getattr(_s, "assumptions", None) or {})})
            if _scn:
                _base = next((s["per_share_value"] for s in _scn if s["name"] == "Base"), None)
                _wv = getattr(getattr(_vo, "wacc", None), "value", None)
                # VAL-SCEN-036: sum(prob x valor). DECISION_RULES.md exige que el reporte
                # muestre CADA escenario Y el ponderado — "it does not show only the average".
                _wgt = getattr(_vo, "scenario_weighted_value", None)
                sc["victor_valuation"] = {
                    "dcf_per_share": _base,          # el FCFF base = el DCF de Victor
                    "weighted_per_share": round(float(_wgt), 2) if _wgt is not None else None,
                    "scenarios": _scn,
                    "wacc": round(float(_wv), 4) if _wv is not None else None,
                    "model": "FCFF DCF (especialista de valuación de Victor)",
                    "note": ("Valor intrínseco por acción. Es OTRA cosa que el target a 12 meses: "
                             "el DCF dice cuánto vale el negocio hoy; el target, a cuánto podría "
                             "cotizar en un año si el mercado paga el mismo múltiplo.")}
    except Exception as _dcfe:
        print(f"[engine] DCF de Victor no expuesto: {str(_dcfe)[:120]}")

    # ── PANEL "JUICIO AI": scorecard CON judge (NO es el principal). Mismas 6 áreas pero con
    #    la cobertura/score elevados por lo que Claude respondió, más su propio perfil/banda/
    #    recomendación para comparar lado a lado contra el determinista. Solo si el judge corrió.
    #    Reusa la MISMA agregación de Victor (apply_overrides + apply_gates) sobre _outputs_ai. ──
    if _outputs_ai:
        try:
            _ai_cats = {}; _ai_raw = 0.0; _ai_confnum = 0.0; _ai_incomplete = []
            for _k, _o in _outputs_ai:
                _cat = _o.category
                _cov = _o.coverage if _o.coverage is not None else 0.0
                _s10 = round(_cat.score_10, 1) if _cat.score_10 is not None else None
                _ai_cats[_k] = {
                    "key": _k, "label": _LABEL[_k], "max": _cat.max_points, "score10": _s10,
                    "points": round(_cat.awarded_points, 2) if _cat.awarded_points is not None else None,
                    "coverage": round(_cov, 2), "status": "scored" if _s10 is not None else "not_scorable",
                    "confidence": round(_cat.confidence) if _cat.confidence is not None else None,
                    "mandatory_flags": list(getattr(_o, "mandatory_flags", []) or []),
                    "reason": None if _s10 is not None else "cobertura insuficiente (sin evidencia, no hay número)"}
                if _s10 is not None:
                    _ai_raw += _cat.awarded_points or 0.0
                else:
                    _ai_incomplete.append(_k)
                if _cat.confidence is not None:
                    _ai_confnum += _cat.max_points * _cat.confidence
            _ai_conf = round(_ai_confnum / 100.0) if _ai_confnum > 0 else None
            _ai_judgment = {"categories": _ai_cats, "raw_total": round(_ai_raw, 1),
                            "total_confidence": _ai_conf, "incomplete": sorted(set(_ai_incomplete)),
                            "judge_model": getattr(settings, "judge_model", None)}
            # agregación REAL de Victor sobre los outputs CON judge → perfil/banda/recomendación del panel
            try:
                _aibk = {k: o for k, o in _outputs_ai}
                if all(k in _aibk for k in WBJ_ORDER):
                    from wbj.aggregate import (AggregateInputs, apply_overrides, CategoryPoints,
                        CategoryConfidences, apply_gates, raw_total as _v_rawtot2)
                    _aii = AggregateInputs(
                        business=_aibk["business"], financial=_aibk["financial"], market=_aibk["market"],
                        technical=_aibk["technical"], risk=_aibk["risk"], valuation=_aibk["valuation"],
                        facts_table=(getattr(pk, "facts_table", {}) or {}))
                    _aiovr = apply_overrides(_aii)
                    def _aiP(k):
                        _p = _aibk[k].category.awarded_points
                        return float(_p) if _p is not None else 0.0
                    def _aiCf(k):
                        _c = _aibk[k].category.confidence
                        return float(_c) if _c is not None else 0.0
                    _aicp = CategoryPoints(**{k: _aiP(k) for k in WBJ_ORDER})
                    _aicc = CategoryConfidences(**{k: _aiCf(k) for k in WBJ_ORDER})
                    _aivalflags = list(getattr(_aibk["valuation"], "mandatory_flags", []) or [])
                    _aipreprofit = False
                    try:
                        _niv2 = (getattr(pk, "facts_table", {}) or {}).get("net_income")
                        if _niv2 is not None and getattr(_niv2, "value", None) is not None:
                            _aipreprofit = float(_niv2.value) < 0
                    except Exception:
                        pass
                    _airunway = False
                    try:
                        _rm2 = (getattr(_aibk["risk"], "liquidity_and_solvency", {}) or {}).get("cash_runway_months")
                        _airunway = (_rm2 is not None and float(_rm2) < 12.0)
                    except Exception:
                        pass
                    _aipr = apply_gates(_v_rawtot2(_aicp), _aicp, _aicc, _aiovr,
                                        runway_unfunded=_airunway, pre_profit=_aipreprofit,
                                        valuation_mandatory_flags=_aivalflags)
                    _airec, _aiclass = _wbj_reco_from_profile(_aipr.label)
                    _ai_judgment.update({
                        "profile": _aipr.label, "band": _aipr.descriptive_band,
                        "recommendation": _airec, "classification": _aiclass,
                        "raw_total": round(_aipr.raw_score, 1),
                        "total_confidence": round(_aipr.total_confidence),
                        "passed_gates": list(_aipr.passed_gates), "failed_gates": list(_aipr.failed_gates),
                        "overrides": list(_aipr.overrides), "warnings": list(_aipr.warnings)})
            except Exception as _aiae:
                print(f"[engine] {ticker}: agregación del panel Juicio AI omitida: {str(_aiae)[:130]}")
            sc["ai_judgment"] = _ai_judgment
            print(f"[engine] {ticker}: panel 'Juicio AI' listo — determinista {round(raw_total,1)} vs "
                  f"con-judge {_ai_judgment['raw_total']} (perfil {_ai_judgment.get('profile','—')})")
        except Exception as _aje:
            print(f"[engine] panel Juicio AI omitido: {str(_aje)[:140]}")

    # ── THESIS KILLERS (CLAUDE.md + 05_risk_analysis/DECISION_RULES.md: "Always list at
    #    least three risks that could invalidate the thesis"). Son CONTENIDO, no puntaje:
    #    salen del juez cualitativo, así que viven en los outputs con-judge, pero se
    #    publican en el reporte principal porque el Cerebro los exige ahí. No mueven ni
    #    un punto — el score sigue siendo el determinista. ──
    try:
        _tk = {"risk": [], "business": [], "market": [], "source": None}
        for _k, _o in (_outputs_ai or []):
            if _k == "risk":
                _tk["risk"] = list(getattr(_o, "thesis_killers", None) or [])
            elif _k == "business":
                _tk["business"] = list(getattr(_o, "three_thesis_killers", None) or [])
            elif _k == "market":
                _tk["market"] = list(getattr(_o, "three_growth_thesis_killers", None) or [])
        _n = len(_tk["risk"]) + len(_tk["business"]) + len(_tk["market"])
        if _n:
            _tk["source"] = "juez cualitativo de Victor (clase Q) sobre los packets de los especialistas"
            sc["thesis_killers"] = _tk
            print(f"[engine] {ticker}: thesis killers → riesgo {len(_tk['risk'])}, "
                  f"negocio {len(_tk['business'])}, mercado {len(_tk['market'])}")
        else:
            # Vacío honesto: el motor determinista NUNCA inventa riesgos. Se dice por qué.
            sc["thesis_killers"] = {"risk": [], "business": [], "market": [], "source": None,
                "unavailable_reason": ("Los thesis killers son judgment-only por diseño: el motor "
                    "determinista no inventa riesgos. Requieren el juez cualitativo (ANTHROPIC_API_KEY "
                    "con saldo). Sin él quedan NOT_SCORABLE, no vacíos por error.")}
    except Exception as _tke:
        print(f"[engine] thesis killers omitidos: {str(_tke)[:120]}")

    if _victor_gates:
        sc["victor_gates"] = _victor_gates                 # perfil/banda/overrides reales de Victor
    if _qual_prov:
        # DATA_POLICY.md exige linaje: de dónde salió cada input cualitativo, su clase
        # de evidencia y si es un proxy registrado. Sin esto, un proxy se lee igual que
        # un dato reportado en el filing.
        sc["qual_provenance"] = _qual_prov
    if _victor_contradictions is not None:
        sc["victor_contradictions"] = _victor_contradictions
    if _victor_levels:
        sc["victor_levels"] = _victor_levels               # síntesis de niveles de precio real
    if _victor_final_objs:
        sc["_victor_final"] = _victor_final_objs            # objetos crudos para el reporte final (uso interno)
    if _pk_staleness:
        sc["packet_staleness"] = _pk_staleness             # frescura por tipo de dato (DATA_POLICY.md)
    try:
        if _proxy_inputs:
            sc["proxy_inputs"] = _proxy_inputs             # respaldos de fuente secundaria (MISSING_DATA_POLICY paso 4)
    except NameError:
        pass

    # ── TARGETS + FAIR VALUE de Victor (su targets.py) — deterministas, no del LLM ──
    # TARGETS + FAIR VALUE: preferimos data FMP (actual, consistente con los scores) sobre el EDGAR
    # quick. Construimos el shape que price_targets espera —annual.{net_income,revenue,diluted_shares}
    # como listas {val} ascendentes— desde el MISMO packet FMP de los especialistas. Es crítico tras
    # un split (p.ej. NVDA 10:1 en 2024): el EDGAR quick puede traer utilidades/acciones de otra base
    # y dar un fair value incoherente o no-puntuable. price_targets (targets.py) sigue siendo el de Victor.
    _pt_packet = dict_packet
    if _fmp_annual:
        def _sv(rows, k):
            out = []
            for r in reversed(rows):   # packet newest-first → ascendente
                v = r.get(k) if isinstance(r, dict) else None
                try:
                    if v is not None: out.append({"val": float(v)})
                except (TypeError, ValueError): pass
            return out
        _pt_packet = {"annual": {"net_income": _sv(_fmp_annual, "net_income"),
                                 "revenue": _sv(_fmp_annual, "revenue"),
                                 "diluted_shares": _sv(_fmp_annual, "diluted_shares")}}
    # El MISMO precio contra el que puntuó el engine, no el de yfinance.
    # `price` llega de `/api/analyze`, que lo toma de yfinance: durante la
    # sesión ése es el último print y se mueve, mientras el packet lleva el
    # cierre ajustado ya liquidado (V-05). Con los dos en la misma página, el
    # upside del target se medía contra un precio distinto del que usó la
    # valuación — a las 13:41 UTC de hoy eran 198 y 195.04. Se cae a `price`
    # sólo si el packet no trae precio.
    _pk_price = None
    try:
        _pf = (getattr(pk, "facts_table", None) or {}).get("price")
        if _pf is not None and getattr(_pf, "is_valid", False):
            _pk_price = float(_pf.value)
    except Exception:
        _pk_price = None
    _target_price = _pk_price if _pk_price else price
    if _pt_packet:
        try:
            pt = price_targets(_pt_packet, _target_price)
            if isinstance(pt, dict) and pt.get("status") == "ok":
                sm = {s["key"]: s.get("target") for s in pt.get("scenarios", [])}
                sc["victor_targets_12m"] = {"bull": sm.get("bull"), "base": sm.get("base"), "bear": sm.get("bear")}
                sc["victor_fair_value"] = sm.get("base")      # el target "Medio" ES el fair value de Victor
                # QUÉ es ese número, viajando CON él. La UI lo explica en un
                # tooltip y el PDF no: exportado, "Fair Value: $277" se lee
                # como valor intrínseco cuando es un target a 12 meses por
                # múltiplo — y el DCF del especialista dice otra cosa ($111
                # en NVDA). Ninguna superficie debe publicar uno sin decir
                # cuál es (CONTRADICTION_RESOLUTION.md regla 5).
                sc["victor_fair_value_basis"] = {
                    "label": "Target Base 12M (múltiplo)",
                    "method": "EPS x (1+crecimiento) x (P/E actual x factor)",
                    "horizon": "12 meses",
                    "not": "No es el valor intrínseco del DCF; ése va en victor_valuation.dcf_per_share",
                    "priced_against": _target_price,
                }
                sc["victor_targets_detail"] = pt
            elif isinstance(pt, dict):
                sc["victor_targets_reason"] = pt.get("reason")   # por qué no hay target (se surfacea honesto)
        except Exception as e:
            print(f"[engine] targets de Victor omitidos: {str(e)[:140]}")
    # ── VENTAS ANUALES + CRECIMIENTO para las gráficas ──
    # PREFERIMOS los fundamentales FMP del MISMO packet que usan los 6 especialistas (data actual,
    # años correctos y consistentes con los scores). El packet EDGAR quick (_build_packet) queda solo
    # como RESPALDO: para algunas empresas (p.ej. tras un split, o por drift de tags us-gaap) devuelve
    # años duplicados/truncados o valores mezclados con trimestrales — que es lo que rompía el gráfico.
    try:
        years = []; revenue = []; ni_list = []; op_list = []; gp_list = []
        if _fmp_annual:
            def _num(r, k):
                v = r.get(k) if isinstance(r, dict) else None
                try: return float(v) if v is not None else None
                except (TypeError, ValueError): return None
            _rows = list(reversed(_fmp_annual))[-6:]   # packet newest-first → ascendente, últimos 6 años
            for r in _rows:
                years.append(str(r.get("calendarYear") or str(r.get("date", ""))[:4] or ""))
                revenue.append(_num(r, "revenue"))
                ni_list.append(_num(r, "net_income")); op_list.append(_num(r, "ebit")); gp_list.append(_num(r, "gross_profit"))
        else:
            _a = (dict_packet or {}).get("annual", {}) or {}
            def _ser(key):
                return [(str(r.get("end", ""))[:4], r.get("val")) for r in _a.get(key, []) if r.get("val") is not None][-6:]
            rev = _ser("revenue"); ni = _ser("net_income"); op = _ser("operating_income"); gp = _ser("gross_profit")
            years = [y for y, _ in rev]; revenue = [v for _, v in rev]
            _nib = dict(ni); _opb = dict(op); _gpb = dict(gp)
            ni_list = [_nib.get(y) for y in years]; op_list = [_opb.get(y) for y in years]; gp_list = [_gpb.get(y) for y in years]
        rev_growth = [None] + [round((revenue[i] / revenue[i - 1] - 1) * 100, 1)
                               if (revenue[i] is not None and revenue[i - 1]) else None
                               for i in range(1, len(revenue))]
        cagr = None
        if len(revenue) >= 3 and revenue[0] and revenue[-1] and revenue[0] > 0 and revenue[-1] > 0:
            cagr = round(((revenue[-1] / revenue[0]) ** (1 / (len(revenue) - 1)) - 1) * 100, 1)
        def _mgn(num, den):
            return [round(num[i] / den[i] * 100, 1) if (num[i] is not None and den[i]) else None for i in range(len(den))]
        sc["financials_annual"] = {
            "years": years, "revenue": revenue, "net_income": ni_list,
            "revenue_growth_yoy": rev_growth, "revenue_cagr": cagr,
            "net_margin": _mgn(ni_list, revenue), "operating_margin": _mgn(op_list, revenue),
            "gross_margin": _mgn(gp_list, revenue)}
    except Exception as e:
        print(f"[engine] financials anuales omitidos: {str(e)[:140]}")

    # ── PANELES DE VICTOR para Full Research: "En palabras simples" (su narrative()),
    #    puntaje promedio 1-10 de los agentes, y "Qué significa el puntaje" (su brief.py,
    #    byte-idéntico al suyo: _classification + _category_meaning). Todo determinista. ──
    try:
        # `victor_scorecard` es el QUICK de Victor, no el deep. Se llenaba con
        # `sc["raw_total"]` --el agregado profundo-- y eso hacia MENTIR a su
        # propia narrativa: `targets.py:216` escribe literalmente
        #
        #     f"Quick score: {scorecard['overall_10']}/10, computed from
        #      {covered} of 100 evidence points"
        #
        # o sea el panel decia "Quick score" mostrando el numero del deep. Y
        # el deep ya se ve DOS veces: en el gauge de Raw Score y, con juez, en
        # la pestaña de Juicio AI.
        #
        # En el motor de Victor son dos comandos distintos --`wbj scorecard`
        # (quick) y el analisis profundo-- y este panel es la cara del primero.
        # `quick_scorecard` corre sobre el packet EDGAR, que ya esta armado.
        _victor_sc = None
        if dict_packet:
            try:
                from wbj.quick import quick_scorecard as _qsc
                _q = _qsc(dict_packet)
                _victor_sc = {
                    "overall_10": _q.get("overall_10"),
                    "evidence_points_covered": _q.get("evidence_points_covered"),
                    "categories": [
                        {"key": r.get("key"), "label": r.get("label"),
                         "score10": r.get("score10"), "status": r.get("status"),
                         "reason": r.get("reason")}
                        for r in (_q.get("categories") or [])],
                }
            except Exception as _e:
                print(f"[engine] quick para victor_scorecard fallo: {str(_e)[:120]}")
        if _victor_sc is None:
            # Sin packet EDGAR no hay quick. Se cae al deep antes que dejar el
            # panel vacio, y se DICE cual es -- la interfaz lo rotula.
            _overall10 = round(float(sc["raw_total"]) / 10.0, 1) if sc.get("raw_total") is not None else None
            _evid = 0.0
            _cat_rows = []
            for _k in WBJ_ORDER:
                _c = (sc.get("categories") or {}).get(_k)
                if not _c:
                    continue
                _evid += float(_c.get("max") or 0) * float(_c.get("coverage") or 0)
                _cat_rows.append({"key": _k, "label": _c.get("label"), "score10": _c.get("score10"),
                                  "status": _c.get("status"), "reason": _c.get("reason")})
            _victor_sc = {"overall_10": _overall10,
                          "evidence_points_covered": int(round(_evid)),
                          "categories": _cat_rows, "_es_deep": True}
        sc["victor_scorecard"] = _victor_sc

        # "Qué significa el puntaje" — clasificación (favorece/neutral/evitar) + significado por
        # categoría. _interpretation NO usa el packet (solo el scorecard), por eso va {}.
        try:
            from wbj.brief import _interpretation as _victor_interp
            _nxt_e = None
            try:
                _nd = _next_earnings_date(ticker)
                if _nd:
                    _nxt_e = {"date": _nd.strftime("%Y-%m-%d") if hasattr(_nd, "strftime") else str(_nd)}
            except Exception:
                _nxt_e = None
            sc["victor_interpretation"] = _victor_interp({}, _victor_sc, _nxt_e)
        except Exception as _ie:
            print(f"[engine] interpretación de Victor omitida: {str(_ie)[:120]}")

        # "En palabras simples" — su narrative() tal cual. Necesita el packet anual en su shape
        # {annual: {campo: [{val}, ...] ascendente}}. total_equity del packet → 'equity' que él pide.
        try:
            from wbj.targets import narrative as _victor_narrative
            if _fmp_annual:
                def _sv2(k):
                    out = []
                    for r in reversed(_fmp_annual):        # newest-first → ascendente
                        v = r.get(k) if isinstance(r, dict) else None
                        try:
                            if v is not None: out.append({"val": float(v)})
                        except (TypeError, ValueError): pass
                    return out
                _narr_pk = {"annual": {
                    "revenue": _sv2("revenue"), "net_income": _sv2("net_income"),
                    "operating_cash_flow": _sv2("operating_cash_flow"), "capex": _sv2("capex"),
                    "long_term_debt": _sv2("long_term_debt"), "equity": _sv2("total_equity")}}
                _tg = sc.get("victor_targets_detail") or {"status": "not_scorable"}
                sc["victor_narrative"] = _victor_narrative(_narr_pk, _victor_sc, _tg)
        except Exception as _ne:
            print(f"[engine] narrativa de Victor omitida: {str(_ne)[:120]}")
    except Exception as _pe:
        print(f"[engine] paneles de Victor omitidos: {str(_pe)[:140]}")
    # Guardar bajo el reloj congelado del packet. `_reloj` puede no existir si
    # el packet fallo antes de construirse; en ese caso no hay nada estable
    # que cachear.
    try:
        if _reloj:
            sc["_reloj_packet"] = _reloj      # clave de sesion, la reusa /api/analyze
            _engine_cache_put(ticker, _reloj, sc)
    except NameError:
        pass
    return sc


#: Un análisis a la vez, igual que `engine/scripts/webapp.py` de Victor:
#: *"One analysis at a time: providers share one httpx client/cache."*
#:
#: Su razón vale aquí igual: los cuatro proveedores comparten un `Cache` y un
#: cliente httpx por proceso. Y desde que `/api/analyze` memoiza el scorecard,
#: el pase del LLM y las presentaciones de EDGAR, dos peticiones a la vez
#: competirían además por esos tres diccionarios — la segunda podría leer una
#: entrada a medio escribir, o duplicar 40 s de trabajo que la primera ya está
#: haciendo. Serializar cuesta espera; no serializar cuesta corrección.
_ANALYZE_LOCK = threading.Lock()


def _error_publico(exc: BaseException, contexto: str) -> str:
    """El detalle al log; al navegador, una frase que no revela nada.

    Victor devuelve `str(e)` al cliente en `webapp.py`, y en su caso es
    inofensivo: liga el servidor a `127.0.0.1`, asi que el unico que lee
    esos mensajes es el. Este servicio arranca con `--host 0.0.0.0` en
    Render, de cara a internet, y ahi el mismo texto puede llevar rutas del
    servidor, fragmentos de SQL y -- si algun dia una excepcion de httpx
    escapa de `raise_for_status()` -- la URL completa CON la clave en la
    query. Hoy verifique que ninguna ruta esta en ese ultimo caso; el
    cambio es para que siga siendo cierto sin depender de revisarlo.

    Es su mismo razonamiento aplicado a un contexto distinto, no una
    desviacion de su metodologia.
    """
    logging.getLogger("vertex").warning("%s: %s", contexto, exc, exc_info=True)
    return f"{contexto}: no se pudo completar"


def _error_de_fuente(exc: BaseException, contexto: str) -> str:
    """Como `_error_publico`, pero deja pasar los errores QUE ESCRIBIMOS NOSOTROS.

    `MassiveError` y `MarketSnackError` no llevan excepción de terceros dentro:
    sus mensajes son frases nuestras ("Falta MASSIVE_API_KEY en el entorno",
    "Ticker vacío", el código HTTP con el cuerpo de la respuesta). El centinela
    de §8 de `auditar_tito.py` lo comprueba de verdad: mete una clave falsa en
    el entorno, provoca el fallo por tres caminos y exige que la clave no
    aparezca en ninguno de los mensajes.

    La diferencia importa en pantalla. "Falta MASSIVE_API_KEY" dice qué hacer;
    "no se pudo completar" manda a Kevin a leer logs de Render para descubrir
    que faltaba una variable de entorno. Cualquier otra excepción —una de httpx
    que escapara de `raise_for_status()`, con la URL completa dentro— cae al
    camino ciego de `_error_publico`.
    """
    try:
        from wbj.tito.marketsnack import MarketSnackError
        from wbj.tito.massive import MassiveError
    except Exception:                              # el motor no está instalado
        return _error_publico(exc, contexto)
    if isinstance(exc, (MassiveError, MarketSnackError)):
        logging.getLogger("vertex").warning("%s: %s", contexto, exc)
        return str(exc)
    return _error_publico(exc, contexto)


@app.get("/api/analyze")
def analyze_ticker(ticker: str, explain: bool = False):
    """Análisis completo. `explain=1` añade la explicación en palabras del
    2º pase LLM, que cuesta ~18 s y que NINGUNA pantalla consume hoy
    (`grep wbj_explanation` sobre la plataforma: 0 usos). Se paga sólo si
    alguien la pide."""
    with _ANALYZE_LOCK:
        resultado = _analyze_ticker_serializado(ticker, explain)
    # La amplitud de sector se calienta DESPUES, y fuera del lock.
    #
    # Pedirla durante el analisis disparaba cientos de peticiones a FMP, agotaba
    # el limite y los 429 caian sobre las llamadas del propio ticker -- su
    # estado de flujo de caja y su historico de precios llegaron a fallar
    # detras de esa tormenta. Una metrica de contexto de 3 puntos tumbando las
    # seis categorias.
    #
    # Aqui ya no compite con nada: el usuario tiene su reporte y el hilo
    # trabaja para el SIGUIENTE analisis de ese sector, que la encontrara en
    # cache. Si falla, no se entera nadie y la metrica queda NOT_SCORABLE.
    _calentar_amplitud_en_segundo_plano(ticker)
    return resultado


def _calentar_amplitud_en_segundo_plano(ticker: str) -> None:
    """Deja lista la amplitud del sector de `ticker` para la proxima vez."""
    def _trabajo():
        try:
            sys.path.insert(0, _WBJ_ENGINE_PATH)
            from wbj.config import load_settings
            from wbj.overlay.amplitud_sector import amplitud_de_sector
            from wbj.providers.cache import Cache
            from wbj.providers.fmp import FMPProvider

            s = load_settings()
            f = FMPProvider(s, Cache(s.cache_dir))
            perfil = (f.profile(ticker) or [{}])
            sector = (perfil[0] or {}).get("sector") if isinstance(perfil, list) else None
            if sector:
                amplitud_de_sector(f, sector)
        except Exception:
            logging.getLogger(__name__).info(
                "no se pudo calentar la amplitud de sector", exc_info=True)

    try:
        threading.Thread(target=_trabajo, daemon=True).start()
    except Exception:
        pass


def _analyze_ticker_serializado(ticker: str, explain: bool = False):
    ticker = ticker.upper().strip()
    try:
        stock = vertex_market.Ticker(ticker)
        try:
            info = stock.info or {}
        except Exception:
            info = {}
        hist  = _resilient_history(stock, ticker, "6mo")   # yfinance → respaldo FMP (no se cae)
        if hist is None or hist.empty:
            raise HTTPException(status_code=404, detail="Sin datos")

        hist_1m = _resilient_history(stock, ticker, "1mo")
        if hist_1m is None or hist_1m.empty:
            hist_1m = hist.tail(22)                          # último mes aprox. desde la historia ya obtenida
        precios_hist = [round(float(x), 2) for x in hist_1m['Close'].tolist()]
        fechas_hist  = [x.strftime("%b %d") for x in hist_1m.index.tolist()]
        precio_actual = precios_hist[-1] if precios_hist else round(float(hist['Close'].iloc[-1]), 2)

        # ── OHLC + Volume for candlestick chart (TradingView format) ─────────
        ohlc_hist   = []
        volumen_hist = []
        for idx, row in hist_1m.iterrows():
            t = idx.strftime("%Y-%m-%d")
            ohlc_hist.append({
                "time":  t,
                "open":  round(float(row['Open']), 2),
                "high":  round(float(row['High']), 2),
                "low":   round(float(row['Low']), 2),
                "close": round(float(row['Close']), 2),
            })
            volumen_hist.append({
                "time":  t,
                "value": int(row['Volume']) if not math.isnan(row['Volume']) else 0,
                "up":    bool(row['Close'] >= row['Open']),
            })

        # ── HISTORIAL 1 AÑO para la gráfica de Victor (su price_history: [{time,value}]) ──
        # La gráfica de precio+targets es la SUYA (engine/scripts/webapp.py) y necesita el último
        # año de cierres diarios. Se usa su función tal cual; si Yahoo falla, respaldo resiliente.
        chart_history = []
        try:
            # El engine vive en engine/ y no está instalado como paquete: hay que meterlo
            # en sys.path ANTES de importarlo. Sin esto el import solo funcionaba si
            # _engine_scorecard ya había corrido, y ese corre DESPUÉS — así que en el
            # primer análisis del proceso la gráfica se quedaba sin historial.
            if _WBJ_ENGINE_PATH not in sys.path:
                sys.path.insert(0, _WBJ_ENGINE_PATH)
            from wbj.targets import price_history as _victor_price_history
            chart_history = _victor_price_history(ticker) or []
        except Exception as _phe:
            print(f"[analyze] price_history de Victor falló: {str(_phe)[:120]}")
        if not chart_history:
            try:
                _h1y = _resilient_history(stock, ticker, "1y")
                if _h1y is not None and not _h1y.empty:
                    chart_history = [{"time": _i.strftime("%Y-%m-%d"), "value": round(float(_c), 2)}
                                     for _i, _c in zip(_h1y.index, _h1y['Close'].tolist())
                                     if _c == _c]          # descarta NaN
            except Exception as _h1e:
                print(f"[analyze] respaldo de historial 1a falló: {str(_h1e)[:120]}")

        # ── Earnings date / EPS estimate ─────────────────────────────────────
        earnings_info = fetch_earnings_info(stock, info)
        earnings_hist = fetch_earnings_history(stock)          # #2 — sorpresas + reacción post-earnings
        insiders_snapshot = fetch_insiders_data(stock, ticker)
        insiders_context  = format_insiders_context(insiders_snapshot)
        finnhub_context   = format_finnhub_context(ticker)
        portfolio_fit     = compute_ticker_vs_portfolio(ticker, get_portfolio_snapshot())

        logo_url = obtener_logo(ticker, info.get("website", ""))

        # ── INSTITUTIONAL TARGETS ───────────────────────────────────────────
        institutional = calculate_institutional_targets(ticker, info, hist)
        targets = institutional["targets"]
        methodology = institutional["methodology"]

        # ── NOTICIAS ────────────────────────────────────────────────────────
        raw_news = stock.news if stock.news else []
        noticias_formateadas = []
        for item in raw_news[:6]:
            pub_time = datetime.fromtimestamp(item.get("providerPublishTime", 0)).strftime('%Y-%m-%d %I:%M %p')
            noticias_formateadas.append({
                "title": item.get("title", "No Title"),
                "publisher": item.get("publisher", "Yahoo Finance"),
                "publish_time": pub_time,
                "summary": item.get("summary", "No description available.")
            })

        titulares_contexto, news_catalysts = _news_catalyst_context(noticias_formateadas)   # #3
        sec_8k = fetch_recent_8k(ticker)                                                    # #3 — 8-K reales (SEC EDGAR)
        _8k_block, _8k_tags = _8k_catalyst_block(sec_8k)
        if _8k_tags:
            news_catalysts = sorted(set((news_catalysts or []) + _8k_tags))
        analisis_timestamp = datetime.now().strftime('%m/%d/%Y, %I:%M:%S %p')
        report_id = datetime.now().strftime('%Y%m%d_%H%M%S') + f"_{ticker}"

        # ── LONG-TERM MEMORY: recall the agent's prior report on this ticker ──
        prior_report = get_prior_report(ticker)
        memory_block = build_memory_block(prior_report, precio_actual)
        # #3 — memoria PROFUNDA (últimas N llamadas con su resultado) + posiciones de opciones abiertas
        _deep_mem_block, _deep_mem_meta = _deep_memory_block(ticker, precio_actual)
        _opt_block, _open_opts = _open_options_block(ticker)

        # ── #3 CALIBRACIÓN: historial de aciertos del agente para el prompt ───
        calib_stats = get_calibration_cached()
        calibration_block = ""
        if calib_stats and calib_stats.get("n"):
            _rl = ", ".join(f"{k} {v['hit_rate']}% (n={v['n']})"
                            for k, v in calib_stats["by_recommendation"].items()
                            if v.get("hit_rate") is not None)
            _err = calib_stats.get("avg_upside_error_pct")
            _en = f" Error medio de upside a 180d: {_err:+.1f}% (negativo = histórico sobreoptimista)." if _err is not None else ""
            calibration_block = (
                f"\nTU HISTORIAL DE CALIBRACIÓN (acierto direccional realizado, no opinión): "
                f"global {calib_stats['overall_hit_rate']}% en {calib_stats['n']} evaluaciones; "
                f"por recomendación: {_rl}.{_en} Usa esto para NO sobreestimar tu convicción: "
                f"si tus BUY históricamente aciertan ~X%, no presentes 90% sin evidencia excepcional. Calibra con humildad.")

        # ── #4 RÉGIMEN DE MERCADO: que el LLM razone consciente del entorno ───
        _regime_now = get_regime_cached()
        regime_block = ""
        if _regime_now and _regime_now.get("label"):
            regime_block = (
                f"\nRÉGIMEN DE MERCADO ACTUAL (clasificado por el motor Vertex): {_regime_now['label']}"
                f"{(' · VIX ' + str(_regime_now.get('vix'))) if _regime_now.get('vix') is not None else ''}"
                f"{(' · SPY vs 200d ' + str(_regime_now.get('spy_vs_200d_pct')) + '%') if _regime_now.get('spy_vs_200d_pct') is not None else ''}. "
                f"Ajusta tu lectura al régimen: en estrés/bajista prioriza riesgo, macro y fundamentales y sé escéptico con el momentum; "
                f"en tendencia alcista/calma el flujo institucional y los técnicos pesan más. Refléjalo en la tesis.")

        # Aquí iba la inteligencia de opciones (GEX, walls, gamma flip) y los
        # cuatro bloques de Quant Data (premium neto, convicción por ΔOI,
        # dark pool, net-flow) más los targets de gamma. Todo eso salió del
        # proyecto: Quant Data no es una fuente del sistema y su plan API
        # está inactivo, y las cadenas de opciones venían de Yahoo. Un
        # prompt que describe niveles inexistentes le pide al LLM que
        # razone sobre datos que nadie midió.
        gex_block = ""
        # La calibración SÍ se queda: es el track-record propio del ticker
        # (Reportes/*/prediccion.json), no un dato de opciones.
        try:
            _cal_block = _calibration_prompt_block(ticker)
            if _cal_block:
                gex_block += _cal_block
        except Exception as _ecal:
            print(f"[calibración] bloque omitido: {_ecal}")

        # ── ENGINE DE VICTOR PRIMERO: los NÚMEROS mandan y la narrativa los SIGUE
        #    (FINAL_REPORT_SCHEMA.md: "narrative follows the numbers"). Calculamos el veredicto
        #    determinista ANTES del prompt para que la prosa del LLM no pueda contradecir el gate. ──
        try:
            _eng = _engine_scorecard(ticker, info, precio_actual)
        except Exception as _eov:
            print(f"[analyze] overlay Victor omitido: {_eov}"); _eng = None

        # ── TARGETS = LOS DE VICTOR (su targets.py: EPS × (1+g) × P/E_actual×factor), NO el modelo
        #    de volatilidad/ATR/consenso de Vertex (calculate_institutional_targets). Victor define
        #    UN horizonte: 12 meses. Los cortos (7d/30d/3m/6m) NO existen en su metodología → no se
        #    inventan, se eliminan ("sin evidencia, no hay número"). El Fair Value es su target base.
        #    Se aplica ANTES del prompt para que la narrativa SIGA los números de Victor. ──
        _victor_only_targets = False
        try:
            _vt0 = (_eng or {}).get("victor_targets_12m") or {}
            if all(_vt0.get(k) is not None for k in ("bull", "base", "bear")):
                targets = {"12m": {k: round(float(_vt0[k]), 2) for k in ("bull", "base", "bear")}}
                _victor_only_targets = True
                print(f"[analyze] {ticker}: targets = Victor 12m puro (bull {targets['12m']['bull']} / "
                      f"base {targets['12m']['base']} / bear {targets['12m']['bear']})")
            else:
                print(f"[analyze] {ticker}: sin targets de Victor "
                      f"({(_eng or {}).get('victor_targets_reason') or 'no disponibles'}) → se mantienen los de Vertex")
        except Exception as _vte:
            print(f"[analyze] targets de Victor no aplicados: {str(_vte)[:120]}")
        # Bloque de targets para el prompt: solo los horizontes que REALMENTE existen.
        if _victor_only_targets:
            _targets_block = (
                "PRICE TARGETS YA CALCULADOS (metodología de Victor — usa estos en tu narrativa; NO los modifiques):\n"
                f"- 12M: Bull ${targets['12m']['bull']} | Base ${targets['12m']['base']} | Bear ${targets['12m']['bear']}\n"
                "  (Victor define UN solo horizonte: 12 meses. NO inventes ni cites targets a 7D/30D/3M/6M — "
                "no existen en su metodología.)")
        else:
            _targets_block = ("PRICE TARGETS YA CALCULADOS (usa estos en tu narrativa; NO los modifiques):\n" +
                "\n".join(f"- {_lab}: Bull ${targets[_k]['bull']} | Base ${targets[_k]['base']} | Bear ${targets[_k]['bear']}"
                          for _k, _lab in (("7d", "7D"), ("30d", "30D"), ("3m", "3M"), ("6m", "6M"), ("12m", "12M"))
                          if targets.get(_k)))
        # DCF que ve el LLM: el FCFF del especialista de Victor. El "Vertex DCF" anterior era
        # eps × P/E_objetivo (un múltiplo, no un DCF) y contradecía al fair value del reporte.
        _vval0 = (_eng or {}).get("victor_valuation") or {}
        _victor_dcf_txt = (f"${_vval0['dcf_per_share']} por acción (FCFF base)"
                           if _vval0.get("dcf_per_share") is not None
                           else "no puntuable (sin DCF publicable)")
        _victor_prompt_block = ""
        if _eng and _eng.get("categories"):
            _vg0 = _eng.get("victor_gates") or {}
            _prof0 = _vg0.get("profile")
            if not _prof0:                                   # respaldo: gate sobre las categorías
                try:
                    _prof0 = _wbj_gates({"categories": _eng["categories"], "raw_total": _eng["raw_total"],
                                         "total_confidence": _eng["total_confidence"],
                                         "incomplete": _eng.get("incomplete", [])}).get("profile")
                except Exception:
                    _prof0 = None
            if _prof0:
                _reco0, _clasif0 = _wbj_reco_from_profile(_prof0)
                _victor_prompt_block = (
                    "\nVEREDICTO DETERMINISTA WBJ (motor de Victor — MANDA sobre tu narrativa):\n"
                    f"- Perfil/gate: {_prof0} | Recomendación FINAL: {_reco0} ({_clasif0})\n"
                    f"- Raw score: {_eng.get('raw_total')}/100 | Confianza total: {_eng.get('total_confidence')}/100\n"
                    "Tu prosa (tesis_inversion_completa, should_you_buy_now, the_bottom_line, "
                    "recomendacion_porque, conviccion_porque) DEBE ser COHERENTE con esta recomendación "
                    "FINAL: explícala en palabras simples; NUNCA la contradigas ni propongas la acción "
                    "opuesta. Los targets y el Fair Value son referencia; la clasificación de research la "
                    "fija el gate determinista de Victor, no tú.\n"
                    "La CONVICCIÓN también es la suya: el raw score de arriba ES el puntaje de convicción "
                    "(0-100). No existe ningún motor de convicción ponderado — no inventes ni cites otro "
                    "número de convicción, y en 'conviccion_porque' explica ESE puntaje y de qué agentes "
                    "sale.\n")

        # ── PROMPT GEMINI (AI narrative sobre targets ya calculados) ─────────
        # Sin convicción ni premium neto: eran de Quant Data. Queda el régimen.
        _key_sig = _key_signals_summary(None, None, _regime_now, None)
        _earn_block = _earnings_depth_block(earnings_hist, earnings_info)
        prompt = f"""
Analiza en profundidad la compañía {ticker} ({info.get('longName', ticker)}) con un enfoque institucional para Vertex Holding Group.

{_key_sig}

DATOS DE MERCADO (ya calculados por el motor cuantitativo de Vertex):
- Precio Spot: ${precio_actual} {info.get('currency', 'USD')}
- P/E actual: {info.get('trailingPE', 'N/A')} | Forward P/E: {info.get('forwardPE', 'N/A')}
- Market Cap: {info.get('marketCap', 'N/A')}
- Revenue Growth YoY: {info.get('revenueGrowth', 'N/A')}
- Gross Margins: {info.get('grossMargins', 'N/A')} | EBITDA Margins: {info.get('ebitdaMargins', 'N/A')}
- Free Cash Flow: {info.get('freeCashflow', 'N/A')}
- Beta: {info.get('beta', 'N/A')}
- Analyst Mean Target (Wall Street): ${methodology.get('analyst_mean', 'N/A')}
- DCF (FCFF, especialista de valuación de Victor): {_victor_dcf_txt}
- Annual Volatility: {methodology.get('annual_volatility_pct', 'N/A')}% | ATR-14: ${methodology.get('atr_14', 'N/A')}
- Noticias recientes: {titulares_contexto}
- Proximo Earnings: {('en ' + str(earnings_info['days_until']) + ' dias (' + earnings_info['label'] + ')') if earnings_info.get('days_until') is not None else 'N/A'}{(' | EPS estimado: $' + str(earnings_info['eps_estimate'])) if earnings_info.get('eps_estimate') else ''}
- Actividad de Insiders y Flujo Institucional (SEC/13F): {insiders_context if insiders_context else 'N/A'}
- Contexto Finnhub (15-min | fundamentales/news/sentiment/insiders/congreso): {finnhub_context if finnhub_context else 'N/A'}
- Ajuste con TU portafolio actual (consciencia de cartera): {format_portfolio_fit(portfolio_fit)}
- Riesgo de factores y contribucion marginal al riesgo (estilo BlackRock/Aladdin): {format_factor_risk(portfolio_fit)}
{memory_block}{_deep_mem_block}{_opt_block}{calibration_block}{regime_block}{gex_block}{_earn_block}{_8k_block}

{_targets_block}

INSTRUCCIÓN CRÍTICA:
Basa tu recomendación final, el Fair Value y tu tesis estrictamente en los **targets a futuro de 1 año** calculados y los **targets de Wall Street (Analyst Mean Target)**. NO bases tu recomendación ni tu Fair Value en el valor intrínseco actual histórico o descontado. Tu decisión e indicador de valor justo deben responder puramente a la proyección futura a 1 año.
{_victor_prompt_block}
PROBABILIDADES CALIBRADAS (rellena 'probabilities'):
Da probabilidades 0-100 ANCLADAS EN BASE-RATES, no en optimismo. Pregúntate: ¿con qué frecuencia históricamente una acción con esta volatilidad/perfil logra este movimiento? Evita sobreconfianza: si dices 90%, debe haber evidencia fuerte. En 'rationale' ancla explícitamente en frecuencias base. Estas probabilidades se usan para dimensionar la posición con Kelly fraccional, así que la calibración importa más que el optimismo.

Genera el reporte financiero estructurado con análisis narrativo, fundamentales, tesis y riesgos.
En 'calculos_y_crecimiento_ai' explica la ARITMÉTICA REAL de este reporte, paso a paso, para que se
entienda de dónde sale cada número. Usa SOLO lo que aparece arriba:
- Los targets a 12 meses de Victor y su fórmula: EPS × (1+crecimiento) × (P/E actual × factor del escenario),
  con el crecimiento anclado al CAGR de utilidades a 5 años. Di qué EPS, qué crecimiento y qué múltiplo se usaron.
- El Fair Value, que ES el target base de Victor. NO lo promedies con Wall Street ni con nada más:
  si citas el consenso de analistas es como CONTRASTE, nunca como parte del cálculo.
- El DCF (FCFF) del especialista de valuación, si está disponible, con su WACC y sus supuestos.
- El puntaje de los 6 agentes: qué categoría empuja hacia arriba, cuál hacia abajo, y por qué.
Si un dato no está arriba, di que no está — nunca inventes un cálculo intermedio.

En 'analistas_consenso' resume qué dice Wall Street y CONTRÁSTALO con el veredicto de Victor: dónde
coinciden, dónde divergen y qué explicaría la diferencia. El consenso es contexto, no es la conclusión.
"""

        # El pase estructurado del LLM describe numeros que ya estan
        # congelados: mismo ticker + misma sesion cerrada => misma entrada,
        # asi que reusarlo es correcto y ahorra ~34 s. La clave es el reloj
        # del packet, igual que la del motor.
        _reloj_llm = (_eng or {}).get("_reloj_packet") if isinstance(_eng, dict) else None
        _hit_llm = _LLM_CACHE.get((ticker, _reloj_llm)) if _reloj_llm else None
        if _hit_llm is not None:
            import copy as _cp
            analisis_json, _analysis_src = _cp.deepcopy(_hit_llm[0]), _hit_llm[1]
            print(f"[analyze] {ticker}: analisis estructurado servido de cache")
        else:
            analisis_json, _analysis_src = _analyze_structured(prompt, temp=0.2)
            if _reloj_llm and isinstance(analisis_json, dict):
                if len(_LLM_CACHE) >= _LLM_CACHE_MAX:
                    _LLM_CACHE.pop(next(iter(_LLM_CACHE)), None)
                import copy as _cp
                _LLM_CACHE[(ticker, _reloj_llm)] = (_cp.deepcopy(analisis_json), _analysis_src)
        analisis_json = _coerce_analysis_shapes(analisis_json)

        # ── FAIR VALUE ──
        # Con targets de Victor: el Fair Value ES su target BASE (no un promedio con Wall Street).
        # Sin ellos: se mantiene el respaldo de Vertex (promedio de targets 12M + Analyst Mean).
        if _victor_only_targets:
            _fv_v = float(targets['12m']['base'])
            analisis_json['fair_value'] = round(_fv_v, 2)
            analisis_json['upside_pct'] = round(((_fv_v - precio_actual) / precio_actual) * 100, 2) if precio_actual else 0.0
        else:
            avg_my_12m = (targets['12m']['bull'] + targets['12m']['base'] + targets['12m']['bear']) / 3
            wall_street_mean = methodology.get('analyst_mean', precio_actual)
            combined_fair_value = (avg_my_12m + float(wall_street_mean)) / 2
            analisis_json['fair_value'] = round(combined_fair_value, 2)
            analisis_json['upside_pct'] = round(((combined_fair_value - precio_actual) / precio_actual) * 100, 2)

        # ── CONVICCIÓN = EL PUNTAJE DE LOS AGENTES DE VICTOR ─────────────────────────
        # El "Motor de Convicción Ponderado" de Vertex queda ELIMINADO por completo: era un
        # composite propio (25/20/20/15/10/5/5 sobre 7 señales, con pesos por régimen, tilt por
        # IC realizado, ancla de flujo y calibración bayesiana) que competía con el scorecard.
        # Ahora la convicción ES el raw_total (0-100) de los 6 especialistas y la recomendación
        # sale de sus gates — un solo motor, el de Victor, sin ponderaciones paralelas.
        regime = get_regime_cached()          # se conserva: el track record agrupa por régimen
        analisis_json["regime"] = regime
        _victor_conv = None
        try:
            if _eng and _eng.get("raw_total") is not None:
                _victor_conv = float(_eng["raw_total"])
        except Exception:
            _victor_conv = None
        analisis_json["conviction_source"] = ("puntaje de los 6 agentes de Victor (raw_total 0-100)"
                                              if _victor_conv is not None
                                              else "engine de Victor no disponible")
        # Las 7 señales del LLM fueron ELIMINADAS: el tipo de setup, la calidad fundamental y
        # el override de flujo ahora salen de los agentes de Victor y del flujo computado.

        # ── #2 PROBABILIDADES CALIBRADAS + SIZING (Kelly fraccional, acotado por guardrails) ──
        # ── #5 PLAN DE RIESGO (stops según reglas Vertex) ──
        probs = analisis_json.get("probabilities", {}) or {}
        def _pi(k, d=0):
            try:
                return max(0, min(100, int(probs.get(k, d) or d)))
            except (AttributeError, TypeError, ValueError):
                return d
        p_pos = _pi("p_positive_12m", 50)
        # ── #4/#1 — ANCLA la probabilidad al base-rate empírico ANTES del Kelly (el sizing es muy sensible a p).
        # Kelly ADAPTATIVO: usa el edge realizado MÁS ESPECÍFICO con muestra suficiente
        # (ticker → tipo de setup → recomendación → global), con shrinkage por n. Dimensiona por TU edge medido.
        _rec = analisis_json.get("recommendation")
        # Tipo de setup = el AGENTE DE VICTOR que domina el puntaje (score10 × peso de categoría),
        # no una autoevaluación del LLM. Se guarda con el reporte para medir el track record por área.
        _victor_cats = {}
        try:
            for _k, _c in ((_eng or {}).get("categories") or {}).items():
                if _c.get("score10") is not None:
                    _victor_cats[_k] = float(_c["score10"])
        except Exception:
            _victor_cats = {}
        _cur_setup = _dominant_victor_setup(_victor_cats)
        _cur_setup_lbl = _SETUP_LBL.get(_cur_setup, "n/d") if _cur_setup else "n/d"
        _cand = []
        if calib_stats:
            _bt = (calib_stats.get("by_ticker") or {}).get(ticker)
            if _bt and _bt.get("hit_rate") is not None and _bt.get("n"):
                _cand.append(("ticker " + ticker, float(_bt["hit_rate"]), int(_bt["n"])))
            _bsu = (calib_stats.get("by_setup") or {}).get(_cur_setup_lbl)
            if _bsu and _bsu.get("hit_rate") is not None and _bsu.get("n"):
                _cand.append(("setup " + _cur_setup_lbl, float(_bsu["hit_rate"]), int(_bsu["n"])))
            _brd = (calib_stats.get("by_recommendation") or {}).get(_rec) or {}
            if _brd.get("hit_rate") is not None and _brd.get("n"):
                _cand.append(("recomendación " + str(_rec), float(_brd["hit_rate"]), int(_brd["n"])))
        _br = None                                  # el más específico con n≥5; si no, el más específico con n>0
        for _c in _cand:
            if _c[2] >= 5:
                _br = _c; break
        if _br is None and _cand:
            _br = _cand[0]
        if _br is None and calib_stats and calib_stats.get("overall_hit_rate") is not None and calib_stats.get("n"):
            _br = ("global", float(calib_stats["overall_hit_rate"]), int(calib_stats["n"]))
        prob_anchor = None
        if _br is not None and _br[2] > 0:
            _scope, _brp, _brn = _br
            _Kp = 10.0
            _wb = _brn / (_brn + _Kp)                       # n alto → confía en el base-rate; n bajo → en el LLM
            _p_used = _wb * _brp + (1 - _wb) * p_pos
            prob_anchor = {"llm_p": p_pos, "base_rate": round(_brp, 1), "n": _brn,
                           "weight_base": round(_wb, 2), "p_used": round(_p_used, 1), "scope": _scope}
            p_pos = int(round(max(1, min(99, _p_used))))
        analisis_json["prob_anchoring"] = prob_anchor
        bull12 = float(targets['12m']['bull']); bear12 = float(targets['12m']['bear'])
        reward = max(0.0, (bull12 - precio_actual) / precio_actual)
        risk_dn = max(1e-6, (precio_actual - bear12) / precio_actual)
        rr = reward / risk_dn if risk_dn > 0 else 0.0
        p = p_pos / 100.0; q = 1.0 - p
        kelly_full = max(0.0, (rr * p - q) / rr) if rr > 0 else 0.0   # f* = p - q/b
        kelly_half = kelly_full * 0.5                                  # half-Kelly por seguridad
        held_w = 0.0
        if portfolio_fit and portfolio_fit.get("already_held"):
            try:
                held_w = float(portfolio_fit.get("current_weight_pct", 0) or 0)
            except (TypeError, ValueError):
                held_w = 0.0
        room = max(0.0, 25.0 - held_w)                                # tope de concentración 25%
        suggested = max(0.0, min(kelly_half * 100.0, room))
        cap_reason = ""
        if kelly_half * 100.0 > room:
            cap_reason = (f"Limitado por tope de concentración 25% (ya tienes {held_w:.1f}% en {ticker})."
                          if held_w > 0 else "Limitado por tope de concentración 25%.")

        # #2 — haircut por concentración de factor: si la idea apila sobre el factor ya
        # dominante del book (o es muy redundante), recorta el tamaño sugerido 30%.
        factor_haircut = 1.0
        _rc = (portfolio_fit or {}).get("risk_contribution") or {}
        _fc = (portfolio_fit or {}).get("factors") or {}
        if _fc.get("concentrates_dominant") or _rc.get("verdict") == "concentra":
            factor_haircut = 0.70
            if suggested > 0:
                suggested = suggested * factor_haircut
                _lab = _fc.get("factor_labels", {})
                _dom = _lab.get(_fc.get("book_dominant"), _fc.get("book_dominant"))
                cap_reason = (cap_reason + " Haircut −30% por concentración de factor"
                              + (f" ({_dom})." if _dom else ".")).strip()

        # #7 — recalcula el MCR/Δvol/VaR al PESO REALMENTE RECOMENDADO (no al 5% fijo)
        if portfolio_fit and suggested and suggested > 0:
            try:
                portfolio_fit["risk_contribution"] = _recompute_risk_contribution(portfolio_fit, suggested / 100.0)
            except Exception:
                pass

        # Grado A: calidad fundamental fuerte + puntaje alto — TODO medido por los agentes de
        # Victor. La "calidad fundamental" es el promedio de sus agentes Financial y Business
        # (0-10 → 0-100), en vez de la autoevaluación 'fundamentales' del LLM, que se eliminó.
        _fq = [_victor_cats[k] for k in ("financial", "business") if k in _victor_cats]
        ss_fund = (sum(_fq) / len(_fq)) * 10.0 if _fq else 0.0
        is_a_grade = (ss_fund >= 70 and (_victor_conv or 0) >= 65)
        atr = methodology.get("atr_14")
        try:
            atr_f = float(atr) if atr not in (None, "N/A", "") else None
        except (TypeError, ValueError):
            atr_f = None
        if is_a_grade:
            equity_stop = None
            equity_stop_note = f"Equity A-grade: sin stop fijo. Gestiona por tesis; quiebre de tesis ≈ ${round(bear12, 2)}."
        elif atr_f:
            equity_stop = round(precio_actual - 2 * atr_f, 2)
            equity_stop_note = f"No A-grade: stop sugerido en ${equity_stop} (2×ATR-14 bajo el spot)."
        else:
            equity_stop = round(precio_actual * 0.85, 2)
            equity_stop_note = f"No A-grade: stop sugerido en ${equity_stop} (-15% del spot)."
        # El override de flujo (Quant Data: sesgo alcista con convicción ≥80%) se ELIMINÓ.
        # `trade_plan` se pinta en UN solo sitio —el panel "Plan de operación" del tab de
        # Proyecciones— y ese tab ya lleva el flujo de Víctor (agresión / convicción /
        # inusualidad sobre la cinta de MarketSnack). Dos proveedores midiendo flujo en la
        # misma pantalla es justo la lectura doble que se mandó quitar; Quant Data sigue
        # intacto donde sí manda, que es el prompt de Full Research.

        analisis_json["trade_plan"] = {
            "probabilities": {
                "p_positive_12m": p_pos, "p_touch_bull_12m": _pi("p_touch_bull_12m"),
                "p_touch_bear_12m": _pi("p_touch_bear_12m"), "p_up_10pct_3m": _pi("p_up_10pct_3m"),
                "rationale": probs.get("rationale", "")},
            "reward_pct": round(reward * 100, 1), "risk_pct": round(risk_dn * 100, 1),
            "reward_risk": round(rr, 2),
            "kelly_full_pct": round(kelly_full * 100, 1), "kelly_half_pct": round(kelly_half * 100, 1),
            "suggested_pct": round(suggested, 1), "cap_reason": cap_reason,
            "factor_haircut_pct": round((1 - factor_haircut) * 100, 0),
            "already_held_pct": round(held_w, 1),
            "risk_plan": {
                "is_a_grade": is_a_grade, "equity_stop": equity_stop,
                "equity_stop_note": equity_stop_note, "thesis_break_level": round(bear12, 2),
                "options_stop_rule": "Opciones: stop −20% a −30% de la prima pagada.",
            },
        }

        # ── Batch R · targets en R (1R = entry→bear) + lista FALSABLE de invalidación de tesis ──
        _R_unit = max(1e-9, abs(precio_actual - bear12))
        _base12 = float(targets['12m'].get('base', precio_actual))
        analisis_json["trade_plan"]["targets_r"] = {
            "entry": round(precio_actual, 2), "stop": round(bear12, 2), "r_unit": round(_R_unit, 2),
            "bull_r": round((bull12 - precio_actual) / _R_unit, 2),
            "base_r": round((_base12 - precio_actual) / _R_unit, 2), "bear_r": -1.0,
        }
        # "Qué me haría cambiar de opinión": checkpoints concretos y verificables construidos
        # desde las señales reales del análisis. No depende del LLM → siempre presente.
        #
        # Los checkpoints de GAMMA (put wall, call wall, gamma flip) y de FLUJO salían de
        # Quant Data (`get_gex_cached` → `_gex_from_quantdata`, `_qd_conv`, `_qd_np`) y se
        # ELIMINARON de aquí: este plan se pinta dentro del tab de Proyecciones, donde el
        # bloque "Escenarios de Precio (GEX)" ya da ruptura alcista, ruptura bajista, gamma
        # flip y nodo imán — calculados por el motor de Víctor sobre la cadena de Massive.
        # Tener los mismos cuatro niveles de otro proveedor a dos dedos de distancia es la
        # lectura doble de gamma que se mandó quitar del tab, y cuando discrepaban no había
        # forma de saber cuál mirar. Quedan los checkpoints que son de ESTE análisis y de
        # nadie más: el precio de quiebre de tesis y el catalizador de earnings.
        _inval = []
        _is_bull = _reco_norm(_rec) == RESEARCH_FAVORABLE
        _inval.append({"factor": "Precio", "kind": "price",
                       "trigger": f"Cierre {'bajo' if _is_bull else 'sobre'} ${round(bear12, 2)} (quiebre de tesis = −1R)"})
        _ed = (earnings_info or {}).get("days_until")
        if isinstance(_ed, (int, float)) and 0 <= _ed <= 45:
            _inval.append({"factor": "Earnings", "kind": "catalyst",
                           "trigger": f"Sorpresa o guía {'a la baja' if _is_bull else 'al alza'} en earnings (en {int(_ed)}d)"})
        analisis_json["trade_plan"]["thesis_invalidation"] = _inval

        # La reconciliación comparaba los targets σ/DCF contra los de
        # gamma/flujo. El segundo motor era de opciones y ya no existe, así
        # que no hay dos vistas que reconciliar -- y publicar el campo con
        # una sola sería decir que coinciden.

        # ── #8 — GATE DE COHERENCIA sobre la salida del LLM (contradicciones internas) ──
        try:
            _cf = _agent_coherence_checks(analisis_json, precio_actual)
            analisis_json["coherence_flags"] = _cf
            analisis_json["coherence_ok"] = (len(_cf) == 0)
        except Exception:
            analisis_json["coherence_flags"] = []
            analisis_json["coherence_ok"] = True
        analisis_json["model_source"] = _analysis_src
        analisis_json["earnings_history"] = earnings_hist          # #2
        # ── INTEGRIDAD: cedazo visible sobre los números del reporte (flip/walls/FV/targets/fuente) ──
        try:
            _tgt_lvls = [_safe_num(t.get("level")) for t in (targets or []) if isinstance(t, dict) and t.get("level")]
            analisis_json["integrity"] = _integrity_checks(
                {}, fair_value=analisis_json.get("fair_value"), targets=_tgt_lvls)
        except Exception:
            analisis_json["integrity"] = None
        try:
            analisis_json["ai_concentration"] = _ai_concentration(ticker)
        except Exception:
            analisis_json["ai_concentration"] = None
        analisis_json["news_catalysts"] = news_catalysts            # #3
        analisis_json["sec_8k"] = sec_8k                            # #3 — 8-K reales

        # ── OVERLAY WBJ: sobrescribe los NÚMEROS con los de Victor (engine determinista).
        #    _eng YA se calculó ANTES del prompt (los números mandan, la narrativa los sigue);
        #    aquí solo se consume — no se recalcula (evita correr los 6 especialistas dos veces). ──
        if _eng and _eng.get("categories"):
            _comp = {"categories": _eng["categories"], "raw_total": _eng["raw_total"],
                     "total_confidence": _eng["total_confidence"], "incomplete": _eng.get("incomplete", [])}
            _gates = _wbj_gates(_comp)
            # ── PRINCIPAL: si el aggregate REAL de Victor corrió (apply_overrides+apply_gates
            #    sobre los 6 outputs), su perfil/banda/overrides MANDAN sobre mi _wbj_gates. ──
            _vg = _eng.get("victor_gates")
            if _vg:
                _prof = _vg["profile"]
                _gates["recommendation"], _gates["classification"] = _wbj_reco_from_profile(_prof)
                _gates["profile"] = _prof
                _gates["band"] = _vg["band"]
                _gates["overrides"] = _vg["overrides"]        # los 8 overrides REALES de Victor
                _gates["warnings"] = _vg.get("warnings", [])
                # Las razones de gate también son las de apply_gates de Victor (ProfileResult),
                # para que passed/failed_gates sean CONSISTENTES con el perfil que MANDA. Sin
                # esto se mostraba el perfil de Victor pero las razones aproximadas del backup.
                if "passed_gates" in _vg:
                    _gates["passed_gates"] = _vg["passed_gates"]
                if "failed_gates" in _vg:
                    _gates["failed_gates"] = _vg["failed_gates"]
                _gates["_source"] = "aggregate real de Victor"
            _vt = _eng.get("victor_targets_12m") or {}
            if all(_vt.get(k) is not None for k in ("bull", "base", "bear")):
                targets["12m"] = {k: round(float(_vt[k]), 2) for k in ("bull", "base", "bear")}
                analisis_json["target_bull_12m"] = targets["12m"]["bull"]
                analisis_json["target_base_12m"] = targets["12m"]["base"]
                analisis_json["target_bear_12m"] = targets["12m"]["bear"]
            # Override 7 (SCORING_AND_GATES.md / MAIN-009 / MAIN-010): un conflicto o ausencia de
            # share-count/debt/cash/price NO RESUELTO prohíbe publicar la valuación POR ACCIÓN.
            # Si el override disparó, se SUPRIME fair_value/upside (per-share) en vez de publicarlo.
            _ps_suppress = [_o for _o in (_gates.get("overrides") or [])
                            if _o in ("OVERRIDE_7_MISSING_SHARE_COUNT", "OVERRIDE_7_DATA_CONFLICT_SUPPRESS_PER_SHARE")]
            _vfv = _eng.get("victor_fair_value")
            # EL FAIR VALUE ES EL TARGET BASE de Victor (su targets.py). El Override 7 NO lo borra:
            #  · MAIN-009 ("missing share count" → suprimir per-share) ya está cubierto DENTRO de
            #    price_targets, que devuelve not_scorable si falta diluted_shares o el EPS no es
            #    positivo. Si los targets salieron OK, hubo un conteo de acciones real.
            #  · MAIN-010 en VALIDATION_TESTS.md pide "mark conflicted and rerun affected agents",
            #    NO suprimir el valor. Por eso el conflicto se MARCA (warning visible) en vez de
            #    dejar el Fair Value en blanco.
            # La supresión sigue aplicando al valor por acción del especialista de VALUACIÓN (DCF),
            # que es otro número y se marca aparte.
            if _vfv:
                analisis_json["fair_value"] = round(float(_vfv), 2)
                analisis_json["upside_pct"] = round(((float(_vfv) - precio_actual) / precio_actual) * 100, 2) if precio_actual else 0.0
            elif _ps_suppress:
                # Sin target base publicable Y con Override 7 activo → sí se suprime, y se dice por qué.
                analisis_json["fair_value"] = None
                analisis_json["upside_pct"] = None
            if _ps_suppress:
                analisis_json["valuation_per_share_suppressed"] = {
                    "active": True, "overrides": _ps_suppress,
                    "fair_value_published": bool(_vfv),
                    "reason": ("Conflicto/ausencia material de share-count/deuda/caja/precio marcado por el "
                               "Override 7 (MAIN-009/010): el valor por acción del DCF del especialista de "
                               "valuación queda suprimido y los agentes afectados deberían re-correrse. "
                               + ("El Fair Value mostrado es el TARGET BASE de targets.py, que se calcula "
                                  "con su propio conteo de acciones validado y por eso sí se publica."
                                  if _vfv else
                                  "Tampoco hay target base publicable, así que no se muestra Fair Value."))}
            analisis_json["recommendation"] = _gates["recommendation"]
            analisis_json["conviccion_score"] = int(round(_eng["raw_total"]))
            analisis_json["wbj"] = {"framework": "Ruta 2030 Wall Street Agent System v2.0.0",
                "categories": _eng["categories"], "raw_total": _eng["raw_total"],
                "total_confidence": _eng["total_confidence"], "band": _gates["band"],
                "profile": _gates["profile"], "classification": _gates["classification"],
                "passed_gates": _gates["passed_gates"], "failed_gates": _gates["failed_gates"],
                "overrides": _gates["overrides"], "warnings": _gates.get("warnings", []),
                "contradictions": _eng.get("victor_contradictions") or [],
                # HANDOFF_CONTRACT.md (AGENT.md #5 "preserve warnings" / PROMPT.md #2): la validación
                # del traspaso de los 6 packets se SURFACEA (antes solo iba a logs). {} = todo válido.
                "handoff_validation": (_vg or {}).get("handoff_issues") or {},
                # DATA_POLICY.md: frescura del packet ACTUAL por tipo de dato (afecta confianza y
                # puede pedir RECALC). recalc_recommended = hay ≥1 tipo de dato STALE.
                "data_staleness": _eng.get("packet_staleness") or {},
                "recalc_recommended": bool(_eng.get("packet_staleness") and
                                           any(v == "STALE" for v in (_eng.get("packet_staleness") or {}).values())),
                # MISSING_DATA_POLICY paso 4: inputs tomados de una FUENTE PROXY (respaldo), declarados.
                "proxy_inputs": _eng.get("proxy_inputs") or {},
                # {categoría: score10} plano — se guarda con el reporte para derivar el tipo de
                # setup (agente dominante) y medir el track record por área de Victor.
                "categories_10": {_k: _c.get("score10") for _k, _c in (_eng["categories"] or {}).items()
                                  if _c.get("score10") is not None},
                "gates_source": _gates.get("_source", "gates de compatibilidad"),
                "scores_source": "engine determinista (metodología de Victor)",
                # PANEL "JUICIO AI" (opcional): scorecard PARALELO con lo que respondió el judge.
                # NO afecta el score principal de arriba; se muestra aparte para comparar. Solo
                # existe si corrió el judge (ANTHROPIC_API_KEY con crédito). None → no hay panel.
                "ai_judgment": _eng.get("ai_judgment")}
            analisis_json["victor_targets_detail"] = _eng.get("victor_targets_detail")
            # Paneles de Victor para Full Research (deterministas, de su brief.py/targets.py)
            analisis_json["victor_narrative"] = _eng.get("victor_narrative")           # "En palabras simples"
            analisis_json["victor_scorecard"] = _eng.get("victor_scorecard")           # puntaje 1-10 + evidencia
            analisis_json["victor_interpretation"] = _eng.get("victor_interpretation") # "Qué significa el puntaje"
            analisis_json["victor_targets_reason"] = _eng.get("victor_targets_reason")   # razón si no hay target
            analisis_json["financials_annual"] = _eng.get("financials_annual")
            analisis_json["victor_levels"] = _eng.get("victor_levels")   # niveles de precio (synthesize_levels)
            # DCF REAL de Victor (FCFF del especialista). Sustituye al "Vertex DCF" viejo,
            # que era eps × P/E_objetivo y contradecía al fair value mostrado arriba.
            analisis_json["victor_valuation"] = _eng.get("victor_valuation")
            analisis_json["thesis_killers"] = _eng.get("thesis_killers")
            # ── FILTRO POR PERFIL (orquestador, CLAUDE.md paso 6): cruza la recomendación con
            #    el perfil de Kevin. No cambia el scoring; clasifica el fit con evidencia. ──
            try:
                analisis_json["profile_fit"] = _wbj_profile_fit(info, _gates.get("recommendation"))
            except Exception as _pfe:
                print(f"[analyze] filtro por perfil omitido: {str(_pfe)[:120]}")
            # ── CONTENIDO OBLIGATORIO del reporte (CLAUDE.md): insiders >$1M + 13F + revisitar ──
            try:
                _nxt = None
                try:
                    _nxt = _next_earnings_date(ticker)
                except Exception:
                    _nxt = None
                _fmp_imp = _wbj_fmp_important_insiders(ticker)   # FMP si hay key; None → yfinance
                analisis_json["mandatory_report"] = _wbj_mandatory_report(
                    insiders_snapshot, _gates.get("recommendation"), _nxt, fmp_important=_fmp_imp)
                # CLAUDE.md #4: historial del management (roster factual + trayectoria en otras
                # empresas exitosas). Roster siempre; la parte cualitativa es grounded/opcional.
                # Se le PASAN los settings: sin ellos la función caía siempre a la rama sin-key y
                # la mitad cualitativa de este punto obligatorio nunca se producía. Es una tarea de
                # JUICIO con riesgo de alucinación (y poco texto), así que usa el judge_model.
                _mgmt_settings = None
                try:
                    from wbj.config import load_settings as _ls_mgmt
                    _mgmt_settings = _ls_mgmt()
                except Exception:
                    _mgmt_settings = None
                analisis_json["mandatory_report"]["management"] = _wbj_management_track_record(
                    info, settings=_mgmt_settings)
                # Enlaces directos a EDGAR (Forms 4 y 13F). Vivían en la pestaña
                # "Insiders & 13F", ya eliminada; son la fuente primaria de este
                # bloque obligatorio, así que se sirven junto a él. Cualquier fallo
                # deja el bloque sin enlaces, no rompe el reporte.
                try:
                    analisis_json["mandatory_report"]["edgar"] = fetch_edgar_filings(ticker, limit=8)
                except Exception as _ede:
                    print(f"[analyze] enlaces EDGAR omitidos: {str(_ede)[:100]}")
            except Exception as _mre:
                print(f"[analyze] contenido obligatorio omitido: {str(_mre)[:120]}")
            # ── RE-EJECUCIÓN (CLAUDE.md): ¿la tesis previa quedó obsoleta? Disparadores
            #    deterministas (staleness exacto de Victor + filings SEC nuevos). No cambia números. ──
            try:
                analisis_json["re_execution"] = _wbj_reexecution_triggers(ticker, prior_report)
            except Exception as _rxe:
                print(f"[analyze] disparadores de re-ejecución omitidos: {str(_rxe)[:120]}")
            # ── REPORTE FINAL conforme a FINAL_REPORT_SCHEMA.md (con APÉNDICE DE AUDITORÍA) ──
            #    Ensamblado por el `build_final_report` REAL de Victor con sus objetos crudos
            #    (AggregateInputs, ProfileResult, contradictions, LevelSynthesis) + hash del packet,
            #    versiones de fórmula reales y validation_summary. La narrativa (executive_thesis,
            #    7 frases) la suministra el renderer desde la narrativa ya generada. No cambia números.
            try:
                _vf = (_eng or {}).get("_victor_final")
                if _vf:
                    import datetime as _dtmod
                    from wbj.schemas.final_report import build_final_report, ExecutiveThesis
                    def _lvl_px(_l):
                        if getattr(_l, "zone_low", None) is not None and getattr(_l, "zone_high", None) is not None:
                            return f"${_l.zone_low}-{_l.zone_high}"
                        return f"${getattr(_l, 'value', '')}"
                    _lvls_sum = "; ".join(f"{getattr(_l, 'label', '')} {_lvl_px(_l)}"
                                          for _l in list(_vf["levels"].levels)[:4]) or "sin niveles sintetizados"
                    def _nar(*keys):
                        for _k in keys:
                            _v = analisis_json.get(_k)
                            if _v:
                                return str(_v)[:600]
                        return ""
                    _et = ExecutiveThesis(
                        business_quality=_nar("company_summary_simple", "in_simple_terms"),
                        value_creation_durability=_nar("posicion_competitiva", "porque_mejor_peor_inversion"),
                        growth_engine=_nar("calculos_y_crecimiento_ai", "crecimiento_proyectado"),
                        market_validation=_nar("analistas_consenso"),
                        valuation_message=_nar("analisis_numeros_actuales", "the_bottom_line"),
                        key_levels_summary=_lvls_sum[:600],
                        primary_risk=_nar("biggest_risk", "tesis_riesgos"))
                    _fr = build_final_report(
                        inputs=_vf["inputs"], profile=_vf["profile"],
                        contradictions=_vf["contradictions"], levels=_vf["levels"],
                        executive_thesis=_et, exchange=_vf["exchange"], currency=_vf["currency"],
                        analysis_timestamp=_dtmod.datetime.now(_dtmod.timezone.utc).isoformat(),
                        packet_hashes=({"packet": _vf["packet_hash"]} if _vf["packet_hash"] else {}),
                        formula_versions=_vf["formula_versions"])
                    _frd = _fr.model_dump(mode="json")
                    # Apéndice de auditoría (FINAL_REPORT_SCHEMA Fase 8 "formula and source audit"):
                    # añade la validación del traspaso HANDOFF_CONTRACT a validation_summary.
                    _hi = (_eng.get("victor_gates") or {}).get("handoff_issues") or {}
                    _frd.setdefault("audit", {}).setdefault("validation_summary", {})["handoff_issues"] = _hi
                    _frd["audit"]["validation_summary"]["data_staleness"] = _eng.get("packet_staleness") or {}
                    _frd["audit"]["validation_summary"]["proxy_inputs"] = _eng.get("proxy_inputs") or {}
                    analisis_json["final_report"] = _frd
            except Exception as _fre:
                print(f"[analyze] reporte final (schema) omitido: {str(_fre)[:150]}")
            finally:
                if isinstance(_eng, dict):
                    _eng.pop("_victor_final", None)   # objeto interno: nunca al cliente
            # ── EXPLICACIÓN EN PALABRAS (2º pase LLM) ──────────────────────
            #
            #  Solo explica los números YA congelados; no cambia ningún cálculo.
            #  Cuesta ~18,4 s contra Gemini, así que NO se hace aquí salvo que se
            #  pida con `?explain=1`: metida en el camino crítico acercaba
            #  /api/analyze al corte de Render.
            #
            #  La pantalla ya no la pide por aquí — la pide después, al terminar
            #  el análisis, contra `/api/wbj-explicacion`. El resultado es el
            #  mismo texto; lo que cambia es que el análisis aparece a los ~105 s
            #  en vez de a los ~123 s, y la explicación llega sola encima.
            #  `?explain=1` se mantiene para quien llame la API desde un script.
            if explain:
                try:
                    _ctx = _wbj_explain_context(ticker, info.get("longName", ticker),
                                                precio_actual, analisis_json)
                    _expl, _expl_src = _wbj_explain(_ctx)
                    if _expl:
                        analisis_json["wbj_explanation"] = _expl
                        analisis_json["wbj_explanation_source"] = _expl_src
                        print(f"[analyze] {ticker}: explicación WBJ en palabras generada ({_expl_src})")
                except Exception as _ee:
                    print(f"[analyze] explicación WBJ omitida: {str(_ee)[:120]}")
            # ── MEMORIA (protocolo CLAUDE.md): escribe la tesis + predicción con los números
            #    YA CONGELADOS de Victor. Corrige encima (apila historial); nunca borra. ──
            try:
                _prof_m = (analisis_json.get("wbj") or {}).get("profile")
                _raw_m = (analisis_json.get("wbj") or {}).get("raw_total")
                _fv_m = analisis_json.get("fair_value")
                _thesis_m = (analisis_json.get("tesis_inversion_completa")
                             or f"{_gates.get('classification')} — perfil {_prof_m}; fair value base ${_fv_m}.")
                _inval_m = None
                for _lv in ((_eng.get("victor_levels") or {}).get("levels", []) or []):
                    if isinstance(_lv, dict) and _lv.get("invalidation") is not None:
                        _inval_m = _lv["invalidation"]; break
                if _inval_m is None:
                    _inval_m = analisis_json.get("target_bear_12m")
                _wbj_write_thesis_md(ticker, precio_actual, _prof_m, _raw_m, _fv_m, targets, _thesis_m, _inval_m)
                _wbj_write_prediccion(ticker, report_id, precio_actual, _fv_m, _prof_m, _raw_m,
                                      targets, _gates.get("recommendation"))
            except Exception as _wme:
                print(f"[analyze] memoria (tesis/predicción) omitida: {str(_wme)[:120]}")
            analisis_json["scores_source"] = "victor"

        # ── MEMORY: compare with prior report + persist this one ─────────────
        memory_comparison = compute_memory_comparison(
            prior_report, precio_actual, analisis_json['fair_value'],
            analisis_json.get('recommendation'), analisis_json.get('conviccion_score'))
        save_report(report_id, ticker, precio_actual, analisis_json['fair_value'],
                    analisis_json['upside_pct'], analisis_json.get('recommendation'),
                    analisis_json.get('conviccion_score'), targets,
                    analisis_json.get('tesis_inversion_completa'),
                    victor_categories=(analisis_json.get('wbj') or {}).get('categories_10'))

        _analyze_resp = {
            "report_id": report_id,
            "ticker": ticker,
            "nombre_completo": info.get("longName", ticker),
            "precio_actual": precio_actual,
            "precio_fuente": (_resolve_spot(ticker).get("source") or "yfinance"),
            "precio_as_of": (_resolve_spot(ticker).get("as_of")),
            "pe_ratio": info.get("trailingPE") or "N/A",
            "logo_url": logo_url,
            "fecha_analisis": analisis_timestamp,
            "historial_precios": precios_hist,
            "historial_fechas": fechas_hist,
            "historial_ohlc": ohlc_hist,
            # Último año de cierres diarios [{time:"YYYY-MM-DD", value}] — insumo de la gráfica
            # de precio+targets de Victor (su renderer SVG, periodos 1M/3M/6M/1A).
            "chart_history": chart_history,
            "historial_volumen": volumen_hist,
            "earnings_info": earnings_info,
            "insiders_snapshot": insiders_snapshot,
            "memory_comparison": memory_comparison,
            "deep_memory": _deep_mem_meta,
            "open_options": _open_opts,
            "portfolio_fit": portfolio_fit,
            "noticias_reales": noticias_formateadas,
            "targets": targets,
            # Origen de los targets: "victor" = su targets.py, UN horizonte (12 meses) y el Fair
            # Value es el target base. "vertex" = respaldo (volatilidad/ATR/consenso, 5 horizontes).
            # El frontend usa esto para mostrar SOLO 12M cuando mandan los de Victor.
            "targets_source": ("victor" if _victor_only_targets else "vertex"),
            "targets_horizon_note": ("Metodología de Victor: EPS × (1+crecimiento) × (P/E actual × factor). "
                                     "Un solo horizonte: 12 meses. Fair Value = target Base."
                                     if _victor_only_targets else
                                     "Respaldo Vertex: volatilidad histórica / ATR / DCF simplificado / consenso."),
            "methodology": methodology,
            "analisis": analisis_json
        }
        try:
            save_report_payload(report_id, _analyze_resp)   # #4 — archivo durable en el servidor
        except Exception:
            pass
        return _analyze_resp
    except HTTPException:
        # Los códigos que ya elegimos a propósito (404 "Sin datos") se dejan pasar:
        # antes el except de abajo los reenvolvía como 500 "404: Sin datos".
        raise
    except Exception as e:
        traceback.print_exc()   # el detail se recorta; el traceback completo va al log del servidor
        raise HTTPException(status_code=500, detail=f"{type(e).__name__}: {str(e)[:300]}")



# ─────────────────────────────────────────────────────────────────────────────
# #4 DEBATE ADVERSARIAL — Toro + Oso + Árbitro (gemini-2.5-pro)
# ─────────────────────────────────────────────────────────────────────────────
def _is_quota_error(e):
    s = str(e)
    return ("RESOURCE_EXHAUSTED" in s or "429" in s or "quota" in s.lower()
            or "exhausted" in s.lower())


def _retry_delay_secs(e, default=8.0, cap=20.0):
    m = re.search(r"retry in (\d+(?:\.\d+)?)\s*s", str(e)) or re.search(r"retryDelay'?:?\s*'?(\d+)", str(e))
    try:
        return min(float(m.group(1)), cap) if m else default
    except Exception:
        return default


def _openai_json(prompt, keys, temp=0.3, model="gpt-4o"):
    """Fallback de proveedor (ChatGPT): pide JSON estricto a OpenAI con response_format json_object."""
    if not OPENAI_API_KEY:
        raise RuntimeError("OpenAI no configurado (OPENAI_API_KEY vacío).")
    sysmsg = _con_idioma(
        "Devuelve EXCLUSIVAMENTE un objeto JSON válido (sin markdown, sin ```), "
        "con exactamente estas claves (string salvo las numéricas obvias): " + ", ".join(keys) + ".")
    resp = requests.post(
        "https://api.openai.com/v1/chat/completions",
        headers={"Authorization": f"Bearer {OPENAI_API_KEY}", "Content-Type": "application/json"},
        json={"model": model, "temperature": temp, "response_format": {"type": "json_object"},
              "messages": [{"role": "system", "content": sysmsg},
                           {"role": "user", "content": prompt}]},
        timeout=90)
    if resp.status_code != 200:
        raise RuntimeError(f"OpenAI error {resp.status_code}: {resp.text[:200]}")
    txt = resp.json()["choices"][0]["message"]["content"].strip()
    txt = re.sub(r"^```(?:json)?\s*|\s*```$", "", txt, flags=re.S).strip()
    return json.loads(txt)


def _texto_llm(system_msg, user_msg, temp=0.4, max_tokens=4000):
    """Texto libre (no JSON) desde los DOS proveedores del sistema.

    Devuelve `(texto, fuente, error)`. Nunca lanza: si ninguno responde,
    `texto` viene vacío y `error` explica por qué CADA uno falló — el mismo
    criterio que `_wbj_explain`, donde propagar sólo el último fallo hacía
    que un 429 de cuota se reportara como una variable de entorno ausente.

    Sustituye las llamadas directas a `api.x.ai` que había en `/api/sentiment`
    y en el desaparecido `/api/explore-deep`. Victor no usa Grok en ninguna
    parte de su repo, y su ausencia dejaba esas rutas sin ningún respaldo:
    una sola clave sin configurar las apagaba enteras.
    """
    fallos = []
    if client_gemini is not None:
        try:
            r = _gemini_genera(
                model="gemini-2.5-flash",
                contents=f"{system_msg}\n\n{user_msg}",
                config=types.GenerateContentConfig(
                    temperature=temp, max_output_tokens=max_tokens))
            texto = (r.text or "").strip()
            if texto:
                return texto, "gemini", ""
            fallos.append("gemini: respuesta vacía")
        except Exception as e:
            fallos.append(f"gemini: {type(e).__name__} {str(e)[:110]}")
    else:
        fallos.append("gemini: sin GEMINI_API_KEY")

    if OPENAI_API_KEY:
        try:
            resp = requests.post(
                "https://api.openai.com/v1/chat/completions",
                headers={"Content-Type": "application/json",
                         "Authorization": f"Bearer {OPENAI_API_KEY}"},
                json={"model": "gpt-4o",
                      "messages": [{"role": "system", "content": _con_idioma(system_msg)},
                                   {"role": "user", "content": user_msg}],
                      "max_tokens": max_tokens, "temperature": temp},
                timeout=120)
            if resp.status_code == 200:
                texto = resp.json()["choices"][0]["message"]["content"].strip()
                if texto:
                    return texto, "openai (ChatGPT)", ""
                fallos.append("openai: respuesta vacía")
            else:
                fallos.append(f"openai: HTTP {resp.status_code} {resp.text[:110]}")
        except Exception as e:
            fallos.append(f"openai: {type(e).__name__} {str(e)[:110]}")
    else:
        fallos.append("openai: sin OPENAI_API_KEY")

    return "", None, " | ".join(fallos)






# ─────────────────────────────────────────────────────────────────────────────
def _analyze_structured(prompt, temp=0.2):
    """#7 — genera el reporte estructurado con RESPALDO DE PROVEEDOR: Gemini (schema) → ChatGPT
    (json_object). Devuelve (dict, fuente). El downstream usa .get() con defaults, así que un
    JSON parcial del respaldo degrada con gracia en vez de tumbar el endpoint cuando se agota la cuota."""
    last = None
    for attempt in range(2):
        try:
            r = _gemini_genera(
                model="gemini-2.5-flash", contents=prompt,
                config=types.GenerateContentConfig(
                    response_mime_type="application/json", response_schema=VertexDeepAnalysis, temperature=temp))
            return json.loads(r.text), "gemini"
        except Exception as e:
            last = e
            if _is_quota_error(e) and attempt == 0:
                time.sleep(_retry_delay_secs(e))
                continue
            break
    try:
        keys = list(getattr(VertexDeepAnalysis, "model_fields", None)
                    or getattr(VertexDeepAnalysis, "__fields__", {}) or [])
    except Exception:
        keys = []
    for fn, src in ((_openai_json, "openai (ChatGPT)"),):
        try:
            return fn(prompt, keys, temp), src
        except Exception as e2:
            last = e2
    # Ningún proveedor de narrativa respondió. NO se tumba el endpoint: para cuando
    # llegamos aquí, los 6 especialistas de Victor, sus gates, targets y niveles YA
    # están calculados y son el producto. La prosa es decoración — la regla del
    # proyecto es "los números mandan y la narrativa los sigue", así que devolvemos
    # el análisis determinista con la narrativa marcada como no disponible.
    reason = (f"{type(last).__name__}: {str(last)[:180]}" if last
              else "ningún proveedor de narrativa configurado (falta GEMINI_API_KEY / OPENAI_API_KEY)")
    print(f"[analyze] narrativa no disponible ({reason}) → se entrega el análisis determinista de Victor")
    return _narrative_unavailable(reason), "no disponible"


def _coerce_analysis_shapes(a: dict) -> dict:
    """Normaliza los campos ANIDADOS que devuelve el LLM antes de usarlos.

    Gemini responde validado contra el schema, pero los proveedores de respaldo
    (ChatGPT en modo json_object) solo garantiza "JSON valido" — no el tipo
    de cada campo. Un `probabilities` que llego como string tumbo /api/analyze con
    AttributeError: 'str' object has no attribute 'get', despues de que los 6
    especialistas de Victor ya habian terminado.

    Si el valor es un string con JSON dentro se parsea; si no, se descarta a {} y
    el downstream aplica sus defaults. No se inventa ninguna probabilidad.
    """
    if not isinstance(a, dict):
        return {}
    raw = a.get("probabilities")
    if not isinstance(raw, dict):
        fixed = None
        if isinstance(raw, str) and raw.strip().startswith("{"):
            try:
                parsed = json.loads(raw)
                fixed = parsed if isinstance(parsed, dict) else None
            except (ValueError, TypeError):
                fixed = None
        if raw is not None:
            print(f"[analyze] 'probabilities' llego como {type(raw).__name__}; "
                  f"{'parseado desde JSON' if fixed else 'descartado -> se usan los defaults'}")
        a["probabilities"] = fixed or {}
    return a


def _narrative_unavailable(reason: str) -> dict:
    """Esqueleto honesto cuando la capa de prosa no está disponible.

    NO inventa nada: recommendation, conviccion_score, fair_value, probabilities y
    demás campos de juicio quedan ausentes para que el downstream aplique sus
    defaults y los overrides de Victor rellenen lo que él sí calcula. Solo los
    campos de texto llevan el aviso, para que en pantalla se vea el motivo."""
    aviso = ("Explicación en palabras no disponible: la capa de narrativa (LLM) no respondió — "
             f"{reason}. Los números de este reporte (los 6 especialistas de Victor, sus gates, "
             "targets y niveles de precio) SÍ son válidos y completos; lo único que falta es la prosa.")
    return {
        "narrative_unavailable": True,
        "narrative_error": reason,
        "in_simple_terms": aviso,
        "the_bottom_line": aviso,
        "company_summary_simple": aviso,
    }
@app.get("/api/watchlist-radar")
def get_watchlist_radar(ticker: str):
    """#4 — Fila de watchlist como RADAR DE SEÑALES (no solo quote): convicción institucional QD,
    premium neto, muros GEX / gamma flip / max pain, y ALERTAS accionables (earnings cercano,
    flujo Tipo A ≥$5M, precio pegado a un muro)."""
    tk = ticker.upper().strip()
    out = {"ticker": tk, "alerts": [], "signals": {},
           "generated_at": datetime.now().strftime('%I:%M:%S %p')}
    spot, spot_src = _live_spot(tk)
    out["price"] = spot
    out["price_source"] = spot_src
    # Earnings + alerta
    try:
        stock = vertex_market.Ticker(tk)
        try:
            info = stock.info
        except Exception:
            info = {}
        earn = fetch_earnings_info(stock, info)
        out["next_earnings_label"] = earn.get("label")
        d = earn.get("days_until")
        out["next_earnings_days"] = d
        if isinstance(d, (int, float)) and 0 <= d <= 7:
            out["alerts"].append({"type": "earnings", "level": "warn", "msg": f"Earnings en {int(d)}d"})
    except Exception:
        pass
    # Aquí iban convicción por ΔOI, premium neto, muros de gamma y max pain,
    # todos de Quant Data. Salió del proyecto (no es fuente del sistema y su
    # plan API está inactivo), así que el radar se queda con las señales que
    # SÍ tienen fuente: precio, técnicos y earnings.
    return _json_safe(out)


@app.get("/api/alerts/scan")
def alerts_scan(tickers: str = "", max_tickers: int = 12):
    """#4 — Escaneo agregado de alertas accionables sobre TODA la watchlist en una sola llamada.
    Reúne las alertas de watchlist-radar (earnings cercano, flujo Tipo A ≥$5M, precio pegado a un muro/flip)
    de cada ticker y les pone un `id` estable (ticker:tipo:msg) para que el frontend deduplique y solo
    notifique las NUEVAS. El frontend lo pollea cada pocos minutos y dispara toast + campana."""
    tks = [t.strip().upper() for t in (tickers or "").split(",") if t.strip()][:max(1, min(max_tickers, 25))]
    now_ms = int(time.time() * 1000)
    alerts = []
    for tk in tks:
        try:
            rad = get_watchlist_radar(tk)
        except Exception:
            continue
        for a in (rad.get("alerts") or []):
            typ = a.get("type", "alert")
            msg = a.get("msg", "")
            alerts.append({
                "id": f"{tk}:{typ}:{msg}",
                "ticker": tk, "type": typ, "level": a.get("level", "info"),
                "msg": msg, "ts": now_ms,
            })
    # ordena por severidad (warn > hot > info) para que lo importante salga primero
    _sev = {"warn": 0, "hot": 1, "info": 2}
    alerts.sort(key=lambda x: _sev.get(x["level"], 3))
    return {"ok": True, "checked_at": now_ms, "n": len(alerts), "alerts": alerts}


# `/api/watchlist-quote` se eliminó con la watchlist de Vertex: era la fila de
# cotización de aquella rejilla de tickers y no la llamaba nadie más. El radar
# (`/api/watchlist-radar`) y el escaneo (`/api/alerts/scan`) SIGUEN: son la
# campana, y ahora leen los subyacentes de la watchlist de contratos de Víctor.
def _dir_hit(rec, ret, flat=5.0):
    """Acierto direccional crudo (se usa para todos los buckets).
    M-09: normaliza primero, para que las filas guardadas con el esquema
    anterior (BUY/AVOID) sigan puntuando en el track record."""
    rec = _reco_norm(rec)
    if rec == RESEARCH_FAVORABLE:
        return ret > 0
    if rec in ("SELL", RESEARCH_DESFAVORABLE):
        return ret < 0
    return abs(ret) < flat

# ── TIPO DE SETUP = EL AGENTE DE VICTOR QUE DOMINA LA TESIS ──────────────────────
# Antes salía de las 7 señales del LLM (score×peso). Ahora sale de los 6 agentes reales:
# el que MÁS puntos aporta al raw_total, es decir score10 × peso de categoría. Eso dice qué
# CLASE de tesis fue la llamada (de valuación, de momentum, de calidad de negocio, …) con
# evidencia medida en vez de una autoevaluación del modelo.
_SETUP_LBL = {"business": "Negocio", "financial": "Financiero", "market": "Mercado",
              "technical": "Técnico", "risk": "Riesgo", "valuation": "Valuación"}
_SETUP_W = {"business": 20, "financial": 15, "market": 20, "technical": 20,
            "risk": 15, "valuation": 10}          # pesos de categoría de Victor (suman 100)


def _dominant_victor_setup(cats):
    """Clave del agente que más puntos aporta (score10 × peso). `cats` = {categoría: score10}."""
    best, bv = None, None
    for k, w in _SETUP_W.items():
        v = (cats or {}).get(k)
        try:
            sc = float(v) if v is not None else None
        except (TypeError, ValueError):
            sc = None
        if sc is None:
            continue
        contrib = sc * w
        if bv is None or contrib > bv:
            bv, best = contrib, k
    return best


def _report_setup(r):
    """Tipo de setup de un reporte guardado = el agente de Victor que domina su puntaje."""
    try:
        cats = json.loads(r.get("victor_categories") or "{}")
    except Exception:
        return "n/d"
    best = _dominant_victor_setup(cats)
    return _SETUP_LBL.get(best, "n/d") if best else "n/d"


def compute_calibration_stats():
    """#2 — Tasa de acierto realizada de las llamadas pasadas. Mejoras sobre la versión previa:
    (a) `by_recommendation` se puntúa UNA vez por reporte en su horizonte más largo disponible →
        muestras INDEPENDIENTES (antes mezclaba 30/90/180 del mismo reporte e inflaba n ~3x);
    (b) hit-rate RELATIVO a SPY (alpha) además del absoluto — en mercado alcista el absoluto engaña;
    (c) granularidad `by_recommendation_horizon` por 30/90/180d.
    Todo sale del agente de Victor: la recomendación viene de sus gates, la convicción de su
    raw_total y el tipo de setup del agente que domina sus 6 categorías. Nada de esto pasa por
    un LLM — el acierto se mide contra el precio real."""
    try:
        conn = _db()
        rows = [dict(r) for r in conn.execute("SELECT * FROM reports ORDER BY created_ts DESC").fetchall()]
        conn.close()
    except Exception:
        return None
    if not rows:
        return None
    spy = _cached_price_series("SPY")
    now = datetime.now().timestamp()

    dedup_hits, dedup_alpha = [], []          # un voto por reporte (horizonte más largo)
    by_rec, by_rec_alpha = {}, {}             # idem, por recomendación
    by_tk, by_setup = {}, {}                   # hit-rate realizado por TICKER y por TIPO DE SETUP (alimentan el Kelly adaptativo)
    hz_hits = {"30": {}, "90": {}, "180": {}}  # por horizonte y recomendación (granular)
    err_list = []
    for r in rows:
        base_p = r.get("price_at_analysis")
        if not base_p:
            continue
        rec = _reco_norm(r.get("recommendation"))
        created = r.get("created_ts", 0)
        pred_up = r.get("upside_pct")
        series = _cached_price_series(r["ticker"])
        base_spy = _price_at(spy, created)
        longest = None                         # (ret, alpha, hit, alpha_hit) del mayor horizonte maduro
        for days in (30, 90, 180):
            hts = created + days * 86400
            if hts > now:
                continue
            p = _price_at(series, hts)
            if not p:
                continue
            ret = (p - base_p) / base_p * 100
            hit = _dir_hit(rec, ret)
            hz_hits[str(days)].setdefault(rec, []).append(hit)
            # alpha vs SPY: ¿batió (BUY) / evitó mejor que (SELL) al mercado?
            alpha_hit = None
            if base_spy and base_spy > 0:
                p_spy = _price_at(spy, hts)
                if p_spy:
                    spy_ret = (p_spy - base_spy) / base_spy * 100
                    alpha = ret - spy_ret
                    if rec == RESEARCH_FAVORABLE:
                        alpha_hit = alpha > 0
                    elif rec in ("SELL", RESEARCH_DESFAVORABLE):
                        alpha_hit = alpha < 0
                    else:
                        alpha_hit = abs(alpha) < 5
            longest = (ret, hit, alpha_hit)
            if days == 180 and pred_up is not None:
                try:
                    err_list.append(ret - float(pred_up))
                except (TypeError, ValueError):
                    pass
        if longest is not None:
            ret, hit, alpha_hit = longest
            dedup_hits.append(hit)
            by_rec.setdefault(rec, []).append(hit)
            by_tk.setdefault(r["ticker"], []).append(hit)
            by_setup.setdefault(_report_setup(r), []).append(hit)
            if alpha_hit is not None:
                dedup_alpha.append(alpha_hit)
                by_rec_alpha.setdefault(rec, []).append(alpha_hit)

    if not dedup_hits:
        return None

    def rate(lst):
        return round(100 * sum(lst) / len(lst), 1) if lst else None
    rec_rates = {k: {"hit_rate": rate(v), "n": len(v)} for k, v in by_rec.items()}
    tk_rates = {k: {"hit_rate": rate(v), "n": len(v)} for k, v in by_tk.items()}
    setup_rates = {k: {"hit_rate": rate(v), "n": len(v)} for k, v in by_setup.items()}
    rec_alpha = {k: {"hit_rate": rate(v), "n": len(v)} for k, v in by_rec_alpha.items()}
    hz_rates = {hz: {k: {"hit_rate": rate(v), "n": len(v)} for k, v in d.items()}
                for hz, d in hz_hits.items()}
    avg_err = round(sum(err_list) / len(err_list), 1) if err_list else None
    return {"overall_hit_rate": rate(dedup_hits), "n": len(dedup_hits),
            "by_recommendation": rec_rates, "avg_upside_error_pct": avg_err,
            "by_ticker": tk_rates, "by_setup": setup_rates,
            "alpha_hit_rate": rate(dedup_alpha), "alpha_n": len(dedup_alpha),
            "by_recommendation_alpha": rec_alpha, "by_recommendation_horizon": hz_rates,
            "n_reports": len(rows)}


# ─────────────────────────────────────────────────────────────────────────────
# ACCURACY TRACKER — scores past reports against actual price action
# ─────────────────────────────────────────────────────────────────────────────
_CALIB_CACHE = {"ts": 0.0, "data": None}
def get_calibration_cached(ttl=600):
    """Cached calibration stats (TTL seconds) to avoid recomputing yfinance history on every analysis."""
    now = datetime.now().timestamp()
    if _CALIB_CACHE["data"] is not None and (now - _CALIB_CACHE["ts"]) < ttl:
        return _CALIB_CACHE["data"]
    data = compute_calibration_stats()
    _CALIB_CACHE["ts"] = now
    _CALIB_CACHE["data"] = data
    return data




# ── #3 INFORMATION COEFFICIENT — ¿cada señal realmente predice? ───────────────
_IC_CACHE = {"ts": 0.0, "data": None, "horizon": None}
_IC_DIMS = ["flujo_institucional_opciones", "fundamentales", "earnings",
            "tecnicos", "news_sec", "macro", "riesgo"]
# (Information Coefficient por señal ELIMINADO: medía el poder predictivo de las 7 señales
#  del LLM, que se retiraron. El track record real vive en compute_calibration_stats y ahora
#  se desglosa por AGENTE de Victor, no por señal.)


# ── #4 RÉGIMEN DE MERCADO — pesos de señales condicionados al régimen ─────────
_REGIME_CACHE = {"ts": 0.0, "data": None}


def compute_regime():
    """Classify the current market regime from liquid signals: volatility (^VIX),
    trend (SPY vs 50/200d SMA + slope), breadth (RSP vs SPY 20d), rates (TLT 20d)."""
    out = {"vol": "normal", "trend": "lateral", "breadth": "neutral", "rates": "estable",
           "vix": None, "spy_vs_200d_pct": None, "detail": {}}
    try:
        vix = vertex_market.Ticker("^VIX").history(period="1mo")
        if not vix.empty:
            v = float(vix["Close"].iloc[-1]); out["vix"] = round(v, 1)
            out["vol"] = "calmo" if v < 15 else "estrés" if v > 25 else "normal"
    except Exception:
        pass
    try:
        spy = vertex_market.Ticker("SPY").history(period="1y")["Close"].dropna()
        if len(spy) > 210:
            last = float(spy.iloc[-1])
            s50 = float(spy.tail(50).mean()); s200 = float(spy.tail(200).mean())
            sma200 = spy.rolling(200).mean()
            slope = float(sma200.iloc[-1] - sma200.iloc[-21])
            out["spy_vs_200d_pct"] = round((last / s200 - 1) * 100, 1)
            if last > s50 and s50 > s200 and slope > 0:
                out["trend"] = "alcista"
            elif last < s200 and slope < 0:
                out["trend"] = "bajista"
            else:
                out["trend"] = "lateral"
            out["detail"]["spy_20d_pct"] = round(float(spy.iloc[-1] / spy.iloc[-21] - 1) * 100, 1)
    except Exception:
        pass
    try:
        rsp = vertex_market.Ticker("RSP").history(period="3mo")["Close"].dropna()
        spy3 = vertex_market.Ticker("SPY").history(period="3mo")["Close"].dropna()
        if len(rsp) > 21 and len(spy3) > 21:
            diff = float(rsp.iloc[-1] / rsp.iloc[-21] - 1) * 100 - float(spy3.iloc[-1] / spy3.iloc[-21] - 1) * 100
            out["breadth"] = "estrecho" if diff < -2 else "amplio" if diff > 2 else "neutral"
            out["detail"]["rsp_minus_spy_20d_pct"] = round(diff, 1)
    except Exception:
        pass
    try:
        tlt = vertex_market.Ticker("TLT").history(period="2mo")["Close"].dropna()
        if len(tlt) > 21:
            r = float(tlt.iloc[-1] / tlt.iloc[-21] - 1) * 100   # TLT down => rates up
            out["detail"]["tlt_20d_pct"] = round(r, 1)
            out["rates"] = "subiendo" if r < -2 else "bajando" if r > 2 else "estable"
    except Exception:
        pass
    out["label"] = (f"Tendencia {out['trend']} · vol {out['vol']} · "
                    f"amplitud {out['breadth']} · tasas {out['rates']}")
    return out


def get_regime_cached(ttl=2700):
    now = datetime.now().timestamp()
    if _REGIME_CACHE["data"] is not None and (now - _REGIME_CACHE["ts"]) < ttl:
        return _REGIME_CACHE["data"]
    data = compute_regime()
    _REGIME_CACHE["ts"] = now
    _REGIME_CACHE["data"] = data
    return data


# (Función regime_signal_weights eliminada junto con el motor de convicción ponderado.)


# Caché del strip de salud: 90 s, para no repetir el sondeo en cada pantalla.
_DH_CACHE = {"ts": 0.0, "data": None}


@app.get("/api/data-health")
def data_health():
    """#5 — Salud de fuentes de datos para el strip persistente: qué está configurado y, en la fuente crítica
    (Quant Data), si está VIVA ahora mismo. Cacheado 90s para no martillar la API. Nunca tradees sobre datos
    stale: si QD sale en rojo, los walls/GEX que ves pueden ser de otra sesión."""
    now = time.time()
    if _DH_CACHE["data"] and now - _DH_CACHE["ts"] < 90:
        return _DH_CACHE["data"]
    # Las CUATRO fuentes de datos del sistema, las mismas que el motor de
    # Victor: FMP, FinnHub, FRED y EDGAR. Aquí estaban Quant Data (plan API
    # inactivo, 403 en todo) y yfinance (raspaba un endpoint sin documentar);
    # las dos salieron del proyecto, así que anunciarlas como fuentes era
    # decir que el sistema se apoya en algo que ya no existe.
    _fmp_ok = bool((os.environ.get("FMP_API_KEY") or "").strip())
    sources = [
        {"key": "fmp", "label": "FMP", "role": "precio · estados · consenso", "critical": True,
         "configured": _fmp_ok, "live": None,
         "note": None if _fmp_ok else "Falta FMP_API_KEY"},
        {"key": "edgar", "label": "SEC EDGAR", "role": "filings · insiders · 13F",
         "critical": True, "configured": True, "live": None,
         "note": "Sin key · identifícate con EDGAR_USER_AGENT"},
        {"key": "fred", "label": "FRED", "role": "macro · tasa libre de riesgo",
         "critical": False, "configured": bool((os.environ.get("FRED_API_KEY") or "").strip()),
         "live": None, "note": None},
        {"key": "gemini", "label": "Gemini", "role": "tesis · IA", "critical": True,
         "configured": bool(API_KEY), "live": None, "note": None if API_KEY else "Falta GEMINI_API_KEY"},
        {"key": "finnhub", "label": "Finnhub", "role": "insiders · earnings", "critical": False,
         "configured": bool(FINNHUB_API_KEY), "live": None, "note": None if FINNHUB_API_KEY else "Falta FINNHUB_API_KEY"},
        {"key": "openai", "label": "OpenAI", "role": "desempate (opc.)", "critical": False,
         "configured": bool(OPENAI_API_KEY), "live": None, "note": None if OPENAI_API_KEY else "Opcional · sin configurar"},
        {"key": "plaid", "label": "Plaid", "role": "portafolio", "critical": False,
         "configured": bool(PLAID_CLIENT_ID and PLAID_SECRET), "live": None,
         "note": None if (PLAID_CLIENT_ID and PLAID_SECRET) else "Sin Plaid: el portafolio usa el snapshot guardado (/api/portfolio/import)"},
    ]
    n_crit_down = sum(1 for s in sources if s["critical"] and (not s["configured"] or s["live"] is False))
    # `ok` era un True literal, asi que la respuesta se contradecia a si misma:
    # decia ok=true junto a status="down". El strip lee `status` y por eso siempre
    # se pinto bien, pero cualquiera que consulte el endpoint directamente lee
    # `ok` primero y concluye lo contrario -- me paso auditando esto.
    out = {"ok": not n_crit_down, "sources": sources, "checked_at": int(now),
           "status": ("down" if n_crit_down else "ok"),
           "critical_down": n_crit_down}
    _DH_CACHE.update(ts=now, data=out)
    return out


@app.get("/api/regime")
def get_regime():
    """Régimen de mercado actual (volatilidad, tendencia, amplitud, tasas).

    Ya NO devuelve la tabla de pesos por señal: esos pesos (25/20/20/15/10/5/5 ajustados
    por régimen) eran del motor de convicción ponderado, que fue eliminado. El régimen se
    mantiene porque es una clasificación de mercado independiente y el track record agrupa por él.
    """
    return {"ok": True, "regime": get_regime_cached()}


@app.get("/api/reports/list")
def reports_list(request: Request, limit: int = 60):
    """#4 — Lista de reportes DURABLES desde el servidor (payload completo) para hidratar el archivo
    multi-dispositivo. Cae en silencio si no hay payloads (DBs viejas sin la columna llena).

    **El archivo es privado.** Devolvía TODOS los reportes a CUALQUIERA, que con
    un solo usuario era lo mismo que devolver los suyos. Con cuentas es otra
    cosa: el análisis de una persona lo leerían las demás. Alimentar al agente y
    publicar tu trabajo no son lo mismo — lo que se comparte está en
    `/api/aprendizaje`, y ahí no aparece quién analizó qué.

    Las filas sin `usuario_id` son de la época de un solo usuario. Se tratan
    como de nadie, no como de todos: solo las ve quien entre sin sesión (la
    puerta del token compartido, que es el propio Kevin o un script suyo).
    """
    u = _usuario_actual(request)
    try:
        conn = _db()
        if u is not None:
            rows = conn.execute(
                "SELECT payload FROM reports WHERE payload IS NOT NULL AND usuario_id=? "
                "ORDER BY created_ts DESC LIMIT ?",
                (u["id"], int(max(1, min(limit, 200))))).fetchall()
        else:
            rows = conn.execute(
                "SELECT payload FROM reports WHERE payload IS NOT NULL AND usuario_id IS NULL "
                "ORDER BY created_ts DESC LIMIT ?",
                (int(max(1, min(limit, 200))),)).fetchall()
        conn.close()
        # Se corta por PESO, no solo por número.
        #
        # `limit` acota cuántas filas se piden, pero no cuánto pesan: con el
        # tope por reporte subido, 60 payloads pueden ser cientos de MB en una
        # sola respuesta y tumbar el proceso. Aquí se para al llegar al tope y
        # se dice cuántos se dejaron fuera — el archivo se hidrata con los más
        # recientes, que es el orden en que vienen.
        _TOPE = _payload_respuesta_max()
        out, peso, recortados = [], 0, 0
        for r in rows:
            try:
                blob = r["payload"]
                if peso + len(blob) > _TOPE and out:
                    recortados = len(rows) - len(out)
                    break
                out.append(json.loads(blob))
                peso += len(blob)
            except Exception:
                continue
        if recortados:
            print(f"[reports/list] {recortados} reportes fuera: la respuesta llegaba "
                  f"al tope de {_TOPE/1e6:.0f} MB")
        return {"ok": True, "reports": out,
                # Se declara. Un archivo recortado en silencio parece un archivo
                # que perdió reportes.
                "recortados": recortados,
                "motivo_recorte": (f"la respuesta llegaba al tope de {_TOPE/1e6:.0f} MB; "
                                   "los más recientes van primero" if recortados else None)}
    except Exception as e:
        return {"ok": False, "error": _error_publico(e, "/api/reports/list"), "reports": []}


@app.post("/api/report-delete")
def report_delete(request: Request, report_id: str):
    """#4 — borra un reporte del archivo del servidor (sincroniza el borrado entre dispositivos).

    POST, no GET. Un GET tiene que ser SEGURO: la especificación de HTTP
    permite que navegadores, prefetchers, escáneres de enlaces y la caché
    de ida/vuelta lo reemitan solos. Este borraba filas, así que cualquiera
    de esas cosas podía borrar reportes sin que nadie pulsara nada —
    y `<img src=".../api/report-delete?report_id=X">` en cualquier página
    lo disparaba. La cookie es `SameSite=Strict`, que corta el caso entre
    sitios, pero la reemisión dentro del propio sitio no depende de eso.
    """
    # Solo lo tuyo. Sin este filtro, cualquiera con una cuenta podría borrar el
    # archivo de otro con solo acertar un `report_id` — y los ids llevan ticker
    # y fecha, así que adivinarlos no es difícil.
    u = _usuario_actual(request)
    try:
        conn = _db()
        if u is not None:
            cur = conn.execute("DELETE FROM reports WHERE report_id=? AND usuario_id=?",
                               (report_id, u["id"]))
        else:
            cur = conn.execute("DELETE FROM reports WHERE report_id=? AND usuario_id IS NULL",
                               (report_id,))
        borradas = cur.rowcount
        conn.commit(); conn.close()
        # No se distingue "no existe" de "no es tuyo": distinguirlos convertiría
        # la ruta en un oráculo de qué reportes tienen los demás.
        return {"ok": True, "borrado": bool(borradas)}
    except Exception:
        # El texto de la excepción puede llevar rutas del servidor o SQL;
        # va al log, no al navegador.
        logging.getLogger("vertex").warning("report-delete falló", exc_info=True)
        return {"ok": False, "error": "no se pudo borrar el reporte"}


@app.get("/api/calibration")
def get_calibration(horizon: int = 90):
    """#4 — Diagrama de fiabilidad (reliability diagram): ¿tu convicción PREDICHA acierta a esa frecuencia?
    Agrupa los reportes por convicción y compara la confianza media de cada bucket con el hit-rate REAL a
    `horizon` días. Cierra el lazo de calibración — el gráfico que demuestra si tu '70%' de verdad acierta 70%."""
    try:
        conn = _db()
        rows = [dict(r) for r in conn.execute(
            "SELECT ticker,created_ts,price_at_analysis,recommendation,conviction FROM reports "
            "WHERE conviction IS NOT NULL AND price_at_analysis IS NOT NULL ORDER BY created_ts ASC").fetchall()]
        conn.close()
        now = datetime.now().timestamp()
        edges = [(0, 50), (50, 60), (60, 70), (70, 80), (80, 90), (90, 101)]
        buckets = {f"{lo}-{min(hi,100)}": {"preds": [], "hits": []} for (lo, hi) in edges}
        scored = 0
        for r in rows:
            conv = _safe_num(r.get("conviction"))
            base_p = _safe_num(r.get("price_at_analysis"))
            rec = _reco_norm(r.get("recommendation"))
            if conv <= 0 or base_p <= 0 or rec not in (RESEARCH_FAVORABLE, "SELL", RESEARCH_DESFAVORABLE):
                continue
            hts = _safe_num(r.get("created_ts")) + horizon * 86400
            if hts > now:
                continue
            p = _price_at(_cached_price_series(r["ticker"]), hts)
            if not p:
                continue
            ret = (p - base_p) / base_p * 100
            hit = 1 if _dir_hit(rec, ret) else 0
            for (lo, hi) in edges:
                if lo <= conv < hi:
                    key = f"{lo}-{min(hi,100)}"
                    buckets[key]["preds"].append(conv)
                    buckets[key]["hits"].append(hit)
                    scored += 1
                    break
        out, gap_acc = [], 0.0
        for key, b in buckets.items():
            n = len(b["hits"])
            if n == 0:
                out.append({"bucket": key, "n": 0, "predicted": None, "realized": None, "gap": None})
                continue
            pred = round(sum(b["preds"]) / n, 1)
            real = round(100 * sum(b["hits"]) / n, 1)
            out.append({"bucket": key, "n": n, "predicted": pred, "realized": real, "gap": round(real - pred, 1)})
            gap_acc += abs(real - pred) * n
        cal_err = round(gap_acc / scored, 1) if scored else None
        return {"ok": True, "horizon": horizon, "scored": scored, "buckets": out,
                "calibration_error": cal_err,
                "note": ("Calibración perfecta = puntos sobre la diagonal (confianza = acierto real). Por encima de la "
                         "diagonal = pesimista (aciertas MÁS de lo que dices); por debajo = sobreconfiado. Necesita "
                         "reportes con ≥" + str(horizon) + "d de maduración para puntuar.")}
    except Exception as e:
        return {"ok": False, "error": _error_publico(e, "/api/calibration")}


def _r_outcome(r, series, now):
    """R-multiple del SUBYACENTE para una recomendación, usando el bracket de escenarios del agente:
    bull = reward / bear = stop para un BUY (invertido para SELL/AVOID). 1R = distancia entrada→stop.
    Recorre el path de cierres y resuelve QUÉ nivel se tocó PRIMERO (target → +reward_R · stop → −1R);
    si ninguno, marca a mercado en R al horizonte más largo maduro. Normaliza el retorno por el riesgo
    que el propio agente declaró (la pata bajista), así un +5% con −25% de riesgo (0.2R) ≠ con −5% (1R).
    No necesita tu contrato real: mide la CALIDAD DE LA SEÑAL del agente en unidades de riesgo."""
    entry = _safe_num(r.get("price_at_analysis"))
    bull = _safe_num(r.get("target_bull")); bear = _safe_num(r.get("target_bear"))
    rec = _reco_norm(r.get("recommendation"))
    created = _safe_num(r.get("created_ts"))
    if entry <= 0 or not series:
        return None
    matured_hts = None
    for days in (30, 90, 180):
        if created + days * 86400 <= now:
            matured_hts = created + days * 86400
    if matured_hts is None:
        return None
    if not (bull and bear and bull > entry > bear):   # bracket sano requerido
        return None
    rec = _reco_norm(rec)
    if rec == RESEARCH_FAVORABLE:
        target, stop, up = bull, bear, True
    elif rec in ("SELL", RESEARCH_DESFAVORABLE):
        target, stop, up = bear, bull, False           # gana si baja al bear; stop si sube al bull
    else:
        return None
    one_r = abs(entry - stop)
    if one_r <= 0:
        return None
    t_hit = s_hit = None
    for (ts, px) in sorted(series, key=lambda x: x[0]):
        if ts < created or ts > matured_hts:
            continue
        if up:
            if px >= target and t_hit is None: t_hit = ts
            if px <= stop and s_hit is None: s_hit = ts
        else:
            if px <= target and t_hit is None: t_hit = ts
            if px >= stop and s_hit is None: s_hit = ts
        if t_hit and s_hit:
            break
    reward_r = abs(target - entry) / one_r
    if t_hit and (s_hit is None or t_hit <= s_hit):
        return round(reward_r, 2)        # target primero → gana sus R
    if s_hit and (t_hit is None or s_hit < t_hit):
        return -1.0                       # stop primero → −1R
    px_end = _price_at(series, matured_hts)   # ninguno → marca a mercado en R
    if not px_end:
        return None
    move = (px_end - entry) if up else (entry - px_end)
    return round(move / one_r, 2)


@app.get("/api/track-record")
def get_track_record():
    """#2 — Track record realista: hit-rate por horizonte (30/90/180d) + hit-rate RELATIVO a SPY
    (alpha) + scoring por magnitud + CURVA DE EQUITY simulada (siguiendo cada call) con drawdown."""
    try:
        conn = _db()
        rows = [dict(r) for r in conn.execute("SELECT * FROM reports ORDER BY created_ts ASC").fetchall()]
        conn.close()
        if not rows:
            return {"total_reports": 0, "scored_reports": 0, "hit_rate_30": None, "hit_rate_90": None,
                    "hit_rate_180": None, "n_30": 0, "n_90": 0, "n_180": 0, "detail": [],
                    "alpha_30": None, "alpha_90": None, "alpha_180": None, "equity_curve": None}

        spy = _cached_price_series("SPY")
        now = datetime.now().timestamp()
        buckets = {"30": [], "90": [], "180": []}
        alpha_buckets = {"30": [], "90": [], "180": []}
        detail = []
        equity_pts = []          # curva de equity siguiendo la dirección de cada call (90d)
        equity = 100.0
        peak = 100.0
        max_dd = 0.0
        equity_r_pts = []        # Batch R · curva de R acumulada (suma de R-múltiplos por call maduro)
        cum_r = 0.0
        wins = losses = 0
        tr_recs = []   # una fila por call madura (90d) → desgloses por ticker / régimen / convicción + expectancy
        for r in rows:
            base_p = r.get("price_at_analysis")
            if not base_p:
                continue
            rec = _reco_norm(r.get("recommendation"))
            created = r.get("created_ts", 0)
            series = _cached_price_series(r["ticker"])
            base_spy = _price_at(spy, created)
            row_eval = {"ticker": r["ticker"], "date": r.get("created_at"), "recommendation": rec,
                        "price_at_analysis": base_p, "fair_value": r.get("fair_value"), "horizons": {}}
            ret90 = None
            for label, days in (("30", 30), ("90", 90), ("180", 180)):
                hts = created + days * 86400
                if hts > now:
                    continue
                p = _price_at(series, hts)
                if not p:
                    continue
                ret = (p - base_p) / base_p * 100
                hit = _dir_hit(rec, ret)
                buckets[label].append(hit)
                alpha = None
                if base_spy and base_spy > 0:
                    p_spy = _price_at(spy, hts)
                    if p_spy:
                        alpha = round(ret - (p_spy - base_spy) / base_spy * 100, 1)
                        a_hit = (alpha > 0) if rec == RESEARCH_FAVORABLE else (alpha < 0) if rec in ("SELL", RESEARCH_DESFAVORABLE) else (abs(alpha) < 5)
                        alpha_buckets[label].append(a_hit)
                # magnitud: clasifica el resultado (no solo signo)
                mag = ("ganancia fuerte" if ret >= 8 else "ganancia" if ret > 1 else
                       "plano" if abs(ret) <= 1 else "pérdida" if ret > -8 else "pérdida fuerte")
                row_eval["horizons"][label] = {"price": round(p, 2), "return_pct": round(ret, 1),
                                               "hit": hit, "alpha_pct": alpha, "magnitud": mag}
                if label == "90":
                    ret90 = ret
            if row_eval["horizons"]:
                detail.append(row_eval)
            # curva de equity: posición direccional a 90d (BUY=+ret, SELL=-ret, HOLD ignora)
            if ret90 is not None and rec in (RESEARCH_FAVORABLE, "SELL", RESEARCH_DESFAVORABLE):
                pnl = ret90 if rec == RESEARCH_FAVORABLE else -ret90
                equity *= (1.0 + pnl / 100.0)
                peak = max(peak, equity)
                max_dd = min(max_dd, (equity - peak) / peak * 100.0)
                equity_pts.append({"date": r.get("created_at"), "ticker": r["ticker"],
                                   "equity": round(equity, 1), "pnl_pct": round(pnl, 1)})
                if pnl > 0: wins += 1
                else: losses += 1
                # desglose: régimen GEX en el momento (del payload) + tier de convicción
                regime = "?"
                try:
                    _pl = json.loads(r.get("payload") or "{}")
                    _ng = (_pl.get("gex") or {}).get("net_gex")
                    if _ng is not None:
                        regime = "GEX+" if float(_ng) >= 0 else "GEX−"
                except Exception:
                    pass
                _cv = r.get("conviction")
                conv_tier = "n/d" if _cv is None else ("alta" if _cv >= 70 else "media" if _cv >= 45 else "baja")
                setup = _report_setup(r)   # #2 — tipo de setup = agente de Victor dominante (score10 × peso)
                _rval = _r_outcome(r, series, now)
                tr_recs.append({"ticker": r["ticker"], "regime": regime, "conv": conv_tier, "setup": setup,
                                "dret": pnl, "r": _rval})
                if _rval is not None:
                    cum_r += _rval
                    equity_r_pts.append({"date": r.get("created_at"), "ticker": r["ticker"],
                                         "cum_r": round(cum_r, 2), "r": round(_rval, 2)})

        def rate(lst): return round(100 * sum(lst) / len(lst), 1) if lst else None

        # ── Desglose por ticker / régimen / convicción, con EXPECTANCY (retorno direccional medio por call) ──
        def _agg(items):
            n = len(items)
            if not n:
                return None
            drets = [x["dret"] for x in items]
            w = [d for d in drets if d > 0]; l = [d for d in drets if d <= 0]
            rr = [x["r"] for x in items if x.get("r") is not None]
            rw = [v for v in rr if v > 0]; rl = [v for v in rr if v <= 0]
            return {"n": n, "hit_rate": round(100 * len(w) / n, 1),
                    "expectancy": round(sum(drets) / n, 2),
                    "avg_win": round(sum(w) / len(w), 2) if w else None,
                    "avg_loss": round(sum(l) / len(l), 2) if l else None,
                    "best": round(max(drets), 2), "worst": round(min(drets), 2),
                    "n_r": len(rr),
                    "expectancy_r": round(sum(rr) / len(rr), 2) if rr else None,
                    "win_rate_r": round(100 * len(rw) / len(rr), 1) if rr else None,
                    "avg_win_r": round(sum(rw) / len(rw), 2) if rw else None,
                    "avg_loss_r": round(sum(rl) / len(rl), 2) if rl else None}

        def _grp(key):
            g = {}
            for x in tr_recs:
                g.setdefault(x[key], []).append(x)
            return {k: _agg(v) for k, v in sorted(g.items(), key=lambda kv: -len(kv[1]))}

        breakdown = {"by_ticker": _grp("ticker"), "by_regime": _grp("regime"),
                     "by_conviction": _grp("conv"), "by_setup": _grp("setup"), "overall": _agg(tr_recs),
                     "basis": ("Retorno direccional a 90d por call (a favor de la recomendación). Expectancy = promedio "
                               "por call; >0 = tus calls ganan plata en promedio. 'R' = R-multiple del SUBYACENTE: el "
                               "agente define el bracket (escenario alcista = reward, bajista = stop), 1R = riesgo declarado; "
                               "expectancy R >0 = la señal del agente gana en unidades de riesgo (normaliza el % por el riesgo "
                               "que el propio agente marcó). Régimen = GEX± en el momento del análisis. Sincero: mide la señal "
                               "del agente sobre el subyacente, NO el R real de tu opción (eso requeriría tu CSV de broker).")}
        eq = None
        if equity_pts:
            eq = {"points": equity_pts[-120:], "final": round(equity, 1),
                  "total_return_pct": round(equity - 100.0, 1), "max_drawdown_pct": round(max_dd, 1),
                  "n_trades": wins + losses, "win_rate": rate([True] * wins + [False] * losses),
                  "note": "Equity simulada siguiendo la dirección de cada call a 90d (no incluye apalancamiento de opciones)."}
            if equity_r_pts:                                  # Batch R · curva de R acumulada del subyacente
                _rv = [p["r"] for p in equity_r_pts]
                eq["points_r"] = equity_r_pts[-120:]
                eq["total_r"] = round(cum_r, 2)
                eq["expectancy_r"] = round(cum_r / len(_rv), 2)
                eq["n_r"] = len(_rv)
                eq["r_note"] = "R acumulada del subyacente: cada call suma su R (objetivo cumplido = +reward·R, stop = −1R), usando el bracket del escenario."
        return {
            "total_reports": len(rows), "scored_reports": len(detail),
            "hit_rate_30": rate(buckets["30"]),   "n_30": len(buckets["30"]),
            "hit_rate_90": rate(buckets["90"]),   "n_90": len(buckets["90"]),
            "hit_rate_180": rate(buckets["180"]), "n_180": len(buckets["180"]),
            "alpha_30": rate(alpha_buckets["30"]), "alpha_90": rate(alpha_buckets["90"]),
            "alpha_180": rate(alpha_buckets["180"]),
            "alpha_note": "Alpha = % de calls que batieron a SPY en ese horizonte (lo que importa de verdad).",
            "equity_curve": eq,
            "breakdown": breakdown,
            "detail": detail[-60:][::-1],
            "generated_at": datetime.now().strftime('%m/%d/%Y, %I:%M:%S %p'),
        }
    except Exception as e:
        raise HTTPException(status_code=500, detail=_error_publico(e, "/api/track-record"))


@app.get("/api/portfolio-edge")
def portfolio_edge():
    """Batch Confirmación · cruza tu LIBRO (snapshot de equity) con tu EDGE MEDIDO por ticker (track-record).
    Responde la pregunta clave: ¿tu capital está donde tienes ventaja, o sobreponderado donde la expectancy
    es negativa o aún desconocida? Marca holdings con expectancy negativa y peso alto, y dónde está tu mejor edge."""
    holdings = get_portfolio_snapshot() or []
    total = sum(float(h.get("value") or 0) for h in holdings) or 0.0
    try:
        tr = get_track_record()
    except Exception:
        tr = {}
    by_tk = ((tr or {}).get("breakdown") or {}).get("by_ticker") or {}
    rows = []
    for h in holdings:
        tk = (h.get("ticker") or "").upper()
        if not tk:
            continue
        val = float(h.get("value") or 0)
        w = round(100 * val / total, 1) if total else 0.0
        ed = by_tk.get(tk) or {}
        e = ed.get("expectancy_r") if ed.get("expectancy_r") is not None else ed.get("expectancy")
        if not ed.get("n"):
            flag = "unknown"
        elif e is None:
            flag = "flat"
        elif e < 0:
            flag = "neg"
        elif e > 0:
            flag = "edge"
        else:
            flag = "flat"
        rows.append({"ticker": tk, "value": round(val, 2), "weight_pct": w, "flag": flag,
                     "n": ed.get("n", 0), "hit_rate": ed.get("hit_rate"),
                     "expectancy": ed.get("expectancy"), "expectancy_r": ed.get("expectancy_r"),
                     "n_r": ed.get("n_r", 0)})
    insights = []
    for r in rows:
        if r["flag"] == "neg" and r["weight_pct"] >= 10:
            _e = (f"{r['expectancy_r']:+.2f}R" if r["expectancy_r"] is not None else f"{r['expectancy']:+.1f}%")
            insights.append(f"{r['ticker']}: {r['weight_pct']}% del libro con expectancy {_e} (negativa) — sobreponderado donde no tienes ventaja.")
        elif r["flag"] == "unknown" and r["weight_pct"] >= 15:
            insights.append(f"{r['ticker']}: {r['weight_pct']}% del libro sin track-record medible todavía — riesgo a ciegas.")
    best = sorted([(k, v) for k, v in by_tk.items() if v.get("n") and v.get("expectancy_r") is not None],
                  key=lambda kv: kv[1]["expectancy_r"], reverse=True)[:3]
    best_edge = [{"ticker": k, "expectancy_r": v["expectancy_r"], "n": v["n"], "hit_rate": v.get("hit_rate")} for k, v in best]
    for be in best_edge:
        held = next((r for r in rows if r["ticker"] == be["ticker"]), None)
        if be["expectancy_r"] and be["expectancy_r"] > 0 and (not held or held["weight_pct"] < 5):
            insights.append(f"Tu mejor edge está en {be['ticker']} ({be['expectancy_r']:+.2f}R, n={be['n']}) pero pesa "
                            f"{held['weight_pct'] if held else 0}% del libro — infraponderado donde ganas.")
    rows.sort(key=lambda x: x["weight_pct"], reverse=True)
    return {"ok": True, "total_value": round(total, 2), "holdings": rows, "best_edge": best_edge,
            "insights": insights[:5], "n_holdings": len(rows),
            "note": "Cruce de tu libro (último snapshot) con tu expectancy medida por ticker. Donde no hay muestra suficiente, la celda queda 'sin datos'."}


# ─────────────────────────────────────────────────────────────────────────────
# SENTIMENT ENGINE
# ─────────────────────────────────────────────────────────────────────────────

@app.get("/api/sentiment")
def get_sentiment(ticker: str):
    ticker_clean = ticker.upper().strip()

    company_name = ticker_clean
    ctx = f"Accion: {ticker_clean}"
    try:
        stock = vertex_market.Ticker(ticker_clean)
        info  = stock.info
        company_name = info.get("longName", ticker_clean)
        sector   = info.get("sector",   "N/A")
        industry = info.get("industry", "N/A")
        price    = info.get("currentPrice") or info.get("regularMarketPrice") or "N/A"
        w52      = info.get("52WeekChange")
        mktcap   = info.get("marketCap")
        pe       = info.get("trailingPE") or "N/A"
        ctx = (
            f"{company_name} ({ticker_clean}) | Sector: {sector} | Industry: {industry} | "
            f"Precio actual: ${price} | Market Cap: {'$'+str(round(mktcap/1e9,1))+'B' if mktcap else 'N/A'} | "
            f"Cambio 52W: {str(round(w52*100,1))+'%' if w52 else 'N/A'} | P/E: {pe}"
        )
    except Exception:
        pass

    # ── Fetch REAL Reddit posts to feed the AI as literal context ───────────
    reddit_posts   = fetch_reddit_posts(ticker_clean, limit=8)
    reddit_context = format_reddit_context(reddit_posts)

    # Date window: only consider posts/news from the last 3 months
    three_months_ago = (datetime.now() - timedelta(days=90)).strftime("%Y-%m-%d")
    today_str = datetime.now().strftime("%Y-%m-%d")

    # Human-readable (Spanish) date range for display in the report/UI
    _meses_es = ["enero","febrero","marzo","abril","mayo","junio","julio",
                  "agosto","septiembre","octubre","noviembre","diciembre"]
    _start_dt = datetime.now() - timedelta(days=90)
    _end_dt   = datetime.now()
    date_range_label = (
        f"{_start_dt.day} de {_meses_es[_start_dt.month-1]} de {_start_dt.year} "
        f"— {_end_dt.day} de {_meses_es[_end_dt.month-1]} de {_end_dt.year}"
    )

    system_msg = (
        "Eres el maximo experto mundial en psicologia del inversor, finanzas conductuales, analisis "
        "de sentimiento de mercados financieros y especulacion de tesis de inversion basada en el "
        "sentir colectivo. Tienes acceso en tiempo real a X (Twitter), Reddit, StockTwits, noticias "
        "financieras y la web. Tu especialidad es descifrar el estado emocional colectivo de los "
        "inversores: miedo, codicia, euforia, panico, FOMO, efecto manada, aversion a la perdida y "
        "sesgos cognitivos. Analizas por que la gente compra o vende, que narrativas los mueven "
        "emocionalmente, y como la psicologia de masas impacta el precio de las acciones. "
        "REGLA CRITICA: SOLO analizas publicaciones, posts, tweets, noticias y discusiones de los "
        "ULTIMOS 3 MESES (90 dias). Ignora completamente cualquier contenido, evento o narrativa "
        "anterior a ese periodo, incluso si es relevante historicamente — el sentimiento de mercado "
        "cambia rapido y solo lo reciente importa para esta lectura. "
        "Ademas de describir el sentimiento, ESPECULAS y construyes una TESIS DE INVERSION propia "
        "basada en lo que la gente esta diciendo: si el consenso emocional de la comunidad tiene "
        "fundamento, hacia donde podria ir el precio si esa narrativa se cumple, y que escenario es "
        "mas probable. SIEMPRE das datos especificos, porcentajes estimados, ejemplos reales y citas "
        "de lo que dice la gente con fecha aproximada. Eres directo, detallado, exhaustivo y objetivo."
    )

    # Misma correccion que en el prompt profundo: sin posts NO se sustituye con
    # el recuerdo del modelo. Reddit responde 403 sin autenticar, asi que esta es
    # la rama que siempre corre.
    reddit_block = reddit_context if reddit_context else (
        "(SIN DATOS de Reddit: la fuente no respondio. NO sustituyas esto con lo "
        "que recuerdes de tu entrenamiento. Declara el sentimiento de foros como "
        "NO DISPONIBLE.)")
    user_msg = f"""Analiza el SENTIMIENTO PSICOLOGICO COMPLETO y ACTUAL de los inversores sobre {ticker_clean} ({company_name}).

Datos del activo: {ctx}

VENTANA DE TIEMPO OBLIGATORIA: Analiza UNICAMENTE publicaciones, posts, tweets, discusiones y noticias publicadas entre {three_months_ago} y {today_str} (ultimos 90 dias / 3 meses). Descarta cualquier informacion, narrativa o evento anterior a {three_months_ago}, sin importar que tan relevante haya sido en su momento. Si citas algo, intenta dar la fecha aproximada (semana o mes) dentro de esta ventana.

POSTS REALES DE REDDIT (extraidos en vivo de r/wallstreetbets, r/stocks, r/investing, r/options, r/StockMarket — ordenados por upvotes para ponderar credibilidad). USA ESTOS POSTS REALES como evidencia principal de lo que la comunidad esta diciendo AHORA. Cita y analiza estos posts especificos cuando sea relevante:
{reddit_block}

Responde con EXACTAMENTE estas 7 secciones. Cada seccion debe ser EXHAUSTIVA, MUY DETALLADA, con datos reales, ejemplos especificos, citas con fecha aproximada y cifras concretas. No seas breve — desarrolla cada punto a fondo:

**1. ESTADO EMOCIONAL COLECTIVO (ULTIMOS 3 MESES)**
Cual es la emocion dominante ahora mismo entre los inversores de {ticker_clean}, considerando solo los ultimos 90 dias?
Clasifica: Euforia / Optimismo / Esperanza / Neutral / Ansiedad / Miedo / Panico
Da el porcentaje estimado de Bulls vs Bears en la comunidad en este momento, y como ha evolucionado ese porcentaje semana a semana dentro de los ultimos 3 meses (iba mejorando? empeorando? estable?).
Explica detalladamente que eventos de los ultimos 3 meses generaron ese estado emocional especifico, con fechas aproximadas.

**2. QUE DICE Y PIENSA LA GENTE (ULTIMOS 3 MESES)**
Que narrativa o "story" ha estado circulando sobre {ticker_clean} en X/Twitter, Reddit r/wallstreetbets, r/investing y StockTwits durante los ultimos 90 dias?
Que argumentos concretos usa la gente para justificar comprar o vender, citando ejemplos de discusiones recientes?
Que frases, opiniones o argumentos se repiten mas en la comunidad reciente? Da multiples ejemplos con fecha aproximada.
Ha cambiado la narrativa durante estos 3 meses? Como era al inicio del periodo vs ahora?

**3. PSICOLOGIA Y SESGOS COGNITIVOS (ULTIMOS 3 MESES)**
Que sesgos psicologicos han dominado a los inversores de {ticker_clean} en los ultimos 90 dias?
(FOMO, Efecto Manada, Sesgo de Confirmacion, Anclaje de Precio, Aversion a la Perdida, Overconfidence, etc.)
Por que la gente ha tomado las decisiones emocionales que ha tomado con esta accion recientemente?
Ha habido euforia irracional, pesimismo exagerado o capitulacion de inversores en este periodo? Da ejemplos concretos con fechas.

**4. CATALIZADORES EMOCIONALES (ULTIMOS 3 MESES)**
Que noticias, earnings, anuncios, movimientos de precio o eventos de los ultimos 90 dias han generado las reacciones emocionales mas fuertes? Lista cada catalizador con fecha aproximada y la reaccion que provoco.
Que genera mas MIEDO y que genera mas OPTIMISMO en la comunidad sobre {ticker_clean} ahora mismo, basado en lo discutido recientemente?
Hay algun rumor, tweet viral o narrativa emergente de las ultimas semanas que este moviendo el sentimiento?

**5. SENTIMIENTO vs FUNDAMENTALES (ULTIMOS 3 MESES)**
El sentimiento de los ultimos 90 dias esta alineado con los fundamentales reales de {company_name} o hay una desconexion peligrosa?
Esta el mercado siendo mas emocional que racional con esta accion en este periodo reciente?
Hay una oportunidad de contrarian investing basada en el sentimiento actual?
Que dice el Fear & Greed Index reciente sobre el mercado en general y como aplica a {ticker_clean}?

**6. TESIS DE INVERSION ESPECULATIVA — BASADA EN EL SENTIR DE LA GENTE**
Basandote en TODO lo que la comunidad ha estado diciendo en los ultimos 3 meses, construye tu propia tesis de inversion especulativa:
- Si el consenso emocional/narrativo de la comunidad tiene fundamento real, hacia donde podria ir el precio de {ticker_clean} en los proximos 3-6 meses?
- Cual es el escenario MAS PROBABLE segun el sentir colectivo reciente (alcista, bajista o lateral) y por que?
- Que tendria que pasar para que la narrativa dominante de la comunidad se confirme o se rompa?
- Que esta viendo la gente que el mercado/Wall Street aun no ha valorado completamente (si aplica)?
- Tu propia especulacion: dado el sentimiento reciente, esto es una señal de alerta (euforia excesiva = riesgo) o una oportunidad temprana (acumulacion silenciosa, cambio de narrativa)?

**7. VEREDICTO PSICOLOGICO DEL EXPERTO**
Como el maximo experto en psicologia del inversor: cual es tu evaluacion final del estado mental colectivo sobre {ticker_clean}, basado en los ultimos 3 meses?
El momento psicologico del mercado favorece o perjudica una inversion ahora mismo?
Que le recomendarias al inversor inteligente basandote en el sentimiento reciente, la psicologia de masas y tu tesis especulativa de la seccion anterior?
Cual es la trampa psicologica mas grande que ves en como la gente percibe a {ticker_clean} en este momento?
"""

    # Antes esto llamaba a `api.x.ai` (Grok) sin ningún respaldo, así que
    # una clave sin configurar apagaba la ruta entera. Ahora usa los DOS
    # proveedores del sistema, los mismos que Victor: Gemini y OpenAI.
    llm_text, _fuente, llm_error = _texto_llm(system_msg, user_msg, temp=0.4)
    llm_ok = bool(llm_text)
    if not llm_ok:
        print(f"[sentiment] ningún proveedor respondió — {llm_error}")


    score, label, color = 50, "NEUTRAL", "amber"
    if llm_ok and llm_text:
        txt = llm_text.lower()
        bulls = ["bullish","alcist","optimism","euforia","compra","subir","sube","rally","positiv",
                 "esperanza","confianza","buy","strong","crecimiento","potencial"]
        bears = ["bearish","bajist","pesimism","panico","miedo","venta","bajar","baja","negativ",
                 "ansiedad","temor","sell","riesgo","caida","preocupacion"]
        bc = sum(txt.count(w) for w in bulls)
        nc = sum(txt.count(w) for w in bears)
        tot = bc + nc
        if tot > 0:
            p = (bc / tot) * 100
            score = round(p)
            if   p >= 70: label, color = "MUY BULLISH",  "emerald"
            elif p >= 55: label, color = "BULLISH",       "emerald"
            elif p >= 45: label, color = "NEUTRAL",       "amber"
            elif p >= 30: label, color = "BEARISH",       "red"
            else:         label, color = "MUY BEARISH",   "red"

    return {
        "ticker":        ticker_clean,
        "company_name":  company_name,
        "overall_score": score,
        "overall_label": label,
        "overall_color": color,
        "llm_ok":       llm_ok,
        "llm_text":     llm_text,
        "llm_error":    llm_error,
        "analysis_date": today_str,
        "window_start":  three_months_ago,
        "window_end":    today_str,
        "date_range_label": date_range_label,
        "reddit_posts_count": len(reddit_posts),
        "reddit_posts": reddit_posts,
        "generated_at":  datetime.now().strftime('%m/%d/%Y, %I:%M:%S %p')
    }

# ─────────────────────────────────────────────────────────────────────────────
# PORTFOLIO ENGINE — Plaid + AI Analysis
# Plaid keys: export PLAID_CLIENT_ID="..." PLAID_SECRET="..." PLAID_ENV="sandbox|development|production"
# ─────────────────────────────────────────────────────────────────────────────
PLAID_CLIENT_ID = os.environ.get("PLAID_CLIENT_ID", "")
PLAID_SECRET    = os.environ.get("PLAID_SECRET",    "")
PLAID_ENV       = os.environ.get("PLAID_ENV",       "production")  # sandbox | production

def plaid_base_url():
    env = PLAID_ENV.lower()
    if env == "production":  return "https://production.plaid.com"
    return "https://sandbox.plaid.com"

def plaid_headers():
    return {"Content-Type": "application/json", "PLAID-CLIENT-ID": PLAID_CLIENT_ID, "PLAID-SECRET": PLAID_SECRET}


# ─────────────────────────────────────────────────────────────────────────────
# CUSTODIA DEL access_token DE PLAID (C-03)
#
# Antes el token viajaba como parámetro de URL en 9 endpoints y se guardaba en
# el localStorage del navegador. Las query strings quedan escritas en los logs
# de acceso de Render, en el historial del navegador y en las cabeceras
# `Referer` hacia terceros — y ese token da lectura de cuentas bancarias.
#
# Ahora vive SOLO en el servidor: `/api/plaid/exchange-token` lo guarda y
# devuelve únicamente el `item_id`. El navegador nunca lo ve, así que no puede
# filtrarlo por ninguna de esas vías.
#
# Cifrado en reposo: si `VERTEX_DB_KEY` está definida se cifra con Fernet
# (AES-128-CBC + HMAC). Protege el caso de que alguien obtenga una copia del
# archivo .db (backup, disco) sin tener el entorno del proceso. No protege
# contra un compromiso de la app — para eso está la autenticación de C-02.
# ─────────────────────────────────────────────────────────────────────────────
def _fernet():
    """Cifrador, o None si no hay clave configurada o falta la librería."""
    key = os.environ.get("VERTEX_DB_KEY", "").strip()
    if not key:
        return None
    try:
        import base64, hashlib
        from cryptography.fernet import Fernet
        # Se deriva una clave Fernet válida de cualquier cadena, para no obligar
        # a generar exactamente 32 bytes en base64url.
        return Fernet(base64.urlsafe_b64encode(hashlib.sha256(key.encode()).digest()))
    except Exception as e:
        print(f"[plaid] cifrado no disponible ({str(e)[:80]}); se guarda en claro")
        return None


def _init_plaid_db():
    try:
        conn = _db()
        conn.execute("""CREATE TABLE IF NOT EXISTS plaid_items (
            item_id      TEXT PRIMARY KEY,
            access_token TEXT NOT NULL,
            encrypted    INTEGER DEFAULT 0,
            institution  TEXT,
            created_at   TEXT
        )""")
        conn.commit(); conn.close()
    except Exception as e:
        print(f"[DB] plaid table init error: {e}")

_init_plaid_db()


def _plaid_save_token(item_id, access_token, institution=""):
    f = _fernet()
    stored = f.encrypt(access_token.encode()).decode() if f else access_token
    conn = _db()
    conn.execute("INSERT OR REPLACE INTO plaid_items "
                 "(item_id,access_token,encrypted,institution,created_at) VALUES (?,?,?,?,?)",
                 (item_id, stored, 1 if f else 0, institution,
                  datetime.now().strftime("%Y-%m-%d %H:%M:%S")))
    conn.commit(); conn.close()
    if not f:
        print("[plaid] AVISO: token guardado SIN cifrar. Define VERTEX_DB_KEY para cifrarlo.")


def _plaid_get_token(item_id=""):
    """El token del `item_id` pedido, o el más reciente. '' si no hay ninguno."""
    try:
        conn = _db()
        if item_id:
            row = conn.execute("SELECT access_token,encrypted FROM plaid_items WHERE item_id=?",
                               (item_id,)).fetchone()
        else:
            row = conn.execute("SELECT access_token,encrypted FROM plaid_items "
                               "ORDER BY created_at DESC LIMIT 1").fetchone()
        conn.close()
        if not row:
            return ""
        tok = row["access_token"]
        if not row["encrypted"]:
            return tok
        f = _fernet()
        if not f:
            print("[plaid] token cifrado pero falta VERTEX_DB_KEY — no se puede leer")
            return ""
        return f.decrypt(tok.encode()).decode()
    except Exception as e:
        print(f"[plaid] lectura de token falló: {str(e)[:110]}")
        return ""


def _plaid_items():
    """Conexiones guardadas, sin exponer nunca el token."""
    try:
        conn = _db()
        rows = conn.execute("SELECT item_id,institution,created_at,encrypted "
                            "FROM plaid_items ORDER BY created_at DESC").fetchall()
        conn.close()
        return [dict(r) for r in rows]
    except Exception:
        return []


@app.post("/api/plaid/link-token")
def create_link_token(body: dict = None):
    """Step 1 — Create a Plaid Link token to open the Plaid UI in the browser."""
    try:
        payload = {
            "client_name":    "Vertex Fund OS",
            "country_codes":  ["US"],
            "language":       "en",
            "products":       ["investments"],
            "user":           {"client_user_id": "vertex_user_001"}
        }
        resp = requests.post(f"{plaid_base_url()}/link/token/create", headers=plaid_headers(), json=payload, timeout=15)
        resp.raise_for_status()
        return resp.json()
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Plaid link token error: {str(e)}")


@app.post("/api/plaid/exchange-token")
def exchange_public_token(body: dict):
    """Paso 2 — canjea el public_token de Plaid Link por el access_token.

    C-03: el access_token se guarda EN EL SERVIDOR y no se devuelve. El
    navegador solo recibe el `item_id`, que no sirve para llamar a Plaid.
    """
    public_token = body.get("public_token", "")
    try:
        resp = requests.post(f"{plaid_base_url()}/item/public_token/exchange",
            headers=plaid_headers(), json={"public_token": public_token}, timeout=15)
        resp.raise_for_status()
        data = resp.json()
        token, item_id = data.get("access_token", ""), data.get("item_id", "")
        if not token:
            raise HTTPException(status_code=502, detail="Plaid no devolvió access_token.")
        inst = ""
        try:                    # nombre del banco, solo para mostrarlo en la UI
            ir = requests.post(f"{plaid_base_url()}/item/get", headers=plaid_headers(),
                               json={"access_token": token}, timeout=10)
            inst = (ir.json().get("item", {}) or {}).get("institution_name", "") if ir.ok else ""
        except Exception:
            pass
        _plaid_save_token(item_id, token, inst)
        return {"ok": True, "item_id": item_id, "institution": inst}
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Plaid exchange error: {str(e)}")


@app.get("/api/plaid/items")
def plaid_items():
    """Conexiones de Plaid guardadas. Nunca incluye el access_token."""
    items = _plaid_items()
    return {"ok": True, "connected": bool(items), "items": items}


@app.post("/api/plaid/disconnect")
def plaid_disconnect(body: dict = None):
    """Borra la conexión guardada (y la invalida en Plaid si se puede)."""
    item_id = str((body or {}).get("item_id") or "")
    token = _plaid_get_token(item_id)
    if token:
        try:                    # invalida el token del lado de Plaid, no solo el nuestro
            requests.post(f"{plaid_base_url()}/item/remove", headers=plaid_headers(),
                          json={"access_token": token}, timeout=10)
        except Exception as e:
            print(f"[plaid] item/remove falló: {str(e)[:90]}")
    try:
        conn = _db()
        conn.execute("DELETE FROM plaid_items WHERE item_id=?", (item_id,)) if item_id \
            else conn.execute("DELETE FROM plaid_items")
        conn.commit(); conn.close()
    except Exception as e:
        return {"ok": False, "error": _error_publico(e, "/api/plaid/disconnect")}
    return {"ok": True}



@app.get("/api/accounts")
def get_accounts(item_id: str = ""):
    """Cuentas de inversión para que el usuario elija cuál analizar.
    C-03: el token sale del servidor, ya no lo manda el cliente."""
    access_token = _plaid_get_token(item_id)
    if not access_token:
        raise HTTPException(status_code=409, detail="No hay ninguna cuenta de Plaid conectada.")
    try:
        resp = requests.post(f"{plaid_base_url()}/investments/holdings/get",
            headers=plaid_headers(), json={"access_token": access_token}, timeout=20)
        resp.raise_for_status()
        data     = resp.json()
        accounts = data.get("accounts", [])
        holdings = data.get("holdings", [])

        hcount = {}
        for h in holdings:
            aid = h.get("account_id", "")
            hcount[aid] = hcount.get(aid, 0) + 1

        result = []
        for a in accounts:
            aid = a.get("account_id", "")
            bal = a.get("balances", {})
            result.append({
                "account_id":    aid,
                "name":          a.get("name", "Account"),
                "official_name": a.get("official_name", ""),
                "type":          a.get("type", ""),
                "subtype":       a.get("subtype", ""),
                "current":       bal.get("current") or 0,
                "iso_currency":  bal.get("iso_currency_code", "USD"),
                "holdings_count": hcount.get(aid, 0),
            })
        return {"accounts": result}
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Accounts error: {str(e)}")

@app.get("/api/portfolio")
def get_portfolio(account_id: str = "", item_id: str = ""):
    """
    Fetch full investment holdings + transactions from Plaid and enrich with:
    - Live prices via yfinance
    - P&L per position (day, week, month, 3M, 6M, 1Y, all-time)
    - Options contracts details
    - AI analysis via Gemini on the full portfolio
    """
    access_token = _plaid_get_token(item_id)   # C-03: del servidor, nunca del cliente
    if not access_token:
        raise HTTPException(status_code=409, detail="No hay ninguna cuenta de Plaid conectada.")
    try:
        # ── Fetch holdings ─────────────────────────────────────────────────
        holdings_resp = requests.post(f"{plaid_base_url()}/investments/holdings/get",
            headers=plaid_headers(), json={"access_token": access_token}, timeout=20)
        holdings_resp.raise_for_status()
        plaid_data = holdings_resp.json()

        # Persistimos el libro en el snapshot: es la fuente única del resto del
        # suite (riesgo, stress, what-if, atribución, guardrails, optimizador,
        # griegas), y así todas esas rutas funcionan después sin el access_token.
        # Best-effort — un fallo de DB nunca rompe la carga del portafolio.
        try:
            _snap_eq = _extract_equity_positions(plaid_data, account_id, with_cost=True)
            if _snap_eq:
                save_portfolio_snapshot(_snap_eq, "PLAID")
            save_options_snapshot(_plaid_extract_options(plaid_data, account_id))
        except Exception as _e:
            print(f"[portfolio] snapshot persist skip: {_e}")

        holdings_all = plaid_data.get("holdings", [])
        securities   = plaid_data.get("securities", [])
        accounts     = plaid_data.get("accounts", [])

        # ── Filter by selected account if provided ─────────────────────
        holdings = [h for h in holdings_all if h.get("account_id") == account_id] if account_id else holdings_all

        # ── Map securities by security_id ──────────────────────────────────
        sec_map = {s["security_id"]: s for s in securities}

        # ── Filter accounts by account_id if provided ──────────────────────
        acct_filter = [a for a in accounts if a.get("account_id") == account_id] if account_id else accounts

        # ── helpers a prueba de nan ────────────────────────────────────────
        # Se definen ANTES del primer uso: estaban declarados ~30 lineas mas
        # abajo, dentro de la misma funcion, asi que el bucle de efectivo de
        # aqui reventaba con UnboundLocalError en cuanto habia una cuenta.
        def safe_float(v, default=0.0):
            try:
                f = float(v)
                if math.isnan(f) or math.isinf(f):
                    return default
                return f
            except (TypeError, ValueError):
                return default

        def safe_round(v, decimals=2, default=0.0):
            return round(safe_float(v, default), decimals)

        # ── Extract cash from ALL account types (Robinhood reports cash
        #    inside brokerage accounts, not as a separate depository) ──────
        cash_balance = 0.0
        cash_positions = []   # Cash shown as a line in the portfolio
        for a in acct_filter:
            bal     = a.get("balances", {})
            subtype = (a.get("subtype") or "").lower()
            atype   = (a.get("type") or "").lower()
            acct_name = a.get("name", "Cash")

            # Robinhood cash: Plaid returns it as available balance in the
            # brokerage account, separate from the securities value.
            # We also catch classic cash/depository accounts.
            current_bal   = safe_float(bal.get("current"))
            available_bal = safe_float(bal.get("available"))

            is_cash_acct = subtype in ("cash", "checking", "savings", "money market")
            is_brokerage  = subtype in ("brokerage",) or atype in ("investment",)

            if is_cash_acct:
                cash_val = current_bal
            elif is_brokerage:
                # For brokerage accounts: available - securities value gives
                # the uninvested cash (what Robinhood calls "Buying Power").
                # Plaid also sometimes puts a separate cash holding with
                # ticker "CUR:USD" — we handle that in the holdings loop.
                # Here we just track the account-level available balance.
                cash_val = available_bal if available_bal > 0 else 0.0
            else:
                cash_val = 0.0

            if cash_val > 0:
                cash_balance += cash_val
                cash_positions.append({
                    "account_id":   a.get("account_id",""),
                    "account_name": acct_name,
                    "cash_value":   safe_round(cash_val),
                    "subtype":      subtype,
                })

        total_cost_basis = safe_float(sum(safe_float(h.get("cost_basis", 0)) for h in holdings))

        stocks  = []
        options = []
        other   = []

        for h in holdings:
            sec      = sec_map.get(h.get("security_id"), {})
            ticker   = (sec.get("ticker_symbol") or "").strip()
            name     = sec.get("name", ticker)
            sec_type = (sec.get("type") or "").lower()

            # Skip cash currency holdings (CUR:USD etc.) — already in cash_balance
            if sec_type == "cash" or ticker.startswith("CUR:") or name.upper() in ("USD", "CASH"):
                # El efectivo ya se sumo desde el saldo de la cuenta mas arriba;
                # sumarlo otra vez desde el holding lo contaria doble. Antes aqui
                # habia una expresion sin efecto (`cash_balance`) que leia como si
                # hiciera algo. Se salta y punto.
                continue

            qty        = safe_float(h.get("quantity", 0))
            cost       = safe_float(h.get("cost_basis", 0))       # Real cost — from Robinhood via Plaid
            inst_val   = safe_float(h.get("institution_value", 0)) # Real current value — from Robinhood via Plaid
            inst_price = safe_float(h.get("institution_price", 0)) # Real price — from Robinhood via Plaid

            is_option = (
                sec_type in ("derivative",)
                or (ticker and len(ticker) > 10)
                or sec.get("option_contract") is not None
            )

            # ── Live price from yfinance (for enrichment only) ─────────────
            live_price    = inst_price   # default: Robinhood's own price
            day_chg_pct   = 0.0
            week_chg_pct  = 0.0
            month_chg_pct = 0.0
            m3_chg_pct    = 0.0
            m6_chg_pct    = 0.0
            y1_chg_pct    = 0.0

            if ticker and not is_option and 1 <= len(ticker) <= 6:
                try:
                    stk = vertex_market.Ticker(ticker)
                    fi  = stk.fast_info

                    raw_px = (
                        getattr(fi, 'last_price', None)
                        or getattr(fi, 'regular_market_price', None)
                    )
                    yf_price = safe_float(raw_px)
                    if yf_price > 0:
                        live_price = yf_price

                    prev_close = safe_float(
                        getattr(fi, 'previous_close', None)
                        or getattr(fi, 'regular_market_previous_close', None)
                    )
                    if prev_close > 0 and live_price > 0:
                        day_chg_pct = safe_round(((live_price - prev_close) / prev_close) * 100)
                    else:
                        try:
                            h2d = stk.history(period="2d")
                            if not h2d.empty and len(h2d) >= 2:
                                c1 = safe_float(h2d['Close'].iloc[-1])
                                c0 = safe_float(h2d['Close'].iloc[-2])
                                day_chg_pct = safe_round(((c1 - c0) / c0) * 100) if c0 > 0 else 0.0
                        except Exception:
                            pass

                    hist_1y = stk.history(period="1y")
                    if not hist_1y.empty and len(hist_1y) >= 2:
                        closes = [safe_float(x) for x in hist_1y['Close'].values if safe_float(x) > 0]
                        if len(closes) >= 2:
                            c_now  = live_price if live_price > 0 else closes[-1]
                            c_5d   = closes[-6]   if len(closes) >= 6   else closes[0]
                            c_30d  = closes[-22]  if len(closes) >= 22  else closes[0]
                            c_90d  = closes[-63]  if len(closes) >= 63  else closes[0]
                            c_180d = closes[-126] if len(closes) >= 126 else closes[0]
                            c_1y   = closes[0]
                            def pct(a, b):
                                a, b = safe_float(a), safe_float(b)
                                return safe_round(((a - b) / b) * 100) if b > 0 else 0.0
                            week_chg_pct  = pct(c_now, c_5d)
                            month_chg_pct = pct(c_now, c_30d)
                            m3_chg_pct    = pct(c_now, c_90d)
                            m6_chg_pct    = pct(c_now, c_180d)
                            y1_chg_pct    = pct(c_now, c_1y)
                except Exception:
                    pass

            # ── P&L: use Plaid's institution_value vs cost_basis ──────────
            # This matches EXACTLY what Robinhood shows — their own numbers.
            # live_price * qty used only as fallback if institution_value = 0.
            current_val = inst_val if inst_val > 0 else safe_round(live_price * qty)
            total_pnl   = safe_round(current_val - cost)
            pnl_pct     = safe_round((total_pnl / cost) * 100) if cost > 0 else 0.0

            h_acct_id   = h.get("account_id", "")
            h_acct_name = next((a.get("name","") for a in accounts if a.get("account_id") == h_acct_id), "")

            position = {
                "ticker":        ticker,
                "name":          name,
                "type":          sec_type,
                "account_id":    h_acct_id,
                "account_name":  h_acct_name,
                "qty":           safe_round(qty, 4),
                "cost_basis":    safe_round(cost),       # Real: what you paid
                "inst_price":    safe_round(inst_price, 4),  # Real: Robinhood price
                "live_price":    safe_round(live_price, 4),  # Enriched: yfinance
                "current_val":   safe_round(current_val),    # Real: Robinhood value
                "total_pnl":     total_pnl,                  # Real: matches Robinhood
                "pnl_pct":       pnl_pct,                    # Real: matches Robinhood
                "alltime_pnl_pct": pnl_pct,
                "day_chg_pct":   safe_round(day_chg_pct),
                "week_chg_pct":  safe_round(week_chg_pct),
                "month_chg_pct": safe_round(month_chg_pct),
                "m3_chg_pct":    safe_round(m3_chg_pct),
                "m6_chg_pct":    safe_round(m6_chg_pct),
                "y1_chg_pct":    safe_round(y1_chg_pct),
            }

            if is_option:
                options.append(position)
            elif sec_type in ("equity", "etf", "mutual fund", "fixed income", ""):
                stocks.append(position)
            else:
                other.append(position)

        # ── Portfolio-level totals — cash is part of the portfolio ────────
        securities_value = safe_float(sum(safe_float(p["current_val"]) for p in stocks + options + other))
        live_total       = safe_round(securities_value + safe_float(cash_balance))  # Total including cash
        total_pnl_dollar = safe_round(live_total - total_cost_basis - safe_float(cash_balance))
        # P&L only on invested positions (not cash) — matches Robinhood's display
        invested_cost    = safe_float(total_cost_basis)
        total_pnl_pct    = safe_round((total_pnl_dollar / invested_cost) * 100) if invested_cost > 0 else 0.0

        # Weighted portfolio period changes — nan-safe, securities only
        def weighted_chg(field):
            total_val = sum(safe_float(p["current_val"]) for p in stocks if safe_float(p["current_val"]) > 0)
            if not total_val:
                return 0.0
            weighted_sum = sum(
                safe_float(p.get(field, 0)) * safe_float(p["current_val"])
                for p in stocks
            )
            return safe_round(safe_float(weighted_sum / total_val))

        portfolio_periods = {
            "1d":  weighted_chg("day_chg_pct"),
            "1w":  weighted_chg("week_chg_pct"),
            "1mo": weighted_chg("month_chg_pct"),
            "3mo": weighted_chg("m3_chg_pct"),
            "6mo": weighted_chg("m6_chg_pct"),
            "1y":  weighted_chg("y1_chg_pct"),
            "all": safe_round(total_pnl_pct),
        }

        # ── Persist equity-book snapshot so the per-stock agent is portfolio-aware ──
        try:
            _snap = [{"ticker": p["ticker"], "name": p.get("name", p["ticker"]),
                      "value": safe_float(p["current_val"])}
                     for p in stocks
                     if p.get("ticker") and 1 <= len(p["ticker"]) <= 6 and safe_float(p["current_val"]) > 0]
            save_portfolio_snapshot(_snap, account_id or "ALL")
        except Exception as _se:
            print(f"[DB] snapshot skip: {_se}")

        # ── AI Portfolio Analysis via Gemini ──────────────────────────────
        acct_label = next((a.get("name","") for a in accounts if a.get("account_id") == account_id), "Todas las cuentas") if account_id else "Todas las cuentas"

        # ─────────────────────────────────────────────────────────────────────
        # STEP A: Enrich each held stock with REAL yfinance fundamentals + news
        # ─────────────────────────────────────────────────────────────────────
        def fetch_yf_fundamentals(ticker: str) -> dict:
            """Pull live price, analyst targets, fundamentals, and news from yfinance."""
            out = {}
            if not ticker or len(ticker) > 6:
                return out
            try:
                stk  = vertex_market.Ticker(ticker)
                info = stk.info or {}
                fi   = stk.fast_info

                live_px = (
                    getattr(fi, 'last_price', None)
                    or getattr(fi, 'regular_market_price', None)
                    or info.get('regularMarketPrice')
                    or info.get('currentPrice')
                )
                prev_cl = (
                    getattr(fi, 'previous_close', None)
                    or info.get('previousClose')
                )
                day_chg = round(((float(live_px) - float(prev_cl)) / float(prev_cl)) * 100, 2) if live_px and prev_cl and float(prev_cl) > 0 else None

                out = {
                    "live_price":        round(float(live_px), 2) if live_px else None,
                    "prev_close":        round(float(prev_cl), 2) if prev_cl else None,
                    "day_chg_pct":       day_chg,
                    "market_cap":        info.get("marketCap"),
                    "forward_pe":        info.get("forwardPE"),
                    "trailing_pe":       info.get("trailingPE"),
                    "revenue_growth":    info.get("revenueGrowth"),       # YoY %
                    "earnings_growth":   info.get("earningsGrowth"),
                    "gross_margins":     info.get("grossMargins"),
                    "operating_margins": info.get("operatingMargins"),
                    "total_revenue":     info.get("totalRevenue"),
                    "free_cashflow":     info.get("freeCashflow"),
                    "total_debt":        info.get("totalDebt"),
                    "cash":              info.get("totalCash"),
                    "52w_high":          info.get("fiftyTwoWeekHigh"),
                    "52w_low":           info.get("fiftyTwoWeekLow"),
                    "analyst_target_mean":   info.get("targetMeanPrice"),
                    "analyst_target_high":   info.get("targetHighPrice"),
                    "analyst_target_low":    info.get("targetLowPrice"),
                    "analyst_recommendation": info.get("recommendationKey"),
                    "num_analyst_opinions":  info.get("numberOfAnalystOpinions"),
                    "short_name":        info.get("shortName") or info.get("longName"),
                    "sector":            info.get("sector"),
                    "industry":          info.get("industry"),
                    "business_summary":  (info.get("longBusinessSummary") or "")[:400],
                }
                # Live news headlines
                try:
                    raw_news = stk.news or []
                    out["news"] = [n.get("title","") for n in raw_news[:5] if n.get("title")]
                except Exception:
                    out["news"] = []
            except Exception:
                pass
            return out

        def fmt_billions(v):
            if v is None: return "N/A"
            v = float(v)
            if abs(v) >= 1e12: return f"${v/1e12:.2f}T"
            if abs(v) >= 1e9:  return f"${v/1e9:.2f}B"
            if abs(v) >= 1e6:  return f"${v/1e6:.2f}M"
            return f"${v:.0f}"

        def fmt_pct(v):
            if v is None: return "N/A"
            return f"{round(float(v)*100, 1)}%"

        # Enrich held stocks
        held_details = []
        for p in stocks[:25]:
            tk = p.get("ticker","")
            if not tk or len(tk) > 6:
                continue
            f = fetch_yf_fundamentals(tk)
            # Use yfinance live price if available, otherwise Plaid price
            live_px_final = f.get("live_price") or p["live_price"]
            cur_val_final = round(float(live_px_final) * float(p["qty"]), 2) if live_px_final else p["current_val"]
            pnl_dollar    = round(cur_val_final - p["cost_basis"], 2) if p["cost_basis"] else 0
            pnl_pct_final = round((pnl_dollar / p["cost_basis"]) * 100, 2) if p["cost_basis"] else 0

            # Analyst upside from live price
            at_mean = f.get("analyst_target_mean")
            upside  = round(((float(at_mean) - float(live_px_final)) / float(live_px_final)) * 100, 1) if at_mean and live_px_final else None

            news_str = " | ".join(f.get("news", [])[:4]) or "No hay noticias recientes"

            block = f"""  [{tk}] {f.get('short_name') or p['name']}
    Sector: {f.get('sector','N/A')} | Industria: {f.get('industry','N/A')}
    Precio LIVE (Yahoo Finance): ${live_px_final} | Cambio hoy: {f.get('day_chg_pct','N/A')}%
    52W High: ${f.get('52w_high','N/A')} | 52W Low: ${f.get('52w_low','N/A')}
    Qty en portafolio: {p['qty']} acciones | Costo promedio pagado: ${p['cost_basis']} (dato real Robinhood/Plaid)
    Valor actual: ${cur_val_final} | P&L real: ${pnl_dollar} ({pnl_pct_final}%)
    Rendimientos: 1D={p['day_chg_pct']}% | 1M={p['month_chg_pct']}% | 3M={p['m3_chg_pct']}% | 6M={p['m6_chg_pct']}% | 1Y={p['y1_chg_pct']}%
    Market Cap: {fmt_billions(f.get('market_cap'))} | Revenue: {fmt_billions(f.get('total_revenue'))} | Revenue Growth YoY: {fmt_pct(f.get('revenue_growth'))}
    Gross Margin: {fmt_pct(f.get('gross_margins'))} | Op Margin: {fmt_pct(f.get('operating_margins'))} | Free Cash Flow: {fmt_billions(f.get('free_cashflow'))}
    Forward P/E: {f.get('forward_pe','N/A')} | Trailing P/E: {f.get('trailing_pe','N/A')}
    Deuda: {fmt_billions(f.get('total_debt'))} | Cash: {fmt_billions(f.get('cash'))}
    Target Wall Street (media): ${at_mean or 'N/A'} | Upside desde precio actual: {upside or 'N/A'}% | Rating: {f.get('analyst_recommendation','N/A')} ({f.get('num_analyst_opinions','N/A')} analistas)
    Target High: ${f.get('analyst_target_high','N/A')} | Target Low: ${f.get('analyst_target_low','N/A')}
    Negocio (resumen): {f.get('business_summary','N/A')}
    Noticias recientes: {news_str}"""
            held_details.append(block)

        # ─────────────────────────────────────────────────────────────────────
        # STEP B: Fetch REAL live data for the growth watchlist from Yahoo Finance
        # These are curated tickers the AI will analyze and recommend from.
        # Data is 100% real — no simulation, no fake numbers.
        # ─────────────────────────────────────────────────────────────────────
        GROWTH_WATCHLIST = [
            # AI Infrastructure & Software
            "PLTR","APP","CRWD","NET","SNOW","DDOG","MDB","GTLB","HUBS",
            # Quantum Computing
            "IONQ","RGTI","QUBT","QMCO",
            # Semiconductors (non-Mag7)
            "AMD","AVGO","AMAT","ARM","MRVL","SMCI","TSM","ASML",
            # Robotics / Autonomous
            "RDDT","PATH","ACHR",
            # Space Economy
            "RKLB","LUNR","ASTS",
            # Biotech / Longevity
            "RXRX","SANA","CRSP","BEAM","NTLA",
            # Nuclear / Clean Energy
            "OKLO","NNE","CEG","BWXT",
            # Fintech Disruptivo
            "HOOD","AFRM","SOFI","NU",
            # Stable 30% (Mag7 + leaders)
            "NVDA","MSFT","GOOGL","META","AAPL","AMZN","TSLA","NFLX",
        ]

        watchlist_data = {}
        for tk in GROWTH_WATCHLIST:
            watchlist_data[tk] = fetch_yf_fundamentals(tk)

        def build_watchlist_line(tk: str) -> str:
            f = watchlist_data.get(tk, {})
            live_px = f.get("live_price")
            at_mean = f.get("analyst_target_mean")
            upside  = round(((float(at_mean) - float(live_px)) / float(live_px)) * 100, 1) if at_mean and live_px and float(live_px) > 0 else None
            news_str = " | ".join(f.get("news", [])[:3]) or "—"
            return (
                f"  [{tk}] {f.get('short_name','') or tk} | Sector: {f.get('sector','N/A')}\n"
                f"    Precio LIVE: ${live_px or 'N/A'} | 1D: {f.get('day_chg_pct','N/A')}% | 52W High: ${f.get('52w_high','N/A')} | 52W Low: ${f.get('52w_low','N/A')}\n"
                f"    Market Cap: {fmt_billions(f.get('market_cap'))} | Revenue: {fmt_billions(f.get('total_revenue'))} | Rev Growth YoY: {fmt_pct(f.get('revenue_growth'))}\n"
                f"    Gross Margin: {fmt_pct(f.get('gross_margins'))} | Free Cash Flow: {fmt_billions(f.get('free_cashflow'))}\n"
                f"    Wall St Target (media): ${at_mean or 'N/A'} | Upside: {upside or 'N/A'}% | Rating: {f.get('analyst_recommendation','N/A')} ({f.get('num_analyst_opinions','N/A')} analistas)\n"
                f"    Negocio: {f.get('business_summary','')[:250]}\n"
                f"    Noticias: {news_str}"
            )

        growth_tickers  = [t for t in GROWTH_WATCHLIST if t not in ("NVDA","MSFT","GOOGL","META","AAPL","AMZN","TSLA","NFLX")]
        stable_tickers  = ["NVDA","MSFT","GOOGL","META","AAPL","AMZN","TSLA","NFLX"]
        growth_data_str = "\n".join(build_watchlist_line(t) for t in growth_tickers)
        stable_data_str = "\n".join(build_watchlist_line(t) for t in stable_tickers)

        options_summary = "\n".join([
            f"  {p['ticker']}: {p['qty']} contratos | Cost: ${p['cost_basis']} | Valor actual: ${p['current_val']} | P&L: ${p['total_pnl']} ({p['pnl_pct']}%)"
            for p in options[:15]
        ])

        ai_prompt = f"""Eres el CIO mas agresivo y visionario de Vertex Holding Group. Tienes acceso a busqueda web en tiempo real para enriquecer el analisis con noticias actuales y perspectivas de mercado.

TODOS LOS DATOS A CONTINUACION SON REALES — extraidos directamente de Yahoo Finance (yfinance) y Robinhood via Plaid Production. NINGUN dato es simulado o inventado.

══════════════════════════════════════════════
CUENTA: {acct_label}
PORTAFOLIO TOTAL: ${round(live_total,2)} | COSTO TOTAL REAL: ${round(total_cost_basis,2)} | P&L TOTAL: ${total_pnl_dollar} ({total_pnl_pct}%) | CASH: ${round(cash_balance,2)}
RENDIMIENTO PONDERADO: 1D={portfolio_periods['1d']}% | 1W={portfolio_periods['1w']}% | 1M={portfolio_periods['1mo']}% | 3M={portfolio_periods['3mo']}% | 6M={portfolio_periods['6mo']}% | 1Y={portfolio_periods['1y']}%
══════════════════════════════════════════════

POSICIONES ACTUALES CON DATOS REALES DE YAHOO FINANCE + ROBINHOOD/PLAID:
{chr(10).join(held_details) if held_details else 'Sin posiciones en acciones'}

OPCIONES ACTUALES:
{options_summary if options_summary else 'Ninguna'}

══════════════════════════════════════════════
UNIVERSE DE OPORTUNIDADES — DATOS REALES YAHOO FINANCE (usa estos numeros exactos en tus recomendaciones):

--- GROWTH EXPONENCIAL (para el 70% del portafolio) ---
{growth_data_str}

--- ESTABLES MAGNIFICAS 7 + LIDERES (para el 30% del portafolio) ---
{stable_data_str}
══════════════════════════════════════════════

FILOSOFIA DE INVERSION:
- 30% ESTABLES: Mag 7 (NVDA, AAPL, MSFT, AMZN, GOOGL, META, TSLA) + Netflix. Crecimiento solido, moat enorme, liderazgo consolidado. Siguen creciendo extraordinariamente.
- 70% GROWTH EXPONENCIAL: Companias fuera de Mag 7 que pueden multiplicarse 5x-10x+ en 5 años. Sectores: AI pura, computacion cuantica, robotica, espacio, biotech/longevity, energia nuclear, semiconductores next-gen, fintech. Prioriza las que tienen: revenue creciendo +40% YoY, targets de analistas muy superiores al precio actual, y gran TAM.

INSTRUCCION CRITICA: Para cada recomendacion DEBES usar los precios reales de Yahoo Finance que se te dieron arriba. Para cada accion que recomiendas: explica el negocio en terminos simples (que hace, como gana dinero), la tesis de inversion completa basada en los datos reales, y por que es una oportunidad HOY basado en sus numeros reales.

Responde con EXACTAMENTE estas 11 secciones. Usa los datos reales. Se especifico y agresivo:

**1. DIAGNOSTICO DEL PORTAFOLIO**
Analiza el portafolio actual con los datos reales. Que posiciones ganan, cuales pierden, cuanto en $ real. Evalua la asignacion actual vs la estrategia 30/70. Identifica capital mal asignado.

**2. ANALISIS PROFUNDO POR POSICION — TESIS Y ESTADO**
Para CADA accion en el portafolio (usando los datos reales de arriba):
- Precio live de Yahoo Finance, P&L real en $ desde Robinhood, rendimiento por periodo
- Target de Wall Street vs precio actual: upside/downside %
- TESIS: que hace esta empresa (en palabras simples), por que sube o baja, noticias recientes que la afectan, si tiene fundamentales para seguir creciendo
- Decision: FUERTE COMPRA / AGREGAR / MANTENER / REDUCIR / VENDER
- Explicacion del porque basada en los numeros reales

**3. ANALISIS DE OPCIONES — CONVICTION Y PROBABILIDAD**
Para cada contrato de opciones: probabilidad de exito estimada (%), conviccion 0-100, analisis del strike vs precio actual vs target, accion recomendada con razonamiento.

**4. RECOMENDACIONES GROWTH 70% — CON DATOS REALES DE YAHOO FINANCE**
De las acciones del universe de growth de arriba, selecciona las MEJORES oportunidades. Para cada una que recomiendes:
- Nombre de la empresa y que hace (explicacion simple: "Esta empresa hace X, gana dinero cuando Y")
- Precio LIVE de Yahoo Finance (usa el dato real de arriba)
- Target Wall Street y upside % (del dato real de arriba)
- Revenue growth YoY y margenes (del dato real de arriba)
- Noticias recientes que la catalizan
- TESIS completa: por que esta empresa va a multiplicarse, que productos o servicios esta lanzando, cuando empezara a generar ingresos masivos, que dice Wall Street, y el multiplicador esperado en 3-5 años
- Cuanto % del portafolio asignar

**5. RECOMENDACIONES ESTABLES 30% — CON DATOS REALES DE YAHOO FINANCE**
De las Mag 7 y lideres, cuales agregar o mantener. Para cada una:
- Precio LIVE de Yahoo Finance (dato real)
- Target Wall Street y upside % (dato real)
- Por que agregar ahora: catalizadores proximos (earnings, lanzamientos AI, expansion)
- Explicacion simple del negocio
- Crecimiento anual esperado y % del portafolio a asignar

**6. QUE VENDER O REDUCIR — LIBERACION DE CAPITAL**
Posiciones del portafolio actual a vender o reducir (con razonamiento basado en datos reales). Cuanto capital libera y donde reasignarlo especificamente.

**7. LISTA MAESTRA DE OPORTUNIDADES — TODAS LAS QUE CUMPLEN LA TESIS**
Ranking completo de todas las acciones del universe de arriba que cumplen la tesis de growth exponencial. Para cada una: precio real, target real, upside %, multiplicador potencial 3-5 años, tesis resumida en 2-3 oraciones, y por que es oportunidad HOY.

**8. EL FUTURO — DONDE ESTA EL DINERO EN 5-10 AÑOS**
Basado en los datos de revenue growth, margins, y noticias de las empresas del universe: que sectores estan posicionados para explotar. AI Agents/AGI, computacion cuantica, fusion nuclear, humanoides, longevity, next-gen chips, space economy. Cuales son los pure plays con mejores fundamentales actuales (usa los datos reales).

**9. TESIS POR SECTOR DEL FUTURO**
Para cada sector clave: tesis completa de inversion, punto de inflexion en 2-3 años, las 2-3 mejores companias del universe con sus datos reales (precio, target, upside, revenue growth), y multiplicador esperado. Explica el negocio de cada compania en terminos simples.

**10. PLAN DE ACCION — ESTRATEGIA 30/70 CON CAPITAL DISPONIBLE**
Con el cash disponible de ${round(cash_balance,2)} y el capital que se libere vendiendo: plan paso a paso, que comprar primero, montos especificos en $, en que orden, como llegar a la asignacion 30/70 ideal. Usa precios reales para calcular cuantas acciones comprar.

**11. PROYECCION A 1, 3 Y 5 AÑOS**
Partiendo de ${round(live_total,2)}: valor proyectado implementando este plan en escenario base/bull/bear. CAGR esperado. Hitos anuales especificos con numeros en $.
"""

        ai_response = ""
        try:
            gemini_resp = _gemini_genera(
                model='gemini-2.5-flash',
                contents=ai_prompt,
                config=types.GenerateContentConfig(
                    temperature=0.35,
                    max_output_tokens=10000,
                    tools=[types.Tool(google_search=types.GoogleSearch())]
                )
            )
            ai_response = gemini_resp.text
        except Exception:
            try:
                gemini_resp = _gemini_genera(
                    model='gemini-2.5-flash',
                    contents=ai_prompt,
                    config=types.GenerateContentConfig(temperature=0.35, max_output_tokens=10000)
                )
                ai_response = gemini_resp.text
            except Exception as ex2:
                ai_response = f"[AI no disponible: {str(ex2)}]"

        # ── Final safety pass: replace any nan/inf in entire response ──────
        def sanitize_json(obj):
            if isinstance(obj, float):
                return 0.0 if (math.isnan(obj) or math.isinf(obj)) else obj
            if isinstance(obj, dict):
                return {k: sanitize_json(v) for k, v in obj.items()}
            if isinstance(obj, list):
                return [sanitize_json(i) for i in obj]
            return obj

        return sanitize_json({
            "connected":          True,
            # ── Portfolio totals ───────────────────────────────────────────
            "total_value":        safe_round(live_total),          # Securities + Cash
            "securities_value":   safe_round(securities_value),    # Invested positions only
            "total_cost_basis":   safe_round(total_cost_basis),    # What you paid (Robinhood/Plaid)
            "total_pnl_dollar":   safe_round(total_pnl_dollar),    # $ gain/loss on invested positions
            "total_pnl_pct":      safe_round(total_pnl_pct),       # % gain/loss — matches Robinhood
            # ── Cash ──────────────────────────────────────────────────────
            "cash_balance":       safe_round(cash_balance),        # Uninvested cash (buying power)
            "cash_positions":     cash_positions,                  # Breakdown by account
            # ── Positions ─────────────────────────────────────────────────
            "portfolio_periods":  portfolio_periods,
            "stocks":             stocks,
            "options":            options,
            "other":              other,
            "accounts":           accounts,
            "ai_analysis":        ai_response,
            "generated_at":       datetime.now().strftime('%m/%d/%Y, %I:%M:%S %p')
        })

    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Portfolio error: {str(e)}")


# ─────────────────────────────────────────────────────────────────────────────
# PORTFOLIO RISK ENGINE — factor exposure, VaR, beta, concentration (Aladdin-style)
# ─────────────────────────────────────────────────────────────────────────────
def compute_portfolio_risk(positions, lookback="1y"):
    """Quant risk engine over real holdings: annualized volatility, Value-at-Risk,
    market beta, factor exposures, concentration metrics and per-position risk
    contribution. `positions` = list of {ticker, name, value}."""
    import pandas as pd

    eq = [p for p in positions if p.get("ticker") and float(p.get("value") or 0) > 0]
    if len(eq) < 1:
        return {"ok": False, "error": "No hay posiciones de acciones para analizar."}

    total_val = sum(float(p["value"]) for p in eq)
    weights_all = {p["ticker"]: float(p["value"]) / total_val for p in eq}
    name_map = {p["ticker"]: p.get("name", p["ticker"]) for p in eq}
    tickers = list(weights_all.keys())

    factor_etfs = {
        "SPY": "Mercado (S&P 500)",
        "QQQ": "Tech (Nasdaq 100)",
        "SMH": "Semiconductores",
        "IWM": "Small Caps (Russell 2000)",
    }
    all_syms = list(set(tickers + list(factor_etfs.keys())))

    closes = {}
    for s in all_syms:
        try:
            h = vertex_market.Ticker(s).history(period=lookback)
            if not h.empty and "Close" in h:
                ser = h["Close"]
                ser.index = [d.strftime("%Y-%m-%d") for d in ser.index]
                closes[s] = ser
        except Exception:
            pass
    if not closes:
        return {"ok": False, "error": "No se pudo obtener historial de precios."}

    px = pd.DataFrame(closes).dropna(how="all")
    rets = px.pct_change().dropna(how="all")

    avail = [t for t in tickers if t in rets.columns]
    if not avail:
        return {"ok": False, "error": "Sin datos de retorno suficientes para las posiciones."}

    # Renormalize weights to the positions we actually have data for
    w = np.array([weights_all[t] for t in avail], dtype=float)
    w = w / w.sum()
    R = rets[avail].dropna()
    if R.shape[0] < 20:
        return {"ok": False, "error": "Historial insuficiente (se necesitan ~20+ días)."}

    cov_daily = R.cov().values                      # daily covariance matrix
    port_daily = R.values @ w                        # portfolio daily returns
    port_var_daily = float(w @ cov_daily @ w)
    port_vol_daily = math.sqrt(max(port_var_daily, 1e-12))
    ann = math.sqrt(252)
    port_vol_annual = port_vol_daily * ann

    # ── Value-at-Risk (1-day) ────────────────────────────────────────────────
    mean_d = float(np.mean(port_daily))
    std_d  = float(np.std(port_daily, ddof=1)) if len(port_daily) > 1 else port_vol_daily
    hist_var95 = -float(np.percentile(port_daily, 5))
    hist_var99 = -float(np.percentile(port_daily, 1))
    par_var95  = -(mean_d - 1.645 * std_d)
    par_var99  = -(mean_d - 2.326 * std_d)

    def pos(x): return max(x, 0.0)
    var = {
        "hist_95_pct": round(pos(hist_var95) * 100, 2), "hist_95_usd": round(pos(hist_var95) * total_val, 2),
        "hist_99_pct": round(pos(hist_var99) * 100, 2), "hist_99_usd": round(pos(hist_var99) * total_val, 2),
        "param_95_pct": round(pos(par_var95) * 100, 2), "param_95_usd": round(pos(par_var95) * total_val, 2),
        "param_99_pct": round(pos(par_var99) * 100, 2), "param_99_usd": round(pos(par_var99) * total_val, 2),
    }

    # ── Market beta + factor exposures (univariate betas) ────────────────────
    def beta_to(sym):
        if sym not in rets.columns:
            return None
        joined = pd.concat([pd.Series(port_daily, index=R.index, name="p"), rets[sym].rename("f")], axis=1).dropna()
        if joined.shape[0] < 20:
            return None
        f = joined["f"].values; p = joined["p"].values
        vf = float(np.var(f, ddof=1))
        if vf <= 0:
            return None
        b = float(np.cov(p, f, ddof=1)[0, 1] / vf)
        corr = float(np.corrcoef(p, f)[0, 1])
        return {"beta": round(b, 2), "corr": round(corr, 2)}

    market = beta_to("SPY")
    factor_exposure = []
    for sym, label in factor_etfs.items():
        bt = beta_to(sym)
        if bt:
            factor_exposure.append({"symbol": sym, "label": label, "beta": bt["beta"], "corr": bt["corr"]})

    # ── Concentration ────────────────────────────────────────────────────────
    w_full = np.array([weights_all[t] for t in tickers], dtype=float)
    hhi = float(np.sum(w_full ** 2))
    eff_n = round(1.0 / hhi, 1) if hhi > 0 else None
    sorted_w = sorted(weights_all.items(), key=lambda kv: kv[1], reverse=True)
    top1 = sorted_w[0] if sorted_w else (None, 0)
    top3 = sum(v for _, v in sorted_w[:3])
    top5 = sum(v for _, v in sorted_w[:5])

    # Average pairwise correlation (hidden-concentration signal)
    corr_m = R.corr()
    n = len(avail)
    if n > 1:
        iu = np.triu_indices(n, k=1)
        avg_pair_corr = round(float(np.nanmean(corr_m.values[iu])), 2)
    else:
        avg_pair_corr = None

    # Diversification ratio = weighted avg of individual vols / portfolio vol
    indiv_vol = R.std(ddof=1).values * ann
    wavg_vol  = float(np.sum(w * indiv_vol))
    diversification_ratio = round(wavg_vol / port_vol_annual, 2) if port_vol_annual > 0 else None

    # ── Per-position risk contribution ───────────────────────────────────────
    marginal = cov_daily @ w                          # marginal contribution to variance
    ctr = w * marginal                                # contribution to variance per asset
    ctr_pct = (ctr / port_var_daily * 100) if port_var_daily > 0 else np.zeros_like(ctr)
    positions_risk = []
    for i, t in enumerate(avail):
        positions_risk.append({
            "ticker": t,
            "name": name_map.get(t, t),
            "weight_pct": round(w[i] * 100, 2),
            "annual_vol_pct": round(float(indiv_vol[i]) * 100, 1),
            "beta_spy": (beta_to(t) or {}).get("beta"),
            "risk_contribution_pct": round(float(ctr_pct[i]), 1),
        })
    positions_risk.sort(key=lambda x: x["risk_contribution_pct"], reverse=True)

    # ── Correlation matrix (for the heatmap tab later) ───────────────────────
    corr_matrix = {
        "tickers": avail,
        "matrix": [[round(float(corr_m.values[i][j]), 2) for j in range(n)] for i in range(n)],
    }

    return {
        "ok": True,
        "total_value": round(total_val, 2),
        "positions_analyzed": len(avail),
        "positions_skipped": [t for t in tickers if t not in avail],
        "lookback": lookback,
        "annual_vol_pct": round(port_vol_annual * 100, 1),
        "market_beta": market["beta"] if market else None,
        "market_corr": market["corr"] if market else None,
        "var": var,
        "factor_exposure": factor_exposure,
        "concentration": {
            "top_holding_ticker": top1[0],
            "top_holding_pct": round(top1[1] * 100, 1) if top1[0] else None,
            "top3_pct": round(top3 * 100, 1),
            "top5_pct": round(top5 * 100, 1),
            "hhi": round(hhi, 3),
            "effective_holdings": eff_n,
            "num_positions": len(tickers),
            "avg_pairwise_corr": avg_pair_corr,
            "diversification_ratio": diversification_ratio,
        },
        "positions_risk": positions_risk,
        "correlation_matrix": corr_matrix,
        "generated_at": datetime.now().strftime('%m/%d/%Y, %I:%M:%S %p'),
    }


def compute_portfolio_stress(positions, lookback_days=504):
    """Stress & scenario engine over real holdings: historical crisis replay on
    the CURRENT book, Monte Carlo (historical bootstrap), Expected Shortfall
    (CVaR), hypothetical factor shocks, and risk-adjusted performance metrics.
    `positions` = list of {ticker, name, value}."""
    import pandas as pd

    eq = [p for p in positions if p.get("ticker") and float(p.get("value") or 0) > 0]
    if len(eq) < 1:
        return {"ok": False, "error": "No hay posiciones de acciones para analizar."}

    total_val = sum(float(p["value"]) for p in eq)
    weights_all = {p["ticker"]: float(p["value"]) / total_val for p in eq}
    tickers = list(weights_all.keys())

    factor_syms = ["SPY", "QQQ", "SMH", "IWM", "TLT"]
    all_syms = list(set(tickers + factor_syms))

    # ── One max-history fetch per symbol; slice locally for crisis windows ────
    full = {}
    for s in all_syms:
        try:
            h = vertex_market.Ticker(s).history(period="max")
            if not h.empty and "Close" in h and len(h) > 1:
                ser = h["Close"].copy()
                ser.index = pd.to_datetime([d.strftime("%Y-%m-%d") for d in ser.index])
                full[s] = ser
        except Exception:
            pass
    if not full:
        return {"ok": False, "error": "No se pudo obtener historial de precios."}

    pxf = pd.DataFrame(full).sort_index()
    px = pxf.tail(lookback_days + 1)              # recent window for metrics/MC/betas
    rets = px.pct_change().dropna(how="all")
    avail = [t for t in tickers if t in rets.columns and rets[t].notna().sum() >= 40]
    if not avail:
        return {"ok": False, "error": "Sin datos de retorno suficientes para las posiciones."}

    w = np.array([weights_all[t] for t in avail], dtype=float)
    w = w / w.sum()
    R = rets[avail].dropna()
    if R.shape[0] < 60:
        return {"ok": False, "error": "Historial insuficiente para stress (~60+ dias)."}

    port_daily = R.values @ w
    ann = math.sqrt(252)

    # ── Risk-adjusted performance metrics ────────────────────────────────────
    RF = 0.043
    mean_d = float(np.mean(port_daily)); std_d = float(np.std(port_daily, ddof=1))
    ann_return = (1.0 + mean_d) ** 252 - 1.0
    ann_vol = std_d * ann
    dn = port_daily[port_daily < 0]
    dd_dev = float(np.std(dn, ddof=1)) * ann if len(dn) > 1 else 0.0
    curve = np.cumprod(1.0 + port_daily); peak = np.maximum.accumulate(curve)
    ddraw = curve / peak - 1.0; max_dd = float(ddraw.min())
    metrics = {
        "ann_return_pct": round(ann_return * 100, 1),
        "ann_vol_pct": round(ann_vol * 100, 1),
        "sharpe": round((ann_return - RF) / ann_vol, 2) if ann_vol > 0 else None,
        "sortino": round((ann_return - RF) / dd_dev, 2) if dd_dev > 0 else None,
        "calmar": round(ann_return / abs(max_dd), 2) if max_dd < 0 else None,
        "max_drawdown_pct": round(max_dd * 100, 1),
        "time_underwater_pct": round(float(np.mean(ddraw < -1e-9)) * 100, 1),
        "rf_assumed_pct": round(RF * 100, 1),
        "window_days": int(R.shape[0]),
    }

    # ── Expected Shortfall (CVaR), 1-day ─────────────────────────────────────
    def es(level):
        q = np.percentile(port_daily, level); tail = port_daily[port_daily <= q]
        return -float(np.mean(tail)) if len(tail) else 0.0
    cvar = {
        "es_95_pct": round(max(es(5), 0.0) * 100, 2), "es_95_usd": round(max(es(5), 0.0) * total_val, 2),
        "es_99_pct": round(max(es(1), 0.0) * 100, 2), "es_99_usd": round(max(es(1), 0.0) * total_val, 2),
    }

    # ── Monte Carlo (historical bootstrap of portfolio daily returns) ────────
    rng = np.random.default_rng(42)
    def mc(horizon, n=5000):
        idx = rng.integers(0, len(port_daily), size=(n, horizon))
        sampled = port_daily[idx]
        paths = np.cumprod(1.0 + sampled, axis=1)
        end = paths[:, -1] - 1.0
        peaks = np.maximum.accumulate(paths, axis=1)
        ddp = (paths / peaks - 1.0).min(axis=1)
        P = lambda a, p: round(float(np.percentile(a, p)) * 100, 1)
        return {
            "horizon_days": horizon,
            "p5": P(end, 5), "p25": P(end, 25), "p50": P(end, 50), "p75": P(end, 75), "p95": P(end, 95),
            "prob_loss": round(float(np.mean(end < 0)) * 100, 1),
            "prob_loss_10": round(float(np.mean(end < -0.10)) * 100, 1),
            "prob_loss_20": round(float(np.mean(end < -0.20)) * 100, 1),
            "exp_max_drawdown_pct": round(float(np.mean(ddp)) * 100, 1),
            "worst_p5_usd": round(float(np.percentile(end, 5)) * total_val, 2),
            "best_p95_usd": round(float(np.percentile(end, 95)) * total_val, 2),
        }
    monte_carlo = {"h21": mc(21), "h252": mc(252)}

    # ── Betas (recent) for hypothetical shocks + crisis proxy ────────────────
    def beta_pair(a_ret, b_ret):
        j = pd.concat([a_ret.rename("a"), b_ret.rename("b")], axis=1).dropna()
        if j.shape[0] < 40: return None
        vb = float(np.var(j["b"].values, ddof=1))
        if vb <= 0: return None
        return float(np.cov(j["a"].values, j["b"].values, ddof=1)[0, 1] / vb)

    port_ret_series = pd.Series(port_daily, index=R.index)
    betas = {s: (beta_pair(port_ret_series, rets[s]) if s in rets.columns else None) for s in factor_syms}

    def shock(sym, mv):
        b = betas.get(sym)
        if b is None: return None
        return {"port_pct": round(b * mv * 100, 1), "port_usd": round(b * mv * total_val, 2), "beta": round(b, 2)}
    raw_hyp = [
        ("S&P 500 \u221210%", "Correccion amplia de mercado", "SPY", -0.10),
        ("Nasdaq 100 \u221215%", "Selloff tecnologico", "QQQ", -0.15),
        ("Semiconductores \u221220%", "Shock al sector semis", "SMH", -0.20),
        ("Small Caps \u221210%", "Risk-off en small caps", "IWM", -0.10),
        ("Tasas +100 pb", "Subida de tasas (proxy TLT \u22129%)", "TLT", -0.09),
    ]
    hypotheticals = []
    for label, detail, sym, mv in raw_hyp:
        sh = shock(sym, mv)
        if sh: hypotheticals.append({"label": label, "detail": detail, **sh})

    # ── Historical crisis replay on CURRENT weights ──────────────────────────
    crises = [
        ("gfc_2008",   "Crisis Financiera 2008", "2008-09-01", "2009-03-09"),
        ("covid_2020", "Crash COVID 2020",       "2020-02-19", "2020-03-23"),
        ("bear_2022",  "Bear Market 2022",       "2022-01-03", "2022-10-12"),
        ("q4_2018",    "Selloff Q4 2018",        "2018-09-20", "2018-12-24"),
        ("dotcom",     "Dot-com 2000\u20132002", "2000-03-10", "2002-10-09"),
    ]
    spy_beta = {}
    if "SPY" in rets.columns:
        for t in avail:
            spy_beta[t] = beta_pair(rets[t], rets["SPY"])

    def window_ret(sym, start, end):
        if sym not in pxf.columns: return None
        seg = pxf[sym].loc[start:end].dropna()
        if len(seg) < 2: return None
        return float(seg.iloc[-1] / seg.iloc[0] - 1.0)

    scenarios = []
    for key, label, start, end in crises:
        spy_r = window_ret("SPY", start, end)
        port_ret = 0.0; covered = 0.0; proxied = 0.0
        for i, t in enumerate(avail):
            wr = window_ret(t, start, end)
            if wr is not None:
                port_ret += w[i] * wr; covered += w[i]
            elif spy_r is not None and spy_beta.get(t) is not None:
                port_ret += w[i] * spy_beta[t] * spy_r; proxied += w[i]
        scenarios.append({
            "key": key, "label": label, "window": f"{start} \u2192 {end}",
            "port_pct": round(port_ret * 100, 1), "port_usd": round(port_ret * total_val, 2),
            "spy_pct": round(spy_r * 100, 1) if spy_r is not None else None,
            "coverage_pct": round(covered * 100, 0), "proxied_pct": round(proxied * 100, 0),
        })

    return {
        "ok": True,
        "total_value": round(total_val, 2),
        "positions_analyzed": len(avail),
        "positions_skipped": [t for t in tickers if t not in avail],
        "metrics": metrics,
        "cvar": cvar,
        "monte_carlo": monte_carlo,
        "hypotheticals": hypotheticals,
        "scenarios": scenarios,
        "generated_at": datetime.now().strftime('%m/%d/%Y, %I:%M:%S %p'),
    }


def _resolve_positions(account_id="", with_cost=False):
    """Posiciones normalizadas: de Plaid si hay conexión guardada (C-03: el token
    se lee del servidor)
    o del snapshot guardado si no lo hay — así todo el suite de portafolio corre
    con o sin Plaid. Cuando Plaid responde, el resultado se PERSISTE en el snapshot
    para que las rutas siguientes funcionen sin volver a pedir el token.
    Devuelve (positions, source)."""
    access_token = _plaid_get_token()          # C-03: del servidor, nunca del cliente
    if access_token:
        resp = requests.post(f"{plaid_base_url()}/investments/holdings/get",
                             headers=plaid_headers(), json={"access_token": access_token}, timeout=20)
        resp.raise_for_status()
        _data = resp.json()
        positions = _extract_equity_positions(_data, account_id, with_cost=with_cost)
        # Persistimos lo que Plaid devolvió: el snapshot es la fuente única del
        # resto del suite, y así una llamada posterior SIN access_token sigue
        # encontrando el libro. Best-effort: un fallo de DB no rompe la respuesta.
        try:
            if positions:
                save_portfolio_snapshot(
                    _extract_equity_positions(_data, account_id, with_cost=True), "PLAID")
            save_options_snapshot(_plaid_extract_options(_data, account_id))
        except Exception as _e:
            print(f"[portfolio] snapshot persist skip: {_e}")
        return positions, "plaid"
    return get_portfolio_snapshot(), "snapshot"


@app.get("/api/portfolio-risk")
def get_portfolio_risk(account_id: str = ""):
    """Run the quant risk engine on the user's real holdings (Plaid o snapshot guardado)."""
    try:
        positions, _src = _resolve_positions(account_id)
        if not positions:
            return {"ok": False, "error": "No se encontraron posiciones. Conecta Plaid o carga tu portafolio con POST /api/portfolio/import."}
        return compute_portfolio_risk(positions)
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Risk engine error: {str(e)}")


@app.get("/api/portfolio-stress")
def get_portfolio_stress(account_id: str = ""):
    """Run the stress engine on the user's real holdings (Plaid o snapshot guardado)."""
    try:
        positions, _src = _resolve_positions(account_id)
        if not positions:
            return {"ok": False, "error": "No se encontraron posiciones. Conecta Plaid o carga tu portafolio con POST /api/portfolio/import."}
        return compute_portfolio_stress(positions)
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Stress engine error: {str(e)}")


@app.get("/api/portfolio-whatif")
def get_portfolio_whatif(ticker: str = "", action: str = "add",
                         amount: float = 0.0, account_id: str = ""):
    """Recompute portfolio risk BEFORE vs AFTER a hypothetical add/trim of `ticker`."""
    try:
        ticker = ticker.upper().strip()

        def sf(v, d=0.0):
            try:
                f = float(v); return d if (math.isnan(f) or math.isinf(f)) else f
            except (TypeError, ValueError):
                return d

        positions, _src = _resolve_positions(account_id)
        if not positions:
            return {"ok": False, "error": "No se encontraron posiciones. Conecta Plaid o carga tu portafolio con POST /api/portfolio/import."}

        after = [dict(p) for p in positions]
        found = next((p for p in after if p["ticker"] == ticker), None)
        amt = abs(sf(amount))
        if action == "add":
            if found:
                found["value"] += amt
            else:
                after.append({"ticker": ticker, "name": ticker, "value": amt})
        elif action == "trim":
            if not found:
                return {"ok": False, "error": f"{ticker} no esta en tu portafolio para recortar."}
            found["value"] = max(0.0, found["value"] - amt)
            after = [p for p in after if p["value"] > 0]
        else:
            return {"ok": False, "error": "action debe ser 'add' o 'trim'."}

        before_r = compute_portfolio_risk(positions)
        after_r = compute_portfolio_risk(after)
        if not before_r.get("ok") or not after_r.get("ok"):
            return {"ok": False, "error": before_r.get("error") or after_r.get("error") or "No se pudo calcular el riesgo."}

        def slim(r):
            c = r.get("concentration", {}); v = r.get("var", {})
            return {
                "total_value": r.get("total_value"),
                "annual_vol_pct": r.get("annual_vol_pct"),
                "market_beta": r.get("market_beta"),
                "var_95_usd": v.get("hist_95_usd"), "var_95_pct": v.get("hist_95_pct"),
                "effective_holdings": c.get("effective_holdings"),
                "top_holding_ticker": c.get("top_holding_ticker"),
                "top_holding_pct": c.get("top_holding_pct"),
                "avg_pairwise_corr": c.get("avg_pairwise_corr"),
                "num_positions": c.get("num_positions"),
            }

        b = slim(before_r); a = slim(after_r)

        def d(x, y):
            return round(y - x, 2) if (isinstance(x, (int, float)) and isinstance(y, (int, float))) else None

        delta = {
            "total_value": d(b["total_value"], a["total_value"]),
            "annual_vol_pct": d(b["annual_vol_pct"], a["annual_vol_pct"]),
            "market_beta": d(b["market_beta"], a["market_beta"]),
            "var_95_usd": d(b["var_95_usd"], a["var_95_usd"]),
            "effective_holdings": d(b["effective_holdings"], a["effective_holdings"]),
            "avg_pairwise_corr": d(b["avg_pairwise_corr"], a["avg_pairwise_corr"]),
        }

        new_w = None
        af = next((p for p in after if p["ticker"] == ticker), None)
        if af:
            tot = sum(p["value"] for p in after)
            new_w = round(af["value"] / tot * 100, 1) if tot else None

        return {"ok": True, "ticker": ticker, "action": action, "amount": amt,
                "before": b, "after": a, "delta": delta, "new_weight_pct": new_w,
                "generated_at": datetime.now().strftime('%m/%d/%Y, %I:%M:%S %p')}
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"What-if error: {str(e)}")



# ── PERFORMANCE ATTRIBUTION + GUARDRAILS ──────────────────────────────────────
STABLE_LEADERS = {"AAPL", "MSFT", "GOOGL", "GOOG", "AMZN", "META", "NVDA", "BRK.B",
                  "BRK-B", "AVGO", "TSLA", "JPM", "V", "MA", "COST", "WMT", "JNJ",
                  "PG", "HD", "UNH", "XOM", "LLY",
                  # Dividend stalwarts / defensivas estables (bucket 30%)
                  "KO", "PEP", "MCD", "ABBV", "MRK", "CVX", "PFE", "T", "VZ", "CSCO",
                  "IBM", "MMM", "CAT", "HON", "TXN", "CL", "KMB", "PM", "MO", "SO",
                  "DUK", "O", "ABT", "MDT", "CB", "ADP", "LOW", "TGT", "GD", "LMT"}


def _fetch_sectors(tickers):
    """Best-effort ticker -> sector map via yfinance."""
    out = {}
    for t in tickers:
        try:
            info = vertex_market.Ticker(t).info or {}
            out[t] = info.get("sector") or "Otros"
        except Exception:
            out[t] = "Otros"
    return out


def compute_portfolio_attribution(positions, lookback_days=252):
    """Decompose trailing return into per-position and per-sector contributions."""
    import pandas as pd
    eq = [p for p in positions if p.get("ticker") and float(p.get("value") or 0) > 0]
    if not eq:
        return {"ok": False, "error": "No hay posiciones de acciones para analizar."}
    total = sum(float(p["value"]) for p in eq)
    weights = {p["ticker"]: float(p["value"]) / total for p in eq}
    names = {p["ticker"]: p.get("name", p["ticker"]) for p in eq}
    tickers = list(weights.keys())

    closes = {}
    for t in tickers:
        try:
            h = vertex_market.Ticker(t).history(period="2y")
            if not h.empty and "Close" in h:
                closes[t] = h["Close"]
        except Exception:
            pass
    if not closes:
        return {"ok": False, "error": "No se pudo obtener historial de precios."}

    px = pd.DataFrame(closes).dropna(how="all").tail(lookback_days + 1)
    period_ret = {}
    for t in tickers:
        if t in px.columns:
            s = px[t].dropna()
            if len(s) >= 2 and float(s.iloc[0]) > 0:
                period_ret[t] = float(s.iloc[-1] / s.iloc[0] - 1.0)
    avail = [t for t in tickers if t in period_ret]
    if not avail:
        return {"ok": False, "error": "Sin retornos suficientes para atribucion."}

    wsum = sum(weights[t] for t in avail)
    sectors = _fetch_sectors(avail)

    pos_rows = []
    total_contrib = 0.0
    for t in avail:
        w = weights[t] / wsum
        contrib = w * period_ret[t]
        total_contrib += contrib
        pos_rows.append({
            "ticker": t, "name": names.get(t, t), "sector": sectors.get(t, "Otros"),
            "weight_pct": round(w * 100, 1),
            "period_return_pct": round(period_ret[t] * 100, 1),
            "contribution_pct": round(contrib * 100, 2),
        })
    pos_rows.sort(key=lambda r: r["contribution_pct"], reverse=True)

    sec_map = {}
    for r in pos_rows:
        s = r["sector"]
        sec_map.setdefault(s, {"sector": s, "weight_pct": 0.0, "contribution_pct": 0.0, "positions": 0})
        sec_map[s]["weight_pct"] += r["weight_pct"]
        sec_map[s]["contribution_pct"] += r["contribution_pct"]
        sec_map[s]["positions"] += 1
    sectors_list = sorted(sec_map.values(), key=lambda x: x["contribution_pct"], reverse=True)
    for s in sectors_list:
        s["weight_pct"] = round(s["weight_pct"], 1)
        s["contribution_pct"] = round(s["contribution_pct"], 2)

    negatives = [r for r in pos_rows if r["contribution_pct"] < 0]
    return {
        "ok": True,
        "lookback_days": int(min(lookback_days, px.shape[0] - 1)),
        "total_return_pct": round(total_contrib * 100, 1),
        "positions": pos_rows,
        "sectors": sectors_list,
        "top_contributors": pos_rows[:3],
        "top_detractors": negatives[-3:][::-1],
        "positions_analyzed": len(avail),
        "generated_at": datetime.now().strftime('%m/%d/%Y, %I:%M:%S %p'),
    }


def compute_portfolio_guardrails(positions_pnl, risk):
    """Check the book against portfolio rules. positions_pnl: list of
    {ticker,name,value,cost_basis,sector}. risk: compute_portfolio_risk() result."""
    eq = [p for p in positions_pnl if p.get("ticker") and float(p.get("value") or 0) > 0]
    if not eq:
        return {"ok": False, "error": "No hay posiciones para evaluar."}
    total = sum(float(p["value"]) for p in eq)
    rules = []

    def add(name, status, value, threshold, detail):
        rules.append({"rule": name, "status": status, "value": value,
                      "threshold": threshold, "detail": detail})

    # 1 — single-position concentration
    top = max(eq, key=lambda p: p["value"])
    top_w = top["value"] / total * 100
    st = "breach" if top_w > 25 else "warn" if top_w > 20 else "ok"
    add("Concentracion por posicion", st, f"{top['ticker']} {round(top_w, 1)}%", "<=25%",
        f"Tu posicion mas grande es {top['ticker']} con {round(top_w, 1)}% del book.")

    # 2 — top-3 concentration
    top3 = sum(sorted([p["value"] for p in eq], reverse=True)[:3]) / total * 100
    st = "breach" if top3 > 60 else "warn" if top3 > 50 else "ok"
    add("Concentracion Top-3", st, f"{round(top3, 1)}%", "<=60%",
        f"Tus 3 posiciones mas grandes suman {round(top3, 1)}% del book.")

    # 3 — 30/70 mandate
    stable_val = sum(p["value"] for p in eq if p["ticker"].upper() in STABLE_LEADERS)
    stable_pct = stable_val / total * 100
    growth_pct = 100 - stable_pct
    st = "ok" if abs(stable_pct - 30) <= 10 else "warn"
    add("Mandato 30/70 (estable/crecimiento)", st, f"{round(stable_pct)}/{round(growth_pct)}", "30/70 +-10",
        f"Lideres estables {round(stable_pct)}% vs crecimiento {round(growth_pct)}%. Objetivo 30/70.")

    # 4 — sector concentration
    sec_w = {}
    for p in eq:
        s = p.get("sector", "Otros")
        sec_w[s] = sec_w.get(s, 0) + p["value"]
    if sec_w:
        top_sec, top_sec_val = max(sec_w.items(), key=lambda x: x[1])
        top_sec_pct = top_sec_val / total * 100
        st = "breach" if top_sec_pct > 50 else "warn" if top_sec_pct > 40 else "ok"
        add("Concentracion sectorial", st, f"{top_sec} {round(top_sec_pct, 1)}%", "<=40%",
            f"Tu sector mas pesado es {top_sec} con {round(top_sec_pct, 1)}% del book.")

    # 5 — stop-loss review (equity down hard)
    losers = []
    for p in eq:
        cb = float(p.get("cost_basis") or 0)
        if cb > 0:
            pnl = (p["value"] - cb) / cb * 100
            if pnl <= -25:
                losers.append(f"{p['ticker']} ({round(pnl)}%)")
    st = "warn" if losers else "ok"
    add("Revision stop-loss (equity -25%)", st, f"{len(losers)} posiciones", "revisar si A-grade",
        ("Posiciones con perdida >25%: " + ", ".join(losers) + ". Tu regla: equity A-grade sin stop fijo, revisa la tesis.")
        if losers else "Ninguna posicion de equity con perdida mayor a 25%.")

    # 6 — book correlation (from risk engine)
    apc = (risk or {}).get("concentration", {}).get("avg_pairwise_corr")
    if apc is not None:
        st = "breach" if apc >= 0.8 else "warn" if apc >= 0.7 else "ok"
        add("Correlacion del book", st, f"{apc}", "<0.70",
            f"Correlacion promedio entre posiciones {apc}. Alta = diversificacion oculta baja.")

    summary = {
        "breaches": sum(1 for r in rules if r["status"] == "breach"),
        "warnings": sum(1 for r in rules if r["status"] == "warn"),
        "passes": sum(1 for r in rules if r["status"] == "ok"),
    }
    return {"ok": True, "rules": rules, "summary": summary, "total_value": round(total, 2),
            "generated_at": datetime.now().strftime('%m/%d/%Y, %I:%M:%S %p')}


def _extract_equity_positions(data, account_id, with_cost=False):
    """Shared Plaid holdings -> equity positions extractor."""
    holdings_all = data.get("holdings", []); securities = data.get("securities", [])
    sec_map = {s["security_id"]: s for s in securities}
    holdings = [h for h in holdings_all if h.get("account_id") == account_id] if account_id else holdings_all

    def sf(v, d=0.0):
        try:
            f = float(v); return d if (math.isnan(f) or math.isinf(f)) else f
        except (TypeError, ValueError):
            return d

    positions = []
    for h in holdings:
        sec = sec_map.get(h.get("security_id"), {})
        tk = (sec.get("ticker_symbol") or "").strip()
        nm = sec.get("name", tk); stp = (sec.get("type") or "").lower()
        if stp == "cash" or tk.startswith("CUR:") or nm.upper() in ("USD", "CASH"):
            continue
        is_opt = (stp in ("derivative",) or (tk and len(tk) > 10) or sec.get("option_contract") is not None)
        if is_opt or not tk or len(tk) > 6:
            continue
        if stp not in ("equity", "etf", "mutual fund", ""):
            continue
        val = sf(h.get("institution_value", 0))
        if val <= 0:
            val = sf(h.get("institution_price", 0)) * sf(h.get("quantity", 0))
        if val > 0:
            pos = {"ticker": tk, "name": nm, "value": val}
            if with_cost:
                pos["cost_basis"] = sf(h.get("cost_basis", 0))
            positions.append(pos)
    return positions


def _plaid_extract_options(data, account_id=""):
    """Extractor de posiciones de OPCIONES desde el payload de Plaid, a la misma
    forma plana que consume el motor de griegas (`compute_options_analytics`).

    `_extract_equity_positions` descarta las opciones a propósito (solo quiere el
    libro de acciones); esta es su contraparte. Plaid expone el contrato en
    `security.option_contract` con `contract_type` / `strike_price` /
    `expiration_date` / `underlying_security_ticker`.

    Devuelve [] si el broker no reporta opciones — que es lo honesto: sin
    contrato no hay griegas, y no se inventa ninguna.
    """
    holdings_all = data.get("holdings", []) or []
    securities = data.get("securities", []) or []
    sec_map = {s["security_id"]: s for s in securities if s.get("security_id")}
    holdings = ([h for h in holdings_all if h.get("account_id") == account_id]
                if account_id else holdings_all)

    def sf(v, d=0.0):
        try:
            f = float(v)
            return d if (math.isnan(f) or math.isinf(f)) else f
        except (TypeError, ValueError):
            return d

    out = []
    for h in holdings:
        sec = sec_map.get(h.get("security_id"), {})
        oc = sec.get("option_contract")
        if not isinstance(oc, dict):
            continue
        und = str(oc.get("underlying_security_ticker") or "").upper().strip()
        ot = str(oc.get("contract_type") or "").lower().strip()
        ot = "call" if ot.startswith("c") else "put" if ot.startswith("p") else ""
        strike = sf(oc.get("strike_price"))
        expiry = str(oc.get("expiration_date") or "")[:10]
        contracts = sf(h.get("quantity"))          # con signo: negativo = corto
        if not (und and ot and strike > 0 and len(expiry) == 10 and contracts != 0):
            continue
        # Plaid cotiza la opción por acción; el valor del contrato es ×100.
        price = sf(h.get("institution_price"))
        value = sf(h.get("institution_value")) or round(contracts * price * 100, 2)
        cost = sf(h.get("cost_basis"))
        avg = round(cost / (abs(contracts) * 100), 4) if cost and contracts else None
        out.append({"underlying": und, "option_type": ot, "strike": strike,
                    "expiry": expiry, "contracts": contracts, "price": price,
                    "avg_price": avg, "value": round(value, 2)})
    return out


@app.get("/api/portfolio-attribution")
def get_portfolio_attribution(account_id: str = "", lookback_days: int = 252):
    """Return + sector attribution over a trailing window on the real book."""
    try:
        positions, _src = _resolve_positions(account_id)
        if not positions:
            return {"ok": False, "error": "No se encontraron posiciones. Conecta Plaid o carga tu portafolio con POST /api/portfolio/import."}
        return compute_portfolio_attribution(positions, lookback_days=max(20, min(lookback_days, 504)))
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Attribution error: {str(e)}")


@app.get("/api/portfolio-guardrails")
def get_portfolio_guardrails(account_id: str = ""):
    """Check the real book against portfolio rules (concentration, 70/30, stops, corr)."""
    try:
        positions, _src = _resolve_positions(account_id, with_cost=True)
        if not positions:
            return {"ok": False, "error": "No se encontraron posiciones. Conecta Plaid o carga tu portafolio con POST /api/portfolio/import."}
        sectors = _fetch_sectors([p["ticker"] for p in positions])
        for p in positions:
            p["sector"] = sectors.get(p["ticker"], "Otros")
        risk = compute_portfolio_risk([{"ticker": p["ticker"], "name": p["name"], "value": p["value"]} for p in positions])
        return compute_portfolio_guardrails(positions, risk if risk.get("ok") else None)
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Guardrails error: {str(e)}")



# ── IMPORTACIÓN DE PORTAFOLIO (fuente-agnóstica) ──────────────────────────────
# El snapshot guardado (tablas portfolio_holdings / option_holdings) es la fuente
# ÚNICA que alimenta todo el suite de portafolio: riesgo, stress, what-if,
# atribución, guardrails, optimizador, ideas y el panel de griegas. Ninguna de
# esas rutas conoce al broker: solo leen el snapshot.
#
# Hoy lo escriben dos caminos:
#   1. Plaid  — `/api/portfolio` y `_resolve_positions` lo persisten solos.
#   2. Manual — `/api/portfolio/import` (abajo). Es el punto de extensión para
#      cualquier fuente futura: solo tiene que emitir las dos formas de abajo.
#
# Formas normalizadas (contrato estable — no cambiar sin migrar la DB):
#   acción : {"ticker","name","value","cost_basis"?}
#   opción : {"underlying","option_type"("call"|"put"),"strike",
#             "expiry"("YYYY-MM-DD"),"contracts","price","avg_price"?,"value"}

def _norm_import_positions(rows):
    """Valida y normaliza posiciones de acciones de una fuente externa.
    Descarta filas sin ticker o sin valor positivo en vez de guardar basura."""
    out = []
    for r in (rows or []):
        if not isinstance(r, dict):
            continue
        tk = str(r.get("ticker") or "").upper().strip()
        if not tk or len(tk) > 6:
            continue
        val = _safe_num(r.get("value"), 0.0)
        if val <= 0:
            continue
        pos = {"ticker": tk, "name": str(r.get("name") or tk), "value": round(val, 2)}
        cb = r.get("cost_basis")
        if cb not in (None, ""):
            pos["cost_basis"] = round(_safe_num(cb, 0.0), 2)
        out.append(pos)
    return out


def _norm_import_options(rows):
    """Igual que arriba, para opciones: exige los 5 campos que el motor de griegas
    necesita (subyacente, tipo, strike, vencimiento, contratos)."""
    out = []
    for r in (rows or []):
        if not isinstance(r, dict):
            continue
        und = str(r.get("underlying") or "").upper().strip()
        ot = str(r.get("option_type") or "").lower().strip()
        ot = "call" if ot.startswith("c") else "put" if ot.startswith("p") else ""
        strike = _safe_num(r.get("strike"), 0.0)
        expiry = str(r.get("expiry") or "")[:10]
        contracts = _safe_num(r.get("contracts"), 0.0)
        if not (und and ot and strike > 0 and len(expiry) == 10 and contracts != 0):
            continue
        price = _safe_num(r.get("price"), 0.0)
        avg = r.get("avg_price")
        out.append({
            "underlying": und, "option_type": ot, "strike": strike, "expiry": expiry,
            "contracts": contracts, "price": price,
            "avg_price": (_safe_num(avg, 0.0) if avg not in (None, "") else None),
            # El precio de una opción es por acción → ×100 por contrato.
            "value": round(_safe_num(r.get("value"), contracts * price * 100), 2)})
    return out


@app.post("/api/portfolio/import")
def portfolio_import(body: dict = None):
    """Carga del portafolio desde cualquier fuente (manual, CSV, broker futuro).

    Body: {"positions": [...], "options": [...], "source": "manual"}
    Ambas listas son opcionales: se manda la que se quiera reemplazar.
    REEMPLAZA el snapshot (no hace merge) — el snapshot representa "el libro tal
    como está ahora", igual que hacía el camino de broker.
    """
    body = body or {}
    src = str(body.get("source") or "manual").upper()[:20]
    positions = _norm_import_positions(body.get("positions"))
    options = _norm_import_options(body.get("options"))
    if not positions and not options:
        return {"ok": False, "error": ("No se recibió ninguna posición válida. Formato esperado: "
                                       "positions[] con {ticker, value>0}; options[] con "
                                       "{underlying, option_type, strike, expiry, contracts}.")}
    if positions:
        save_portfolio_snapshot(positions, src)
    if options:
        save_options_snapshot(options)
    return {"ok": True, "source": src.lower(), "n_positions": len(positions),
            "n_options": len(options),
            "generated_at": datetime.now().strftime('%m/%d/%Y, %I:%M:%S %p')}


@app.get("/api/portfolio/snapshot")
def portfolio_snapshot_status():
    """Qué hay guardado ahora mismo. La UI lo consulta para saber si el suite de
    análisis puede correr sin conectar Plaid."""
    eq = get_portfolio_snapshot()
    op = get_options_snapshot()
    return {"ok": True, "has_data": bool(eq or op),
            "n_positions": len(eq), "n_options": len(op),
            "total_value": round(sum(_safe_num(p.get("value"), 0.0) for p in eq), 2)}


@app.post("/api/portfolio/clear")
def portfolio_clear():
    """Borra el snapshot guardado (el equivalente a 'desconectar')."""
    save_portfolio_snapshot([])
    save_options_snapshot([])
    return {"ok": True}

# ── OPTIONS GREEKS ENGINE (Black-Scholes from positions + yfinance IV) ─────────
# Modular: las posiciones vienen de get_options_snapshot(), que llenan Plaid o
# /api/portfolio/import. Cualquier fuente futura solo tiene que emitir esa forma.
def _norm_cdf(x):
    return 0.5 * (1.0 + math.erf(x / math.sqrt(2.0)))

def _norm_pdf(x):
    return math.exp(-0.5 * x * x) / math.sqrt(2.0 * math.pi)

def _bs_greeks(S, K, T, sigma, r, opt_type):
    """Per-share Black-Scholes greeks. theta_day = per-calendar-day; vega_1pct = per 1% IV.
    Degrades to intrinsic delta (gamma/theta/vega = 0) at/after expiry or with no vol."""
    if S is None or S <= 0 or K <= 0:
        return {"delta": 0.0, "gamma": 0.0, "theta_day": 0.0, "vega_1pct": 0.0}
    if T <= 0 or sigma <= 0:
        if opt_type == "call":
            delta = 1.0 if S > K else 0.0
        else:
            delta = -1.0 if S < K else 0.0
        return {"delta": delta, "gamma": 0.0, "theta_day": 0.0, "vega_1pct": 0.0}
    sqT = math.sqrt(T)
    d1 = (math.log(S / K) + (r + 0.5 * sigma * sigma) * T) / (sigma * sqT)
    d2 = d1 - sigma * sqT
    pdf = _norm_pdf(d1)
    if opt_type == "call":
        delta = _norm_cdf(d1)
        theta = (-(S * pdf * sigma) / (2 * sqT)) - r * K * math.exp(-r * T) * _norm_cdf(d2)
    else:
        delta = _norm_cdf(d1) - 1.0
        theta = (-(S * pdf * sigma) / (2 * sqT)) + r * K * math.exp(-r * T) * _norm_cdf(-d2)
    gamma = pdf / (S * sigma * sqT)
    vega = S * pdf * sqT / 100.0          # per 1% change in IV
    return {"delta": delta, "gamma": gamma, "theta_day": theta / 365.0, "vega_1pct": vega}


def _bs_price(S, K, T, sigma, r, opt_type):
    """Black-Scholes price per share. Degrades to intrinsic value at/after expiry or with zero vol.
    Used by the trade-plan structurer to estimate entry premium and the option's value at the target."""
    if S is None or S <= 0 or K is None or K <= 0:
        return 0.0
    if T <= 0 or sigma <= 0:
        intr = (S - K) if opt_type == "call" else (K - S)
        return max(0.0, intr)
    sqT = math.sqrt(T)
    d1 = (math.log(S / K) + (r + 0.5 * sigma * sigma) * T) / (sigma * sqT)
    d2 = d1 - sigma * sqT
    if opt_type == "call":
        return S * _norm_cdf(d1) - K * math.exp(-r * T) * _norm_cdf(d2)
    return K * math.exp(-r * T) * _norm_cdf(-d2) - S * _norm_cdf(-d1)






# ── PORTFOLIO OPTIMIZER (mean-variance, Monte Carlo efficient frontier) ────────
def get_agent_views(tickers):
    """Latest saved agent view per ticker for Black-Litterman: expected 12m return
    (from upside_pct) + the conviction used as view confidence."""
    out = {}
    try:
        conn = _db()
        for tk in tickers:
            row = conn.execute(
                "SELECT upside_pct, conviction FROM reports WHERE ticker=? ORDER BY created_ts DESC LIMIT 1",
                (tk.upper(),)).fetchone()
            if row and row["upside_pct"] is not None:
                try:
                    out[tk] = {"exp_return": float(row["upside_pct"]) / 100.0,
                               "conviction": float(row["conviction"] or 50)}
                except (TypeError, ValueError):
                    pass
        conn.close()
    except Exception:
        pass
    return out


def black_litterman_returns(Sigma, w_prior, view_idx, view_q, view_conf, delta=2.5, tau=0.05):
    """Canonical Black-Litterman posterior expected returns.
    Sigma: n×n annualized covariance. w_prior: n prior (benchmark/current) weights.
    view_idx: indices with absolute views; view_q: their expected returns;
    view_conf: confidence in (0,1] (higher → view trusted more, smaller Omega).
    Returns (posterior_mu (n,), pi_equilibrium (n,))."""
    Sigma = np.asarray(Sigma, dtype=float)
    w_prior = np.asarray(w_prior, dtype=float)
    n = Sigma.shape[0]
    pi = delta * Sigma @ w_prior                      # implied equilibrium excess returns
    if not view_idx:
        return pi.copy(), pi
    k = len(view_idx)
    P = np.zeros((k, n))
    for r, idx in enumerate(view_idx):
        P[r, idx] = 1.0
    Q = np.asarray(view_q, dtype=float)
    omega_diag = []
    for r, idx in enumerate(view_idx):
        c = min(0.99, max(0.01, float(view_conf[r])))
        omega_diag.append(max(1e-8, tau * float(Sigma[idx, idx]) * (1.0 - c) / c))
    Omega = np.diag(omega_diag)
    tauSigma = tau * Sigma
    try:
        inv_tauSigma = np.linalg.inv(tauSigma)
        inv_Omega = np.linalg.inv(Omega)
        A = inv_tauSigma + P.T @ inv_Omega @ P
        b = inv_tauSigma @ pi + P.T @ inv_Omega @ Q
        posterior = np.linalg.solve(A, b)
    except np.linalg.LinAlgError:
        return pi.copy(), pi
    return posterior, pi


def compute_portfolio_optimizer(positions, lookback="2y", max_weight=0.25, n_sims=8000):
    import pandas as pd
    eq = [p for p in positions if p.get("ticker") and float(p.get("value") or 0) > 0]
    if len(eq) < 2:
        return {"ok": False, "error": "Se necesitan al menos 2 posiciones para optimizar."}
    total = sum(float(p["value"]) for p in eq)
    cur_w_all = {p["ticker"]: float(p["value"]) / total for p in eq}
    names = {p["ticker"]: p.get("name", p["ticker"]) for p in eq}
    tickers = list(cur_w_all.keys())

    closes = {}
    for t in tickers:
        try:
            h = vertex_market.Ticker(t).history(period=lookback)
            if not h.empty and "Close" in h:
                closes[t] = h["Close"]
        except Exception:
            pass
    px = pd.DataFrame(closes).dropna(how="all")
    rets = px.pct_change().dropna(how="all")
    avail = [t for t in tickers if t in rets.columns and rets[t].notna().sum() >= 60]
    if len(avail) < 2:
        return {"ok": False, "error": "Historial insuficiente para optimizar (se necesitan 2+ con ~60d)."}

    R = rets[avail].dropna()
    n = len(avail)
    mu = R.mean().values * 252.0                      # annualized expected return
    cov = R.cov().values * 252.0                      # annualized covariance
    RF = 0.043
    eff_max = max(max_weight, 1.0 / n + 1e-9)         # keep feasible if few names

    rng = np.random.default_rng(7)

    # current portfolio weights (renormalized) — used as the Black-Litterman prior
    cw = np.array([cur_w_all[t] for t in avail], dtype=float)
    cw = cw / cw.sum()

    # ── #1 BLACK-LITTERMAN: equilibrio de mercado + las vistas guardadas del agente ──
    agent_views = get_agent_views(avail)
    view_idx = [i for i, t in enumerate(avail) if t in agent_views]
    if view_idx:
        vq = [agent_views[avail[i]]["exp_return"] for i in view_idx]
        vc = [min(0.95, max(0.05, agent_views[avail[i]]["conviction"] / 100.0)) for i in view_idx]
        mu_bl, pi_eq = black_litterman_returns(cov, cw, view_idx, vq, vc)
        er_source = "black_litterman"
    else:
        mu_bl = mu.copy()
        er_source = "historical"

    def stats(w):
        ret = float(w @ mu_bl)
        vol = float(math.sqrt(max(w @ cov @ w, 1e-12)))
        sharpe = (ret - RF) / vol if vol > 0 else 0.0
        return ret, vol, sharpe

    cur_ret, cur_vol, cur_sharpe = stats(cw)

    # ── MANDATO 70/30: bucket crecimiento 70%, bucket estable 30% ──
    G_TARGET, S_TARGET = 0.70, 0.30
    growth_idx = [i for i, t in enumerate(avail) if t.upper() not in STABLE_LEADERS]
    stable_idx = [i for i, t in enumerate(avail) if t.upper() in STABLE_LEADERS]
    mandate_feasible = len(growth_idx) >= 1 and len(stable_idx) >= 1

    def cap_block(w, idx, cap, target_sum):
        """Water-filling cap within a bucket, preserving the bucket's target sum."""
        if not idx:
            return
        ai = np.array(idx)
        bcap = max(cap, target_sum / len(idx) + 1e-12)
        sub = w[ai].astype(float)
        for _p in range(25):
            over = sub > bcap + 1e-12
            if not over.any():
                break
            excess = float((sub[over] - bcap).sum())
            sub[over] = bcap
            under = ~over
            us = float(sub[under].sum())
            if us <= 0:
                break
            sub[under] = sub[under] + excess * (sub[under] / us)
        ss = float(sub.sum())
        if ss > 0:
            sub = sub / ss * target_sum
        w[ai] = sub

    def full_waterfill(w):
        for _p in range(25):
            over = w > eff_max + 1e-12
            if not over.any():
                break
            excess = float((w[over] - eff_max).sum())
            w[over] = eff_max
            under = ~over
            us = float(w[under].sum())
            if us <= 0:
                break
            w[under] = w[under] + excess * (w[under] / us)
        s = w.sum()
        if s > 0:
            w /= s

    def sample_weights():
        if mandate_feasible:
            w = np.zeros(n)
            gw = rng.dirichlet(np.ones(len(growth_idx))) * G_TARGET
            sw = rng.dirichlet(np.ones(len(stable_idx))) * S_TARGET
            for j, i in enumerate(growth_idx):
                w[i] = gw[j]
            for j, i in enumerate(stable_idx):
                w[i] = sw[j]
            cap_block(w, growth_idx, eff_max, G_TARGET)
            cap_block(w, stable_idx, eff_max, S_TARGET)
            return w
        w = rng.dirichlet(np.ones(n))
        full_waterfill(w)
        return w

    best_sharpe = {"sharpe": -1e9}; min_vol = {"vol": 1e9}
    frontier = []
    for _ in range(n_sims):
        w = sample_weights()
        ret, vol, sharpe = stats(w)
        frontier.append((vol, ret, sharpe))
        if sharpe > best_sharpe["sharpe"]:
            best_sharpe = {"w": w.copy(), "ret": ret, "vol": vol, "sharpe": sharpe}
        if vol < min_vol["vol"]:
            min_vol = {"w": w.copy(), "ret": ret, "vol": vol, "sharpe": sharpe}

    def pack(d):
        return {"ann_return_pct": round(d["ret"] * 100, 1),
                "ann_vol_pct": round(d["vol"] * 100, 1),
                "sharpe": round(d["sharpe"], 2)}

    def weights_list(w):
        return sorted([{"ticker": avail[i], "name": names.get(avail[i], avail[i]),
                        "weight_pct": round(float(w[i]) * 100, 1),
                        "bucket": ("estable" if avail[i].upper() in STABLE_LEADERS else "crecimiento")}
                       for i in range(n)], key=lambda x: x["weight_pct"], reverse=True)

    def bucket_split(w):
        g = sum(float(w[i]) for i in growth_idx)
        s = sum(float(w[i]) for i in stable_idx)
        return round(g * 100, 1), round(s * 100, 1)

    # rebalance suggestion: current -> max-sharpe
    msw = best_sharpe["w"]
    rebalance = []
    for i, t in enumerate(avail):
        cur = round(float(cw[i]) * 100, 1)
        opt = round(float(msw[i]) * 100, 1)
        dlt = round(opt - cur, 1)
        if opt < 1.5 and cur >= 2:
            act = "vender"        # el óptimo te quiere prácticamente fuera de la posición
        elif dlt <= -3:
            act = "reducir"
        elif dlt >= 3:
            act = "aumentar"
        else:
            act = "mantener"
        rebalance.append({"ticker": t, "current_pct": cur, "optimal_pct": opt,
                          "delta_pct": dlt, "action": act,
                          "bucket": ("estable" if t.upper() in STABLE_LEADERS else "crecimiento")})
    rebalance.sort(key=lambda x: abs(x["delta_pct"]), reverse=True)

    cur_g, cur_s = bucket_split(cw)
    ms_g, ms_s = bucket_split(msw)
    views_applied = [{"ticker": avail[i],
                      "exp_return_pct": round(agent_views[avail[i]]["exp_return"] * 100, 1),
                      "conviction": round(agent_views[avail[i]]["conviction"], 0)} for i in view_idx]
    views_applied.sort(key=lambda x: x["exp_return_pct"], reverse=True)
    expected_returns = sorted([{"ticker": avail[i], "er_pct": round(float(mu_bl[i]) * 100, 1),
                                "has_view": avail[i] in agent_views} for i in range(n)],
                              key=lambda x: x["er_pct"], reverse=True)

    # subsample frontier for plotting
    step = max(1, len(frontier) // 350)
    fr = [{"vol": round(v * 100, 2), "ret": round(r * 100, 2), "sharpe": round(s, 2)}
          for (v, r, s) in frontier[::step]]

    return {
        "ok": True,
        "positions_analyzed": n,
        "rf_assumed_pct": round(RF * 100, 1),
        "max_weight_pct": round(eff_max * 100, 1),
        "expected_returns_source": er_source,
        "views_applied": views_applied,
        "expected_returns": expected_returns,
        "mandate": {"growth_target_pct": 70, "stable_target_pct": 30, "feasible": mandate_feasible,
                    "current_growth_pct": cur_g, "current_stable_pct": cur_s,
                    "optimal_growth_pct": ms_g, "optimal_stable_pct": ms_s,
                    "note": ("Mandato 70/30 aplicado como restricción dura." if mandate_feasible
                             else "No se pudo aplicar 70/30: faltan posiciones en un bucket (tu book es todo crecimiento o todo estable).")},
        "current":    {**pack({"ret": cur_ret, "vol": cur_vol, "sharpe": cur_sharpe}), "weights": weights_list(cw)},
        "max_sharpe": {**pack(best_sharpe), "weights": weights_list(best_sharpe["w"])},
        "min_vol":    {**pack(min_vol),     "weights": weights_list(min_vol["w"])},
        "rebalance": rebalance,
        "frontier": fr,
        "generated_at": datetime.now().strftime('%m/%d/%Y, %I:%M:%S %p'),
    }


@app.get("/api/portfolio-optimizer")
def get_portfolio_optimizer(account_id: str = "", max_weight: float = 0.25):
    """Black-Litterman + 70/30 mandate optimization (Monte Carlo frontier) on the real book."""
    try:
        positions, _src = _resolve_positions(account_id)
        if not positions:
            return {"ok": False, "error": "No se encontraron posiciones. Conecta Plaid o carga tu portafolio con POST /api/portfolio/import."}
        mw = max(0.05, min(float(max_weight), 1.0))
        return compute_portfolio_optimizer(positions, max_weight=mw)
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Optimizer error: {str(e)}")


@app.get("/api/portfolio-ideas")
def get_portfolio_ideas(account_id: str = "", n: int = 5):
    """#Ideas — Genera ideas de inversión NUEVAS (tickers que NO tienes ni has analizado) que
    DIVERSIFIQUEN tu book concentrado en IA, con % sugerido y un razonamiento completo
    (a qué se dedica, cuánto crece hoy, cuánto se espera que crezca, deuda/rentabilidad,
    futuro de la acción vs futuro del mundo, riesgos). Gemini con búsqueda en vivo.
    La opción 'Analyze' del menú de 3 puntos corre el agente completo sobre la idea."""
    if not API_KEY:
        return {"ok": False, "error": "Falta GEMINI_API_KEY para generar ideas de inversión."}
    try:
        held = []
        try:
            positions, _src = _resolve_positions(account_id)
            held = sorted({str(p.get("ticker") or "").upper() for p in (positions or []) if p.get("ticker")})
        except Exception:
            held = []
        held = [h for h in held if h]
        n = max(2, min(int(n), 8))
        held_txt = ", ".join(held) if held else "(libro vacío)"
        prompt = f"""Eres el CIO de un fondo. El inversionista YA tiene estas posiciones: {held_txt}.
Su libro está MUY concentrado en IA y semiconductores. Propón EXACTAMENTE {n} ideas de COMPRA NUEVAS
(acciones de EE.UU. con ticker real que NO estén en su lista de arriba). Quiere COMPRAR cosas buenas, así que
incluye una MEZCLA:
(a) varias empresas de ALTO CRECIMIENTO — AUNQUE tengan valoración o riesgo MAYOR (no las descartes por caras
    si el crecimiento lo justifica; el inversionista tiene apetito de crecimiento),
(b) algunas que DIVERSIFIQUEN su concentración en IA (otros sectores/factores),
(c) idealmente con crecimiento de ingresos año-tras-año, reduciendo deuda o a punto de volverse rentables — o ya
    rentables y de calidad.
Usa datos REALES y recientes (BÚSCALOS con la herramienta de búsqueda; cifras verdaderas, no inventadas).
Devuelve SOLO un objeto JSON crudo (sin markdown, sin ```), con esta forma EXACTA:
{{"ideas":[{{"ticker":"XXX","name":"Nombre real","sector":"sector","suggested_pct":<entero 3-15>,
"idea_type":"crecimiento|diversificador|calidad","growth_tier":"alto|medio",
"reasoning":{{
"why":"por qué debería COMPRARLA — 2-3 frases concretas",
"keep_growing":"por qué la empresa seguirá creciendo",
"current_growth":"cuánto está creciendo HOY, con cifras reales (ej. ingresos +28% YoY)",
"expected_growth":"cuánto se espera que crezca, con cifras (consenso/guía)",
"what_it_does":"a qué se dedica en una frase",
"future_vs_world":"el futuro de la acción comparado con hacia dónde va el mundo (IA, energía, salud, defensa, etc.)",
"debt_profitability":"estado de su deuda y rentabilidad",
"valuation_risk":"valoración (ej. P/E, P/S) y por qué vale la pena aunque sea mayor, o el riesgo que implica",
"risks":"riesgos principales en una frase"
}}}}]}}
Reglas duras: tickers reales que NO estén en [{held_txt}]; al menos la MITAD deben ser growth_tier 'alto';
suggested_pct realista (en conjunto las ideas suman <= 40% — son ideas que se suman a un book existente, no el
book entero); todas las cifras deben ser reales y recientes."""
        resp = _gemini_genera(
            model='gemini-2.5-flash', contents=prompt,
            config=types.GenerateContentConfig(temperature=0.5,
                tools=[types.Tool(google_search=types.GoogleSearch())]))
        raw = (resp.text or "").strip()
        if raw.startswith("```"):
            raw = re.sub(r'^```[a-z]*\n?', '', raw)
            raw = re.sub(r'\n?```$', '', raw)
        data = json.loads(raw.strip())
        ideas = data.get("ideas", []) if isinstance(data, dict) else []
        heldset = set(held)
        clean = []
        for it in ideas:
            tk = str(it.get("ticker", "")).upper().strip()
            if not tk or tk in heldset:
                continue
            try:
                it["suggested_pct"] = int(round(float(it.get("suggested_pct", 5))))
            except Exception:
                it["suggested_pct"] = 5
            it["suggested_pct"] = max(1, min(it["suggested_pct"], 25))
            it["idea_type"] = str(it.get("idea_type", "crecimiento")).lower().strip()
            it["growth_tier"] = str(it.get("growth_tier", "medio")).lower().strip()
            it["ticker"] = tk
            clean.append(it)
        return {"ok": True, "ideas": clean, "held": held,
                "generated_at": datetime.now().strftime('%m/%d/%Y, %I:%M:%S %p')}
    except HTTPException:
        raise
    except Exception as e:
        return {"ok": False, "error": f"No se pudieron generar ideas: {e}"}

# ═════════════════════════════════════════════════════════════════════════════
# #1 — COLECTOR AUTOMÁTICO DE SNAPSHOTS (scheduler in-process)
# El loop empírico (backtest, IC, calibración, confianza de proyecciones) depende
# de capturar un snapshot diario por ticker. Sin esto, todo cae a reglas fijas.
# ═════════════════════════════════════════════════════════════════════════════
VERTEX_PRIMARY_TICKERS = [t.strip().upper() for t in
    os.environ.get("VERTEX_PRIMARY_TICKERS", "SPY,NVDA,PLTR,AMD,GOOGL").split(",") if t.strip()]







def _vertex_startup():
    """Arranque: sólo el aviso de claves.

    Aquí se lanzaba el planificador, que existía para capturar un snapshot
    diario de señales de Quant Data. Esas señales salieron del proyecto, así
    que el hilo se quedó sin trabajo: mantenerlo vivo sería un hilo de fondo
    despertándose cada noche para no hacer nada.
    """
    # #6 — aviso si faltan claves (deben venir del entorno, ya no están en el código)
    try:
        _need = {"FMP_API_KEY": (os.environ.get("FMP_API_KEY") or "").strip(),
                 "GEMINI_API_KEY": API_KEY,
                 "OPENAI_API_KEY": OPENAI_API_KEY,
                 "FINNHUB_API_KEY": FINNHUB_API_KEY}
        missing = [k for k, v in _need.items() if not v]
        if missing:
            print("[KEYS] ⚠ Faltan variables de entorno: " + ", ".join(missing)
                  + "  → carga vertex.env (set -a; source vertex.env; set +a) antes de uvicorn.")
    except Exception:
        pass


# ═════════════════════════════════════════════════════════════════════════════
# PROYECCIONES — el agente de OPCIONES. Vive aparte del de acciones a propósito.
# ═════════════════════════════════════════════════════════════════════════════
# Analyze (acciones) puntúa SÓLO con las cuatro fuentes de Victor: FMP,
# FinnHub, FRED y EDGAR. Ni una línea de aquí abajo toca su score — el motor
# tiene un test (`test_the_engine_no_longer_imports_yahoo`) que falla si Yahoo
# vuelve a entrar ahí.
#
# Esta capa es otra cosa: cadenas de opciones, GEX, muros de gamma, max pain,
# venta de prima y griegas del portafolio. Ese dato NO existe en las cuatro
# fuentes — FMP devuelve 404 en `options-chain` con este plan y Quant Data
# tiene el plan API inactivo. Yahoo es el único que las sirve, así que se usa
# AQUÍ y sólo aquí. Si Yahoo se cae, estos paneles salen vacíos y el análisis
# de acciones sigue exactamente igual: el fallo es ruidoso, no silencioso.
#
# Las funciones de Quant Data se conservan porque toda esta capa fue escrita
# para funcionar con o sin ellas: sin clave, `_quantdata_ready()` da False y
# cada una devuelve None, y el cálculo cae a lo que se deriva de la cadena.
class _YahooPerezoso:
    """Carga yfinance en el PRIMER uso, no al importar el módulo.

    Ninguna de las 139 funciones alcanzables desde `/api/analyze` lo toca —
    verificado recorriendo el árbol de llamadas — pero un `import` en la
    cabecera lo metía en memoria igual, en cada arranque, aunque nadie
    abriera Proyecciones. La separación entre los dos agentes pasa a ser
    física y no sólo de disciplina: si el análisis de acciones lo cargara
    alguna vez, se vería.

    También abarata el arranque en Render: yfinance arrastra pandas y varias
    dependencias más que el análisis de acciones no necesita.
    """

    _mod = None

    def __getattr__(self, nombre):
        if _YahooPerezoso._mod is None:
            import yfinance as _yf          # noqa: PLC0415 — a propósito
            _YahooPerezoso._mod = _yf
        return getattr(_YahooPerezoso._mod, nombre)


yf = _YahooPerezoso()

# ── QUANT DATA PROVIDER (options flow + exposure + dark pool) ──────────────────
# Modular institutional-data source chosen over Unusual Whales. Fills the
# dark_pool / tape_flow slots of the GEX engine and feeds the agent's 25% flow
# signal. Set QUANTDATA_API_KEY to activate; everything degrades to None when
# absent, so the platform runs identically with or without it.
QUANTDATA_API_KEY = os.environ.get("QUANTDATA_API_KEY", "")   # <-- pega tu API key de Quant Data
# Sin "/v1": la API no lo usa. Con el sufijo, TODAS las rutas devolvian
# 404 ("No resource found at 'v1/option/flow'"), asi que el flujo de
# opciones, el dark pool y el GEX llevaban muertos en silencio -- cada
# /api/analyze gastaba 25 peticiones y ~8 s en rutas inexistentes. Sin el
# sufijo responden 403 (existen; el plan no las cubre), que es un estado
# distinto y ademas lo memoriza `_QD_SIN_DERECHO` para no repetirlas.
QUANTDATA_BASE    = os.environ.get("QUANTDATA_BASE", "https://api.quantdata.us")
# Endpoint paths centralized. All confirmed from Quant Data's API reference
# (quantdata.us/api). Base = https://api.quantdata.us/v1, all POST with body
# {"sessionDate": "YYYY-MM-DD", "filter": {"ticker": "..."}}.
QUANTDATA_ENDPOINTS = {
    "net_premium": os.environ.get("QD_EP_NETPREMIUM", "/options/tool/net-drift"),
    "flow":        os.environ.get("QD_EP_FLOW",       "/options/tool/order-flow/consolidated"),
    "exposure":    os.environ.get("QD_EP_EXPOSURE",   "/options/tool/exposure-by-strike"),
    "darkpool":    os.environ.get("QD_EP_DARKPOOL",   "/equities/tool/dark-pool-levels"),
    "oi_change":   os.environ.get("QD_EP_OICHANGE",   "/options/tool/open-interest-change"),
    "equity_prints": os.environ.get("QD_EP_PRINTS",   "/equities/tool/equity-prints"),
    "net_flow":    os.environ.get("QD_EP_NETFLOW",    "/options/tool/net-flow"),
    "max_pain":    os.environ.get("QD_EP_MAXPAIN",    "/options/tool/max-pain"),
}


def compute_gex(ticker, max_expiries=4, max_dte=60):
    """Dealer gamma exposure + key levels from the option chain (free, model-derived).
    Returns net GEX, gamma flip, call/put walls, max pain, P/C ratios and unusual-volume
    strikes (volume > OI = fresh positioning). Calls +, puts - (SqueezeMetrics-style)."""
    try:
        tk = yf.Ticker(ticker)
        h = tk.history(period="5d")
        if h.empty:
            return None
        spot = _safe_num(h["Close"].iloc[-1], 0.0)
        exps = list(tk.options or [])
    except Exception:
        return None
    if spot <= 0 or not exps:
        return None
    now = datetime.now()
    rows, used_exp = [], []
    for exp in exps:
        try:
            dte = (datetime.strptime(exp, "%Y-%m-%d") - now).days
        except Exception:
            continue
        if dte < 0 or dte > max_dte:
            continue
        if len(used_exp) >= max_expiries:
            break
        try:
            ch = tk.option_chain(exp)
        except Exception:
            continue
        T = max(dte / 365.0, 1.0 / 365.0)
        for df, typ in ((ch.calls, "call"), (ch.puts, "put")):
            for _, r in df.iterrows():
                try:
                    K = _safe_num(r["strike"]); oi = _safe_num(r.get("openInterest"))
                    vol = _safe_num(r.get("volume")); iv = _safe_num(r.get("impliedVolatility"))
                except Exception:
                    continue
                if K <= 0 or iv <= 0:
                    continue
                gm = _bs_greeks(spot, K, T, iv, 0.043, typ)["gamma"]
                rows.append({"exp": exp, "typ": typ, "K": K, "oi": oi, "vol": vol, "iv": iv, "T": T, "gamma": gm})
        used_exp.append(exp)
    if not rows:
        return None
    strike_gex, call_oi_by_K, put_oi_by_K = {}, {}, {}
    tot_call_oi = tot_put_oi = tot_call_vol = tot_put_vol = 0.0
    for x in rows:
        sign = 1.0 if x["typ"] == "call" else -1.0
        g = x["gamma"] * x["oi"] * 100.0 * spot * spot * 0.01 * sign
        strike_gex[x["K"]] = strike_gex.get(x["K"], 0.0) + g
        if x["typ"] == "call":
            call_oi_by_K[x["K"]] = call_oi_by_K.get(x["K"], 0.0) + x["oi"]
            tot_call_oi += x["oi"]; tot_call_vol += x["vol"]
        else:
            put_oi_by_K[x["K"]] = put_oi_by_K.get(x["K"], 0.0) + x["oi"]
            tot_put_oi += x["oi"]; tot_put_vol += x["vol"]
    net_gex = sum(strike_gex.values())
    calls_above = {k: v for k, v in call_oi_by_K.items() if k >= spot}
    puts_below = {k: v for k, v in put_oi_by_K.items() if k <= spot}
    call_wall = max(calls_above, key=calls_above.get) if calls_above else (max(call_oi_by_K, key=call_oi_by_K.get) if call_oi_by_K else None)
    put_wall = max(puts_below, key=puts_below.get) if puts_below else (max(put_oi_by_K, key=put_oi_by_K.get) if put_oi_by_K else None)
    flip = _gamma_flip(rows, spot)
    max_pain, max_pain_src = _max_pain_best(ticker, None, call_oi_by_K, put_oi_by_K)   # QD nativo → yfinance
    unusual = []
    for x in rows:
        if x["vol"] > max(x["oi"], 50) and x["vol"] >= 200:
            unusual.append({"strike": x["K"], "type": x["typ"], "exp": x["exp"],
                            "volume": int(x["vol"]), "oi": int(x["oi"]),
                            "vol_oi": round(x["vol"] / max(x["oi"], 1), 1)})
    unusual = sorted(unusual, key=lambda u: u["volume"], reverse=True)[:8]
    # Laddered resistance (call OI clusters above spot) and support (put OI clusters below)
    resistances = sorted(
        [{"strike": round(k, 2), "oi": int(v)} for k, v in call_oi_by_K.items() if k >= spot and v > 0],
        key=lambda x: x["oi"], reverse=True)[:4]
    resistances = sorted(resistances, key=lambda x: x["strike"])          # nearest-above first
    supports = sorted(
        [{"strike": round(k, 2), "oi": int(v)} for k, v in put_oi_by_K.items() if k <= spot and v > 0],
        key=lambda x: x["oi"], reverse=True)[:4]
    supports = sorted(supports, key=lambda x: x["strike"], reverse=True)   # nearest-below first
    dte_dominant = None
    if used_exp:
        try:
            dte_dominant = max((datetime.strptime(used_exp[0], "%Y-%m-%d") - now).days, 0)
        except Exception:
            dte_dominant = None
    return _json_safe({
        "ok": True, "ticker": ticker.upper(), "spot": round(spot, 2), "expiries_used": used_exp,
        "dte_dominant": dte_dominant,
        "net_gex": round(net_gex, 0),
        "net_gex_regime": ("positivo (precio anclado / mean-revert)" if net_gex >= 0
                           else "negativo (movimientos amplificados / tendencia)"),
        "gamma_flip": round(flip, 2) if flip else None,
        "call_wall": call_wall, "put_wall": put_wall, "max_pain": max_pain, "max_pain_source": max_pain_src,
        "resistances": resistances, "supports": supports,
        "pcr_oi": round(tot_put_oi / tot_call_oi, 2) if tot_call_oi else None,
        "pcr_vol": round(tot_put_vol / tot_call_vol, 2) if tot_call_vol else None,
        "total_call_oi": int(tot_call_oi), "total_put_oi": int(tot_put_oi),
        "strike_gex": {round(k, 2): round(v, 0) for k, v in sorted(strike_gex.items())},
        "unusual_activity": unusual,
        "dark_pool": None, "tape_flow": None,   # ← Unusual Whales fills these later
        "source": "computed (yfinance chain + BSM)",
        "generated_at": now.strftime('%m/%d/%Y, %I:%M:%S %p')})


def _gex_from_quantdata(ticker):
    """GEX 100% Quant Data: net GEX, gamma flip, call/put walls, max pain, perfil por strike y
    resistencias/soportes desde la exposición GAMMA de QD; Put/Call por PRIMA (net-flow) y por VOLUMEN +
    actividad inusual (golden/unusual/opening sweeps) desde el order-flow QD; spot de QD/Finnhub.
    pcr_oi/total OI quedan None (QD no expone el OI de cadena completa). Si QD no responde, get_gex_cached
    cae a yfinance+BSM. MISMA forma que compute_gex."""
    try:
        exp = quantdata_exposure(ticker, "GAMMA")
    except Exception:
        exp = None
    if not exp or not exp.get("by_strike"):
        return None
    rows = exp["by_strike"]
    spot = _safe_num(exp.get("stock_price")) or _safe_num(_live_spot(ticker))
    if spot <= 0:
        return None
    net_gex = sum(_safe_num(r.get("net")) for r in rows)
    walls = _qd_gex_walls(exp, spot) or {}
    flip = walls.get("gamma_flip")
    try:
        mp = quantdata_max_pain(ticker)
    except Exception:
        mp = None
    above = sorted([r for r in rows if _safe_num(r.get("strike")) >= spot and _safe_num(r.get("call")) > 0],
                   key=lambda r: _safe_num(r.get("call")), reverse=True)[:4]
    below = sorted([r for r in rows if _safe_num(r.get("strike")) <= spot and abs(_safe_num(r.get("put"))) > 0],
                   key=lambda r: abs(_safe_num(r.get("put"))), reverse=True)[:4]
    resistances = sorted([{"strike": round(_safe_num(r["strike"]), 2), "oi": None} for r in above],
                         key=lambda x: x["strike"])
    supports = sorted([{"strike": round(_safe_num(r["strike"]), 2), "oi": None} for r in below],
                      key=lambda x: x["strike"], reverse=True)
    now = datetime.now()
    # ── Put/Call + actividad inusual NATIVOS de Quant Data (llenan el bloque sin tocar yfinance) ──
    # P/C por PRIMA (dónde está el dinero) desde net-flow; P/C por VOLUMEN y la actividad inusual
    # (golden/unusual/opening sweeps, más rico que el vol>OI de yfinance) desde el order-flow reciente.
    pcr_premium = pcr_vol = None
    call_prem = put_prem = None
    try:
        nf = quantdata_net_flow(ticker, "today")
        if nf:
            call_prem = _safe_num(nf.get("call_total")); put_prem = _safe_num(nf.get("put_total"))
            if call_prem > 0:
                pcr_premium = round(put_prem / call_prem, 2)
    except Exception:
        pass
    unusual = []
    try:
        fw = quantdata_flow_window(ticker, days=3, min_premium=250_000, max_rows=150)
        cvol = pvol = 0.0
        for t in fw:
            cp = str(t.get("cp") or "").upper()
            sz = _safe_num(t.get("size"))
            if cp.startswith("C"):
                cvol += sz
            elif cp.startswith("P"):
                pvol += sz
            if t.get("golden") or t.get("unusual") or t.get("opening"):
                oi = int(_safe_num(t.get("oi")))
                vol = int(sz)
                unusual.append({"strike": t.get("strike"),
                                "type": ("call" if cp.startswith("C") else "put"),
                                "exp": t.get("exp"), "volume": vol, "oi": oi,
                                "vol_oi": round(vol / max(oi, 1), 1),
                                "golden": bool(t.get("golden")), "unusual": bool(t.get("unusual")),
                                "premium": _safe_num(t.get("premium"))})
        if cvol > 0:
            pcr_vol = round(pvol / cvol, 2)
        seen, dedup = set(), []
        for u in sorted(unusual, key=lambda x: (x.get("premium") or 0), reverse=True):
            k = (u["type"], u["strike"], u["exp"])
            if k in seen:
                continue
            seen.add(k); dedup.append(u)
        unusual = dedup[:8]
    except Exception:
        unusual = []
    return _json_safe({
        "ok": True, "ticker": ticker.upper(), "spot": round(spot, 2),
        "expiries_used": (exp.get("expirations") or [])[:4], "dte_dominant": None,
        "net_gex": round(net_gex, 0),
        "net_gex_regime": ("positivo (precio anclado / mean-revert)" if net_gex >= 0
                           else "negativo (movimientos amplificados / tendencia)"),
        "gamma_flip": round(flip, 2) if flip else None,
        "call_wall": walls.get("call_wall"), "put_wall": walls.get("put_wall"),
        "max_pain": mp, "max_pain_source": "quantdata" if mp else None,
        "resistances": resistances, "supports": supports,
        "pcr_oi": None, "pcr_vol": pcr_vol, "pcr_premium": pcr_premium,
        "net_premium_call": round(call_prem, 0) if call_prem is not None else None,
        "net_premium_put": round(put_prem, 0) if put_prem is not None else None,
        "total_call_oi": None, "total_put_oi": None,
        "strike_gex": {round(_safe_num(r["strike"]), 2): round(_safe_num(r.get("net")), 0) for r in rows},
        "unusual_activity": unusual,
        "dark_pool": None, "tape_flow": None,
        "source": "Quant Data (primario)",
        "generated_at": now.strftime('%m/%d/%Y, %I:%M:%S %p')})


def get_gex_cached(ticker, ttl=300, force=False):
    key = ticker.upper(); now = time.time()
    ttl = _gex_ttl(ttl)                       # 0DTE: refresco más rápido cerca del cierre
    ent = _GEX_CACHE.get(key)
    if ent and not force and now - ent[0] < ttl:
        return ent[1]
    # QuantData PRIMARIO (consistente con walls / max pain / flujo / gráfico de gamma, todos QD).
    try:
        val = _gex_from_quantdata(ticker)
    except Exception:
        val = None
    if val is None:                       # QD no respondió → respaldo yfinance + Black-Scholes
        try:
            val = compute_gex(ticker)
        except Exception:
            val = None
    _GEX_CACHE[key] = (now, val)
    return val


def _walls_for_expiry(tk, exp, spot):
    try:
        ch = tk.option_chain(exp)
    except Exception:
        return None
    call_oi, put_oi = {}, {}
    for df, typ in ((ch.calls, "call"), (ch.puts, "put")):
        for _, r in df.iterrows():
            K = _safe_num(r["strike"]); oi = _safe_num(r.get("openInterest"))
            if K <= 0 or oi <= 0:
                continue
            d = call_oi if typ == "call" else put_oi
            d[K] = d.get(K, 0.0) + oi
    ca = {k: v for k, v in call_oi.items() if k >= spot}
    pb = {k: v for k, v in put_oi.items() if k <= spot}
    cw = max(ca, key=ca.get) if ca else (max(call_oi, key=call_oi.get) if call_oi else None)
    pw = max(pb, key=pb.get) if pb else (max(put_oi, key=put_oi.get) if put_oi else None)
    return {"call_wall": cw, "put_wall": pw, "max_pain": _max_pain(call_oi, put_oi)}


def _qd_conviction(flow, oi_change_map=None):
    """Kevin's institutional-conviction filter on the Quant Data tape. Counts ONLY trades whose
    contract's OPEN INTEREST ACTUALLY GREW that session (real day-over-day ΔOI > 0 from the
    open-interest-change endpoint = position was added/accumulated — catches the multi-day builds
    that vol>OI misses, e.g. +30k on top of an existing 70k) AND are aggressive buys (side contains
    ASK), premium tiered by DTE: $1M (<=10d) · $5M (11-45d) · $10M (>45d). CALL=bullish, PUT=bearish;
    delta 0.60-0.90 = direccional, 0.30-0.59 = especulativo. If oi_change_map is None (endpoint
    unavailable) it falls back to the same-day vol>OI 'opening' proxy."""
    if not flow:
        return None
    now = datetime.now()
    use_oi = isinstance(oi_change_map, dict) and len(oi_change_map) > 0
    bull = bear = 0.0
    strong = []
    for t in flow:
        try:
            prem = abs(_safe_num(t.get("premium")))
            if prem <= 0:
                continue
            cp = str(t.get("cp") or "").upper()
            strike = _safe_num(t.get("strike"))
            exp = str(t.get("exp") or "")[:10]
            # --- real "added to open interest" gate (ΔOI day-after vs day-of) ---
            oi_chg = None
            if use_oi:
                cp_full = "CALL" if cp.startswith("C") else ("PUT" if cp.startswith("P") else cp)
                ent = oi_change_map.get(f"{cp_full}|{round(strike, 2)}|{exp}")
                if not ent or _safe_num(ent.get("change")) <= 0:
                    continue                          # OI did NOT grow → not an addition → ignore
                oi_chg = int(_safe_num(ent.get("change")))
            else:
                if not t.get("opening"):              # fallback: same-day vol>OI proxy
                    continue
            side = str(t.get("side") or "").upper()
            if "ASK" not in side:                 # only aggressive buys (ASK / ABOVE_ASK)
                continue
            dte = _safe_num(t.get("dte"))
            if dte <= 0:
                try:
                    dte = max((datetime.strptime(exp, "%Y-%m-%d") - now).days, 0)
                except Exception:
                    dte = 30
            thr = 1e6 if dte <= 10 else (5e6 if dte <= 45 else 10e6)
            if prem < thr:
                continue
            dlt = abs(_safe_num(t.get("delta")))
            if cp.startswith("C"):
                bull += prem
            elif cp.startswith("P"):
                bear += prem
            strong.append({
                "cp": "CALL" if cp.startswith("C") else ("PUT" if cp.startswith("P") else "?"),
                "strike": strike or None, "exp": t.get("exp"),
                "dte": int(dte), "premium": round(prem, 0), "side": side,
                "kind": t.get("kind"), "delta": round(dlt, 2) if dlt else None,
                "vol_oi": t.get("vol_oi"), "oi_change": oi_chg,
                "type": "direccional" if (0.60 <= dlt <= 0.90) else "especulativo",
            })
        except Exception:
            continue
    if not strong:
        return None
    tot = bull + bear
    strong.sort(key=lambda x: x["premium"], reverse=True)
    return {"bias": "alcista" if bull > bear else ("bajista" if bear > bull else "neutral"),
            "bull_premium": round(bull, 0), "bear_premium": round(bear, 0),
            "strength_pct": round(max(bull, bear) / tot * 100, 0) if tot > 0 else None,
            "qualifying": len(strong), "strong_trades": strong[:15],
            "oi_confirmed": use_oi}


def _qd_conviction_prompt_block(conv):
    """Render the OI-confirmed conviction dict as a Spanish prompt block for the agent's reasoning.
    Empty string when there's no qualifying conviction so the prompt stays clean."""
    if not conv or not isinstance(conv, dict) or not conv.get("qualifying"):
        return ""
    bias = str(conv.get("bias", "neutral"))
    strg = conv.get("strength_pct")
    bull = _safe_num(conv.get("bull_premium"))
    bear = _safe_num(conv.get("bear_premium"))
    metodo = ("confirmados por crecimiento REAL del open interest (ΔOI>0, comparando OI día-después vs "
              "día-de la transacción — no un proxy)" if conv.get("oi_confirmed")
              else "marcados como apertura por el proxy vol>OI del mismo día")
    lines = []
    for t in (conv.get("strong_trades") or [])[:8]:
        doi = t.get("oi_change")
        doi_txt = (f"ΔOI +{int(_safe_num(doi)):,} contratos" if doi is not None else "abre OI")
        lines.append(
            f"  - {t.get('cp')} ${t.get('strike')} vence {str(t.get('exp'))[:10]} ({t.get('dte')}d): "
            f"premium ${_safe_num(t.get('premium')):,.0f}, {doi_txt}, "
            f"delta {t.get('delta')} ({t.get('type')}){(', ' + str(t.get('kind'))) if t.get('kind') else ''}")
    trades_txt = "\n".join(lines)
    return (
        f"\nCONVICCIÓN INSTITUCIONAL CONFIRMADA POR OPEN INTEREST (Quant Data — la evidencia de flujo de MÁS "
        f"alta calidad que tienes): solo compras agresivas (ASK/above-ask) {metodo}, con premium por plazo "
        f"$1M≤10d / $5M≤45d / $10M>45d. Sesgo de convicción: {bias.upper()}"
        f"{(' · fuerza ' + str(int(strg)) + '%') if strg is not None else ''} "
        f"· {conv.get('qualifying')} trades calificados. Premium alcista (calls que abren OI) ${bull:,.0f} "
        f"vs bajista (puts que abren OI) ${bear:,.0f}.\nTrades de mayor convicción:\n{trades_txt}\n"
        f"INTERPRETACIÓN: son institucionales ABRIENDO posición nueva con dinero real (el open interest creció), "
        f"no cerrando ni rolando. Por eso pesa más que el premium neto suelto. Si este sesgo COINCIDE con tu tesis, "
        f"sube la puntuación de 'flujo institucional' (señal 25%) y CITA los contratos concretos "
        f"(strike/vencimiento/ΔOI/delta) en tu tesis. Si CONTRADICE tu tesis fundamental, NO lo ignores: "
        f"el smart money podría anticipar un catalizador — explica el conflicto en 'tesis_riesgos' y modera tu convicción.")


def _qd_confluence(conv, gex, darkpool, spot=None, dp_flow=None):
    """Confluence engine: do Kevin's three Quant Data pillars agree?
      1) Convicción (tape ΔOI)              — señal líder, peso 0.50
      2) GEX / posicionamiento de dealers   — walls + gamma flip, peso 0.30
      3) Dark pool (notional soporte/resist)— peso 0.20
    Returns verdict (confirmacion/divergencia/mixto/parcial/posicionamiento/neutral), a badge,
    a -1..1 score, per-signal votes (+1 alcista / -1 bajista / 0 neutral) and a summary.
    HONEST: dark-pool prints are sideless, so its vote is a POSITIONAL lean (dónde están los
    bloques vs el precio), not a buy/sell read."""
    spot = _safe_num(spot) or (_safe_num(gex.get("spot")) if isinstance(gex, dict) else 0)
    def _lab(v):
        return "alcista" if v > 0 else ("bajista" if v < 0 else "neutral")

    # 1) Convicción (tape ΔOI) — la señal líder
    va, da = 0, "sin trades de convicción"
    if isinstance(conv, dict) and conv.get("qualifying"):
        b = str(conv.get("bias"))
        va = 1 if b == "alcista" else (-1 if b == "bajista" else 0)
        s = conv.get("strength_pct")
        da = f"convicción {b}{(' ' + str(int(s)) + '%') if s is not None else ''} · {conv.get('qualifying')} trades ΔOI+"

    # 2) GEX / posicionamiento (walls + gamma flip)
    vb, db = 0, "GEX no disponible"
    if isinstance(gex, dict) and gex.get("ok"):
        cw, pw = _safe_num(gex.get("call_wall")), _safe_num(gex.get("put_wall"))
        flipn = _safe_num(gex.get("gamma_flip"))
        if cw and spot and spot > cw * 1.001:
            vb, db = 1, f"spot ${round(spot,2)} rompió el call wall ${cw} (resistencia superada)"
        elif pw and spot and spot < pw * 0.999:
            vb, db = -1, f"spot ${round(spot,2)} bajo el put wall ${pw} (soporte roto)"
        elif flipn and spot:
            if spot >= flipn:
                vb, db = 1, f"sobre el gamma flip ${flipn} (GEX+, los dips se soportan)"
            else:
                vb, db = -1, f"bajo el gamma flip ${flipn} (GEX−, movimientos amplificados)"
        else:
            vb, db = 0, "GEX sin gamma flip ni ruptura de wall (neutral)"

    # 3) Dark pool (posicional, sideless): notional en soporte vs resistencia
    vc, dc = 0, "sin niveles dark pool"
    # Preferimos el flujo DIRECCIONAL de prints (compra ASK vs venta BID) cuando está disponible;
    # si no, caemos al posicional (soporte/resistencia por notional).
    if isinstance(dp_flow, dict) and dp_flow.get("total_notional"):
        bN, sN = _safe_num(dp_flow.get("buy_notional")), _safe_num(dp_flow.get("sell_notional"))
        bM, sM2 = bN / 1e6, sN / 1e6
        if bN > sN * 1.15:
            vc, dc = 1, f"prints dark COMPRA (${bM:,.0f}M ask vs ${sM2:,.0f}M bid)"
        elif sN > bN * 1.15:
            vc, dc = -1, f"prints dark VENTA (${sM2:,.0f}M bid vs ${bM:,.0f}M ask)"
        else:
            vc, dc = 0, f"prints dark equilibrados (${bM:,.0f}M compra / ${sM2:,.0f}M venta)"
    elif isinstance(darkpool, list) and darkpool and spot:
        supp = sum(_safe_num(x.get("value")) for x in darkpool
                   if _safe_num(x.get("price")) and _safe_num(x.get("price")) < spot)
        resist = sum(_safe_num(x.get("value")) for x in darkpool
                     if _safe_num(x.get("price")) > spot)
        sM, rM = supp / 1e6, resist / 1e6
        if supp > resist * 1.2:
            vc, dc = 1, f"bloques en SOPORTE (${sM:,.0f}M abajo vs ${rM:,.0f}M arriba)"
        elif resist > supp * 1.2:
            vc, dc = -1, f"bloques en RESISTENCIA (${rM:,.0f}M arriba vs ${sM:,.0f}M abajo)"
        else:
            vc, dc = 0, f"dark pool equilibrado (${sM:,.0f}M soporte / ${rM:,.0f}M resistencia)"

    score = round(0.50 * va + 0.30 * vb + 0.20 * vc, 2)
    others = [vb, vc]
    agree = sum(1 for o in others if o != 0 and o == va) if va != 0 else 0
    oppose = sum(1 for o in others if o != 0 and o == -va) if va != 0 else 0

    if va != 0:
        direction = _lab(va)
        if oppose == 0 and agree >= 1:
            verdict = "confirmacion"
            badge = "ALTA CONVICCIÓN" if agree == 2 else "CONFIRMACIÓN"
        elif oppose >= 1 and agree == 0:
            verdict, badge = "divergencia", "DIVERGENCIA"
        elif oppose >= 1 and agree >= 1:
            verdict, badge = "mixto", "MIXTO"
        else:
            verdict, badge = "parcial", "PARCIAL"
    else:
        pos = vb + vc
        direction = _lab(1 if pos > 0 else (-1 if pos < 0 else 0))
        if pos != 0 and vb != 0 and vc != 0 and vb == vc:
            verdict, badge = "posicionamiento", "SOLO POSICIONAMIENTO"
        else:
            verdict, badge = "neutral", "SIN SEÑAL"

    sig = {"conviccion": {"vote": va, "label": _lab(va), "detail": da},
           "gex":        {"vote": vb, "label": _lab(vb), "detail": db},
           "darkpool":   {"vote": vc, "label": _lab(vc), "detail": dc}}

    if verdict == "confirmacion":
        summary = (f"Confluencia {direction.upper()}: las señales se confirman entre sí "
                   f"({'las 3 alineadas' if agree == 2 else 'convicción + 1 confirmación'}). Setup de mayor probabilidad.")
    elif verdict == "divergencia":
        summary = (f"DIVERGENCIA: la convicción de tape es {sig['conviccion']['label']} pero el posicionamiento la "
                   f"contradice. El smart money y los dealers/bloques no coinciden — reduce tamaño y espera confirmación.")
    elif verdict == "mixto":
        summary = (f"Señales mixtas sobre una convicción {sig['conviccion']['label']}: una confirma y otra contradice. "
                   f"Sesgo {direction} sin consenso.")
    elif verdict == "parcial":
        summary = (f"Solo hay convicción de tape ({sig['conviccion']['label']}); GEX y dark pool neutrales. "
                   f"Direccional sin confirmación de posicionamiento.")
    elif verdict == "posicionamiento":
        summary = f"Sin convicción de tape, pero el posicionamiento (GEX + dark pool) inclina {direction}."
    else:
        summary = "Sin señal clara de confluencia: los pilares no coinciden o faltan datos."

    return {"ok": True, "direction": direction, "verdict": verdict, "badge": badge,
            "score": score, "agree": agree, "oppose": oppose, "signals": sig, "summary": summary}


    def _lab(v):
        return "alcista" if v > 0 else ("bajista" if v < 0 else "neutral")


def _qd_confluence_prompt_block(confl):
    """Render the confluence verdict as a Spanish prompt block for the agent's reasoning."""
    if not confl or not isinstance(confl, dict) or not confl.get("ok"):
        return ""
    s = confl["signals"]
    return (
        f"\nCONFLUENCIA DE SEÑALES (motor Vertex — ¿coinciden tus 3 pilares de Quant Data?): "
        f"veredicto {confl['badge']} · dirección {confl['direction'].upper()} · score {confl['score']:+.2f}. "
        f"(1) Convicción tape: {s['conviccion']['detail']}. (2) GEX: {s['gex']['detail']}. "
        f"(3) Dark pool: {s['darkpool']['detail']}. {confl['summary']} "
        f"USO: cuando los 3 pilares se CONFIRMAN, sube tu convicción y tu probabilidad calibrada y dilo en la tesis; "
        f"cuando hay DIVERGENCIA, BÁJALAS y explica el conflicto en 'tesis_riesgos' (tape institucional vs "
        f"posicionamiento de dealers/bloques en desacuerdo suele preceder volatilidad o un head-fake).")


def _qd_darkpool_prompt_block(darkpool, spot, dp_flow=None):
    """Render Quant Data dark-pool levels as a prompt block: top support (below spot) and
    resistance (above spot) zones by notional, plus the BUY/SELL proxy from prints when available."""
    if not darkpool or not isinstance(darkpool, list):
        return ""
    spot = _safe_num(spot)
    if not spot:
        return ""
    below = [x for x in darkpool if _safe_num(x.get("price")) and _safe_num(x.get("price")) < spot]
    above = [x for x in darkpool if _safe_num(x.get("price")) > spot]
    supp = sorted(below, key=lambda x: _safe_num(x.get("value")), reverse=True)[:4]
    resist = sorted(above, key=lambda x: _safe_num(x.get("value")), reverse=True)[:4]

    def _fmt(rows):
        return ", ".join(
            f"${round(_safe_num(r.get('price')), 2)} (${_safe_num(r.get('value'))/1e6:,.0f}M, "
            f"{int(_safe_num(r.get('size'))):,} sh)" for r in rows) or "—"
    tot_s = sum(_safe_num(x.get("value")) for x in below)
    tot_r = sum(_safe_num(x.get("value")) for x in above)
    flow_txt = ""
    if isinstance(dp_flow, dict) and dp_flow.get("total_notional"):
        bM = _safe_num(dp_flow.get("buy_notional")) / 1e6
        sM = _safe_num(dp_flow.get("sell_notional")) / 1e6
        mM = _safe_num(dp_flow.get("mid_notional")) / 1e6
        lean = dp_flow.get("lean_pct")
        flow_txt = (f" PROXY COMPRA/VENTA de los prints dark (tradeSide vs bid/ask): "
                    f"${bM:,.0f}M en el ASK (compra) vs ${sM:,.0f}M en el BID (venta), ${mM:,.0f}M al mid → "
                    f"sesgo {dp_flow.get('bias')}{(' (' + str(int(lean)) + ' net)') if lean is not None else ''}. "
                    f"El mid es neutral; el desbalance ask-vs-bid es la dirección real del dinero institucional.")
    return (
        f"\nDARK POOL (Quant Data — bloques off-exchange agregados por nivel, último mes): "
        f"SOPORTE (bajo el spot, posible acumulación): {_fmt(supp)}. "
        f"RESISTENCIA (sobre el spot, posible distribución): {_fmt(resist)}. "
        f"Notional total ${tot_s/1e6:,.0f}M en soporte vs ${tot_r/1e6:,.0f}M en resistencia.{flow_txt} "
        f"Los niveles agregados son posicionales (imán/soporte/resistencia que confirman o niegan los walls de GEX); "
        f"el proxy compra/venta sí da dirección. Intégralo en tus targets y en tu señal de flujo institucional.")


    def _fmt(rows):
        return ", ".join(
            f"${round(_safe_num(r.get('price')), 2)} (${_safe_num(r.get('value'))/1e6:,.0f}M, "
            f"{int(_safe_num(r.get('size'))):,} sh)" for r in rows) or "—"


def _qd_netflow_prompt_block(nf):
    """Render the net-premium-over-time (net-flow) drift + trend as a prompt block."""
    if not nf or not isinstance(nf, dict) or not nf.get("series"):
        return ""
    cum = _safe_num(nf.get("cum_net"))
    win = {"today": "hoy (intradía)", "7d": "últimos 7 días", "30d": "últimos 30 días",
           "90d": "últimos 90 días"}.get(nf.get("window"), nf.get("window"))
    return (
        f"\nNET DRIFT EN EL TIEMPO (Quant Data net-flow, {win}): premium neto call−put acumulado "
        f"${cum:,.0f} → sesgo {nf.get('bias')}; tendencia {nf.get('trend')}. "
        f"USO: no mires solo el nivel — la TENDENCIA importa. 'acelerando (alcista)' = la presión compradora "
        f"de premium se intensifica (confirma momentum); 'desvaneciéndose' = el flujo pierde fuerza (cuidado con "
        f"agotamiento); 'revirtiendo' = el dinero está cambiando de lado (posible giro). Pondéralo en tu señal de flujo y en tu timing.")


def _qd_gex_walls(exposure, spot):
    """Derive call wall / put wall + zero-gamma pin from a Quant Data exposure dict (GAMMA, by_strike).
    El wall NO es el strike único con más gamma, sino el CENTRO del clúster de strikes contiguos con más
    gamma sumada (ventana ≈ ±1 strike → 2-3 strikes). Más robusto: una mecha aislada en un solo strike no
    desplaza el wall; gana la 'zona de pared' real donde se concentra el posicionamiento del dealer.
    El net devuelto es la suma del clúster (refleja la fuerza de la zona, no de un solo strike)."""
    if not exposure or not isinstance(exposure, dict):
        return None
    rows = [r for r in (exposure.get("by_strike") or []) if r.get("strike")]
    if not rows:
        return None
    # net agregado por strike + ventana = 1.5× el espaciado mediano de strikes (capta el strike ± su vecino inmediato)
    netmap = {}
    for r in rows:
        k = _safe_num(r.get("strike"))
        netmap[k] = netmap.get(k, 0.0) + _safe_num(r.get("net"))
    ks = sorted(netmap.keys())
    gaps = [ks[i + 1] - ks[i] for i in range(len(ks) - 1) if ks[i + 1] - ks[i] > 0]
    W = 1.5 * (sorted(gaps)[len(gaps) // 2] if gaps else 1.0)

    def _cluster(cands, want_max):
        best_k, best_key = None, None
        for k in cands:
            s = sum(n for kk, n in netmap.items() if abs(kk - k) <= W)   # suma del clúster contiguo
            key = (s, netmap.get(k, 0.0))   # desempate: a igual clúster, gana el strike con más gamma PROPIA (el pico real)
            if best_key is None or (key > best_key if want_max else key < best_key):
                best_key, best_k = key, k
        return (best_k, best_key[0]) if best_k is not None else (None, None)

    above = [k for k in ks if k > spot]
    below = [k for k in ks if k < spot]
    cw, cw_net = _cluster(above, True)   # call wall = clúster con MÁS gamma positiva arriba del spot
    pw, pw_net = _cluster(below, False)  # put wall  = clúster con MÁS gamma negativa abajo del spot
    flip = _gamma_flip_from_strikes(rows, spot)
    return {"call_wall": cw, "put_wall": pw,
            "call_wall_net": cw_net, "put_wall_net": pw_net,
            "gamma_flip": flip, "gamma_flip_confidence": (_flip_confidence(rows, spot) if flip else None),
            "max_pain": None}


    def _cluster(cands, want_max):
        best_k, best_key = None, None
        for k in cands:
            s = sum(n for kk, n in netmap.items() if abs(kk - k) <= W)   # suma del clúster contiguo
            key = (s, netmap.get(k, 0.0))   # desempate: a igual clúster, gana el strike con más gamma PROPIA (el pico real)
            if best_key is None or (key > best_key if want_max else key < best_key):
                best_key, best_k = key, k
        return (best_k, best_key[0]) if best_k is not None else (None, None)


def _qd_exposure_walls(ticker, exp, spot, ttl=300):
    """Cached call/put wall from Quant Data GAMMA exposure for a single expiry."""
    key = f"{ticker.upper()}|{exp}"
    nowt = time.time()
    ent = _QD_EXPWALL_CACHE.get(key)
    if ent and nowt - ent[0] < ttl:
        return ent[1]
    w = _qd_gex_walls(quantdata_exposure(ticker, "GAMMA", expiration=exp), spot)
    _QD_EXPWALL_CACHE[key] = (nowt, w)
    return w


def _chain_metrics(tk, exp, spot):
    """ONE option_chain fetch → ATM IV (forward-looking) + TRUE max pain from full-chain open interest.
    Max pain needs the COMPLETE chain OI (every strike), which Quant Data's open-interest-change can't
    give (it only returns strikes whose OI changed), so we use the full yfinance chain here."""
    iv = mp = None
    mp_src = None
    try:
        ch = tk.option_chain(exp)
        ivs = []
        call_oi, put_oi = {}, {}
        for df, oid in ((ch.calls, call_oi), (ch.puts, put_oi)):
            if df is None or df.empty:
                continue
            d2 = df.dropna(subset=["impliedVolatility"])
            if not d2.empty:
                idx = (d2["strike"] - spot).abs().idxmin()
                v = float(d2.loc[idx, "impliedVolatility"])
                if 0.01 < v < 5.0:
                    ivs.append(v)
            for _, r in df.iterrows():
                K = _safe_num(r.get("strike")); oi = _safe_num(r.get("openInterest"))
                if K > 0 and oi > 0:
                    oid[K] = oid.get(K, 0.0) + oi
        if ivs:
            iv = sum(ivs) / len(ivs)
        # #7 — max pain por-vencimiento: QD nativo si honra el filtro (autodetectado), si no yfinance
        try:
            _sym = getattr(tk, "ticker", None) or ""
        except Exception:
            _sym = ""
        mp, mp_src = _max_pain_per_expiry_best(_sym, exp, call_oi, put_oi)
    except Exception:
        pass
    return {"iv": iv, "max_pain": mp, "max_pain_source": mp_src}


def compute_horizon_targets(ticker, net_premium=None, flow=None, ai_12m=None, qd_walls_fn=None, conviction=None, calibrate=True):
    """Targets at Hoy(0DTE)/7/30/60/90/120d + 12m. Levels anchored to Quant Data GEX walls (per expiry)
    when qd_walls_fn is provided, else to the computed chain. Direction driven by tape
    conviction (Kevin's tiers) → net premium → GEX, in that priority. El target 'Hoy' usa el
    vencimiento más cercano (0DTE si existe) + el flujo/convicción del día, mismo motor que el resto."""
    HZ = [0, 7, 30, 60, 90, 120]   # 0 = Hoy (0DTE / vencimiento más cercano)
    try:
        tk = yf.Ticker(ticker)
        h = tk.history(period="3mo")
        spot = _safe_num(h["Close"].iloc[-1], 0.0)
        exps = list(tk.options or [])
    except Exception:
        return None
    if spot <= 0 or not exps:
        return None
    # Annualized volatility from recent daily log-returns → drives the expected-move band.
    try:
        _r = np.log(h["Close"] / h["Close"].shift(1)).dropna()
        ann_vol = float(_r.std() * np.sqrt(252)) if len(_r) > 5 else 0.4
        if not (0.05 <= ann_vol <= 3.0):
            ann_vol = 0.4
    except Exception:
        ann_vol = 0.4
    earn_dt = _next_earnings_date(tk)   # #3 — flag horizons that cross a report
    now = datetime.now()
    # Direction priority: tape conviction > net premium > GEX magnet
    bias = "neutral"
    if conviction and conviction.get("bias") and conviction["bias"] != "neutral":
        bias = conviction["bias"]
    elif net_premium and isinstance(net_premium, dict):
        np_ = _safe_num(net_premium.get("net_premium"))
        bias = "alcista" if np_ > 0 else ("bajista" if np_ < 0 else "neutral")
    dir_pct = _classify_flow_delta(flow, spot) if flow else None
    conv_strength = conviction.get("strength_pct") if conviction else None
    strong_metric = conv_strength if conv_strength is not None else dir_pct

    # ── Dirección del horizonte "Hoy" con flujo PROPIO de la expiración 0DTE (no el agregado del día) ──
    # Kevin: el target de Hoy debe leer solo el tape de la 0DTE/vencimiento más cercano, también en dirección.
    bias0, strong0 = None, None
    try:
        _ne0 = _nearest_expiry(exps, 0, now)
        if _ne0 and _quantdata_ready():
            _nf0 = quantdata_net_flow(ticker, "today", _ne0[0])
            if _nf0 and _nf0.get("n"):
                bias0 = _nf0.get("bias") if _nf0.get("bias") != "neutral" else None
                _gross0 = _safe_num(_nf0.get("call_total")) + _safe_num(_nf0.get("put_total"))
                if _gross0 > 0:
                    strong0 = round(100.0 * abs(_safe_num(_nf0.get("cum_net"))) / _gross0)   # desbalance 0DTE (0–100)
    except Exception:
        bias0, strong0 = None, None

    # #4 — pull this ticker's own backtest so confidence reflects REAL accuracy, not fixed rules.
    bt_cal = None
    if calibrate:
        try:
            _bt = _backtest_cached(ticker)
            if _bt and _bt.get("ok") and _bt.get("n_snapshots"):
                _cd = _bt.get("confluence_direction") or {}
                bt_cal = {"hr": _bt.get("target_hit_rate") or {},
                          "dir_acc": _cd.get("accuracy_pct"), "dir_n": _cd.get("evaluated") or 0}
        except Exception:
            bt_cal = None

    targets, used_qd = [], False
    for i, hz in enumerate(HZ):
        ne = _nearest_expiry(exps, hz, now)
        if not ne:
            continue
        exp, dte = ne
        w, wsrc = None, "cadena"
        if qd_walls_fn:
            try:
                w = qd_walls_fn(exp, spot)
            except Exception:
                w = None
            if w:
                wsrc = "Quant Data GEX"; used_qd = True
        if not w:
            w = _walls_for_expiry(tk, exp, spot)
        if not w:
            continue
        _cm = _chain_metrics(tk, exp, spot)   # #2 IV + #5 max pain real (una sola bajada de cadena)
        iv = _cm["iv"]
        chain_mp = _cm["max_pain"]
        pin = chain_mp or w.get("gamma_flip")  # max pain real (OI completo) o, si no, el gamma flip
        # ── Dirección = mezcla de FLUJO y el IMÁN DE GAMMA dominante (no flujo-solo) ──
        cw, pw = w.get("call_wall"), w.get("put_wall")
        cwn, pwn = w.get("call_wall_net"), w.get("put_wall_net")
        # imán dominante: el wall con mayor |gamma neta| (o, si no se conoce, el más cercano al spot)
        magnet, mag_dir = None, None
        if cw and pw and cwn is not None and pwn is not None:
            magnet, mag_dir = (cw, "up") if abs(cwn) >= abs(pwn) else (pw, "down")
        elif cw and pw:
            magnet, mag_dir = (cw, "up") if abs(cw - spot) <= abs(spot - pw) else (pw, "down")
        elif cw:
            magnet, mag_dir = cw, "up"
        elif pw:
            magnet, mag_dir = pw, "down"
        flow_dir = (bias0 if (hz == 0 and bias0) else bias)   # Hoy usa el sesgo 0DTE propio si existe
        sm_use = strong0 if (hz == 0 and strong0 is not None) else strong_metric
        conflict = False
        if magnet is None:
            level = pin or cw or pw
            direction = "up" if (level and level > spot) else "down"
            basis = ("max pain" if chain_mp else ("pin gamma" if w.get("gamma_flip") else "estructura"))
        elif flow_dir == "neutral":
            direction, level, basis = mag_dir, magnet, "imán gamma"
        elif (flow_dir == "alcista" and mag_dir == "up") or (flow_dir == "bajista" and mag_dir == "down"):
            direction = mag_dir
            level = cw if mag_dir == "up" else pw
            basis = "flujo + gamma"
        else:
            # flujo y gamma se contradicen → el flujo manda SOLO si la convicción es fuerte (≥60%)
            conflict = True
            if sm_use is not None and sm_use >= 60:
                direction = "up" if flow_dir == "alcista" else "down"
                level = cw if flow_dir == "alcista" else pw
                basis = "flujo fuerte vs imán"
            else:
                direction, level, basis = mag_dir, magnet, "imán gamma vs flujo débil"
        # Sin reversión forzada; cada horizonte usa su propia estructura.
        vol_use = iv if iv else ann_vol
        em = _expected_move(spot, vol_use, dte)
        level, capped = _clamp_target(level, spot, direction, em)
        earnings_soon = bool(earn_dt and now <= earn_dt <= now + timedelta(days=hz))
        # Base confidence by horizon — fallback only while there's no backtest yet.
        conf = "alta" if hz <= 30 else ("media" if hz <= 90 else "media-baja")
        if sm_use is not None:
            if sm_use >= 60 and hz <= 60:
                conf = "muy alta"
            elif sm_use < 40 and hz <= 30:
                conf = "media"
        # #4 + rigor estadístico — calibra SOLO con muestra suficiente y mapea la confianza desde el
        # PISO de Wilson, no del % crudo (n baja → piso bajo → confianza menor, automáticamente).
        cal_pct = cal_lo = cal_n = None
        cal_low_sample = False
        if bt_cal:
            _hr = bt_cal["hr"].get(str(hz))
            if _hr and _hr.get("hit_rate_pct") is not None and _hr.get("total", 0) >= _BT_MIN_HZ_N:
                cal_n = int(_hr["total"]); cal_pct = _hr["hit_rate_pct"]
                cal_lo = round(_wilson_lower(round(cal_pct / 100.0 * cal_n), cal_n) * 100, 1)
            elif bt_cal.get("dir_acc") is not None and bt_cal.get("dir_n", 0) >= _BT_MIN_DIR_N:
                cal_n = int(bt_cal["dir_n"]); cal_pct = bt_cal["dir_acc"]
                cal_lo = round(_wilson_lower(round(cal_pct / 100.0 * cal_n), cal_n) * 100, 1)
            elif (_hr and _hr.get("total", 0) > 0) or bt_cal.get("dir_n", 0) > 0:
                cal_low_sample = True   # hay backtest pero muestra demasiado chica → NO calibramos
        _cal_conf = _confidence_from_hit(cal_lo) if cal_lo is not None else None
        _src = f"{wsrc} · {basis}"
        if capped:
            _src += " · ajustado a mov. esperado"
            if conf in ("muy alta", "alta"):
                conf = "media"  # capped a far wall → menos certeza del nivel exacto
        if _cal_conf:                                       # el backtest real (con muestra) manda sobre la regla
            conf = _cal_conf
            _src += f" · calibrado {cal_pct:.0f}% (piso {cal_lo:.0f}%, n={cal_n})"
        elif cal_low_sample:
            _src += " · n insuficiente → confianza por reglas"
        if earnings_soon:                                   # #3 — earnings en el horizonte = otro régimen
            _src += " · ⚠ earnings en el rango"
            if conf in ("muy alta", "alta"):
                conf = "media"
        if hz == 0:                                         # Hoy / 0DTE: ruido intrínseco salvo convicción fuerte
            _src = "0DTE · " + _src
            if bias0:
                _src += " · dir. flujo 0DTE propio"
            if conf in ("muy alta", "alta") and not (sm_use is not None and sm_use >= 60):
                conf = "media"
        targets.append({"label": ("Hoy" if hz == 0 else f"{hz}d"), "horizon_days": hz, "expiry": exp, "dte": dte,
                        "level": round(level, 2) if level else None, "direction": direction,
                        "confidence": conf, "capped": capped, "basis": basis, "conflict": conflict,
                        "calibrated_pct": (round(cal_pct, 0) if cal_pct is not None else None),
                        "calibrated_lo": (round(cal_lo, 0) if cal_lo is not None else None),
                        "calibrated_n": cal_n,
                        "expected_move": round(em, 2) if em else None,
                        "em_low": round(spot - em, 2) if em else None,
                        "em_high": round(spot + em, 2) if em else None,
                        "iv": (round(iv, 4) if iv else None), "vol_used": round(vol_use, 4),
                        "vol_source": ("IV" if iv else "histórica"),
                        "earnings_soon": earnings_soon,
                        "gamma_flip": w.get("gamma_flip"), "max_pain": chain_mp,
                        "call_wall": cw, "put_wall": pw,
                        "call_wall_net": cwn, "put_wall_net": pwn,
                        "max_pain_source": _cm.get("max_pain_source"),
                        "source": _src})
    if ai_12m and _safe_num(ai_12m) > 0:
        a = _safe_num(ai_12m)
        targets.append({"label": "12 meses", "horizon_days": 365, "level": round(a, 2),
                        "direction": "up" if a > spot else "down", "confidence": "fundamental",
                        "source": "DCF / fundamental del agente"})
    else:
        targets.append({"label": "12 meses", "horizon_days": 365, "level": None, "direction": None,
                        "confidence": "fundamental",
                        "source": "Corre la tesis AI para el target fundamental de 12m"})
    return _json_safe({"ok": True, "ticker": ticker.upper(), "spot": round(spot, 2),
                       "bias": bias, "directional_pct": dir_pct, "conviction": conviction,
                       "gex_source": "Quant Data" if used_qd else "computed (yfinance)",
                       "targets": targets,
                       "note": "Targets cortos (Hoy/0DTE–120d) guiados por GEX + convicción del tape; el de 12m es "
                               "fundamental. 'Hoy' usa el vencimiento más cercano + flujo del día. Escenarios probabilísticos, no predicciones.",
                       "generated_at": now.strftime('%m/%d/%Y, %I:%M:%S %p')})


def get_horizon_targets_cached(ticker, net_premium=None, flow=None, ai_12m=None, ttl=300):
    key = f"{ticker.upper()}|{ai_12m}|{bool(net_premium)}"
    nowt = time.time()
    ent = _HZTGT_CACHE.get(key)
    if ent and nowt - ent[0] < ttl:
        return ent[1]
    val = compute_horizon_targets(ticker, net_premium, flow, ai_12m)
    _HZTGT_CACHE[key] = (nowt, val)
    return val


# El `/api/projection-targets` de Quant Data —el que llamaba a
# `compute_horizon_targets` con walls y convicción de QD— vivía aquí y la
# fusión con `main` lo resucitó. Se borra: la ruta la sirve el motor de Víctor,
# arriba. Dos `@app.get` con el mismo path no dan error en FastAPI; gana el
# primero y el segundo queda como código que nadie ejecuta y que en la
# siguiente lectura parece la implementación vigente.


def _chain_quote_map(ticker, expiries, ttl=180):
    """Quotes REALES (bid/ask/mid + OI + volumen) por (expiry, CALL/PUT, strike) desde la cadena de yfinance.
    Permite que el plan de opciones use el FILL real (mid del bid/ask) y la LIQUIDEZ real (OI/spread) en vez del
    precio teórico Black-Scholes. Best-effort y cacheado: si yfinance no responde, devuelve {} y el plan cae al
    teórico sin romperse. El spread bid/ask en contratos chicos/OTM se come 10–30% de la prima — esto lo expone."""
    exps = sorted({str(e)[:10] for e in (expiries or []) if e})
    if not exps:
        return {}
    key = (ticker.upper(), tuple(exps))
    now = time.time()
    hit = _QMAP_CACHE.get(key)
    if hit and now - hit[0] < ttl:
        return hit[1]
    out = {}
    try:
        tk = yf.Ticker(ticker)
        for e in exps:
            try:
                ch = tk.option_chain(e)
            except Exception:
                continue
            for df, cp in ((getattr(ch, "calls", None), "CALL"), (getattr(ch, "puts", None), "PUT")):
                if df is None or getattr(df, "empty", True):
                    continue
                for _, r in df.iterrows():
                    K = _safe_num(r.get("strike"))
                    if K <= 0:
                        continue
                    bid = _safe_num(r.get("bid")); ask = _safe_num(r.get("ask")); last = _safe_num(r.get("lastPrice"))
                    mid = round((bid + ask) / 2, 4) if (bid > 0 and ask > 0) else (last if last > 0 else None)
                    out[(e, cp, round(K, 2))] = {"oi": int(_safe_num(r.get("openInterest"))),
                                                 "vol": int(_safe_num(r.get("volume"))),
                                                 "bid": (bid or None), "ask": (ask or None), "mid": mid}
    except Exception:
        return {}
    _QMAP_CACHE[key] = (now, out)
    return out


@app.get("/api/trade-plan")
def trade_plan_endpoint(ticker: str, capital: float = 500.0, risk_pct: float = 15.0,
                        horizons: str = "30,60,90", stop_pct: float = 25.0,
                        alloc_pct: float = None):
    """Convierte la SEÑAL (dirección + convicción + target por horizonte) en una OPERACIÓN de opción
    CONCRETA: contrato (CALL/PUT ≈ATM), prima de entrada (Black-Scholes con la IV del horizonte),
    valor proyectado de la opción si el subyacente llega al target (rápido vs al vencimiento), R:R,
    breakeven, stop (−20/−30% de la prima, con excepción de flujo Tipo A $5M+) y nº de contratos
    dimensionado a tu capital y presupuesto de riesgo. Complementa el trade_plan de equity del agente."""
    ticker = ticker.upper().strip()
    RF = 0.043
    stop_frac = max(min(float(stop_pct), 90.0), 1.0) / 100.0
    try:
        hz_list = [int(x) for x in str(horizons).split(",") if x.strip().isdigit()][:5] or [30, 60, 90]
    except Exception:
        hz_list = [30, 60, 90]
    ready = _quantdata_ready()
    np_ = quantdata_net_premium(ticker) if ready else None
    fl = quantdata_flow(ticker) if ready else None
    oic = quantdata_oi_change(ticker) if ready else None
    oic_map = oic.get("map") if isinstance(oic, dict) else None
    conv = _qd_conviction(fl, oi_change_map=oic_map) if fl else None
    walls_fn = (lambda e, s: _qd_exposure_walls(ticker, e, s)) if ready else None
    t = compute_horizon_targets(ticker, np_, fl, None, qd_walls_fn=walls_fn, conviction=conv, calibrate=True)
    if not t or not t.get("ok") or not t.get("targets"):
        return {"ok": False, "error": "Sin targets para estructurar (cadena/QD no disponible)."}
    spot = _safe_num(t.get("spot"))
    if spot <= 0:
        return {"ok": False, "error": "Sin spot disponible."}

    # IV vs volatilidad REALIZADA (proxy de VRP, sin necesitar IV-rank histórico): si la IV del
    # horizonte está cara vs la realizada, comprar prima larga es ineficiente → favorecer débito spread;
    # si está barata, la opción simple es más eficiente (más convexidad por el costo).
    realized_ann = None
    try:
        _ser = _cached_price_series(ticker, period="6mo")
        if _ser and len(_ser) > 20:
            _cl = [c for _, c in _ser]
            _rets = [math.log(_cl[i] / _cl[i - 1]) for i in range(1, len(_cl)) if _cl[i - 1] > 0]
            if len(_rets) > 10:
                _mean = sum(_rets) / len(_rets)
                _var = sum((x - _mean) ** 2 for x in _rets) / (len(_rets) - 1)
                realized_ann = (_var ** 0.5) * (252 ** 0.5)
    except Exception:
        realized_ann = None

    def _iv_regime(iv):
        if not realized_ann or realized_ann <= 0 or not iv:
            return None, None
        ratio = iv / realized_ann
        if ratio >= 1.25:
            return round(ratio, 2), "IV rica vs realizada → el débito spread es más eficiente (vendes vol cara)"
        if ratio <= 1.00:
            return round(ratio, 2), "IV barata vs realizada → la opción simple larga es más eficiente (más convexidad)"
        return round(ratio, 2), "IV en línea con la realizada → estructura por capital/preferencia"

    # Tipo A activo = una sola transacción ≥ $5M alineada con el sesgo dominante (excepción de stop de Kevin)
    tipo_a = False
    dom_bias = (conv or {}).get("bias")
    if conv and conv.get("strong_trades"):
        want = "CALL" if dom_bias == "alcista" else ("PUT" if dom_bias == "bajista" else None)
        for st in conv["strong_trades"]:
            if _safe_num(st.get("premium")) >= 5e6 and (want is None or st.get("cp") == want):
                tipo_a = True
                break

    # Anclaje institucional: dónde se acumularon MÁS millones direccionales (ventana 90d, $1M+).
    inst_rows = quantdata_flow_window(ticker, days=90, min_premium=1_000_000) if ready else None
    inst_overall = None
    if inst_rows and dom_bias in ("alcista", "bajista"):
        inst_overall = _institutional_strike(inst_rows, dom_bias)

    def strike_round(x):
        if x >= 100:
            return round(x / 5.0) * 5.0
        if x >= 25:
            return float(round(x))
        return round(x * 2) / 2.0

    by_hz = {x.get("horizon_days"): x for x in t["targets"]}
    short = [x for x in t["targets"] if x.get("horizon_days", 0) < 365 and x.get("level")]
    # Quotes reales de la cadena (mid del bid/ask + OI) para que la entrada y la liquidez NO sean teóricas
    _need_exp = sorted({x.get("expiry") for x in t["targets"] if x.get("expiry")})
    qmap = _chain_quote_map(ticker, _need_exp)
    plans = []
    for hz in hz_list:
        tg = by_hz.get(hz)
        if not (tg and tg.get("level") and tg.get("direction")):
            tg = min(short, key=lambda x: abs(x["horizon_days"] - hz)) if short else None
        if not tg or not tg.get("level") or not tg.get("direction"):
            continue
        direction = tg["direction"]
        opt = "call" if direction == "up" else "put"
        atm_K = strike_round(spot)
        bias_for_anchor = "alcista" if opt == "call" else "bajista"
        inst_hz = _institutional_strike(inst_rows, bias_for_anchor, near_dte=hz) if inst_rows else None
        anchor_K = (inst_hz["strike"] if inst_hz
                    else (inst_overall["strike"] if (inst_overall and inst_overall.get("cp") == opt.upper()) else None))
        K = _kevin_long_strike(anchor_K, spot, opt, atm_K)   # tu regla: ATM o ITM, nunca OTM
        long_mny = _moneyness(K, spot, opt)
        dte = int(tg.get("dte") or hz)
        iv = _safe_num(tg.get("vol_used")) or _safe_num(tg.get("iv"))
        if iv <= 0 or dte <= 0:
            continue
        entry_theo = _bs_price(spot, K, dte / 365.0, iv, RF, opt)    # prima teórica BSM (por acción)
        rq = _q_lookup(qmap, tg.get("expiry"), opt, K)               # fill REAL (mid) + liquidez de la cadena
        liq_oi = liq_vol = liq_ask = liq_spread = None
        if rq and rq.get("mid") and rq["mid"] > 0:
            entry = float(rq["mid"]); pricing_basis = "mid real (bid/ask)"
            liq_oi, liq_vol, liq_ask = rq.get("oi"), rq.get("vol"), rq.get("ask")
            if rq.get("bid") and rq.get("ask") and entry > 0:
                liq_spread = round((rq["ask"] - rq["bid"]) / entry * 100, 1)   # % del mid
        else:
            entry = entry_theo; pricing_basis = "teórico (sin quote en vivo)"
        if entry <= 0:
            continue
        entry_c = entry * 100.0
        level = _safe_num(tg["level"])
        # Valor de salida en el target bajo 2 supuestos de tiempo (theta):
        val_fast = _bs_price(level, K, max(dte / 2.0, 0.5) / 365.0, iv, RF, opt) * 100.0   # llega a mitad del horizonte
        intr = max(0.0, (level - K) if opt == "call" else (K - level)) * 100.0             # llega al vencimiento (solo intrínseco)
        breakeven = (K + entry) if opt == "call" else (K - entry)
        stop_price = entry * (1 - stop_frac)
        planned_risk_c = entry_c * stop_frac
        budget = max(float(capital), 0.0) * max(float(risk_pct), 0.0) / 100.0
        n_by_risk = int(budget // planned_risk_c) if planned_risk_c > 0 else 0
        n_by_cap = int(float(capital) // entry_c) if entry_c > 0 else 0
        if alloc_pct is not None:                       # Kelly del agente → dimensiona por CAPITAL desplegado en prima
            alloc_dollars = max(float(capital), 0.0) * max(float(alloc_pct), 0.0) / 100.0
            n_by_alloc = int(alloc_dollars // entry_c) if entry_c > 0 else 0
            contracts = max(0, min(n_by_alloc, n_by_cap))
        else:
            contracts = max(0, min(n_by_risk, n_by_cap))
        reward_fast_c = val_fast - entry_c
        reward_intr_c = intr - entry_c
        rr_fast = (reward_fast_c / planned_risk_c) if planned_risk_c > 0 else None
        rr_intr = (reward_intr_c / planned_risk_c) if planned_risk_c > 0 else None
        notes = []
        if tg.get("vol_source") != "IV":
            notes.append("IV histórica (proxy): la prima real puede diferir")
        if tg.get("earnings_soon"):
            notes.append("⚠ earnings en el horizonte: posible IV crush tras el evento")
        if tg.get("capped"):
            notes.append("target ajustado al movimiento esperado")
        if tg.get("conflict"):
            notes.append("flujo vs imán de gamma en conflicto")
        # --- Liquidez / fill real del contrato (qué tan fácil es ENTRAR y SALIR) ---
        if pricing_basis.startswith("mid real"):
            if liq_oi is not None and liq_oi < 50:
                notes.append(f"⚠ OI bajo ({liq_oi}) en el strike — difícil de cerrar sin mover el precio")
            if liq_spread is not None and liq_spread > 20:
                notes.append(f"⚠ spread ancho (~{liq_spread}% del mid) — el bid/ask te come prima al entrar y salir")
        else:
            notes.append("prima teórica (sin quote en vivo): el fill real puede diferir 10–30% en strikes finos")
        # --- Estructuras de débito para capital chico (dos variantes) ---
        step = 5.0 if spot >= 100 else (1.0 if spot >= 25 else 0.5)
        _alloc_d = (max(float(capital), 0.0) * max(float(alloc_pct), 0.0) / 100.0) if alloc_pct is not None else None
        # 1) Tu regla: largo ATM/ITM (K) · corto OTM hacia el target → ITM/OTM o ATM/OTM
        s_short = (max(strike_round(level), K + step) if opt == "call" else min(strike_round(level), K - step))
        spread = _build_debit_spread(spot, K, s_short, level, dte, iv, RF, opt, stop_frac, capital, budget, entry_c, alloc=_alloc_d)
        # 2) OTM/OTM barato tipo lotería: largo a mitad de camino al target, corto en/junto al target
        if opt == "call":
            l2 = max(strike_round((spot + level) / 2.0), strike_round(spot) + step)
            s2 = max(strike_round(level), l2 + step)
        else:
            l2 = min(strike_round((spot + level) / 2.0), strike_round(spot) - step)
            s2 = min(strike_round(level), l2 - step)
        spread_otm = _build_debit_spread(spot, l2, s2, level, dte, iv, RF, opt, stop_frac, capital, budget, entry_c, alloc=_alloc_d)
        # Sizing por CAPITAL (no solo por presupuesto de riesgo): qué estructura cabe de verdad en tu cuenta
        naked_fits = int(float(capital) // entry_c) >= 1
        if naked_fits:
            recommended = "opción simple"
        elif spread and spread.get("fits_capital"):
            recommended = f"débito spread {spread['combo']}"
        elif spread_otm and spread_otm.get("fits_capital"):
            recommended = "débito spread OTM/OTM"
        else:
            recommended = None
        if contracts == 0:
            if recommended == "opción simple":
                notes.append(f"Cabe 1 opción simple (${entry_c:,.0f}) pero excede tu presupuesto de riesgo {risk_pct:.0f}% — decides tú.")
            elif recommended and spread and spread.get("fits_capital"):
                notes.append(f"La opción simple no cabe en ${float(capital):,.0f} → usa el {recommended} (≈${spread['net_debit_contract']:,.0f}/contrato, riesgo {spread.get('risk_pct_at_1')}%).")
            elif recommended and spread_otm:
                notes.append(f"Solo cabe el {recommended} (≈${spread_otm['net_debit_contract']:,.0f}/contrato) en ${float(capital):,.0f}.")
            else:
                notes.append(f"Ni opción simple ni spread caben en ${float(capital):,.0f} — sube capital, usa menos DTE, o un strike más OTM.")
        plans.append({
            "label": tg.get("label"), "horizon_days": tg.get("horizon_days"),
            "direction": direction, "opt_type": opt.upper(), "strike": K,
            "long_moneyness": long_mny,
            "inst_strike": (inst_hz["strike"] if inst_hz else anchor_K),
            "inst_premium": (inst_hz["premium"] if inst_hz else None),
            "inst_trades": (inst_hz["trades"] if inst_hz else None),
            "inst_exp": (inst_hz["exp_top"] if inst_hz else None),
            "anchored": bool(anchor_K is not None),
            "expiry": tg.get("expiry"), "dte": dte,
            "iv_pct": round(iv * 100, 1), "iv_source": tg.get("vol_source"),
            "entry_price": round(entry, 2), "entry_cost_contract": round(entry_c, 0),
            "entry_basis": pricing_basis, "entry_theo": round(entry_theo, 2),
            "entry_ask": (round(liq_ask, 2) if liq_ask else None),
            "entry_spread_pct": liq_spread, "strike_oi": liq_oi, "strike_vol": liq_vol,
            "liquidity_ok": (None if not pricing_basis.startswith("mid real")
                             else bool((liq_oi or 0) >= 50 and (liq_spread is None or liq_spread <= 20))),
            "breakeven": round(breakeven, 2), "target_underlying": round(level, 2),
            "exit_value_fast": round(val_fast, 0), "exit_value_expiry": round(intr, 0),
            "reward_fast_contract": round(reward_fast_c, 0), "reward_expiry_contract": round(reward_intr_c, 0),
            "rr_fast": round(rr_fast, 2) if rr_fast is not None else None,
            "rr_expiry": round(rr_intr, 2) if rr_intr is not None else None,
            "stop_pct": round(stop_frac * 100, 0), "stop_price": round(stop_price, 2), "stop_band": "−20% a −30%",
            "planned_risk_contract": round(planned_risk_c, 0), "max_loss_contract": round(entry_c, 0),
            "contracts": contracts, "total_cost": round(entry_c * contracts, 0),
            "n_by_risk": n_by_risk, "n_by_cap": n_by_cap,
            "total_risk": round(planned_risk_c * contracts, 0),
            "total_reward_fast": round(reward_fast_c * contracts, 0),
            "confidence": tg.get("confidence"), "calibrated_pct": tg.get("calibrated_pct"),
            "earnings_soon": tg.get("earnings_soon"), "tipo_a_active": tipo_a, "notes": notes,
            "spread": spread, "spread_otm": spread_otm, "recommended": recommended,
            "iv_vs_realized": _iv_regime(iv)[0], "iv_structure_hint": _iv_regime(iv)[1],
            "realized_vol_pct": round(realized_ann * 100, 1) if realized_ann else None,
        })
    if not plans:
        return {"ok": False, "error": "No se pudo estructurar ninguna operación (sin IV/targets válidos)."}
    return _json_safe({
        "ok": True, "ticker": ticker, "spot": round(spot, 2),
        "capital": float(capital), "risk_pct": float(risk_pct), "stop_pct": round(stop_frac * 100, 0),
        "alloc_pct": (round(float(alloc_pct), 1) if alloc_pct is not None else None),
        "sizing_basis": ("kelly_alloc" if alloc_pct is not None else "risk_budget"),
        "bias": t.get("bias"), "conviction": conv, "tipo_a_active": tipo_a,
        "ai_concentration": _ai_concentration(ticker),
        "inst_anchor": inst_overall,
        "flow_exception": ("Flujo Tipo A ($5M+) activo y alineado con el sesgo: tu regla permite mantener pese al "
                           "stop −20/−30% mientras el flujo siga vivo."
                           if tipo_a else "Sin flujo Tipo A ($5M+) alineado: respeta el stop −20/−30% sin excepción."),
        "plans": plans,
        "disclaimer": ("Estructura estimada con Black-Scholes (IV del horizonte, r=4.3%). El strike LARGO se ancla al "
                       "strike institucional con más millones direccionales (ventana 90d, $1M+) y se ajusta a tu regla: "
                       "ATM o ITM, nunca OTM. El valor en el target depende de CUÁNDO llegue (theta): 'rápido' = a mitad "
                       "del horizonte con valor temporal; 'al vencimiento' = solo intrínseco. No es consejo de inversión."),
        "generated_at": datetime.now().strftime('%m/%d/%Y, %I:%M:%S %p')})


    def _iv_regime(iv):
        if not realized_ann or realized_ann <= 0 or not iv:
            return None, None
        ratio = iv / realized_ann
        if ratio >= 1.25:
            return round(ratio, 2), "IV rica vs realizada → el débito spread es más eficiente (vendes vol cara)"
        if ratio <= 1.00:
            return round(ratio, 2), "IV barata vs realizada → la opción simple larga es más eficiente (más convexidad)"
        return round(ratio, 2), "IV en línea con la realizada → estructura por capital/preferencia"


    def strike_round(x):
        if x >= 100:
            return round(x / 5.0) * 5.0
        if x >= 25:
            return float(round(x))
        return round(x * 2) / 2.0


@app.get("/api/confluence")
def confluence_endpoint(ticker: str):
    """Confirmation/divergence engine: do the 3 Quant Data pillars agree?
    Convicción (tape ΔOI) + GEX/posicionamiento + dark pool → veredicto + badge + votos."""
    ticker = ticker.upper().strip()
    if not _quantdata_ready():
        return {"ok": False, "error": _quantdata_reason()}
    try:
        gex = get_gex_cached(ticker)
        spot = gex.get("spot") if isinstance(gex, dict) else None
        fl = quantdata_flow(ticker)
        oic = quantdata_oi_change(ticker)
        oimap = oic.get("map") if isinstance(oic, dict) else None
        conv = _qd_conviction(fl, oi_change_map=oimap) if fl else None
        dp = quantdata_darkpool(ticker)
        dpf = quantdata_dark_prints(ticker)                      # buy/sell proxy (directional)
        confl = _qd_confluence(conv, gex, dp, spot, dp_flow=dpf)
        return _json_safe({"ok": True, "ticker": ticker, "spot": spot,
                           "confluence": confl, "conviction": conv, "dark_flow": dpf})
    except Exception as e:
        return {"ok": False, "error": _error_publico(e, "/api/confluence")}


def _income_flow_sells(ticker, dte_max):
    """Ventas de prima institucionales más GRANDES del chain (lado BID/BELOW_BID), agrupadas por
    (exp, cp, strike) sumando premium. Marcan dónde el dinero grande VENDE prima = dónde apuesta a que
    el precio NO llega. Se usan para alinear los cortos de nuestras estructuras. {} si no hay Quant Data."""
    try:
        if not _quantdata_ready():
            return {}
        flow = quantdata_flow_window(ticker, days=max(int(dte_max), 30), min_premium=250_000, max_rows=400) or []
    except Exception:
        return {}
    sells = {}
    for t in flow:
        if "BID" not in str(t.get("side") or "").upper():          # solo ventas (BID / BELOW_BID)
            continue
        cp = str(t.get("cp") or "").upper()
        K = _safe_num(t.get("strike"))
        exp = str(t.get("exp") or "")[:10]
        if K <= 0 or not exp:
            continue
        key = (exp, "CALL" if cp.startswith("C") else "PUT", round(K, 2))
        sells[key] = sells.get(key, 0.0) + abs(_safe_num(t.get("premium")))
    return sells


def build_income_strategies(ticker, dte_min=7, dte_max=30, capital=500.0, risk_pct=50.0):
    """Estructuras de VENTA DE PRIMA (income) sobre expiraciones dte_min–dte_max (por defecto 7–30 DTE):
    Iron Condor, Put Credit Spread (bull put), Call Credit Spread (bear call) y Cash-Secured Put.
    Los cortos se colocan ~1σ (delta≈0.16) FUERA del movimiento esperado (IV × √T), y se alinean con
    los OI walls y con las VENTAS institucionales más grandes del chain. Para cada estructura: crédito,
    máx ganancia/pérdida, breakevens, POP (prob. de ganar, riesgo-neutral desde IV) y retorno sobre riesgo.
    Rankea por POP × RoR. Escanea varias expiraciones de la ventana y elige la mejor."""
    RF = 0.043
    try:
        tk = yf.Ticker(ticker)
        h = tk.history(period="3mo")
        spot = _safe_num(h["Close"].iloc[-1], 0.0)
        exps_all = list(tk.options or [])
    except Exception:
        return {"ok": False, "error": "Sin datos de mercado para el ticker."}
    if spot <= 0 or not exps_all:
        return {"ok": False, "error": "Sin cadena de opciones disponible."}
    now = datetime.now()
    _dte = lambda e: ((datetime.strptime(e, "%Y-%m-%d") - now).days if e else None)
    win = [(e, _dte(e)) for e in exps_all]
    win = [(e, d) for e, d in win if d is not None and dte_min <= d <= dte_max]
    if not win:                                    # sin expiraciones en la ventana → la más cercana al centro
        fut = [(e, _dte(e)) for e in exps_all if (_dte(e) or 0) >= 1]
        fut.sort(key=lambda x: abs((x[1] or 0) - (dte_min + dte_max) // 2))
        win = fut[:1]
    if not win:
        return {"ok": False, "error": "Sin expiraciones utilizables."}
    win.sort(key=lambda x: x[1])
    picks = [win[0], win[len(win) // 2], win[-1]] if len(win) > 3 else win   # muestrea corta/media/larga
    seen = set(); picks = [p for p in picks if not (p[0] in seen or seen.add(p[0]))]
    sells = _income_flow_sells(ticker, dte_max)
    risk_budget = max(0.0, _safe_num(capital) * _safe_num(risk_pct) / 100.0)

    def _p_below(K, T, iv):                         # prob. riesgo-neutral de que S_T < K (lognormal con IV)
        if iv <= 0 or T <= 0 or K <= 0 or spot <= 0:
            return None
        d2 = (math.log(spot / K) + (RF - 0.5 * iv * iv) * T) / (iv * math.sqrt(T))
        return _norm_cdf(-d2)
    _p_above = lambda K, T, iv: (None if _p_below(K, T, iv) is None else 1.0 - _p_below(K, T, iv))

    all_structs, per_exp = [], []
    for exp, dte in picks:
        try:
            ch = tk.option_chain(exp)
        except Exception:
            continue
        T = max(int(dte), 1) / 365.0
        # IV ATM + walls + filas por strike desde UNA sola bajada de cadena
        ivs = []
        for df in (ch.calls, ch.puts):
            if df is None or df.empty:
                continue
            d2 = df.dropna(subset=["impliedVolatility"])
            if d2.empty:
                continue
            idx = (d2["strike"] - spot).abs().idxmin()
            v = float(d2.loc[idx, "impliedVolatility"])
            if 0.01 < v < 5.0:
                ivs.append(v)
        iv = (sum(ivs) / len(ivs)) if ivs else 0.0
        if iv <= 0:
            continue
        em = _expected_move(spot, iv, dte)
        call_oi, put_oi = {}, {}
        calls, puts = {}, {}
        for df, oid, rowd in ((ch.calls, call_oi, calls), (ch.puts, put_oi, puts)):
            if df is None or df.empty:
                continue
            for _, r in df.iterrows():
                K = _safe_num(r.get("strike"))
                if K <= 0:
                    continue
                rowd[round(K, 2)] = r
                oi = _safe_num(r.get("openInterest"))
                if oi > 0:
                    oid[round(K, 2)] = oid.get(round(K, 2), 0.0) + oi
        call_ks = sorted(calls.keys()); put_ks = sorted(puts.keys())
        if len(call_ks) < 3 or len(put_ks) < 3:
            continue
        _ca = {k: v for k, v in call_oi.items() if k >= spot}
        _pb = {k: v for k, v in put_oi.items() if k <= spot}
        call_wall = max(_ca, key=_ca.get) if _ca else None
        put_wall = max(_pb, key=_pb.get) if _pb else None
        gap = 1.0
        if len(call_ks) > 1:
            gaps = [call_ks[i + 1] - call_ks[i] for i in range(len(call_ks) - 1)]
            gap = sorted(gaps)[len(gaps) // 2] if gaps else 1.0

        def _px(rowmap, K, opt):
            r = rowmap.get(round(K, 2))
            if r is None:
                return _bs_price(spot, K, T, iv, RF, opt)
            bid = _safe_num(r.get("bid")); ask = _safe_num(r.get("ask")); last = _safe_num(r.get("lastPrice"))
            if bid > 0 and ask > 0:
                return (bid + ask) / 2.0
            if last > 0:
                return last
            return _bs_price(spot, K, T, iv, RF, opt)
        _dlt = lambda K, opt: _bs_greeks(spot, K, T, iv, RF, opt)["delta"]
        _near = lambda ks, tgt: (min(ks, key=lambda k: abs(k - tgt)) if ks else None)

        def _by_delta(ks, opt, dtarget=0.16):       # strike cuyo |delta| ≈ objetivo (≈ borde 1σ)
            best, bd = None, 1e9
            for k in ks:
                d = abs(abs(_dlt(k, opt)) - dtarget)
                if d < bd:
                    bd, best = d, k
            return best

        put_below = [k for k in put_ks if k < spot]
        call_above = [k for k in call_ks if k > spot]
        short_put = _by_delta(put_below, "put") or _near(put_ks, spot - em)
        short_call = _by_delta(call_above, "call") or _near(call_ks, spot + em)
        # snap hacia el wall si está a ≤1 gap (alinéate con el OI institucional)
        if put_wall and short_put and abs(put_wall - short_put) <= gap * 1.5 and put_wall <= spot:
            short_put = _near(put_ks, put_wall)
        if call_wall and short_call and abs(call_wall - short_call) <= gap * 1.5 and call_wall >= spot:
            short_call = _near(call_ks, call_wall)
        wt = max(gap, round(0.4 * em / gap) * gap) if em else gap * 2   # ancho de ala
        long_put = _near([k for k in put_ks if k < (short_put or spot)], (short_put or spot) - wt) if short_put else None
        long_call = _near([k for k in call_ks if k > (short_call or spot)], (short_call or spot) + wt) if short_call else None

        _sell_at = lambda cp, K: max((v for (e2, c2, k2), v in sells.items()
                                      if e2 == exp and c2 == cp and abs(k2 - K) <= gap * 0.6), default=0.0)

        def _mk(kind, direction, legs, credit, maxloss, be_lo, be_hi, pop, note, collateral=None):
            credit = round(max(0.0, credit), 2); maxloss = round(max(0.01, maxloss), 2)
            ror = credit / maxloss if maxloss > 0 else None
            score = round((pop * 100.0) * (ror or 0), 1) if pop is not None else None
            if collateral:
                contracts = int(_safe_num(capital) // collateral) if collateral > 0 else 0
            else:
                contracts = int(risk_budget // (maxloss * 100.0)) if maxloss > 0 else 0
            return {"kind": kind, "exp": exp, "dte": int(dte), "direction": direction, "iv_pct": round(iv * 100, 1),
                    "legs": legs, "credit": credit, "credit_usd": round(credit * 100, 0),
                    "max_profit_usd": round(credit * 100, 0), "max_loss": maxloss, "max_loss_usd": round(maxloss * 100, 0),
                    "breakeven_low": (round(be_lo, 2) if be_lo else None), "breakeven_high": (round(be_hi, 2) if be_hi else None),
                    "pop_pct": (round(pop * 100, 1) if pop is not None else None),
                    "ror_pct": (round(ror * 100, 1) if ror else None), "score": score,
                    "contracts": max(0, contracts), "collateral_usd": (round(collateral, 0) if collateral else None),
                    "note": note}

        def _leg(action, opt, K):
            return {"action": action, "type": opt, "strike": round(K, 2),
                    "price": round(_px(puts if opt == "put" else calls, K, opt), 2),
                    "delta": round(_dlt(K, opt), 3), "inst_sell_usd": round(_sell_at(opt.upper(), K), 0)}

        structs = []
        # ── Iron Condor (neutral, riesgo definido) ──
        if short_put and short_call and long_put and long_call and short_put > long_put and long_call > short_call:
            cr = (_px(puts, short_put, "put") - _px(puts, long_put, "put")
                  + _px(calls, short_call, "call") - _px(calls, long_call, "call"))
            width = max(short_put - long_put, long_call - short_call)
            ml = width - cr
            be_lo, be_hi = short_put - cr, short_call + cr
            pa, pb2 = _p_below(be_hi, T, iv), _p_below(be_lo, T, iv)
            pop = (pa - pb2) if (pa is not None and pb2 is not None) else None
            structs.append(_mk("Iron Condor", "neutral",
                               [_leg("BUY", "put", long_put), _leg("SELL", "put", short_put),
                                _leg("SELL", "call", short_call), _leg("BUY", "call", long_call)],
                               cr, ml, be_lo, be_hi, pop,
                               "Ganas si el precio se queda ENTRE los cortos al vencimiento. Neutral, riesgo definido por las alas."))
        # ── Put Credit Spread / Bull Put (alcista-neutral) ──
        if short_put and long_put and short_put > long_put:
            cr = _px(puts, short_put, "put") - _px(puts, long_put, "put")
            ml = (short_put - long_put) - cr
            be_lo = short_put - cr
            pop = _p_above(be_lo, T, iv)
            structs.append(_mk("Put Credit Spread", "alcista-neutral",
                               [_leg("BUY", "put", long_put), _leg("SELL", "put", short_put)],
                               cr, ml, be_lo, None, pop,
                               "Vendes soporte: ganas si el precio se mantiene ARRIBA del breakeven. Sesgo alcista-neutral."))
        # ── Call Credit Spread / Bear Call (bajista-neutral) ──
        if short_call and long_call and long_call > short_call:
            cr = _px(calls, short_call, "call") - _px(calls, long_call, "call")
            ml = (long_call - short_call) - cr
            be_hi = short_call + cr
            pop = _p_below(be_hi, T, iv)
            structs.append(_mk("Call Credit Spread", "bajista-neutral",
                               [_leg("SELL", "call", short_call), _leg("BUY", "call", long_call)],
                               cr, ml, None, be_hi, pop,
                               "Vendes resistencia: ganas si el precio se mantiene DEBAJO del breakeven. Sesgo bajista-neutral."))
        # ── Cash-Secured Put (alcista-neutral / quiero las acciones) ──
        if short_put:
            cr = _px(puts, short_put, "put")
            ml = short_put - cr                      # pérdida si el subyacente → 0 (menos el crédito)
            be_lo = short_put - cr
            pop = _p_above(be_lo, T, iv)
            structs.append(_mk("Cash-Secured Put", "alcista-neutral",
                               [_leg("SELL", "put", short_put)],
                               cr, ml, be_lo, None, pop,
                               "Cobras prima por comprometerte a comprar en el corto. Si baja, te asignan a un precio menor; si no, te quedas la prima.",
                               collateral=short_put * 100.0))

        structs = [s for s in structs if s["credit"] > 0]
        for s in structs:
            all_structs.append(s)
        ic = next((s for s in structs if s["kind"] == "Iron Condor"), None)
        per_exp.append({"exp": exp, "dte": int(dte), "iv_pct": round(iv * 100, 1),
                        "expected_move": round(em, 2), "em_low": round(spot - em, 2), "em_high": round(spot + em, 2),
                        "call_wall": call_wall, "put_wall": put_wall,
                        "strategies": sorted(structs, key=lambda s: (s["score"] or 0), reverse=True),
                        "_ic_score": (ic["score"] if ic else -1)})

    if not per_exp:
        return {"ok": False, "error": "No se pudieron construir estructuras (cadena/IV insuficiente en 7–30 DTE)."}
    per_exp.sort(key=lambda x: x["_ic_score"], reverse=True)
    primary = per_exp[0]
    ranked = sorted(all_structs, key=lambda s: (s["score"] or 0), reverse=True)[:8]
    big_sells = sorted(({"exp": e, "cp": c, "strike": k, "premium_usd": round(v, 0)}
                        for (e, c, k), v in sells.items()), key=lambda x: x["premium_usd"], reverse=True)[:8]
    return {"ok": True, "ticker": ticker.upper(), "spot": round(spot, 2),
            "dte_window": [int(dte_min), int(dte_max)], "capital": _safe_num(capital), "risk_pct": _safe_num(risk_pct),
            "primary": primary, "expiries": [{k: v for k, v in pe.items() if k != "_ic_score"} for pe in per_exp],
            "ranked": ranked, "big_sells": big_sells,
            "note": "Cortos ~1σ (delta≈0.16) fuera del movimiento esperado, alineados con OI walls y con las ventas "
                    "institucionales más grandes. POP es riesgo-neutral (desde IV); en la práctica suele salir algo mejor. "
                    "Crédito con mids del chain (fallback Black-Scholes). Riesgo definido salvo el Cash-Secured Put."}


    def _p_below(K, T, iv):                         # prob. riesgo-neutral de que S_T < K (lognormal con IV)
        if iv <= 0 or T <= 0 or K <= 0 or spot <= 0:
            return None
        d2 = (math.log(spot / K) + (RF - 0.5 * iv * iv) * T) / (iv * math.sqrt(T))
        return _norm_cdf(-d2)


        def _px(rowmap, K, opt):
            r = rowmap.get(round(K, 2))
            if r is None:
                return _bs_price(spot, K, T, iv, RF, opt)
            bid = _safe_num(r.get("bid")); ask = _safe_num(r.get("ask")); last = _safe_num(r.get("lastPrice"))
            if bid > 0 and ask > 0:
                return (bid + ask) / 2.0
            if last > 0:
                return last
            return _bs_price(spot, K, T, iv, RF, opt)


        def _by_delta(ks, opt, dtarget=0.16):       # strike cuyo |delta| ≈ objetivo (≈ borde 1σ)
            best, bd = None, 1e9
            for k in ks:
                d = abs(abs(_dlt(k, opt)) - dtarget)
                if d < bd:
                    bd, best = d, k
            return best


        def _mk(kind, direction, legs, credit, maxloss, be_lo, be_hi, pop, note, collateral=None):
            credit = round(max(0.0, credit), 2); maxloss = round(max(0.01, maxloss), 2)
            ror = credit / maxloss if maxloss > 0 else None
            score = round((pop * 100.0) * (ror or 0), 1) if pop is not None else None
            if collateral:
                contracts = int(_safe_num(capital) // collateral) if collateral > 0 else 0
            else:
                contracts = int(risk_budget // (maxloss * 100.0)) if maxloss > 0 else 0
            return {"kind": kind, "exp": exp, "dte": int(dte), "direction": direction, "iv_pct": round(iv * 100, 1),
                    "legs": legs, "credit": credit, "credit_usd": round(credit * 100, 0),
                    "max_profit_usd": round(credit * 100, 0), "max_loss": maxloss, "max_loss_usd": round(maxloss * 100, 0),
                    "breakeven_low": (round(be_lo, 2) if be_lo else None), "breakeven_high": (round(be_hi, 2) if be_hi else None),
                    "pop_pct": (round(pop * 100, 1) if pop is not None else None),
                    "ror_pct": (round(ror * 100, 1) if ror else None), "score": score,
                    "contracts": max(0, contracts), "collateral_usd": (round(collateral, 0) if collateral else None),
                    "note": note}


        def _leg(action, opt, K):
            return {"action": action, "type": opt, "strike": round(K, 2),
                    "price": round(_px(puts if opt == "put" else calls, K, opt), 2),
                    "delta": round(_dlt(K, opt), 3), "inst_sell_usd": round(_sell_at(opt.upper(), K), 0)}


@app.get("/api/income-strategies")
def income_strategies_endpoint(ticker: str, dte_min: int = 7, dte_max: int = 30,
                               capital: float = 500.0, risk_pct: float = 50.0):
    """Estructuras de venta de prima (Iron Condor, credit spreads, CSP) en la ventana 7–30 DTE,
    con crédito, POP, breakevens y retorno sobre riesgo. Motor: build_income_strategies."""
    ticker = (ticker or "").upper().strip()
    if not ticker:
        return {"ok": False, "error": "Ticker requerido."}
    try:
        return _json_safe(build_income_strategies(ticker, dte_min=dte_min, dte_max=dte_max,
                                                  capital=capital, risk_pct=risk_pct))
    except Exception as e:
        return {"ok": False, "error": _error_publico(e, "/api/income-strategies")}


@app.get("/api/net-flow")
def net_flow_endpoint(ticker: str, window: str = "today", expiration: str = ""):
    """Net call vs put premium over time (net-flow). window: today/7d/30d/90d.
    expiration (YYYY-MM-DD) restringe el flujo a los contratos de esa expiración (0DTE = la del día)."""
    ticker = ticker.upper().strip()
    if window not in ("today", "7d", "30d", "90d"):
        window = "today"
    if not _quantdata_ready():
        return {"ok": False, "error": _quantdata_reason()}
    try:
        exp = (expiration or "").strip()
        nf = quantdata_net_flow(ticker, window, expiration=(exp or None))
        if not nf:
            return {"ok": False, "error": "Sin datos de net-flow para esta ventana."}
        exps = []
        try:
            exps = (quantdata_exposure(ticker, "GAMMA") or {}).get("expirations") or []
        except Exception:
            exps = []
        prints = []
        try:
            _oic = quantdata_oi_change(ticker)          # ΔOI por contrato para confirmar apertura de los prints
            _oimap = (_oic or {}).get("map") if isinstance(_oic, dict) else None
            prints = quantdata_big_prints(ticker, window, expiration=(exp or None), oi_map=_oimap)
        except Exception:
            prints = []
        return _json_safe({"ok": True, "ticker": ticker, "expirations": exps, "prints": prints, **nf})
    except Exception as e:
        return {"ok": False, "error": _error_publico(e, "/api/net-flow")}


@app.get("/api/gex-strike")
def gex_strike_endpoint(ticker: str, exp: str = "", greek: str = "GAMMA"):
    """Quant Data exposure by strike for GAMMA / VANNA / CHARM / DELTA, optionally for one expiration.
    Returns by_strike, spot, call/put walls (gamma) and the list of available expirations."""
    ticker = ticker.upper().strip()
    greek = (greek or "GAMMA").upper()
    if greek not in ("GAMMA", "VANNA", "CHARM", "DELTA"):
        greek = "GAMMA"
    if not _quantdata_ready():
        return {"ok": False, "error": _quantdata_reason()}
    try:
        expf = exp if (exp and exp.lower() not in ("all", "todas", "")) else None
        ex = quantdata_exposure(ticker, greek, expiration=expf)
        if not ex or not ex.get("by_strike"):
            return {"ok": False, "error": f"Sin datos de exposición {greek}."}
        spot = ex.get("stock_price")
        if not spot:
            g = get_gex_cached(ticker)
            spot = g.get("spot") if isinstance(g, dict) else None
        # walls/gamma-flip solo tienen sentido en GAMMA
        walls = _qd_gex_walls(ex, _safe_num(spot)) if (spot and greek == "GAMMA") else None
        # #5 — max pain REAL (OI completo de la cadena) para el vencimiento seleccionado
        max_pain = None
        if greek == "GAMMA" and expf and spot:
            try:
                max_pain = _chain_metrics(yf.Ticker(ticker), expf, _safe_num(spot)).get("max_pain")
            except Exception:
                max_pain = None
        if walls is not None:
            walls["max_pain"] = max_pain
        exps = ex.get("expirations") or []
        if expf and not exps:
            base = quantdata_exposure(ticker, greek)
            exps = base.get("expirations") if base else []
        return _json_safe({"ok": True, "ticker": ticker, "greek": greek, "exp": expf or "all", "spot": spot,
                           "by_strike": ex["by_strike"], "walls": walls, "max_pain": max_pain,
                           "expirations": exps})
    except Exception as e:
        return {"ok": False, "error": _error_publico(e, "/api/gex-strike")}


def _collect_signal_snapshot(ticker):
    """Snapshot today's full signal set for a ticker into signal_snapshots (idempotente por día)."""
    ticker = ticker.upper().strip()
    gex = get_gex_cached(ticker)
    spot = _safe_num(gex.get("spot")) if isinstance(gex, dict) else 0.0
    if spot <= 0:
        return {"ok": False, "error": "Sin spot/GEX para capturar el snapshot."}
    qd = _quantdata_ready()
    fl = quantdata_flow(ticker) if qd else None
    oic = quantdata_oi_change(ticker) if qd else None
    oimap = oic.get("map") if isinstance(oic, dict) else None
    conv = _qd_conviction(fl, oi_change_map=oimap) if fl else None
    npm = quantdata_net_premium(ticker) if qd else None
    dp = quantdata_darkpool(ticker) if qd else None
    dpf = quantdata_dark_prints(ticker) if qd else None
    confl = _qd_confluence(conv, gex, dp, spot, dp_flow=dpf)
    targets = []
    try:
        wallsfn = (lambda e, s: _qd_exposure_walls(ticker, e, s)) if qd else None
        t = compute_horizon_targets(ticker, net_premium=npm, flow=fl, ai_12m=0.0,
                                    qd_walls_fn=wallsfn, conviction=conv, calibrate=False)
        for tg in ((t.get("targets") if isinstance(t, dict) else []) or []):
            lvl = _safe_num(tg.get("level")); hz = tg.get("horizon_days")
            if lvl > 0 and hz:
                targets.append({"hz": int(hz), "level": round(lvl, 2), "dir": tg.get("direction")})
    except Exception:
        pass
    row = {"ticker": ticker, "snap_date": datetime.now().strftime("%Y-%m-%d"), "spot": round(spot, 2),
           "confl_verdict": confl.get("verdict"), "confl_direction": confl.get("direction"),
           "confl_score": confl.get("score"),
           "conv_bias": (conv or {}).get("bias"), "conv_strength": (conv or {}).get("strength_pct"),
           "net_premium": (_safe_num(npm.get("net_premium")) if isinstance(npm, dict) else None),
           "dark_bias": (dpf or {}).get("bias"),
           "call_wall": _safe_num(gex.get("call_wall")) or None,
           "put_wall": _safe_num(gex.get("put_wall")) or None,
           "gamma_flip": _safe_num(gex.get("gamma_flip")) or None,
           "targets_json": json.dumps(targets), "created_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S")}
    try:
        _store_signal_snapshot(row)
    except Exception as e:
        return {"ok": False, "error": f"DB: {e}"}
    return {"ok": True, "snapshot": row}


def _backtest_signals(ticker):
    """Read stored snapshots + realized prices (yfinance) and evaluate. Honest: only snapshots
    whose horizon already elapsed count; starts empty and fills as you collect daily."""
    ticker = ticker.upper().strip()
    try:
        conn = _db()
        rows = conn.execute("SELECT * FROM signal_snapshots WHERE ticker=? ORDER BY snap_date", (ticker,)).fetchall()
        conn.close()
        snaps = [dict(r) for r in rows]
    except Exception as e:
        return {"ok": False, "error": f"DB: {e}"}
    if not snaps:
        return {"ok": True, "ticker": ticker, "n_snapshots": 0,
                "message": "Aún no hay snapshots para este ticker. Captura señales unos días y vuelve."}
    try:
        # Historial de precios desde FMP, no desde Yahoo: era el ÚLTIMO uso
        # de yfinance alcanzable desde `/api/analyze`. Yahoo se queda sólo
        # donde no hay alternativa —las cadenas de opciones de Proyecciones—
        # y fuera del camino que produce un score.
        hist = vertex_market.Ticker(ticker).history(period="1y")
        closes = {d.strftime("%Y-%m-%d"): float(c) for d, c in hist["Close"].items()}
        highs = {d.strftime("%Y-%m-%d"): float(c) for d, c in hist["High"].items()}
        lows = {d.strftime("%Y-%m-%d"): float(c) for d, c in hist["Low"].items()}
        dates = sorted(closes.keys())
    except Exception as e:
        return {"ok": False, "error": f"No pude bajar precios realizados: {e}"}
    ev = _backtest_eval(snaps, closes, highs, lows, dates)
    return {"ok": True, "ticker": ticker, "n_snapshots": len(snaps),
            "date_range": [snaps[0]["snap_date"], snaps[-1]["snap_date"]],
            "note": "Solo se evalúan snapshots cuyo horizonte ya transcurrió.", **ev}


@app.get("/api/collect-signals")
def collect_signals_endpoint(ticker: str):
    """Capture today's signal snapshot for forward backtesting."""
    return _json_safe(_collect_signal_snapshot(ticker))


@app.get("/api/backtest")
def backtest_endpoint(ticker: str):
    """Backtest stored snapshots: confluence direction accuracy + target hit-rate per horizon."""
    return _json_safe(_backtest_signals(ticker))


def _reconstruct_confluence_snapshot(ticker, date, spot):
    """Rebuild the confluence as it WOULD have looked on `date` using Quant Data history
    (sessionDate). Direction signals only — target levels need the live options chain, so the
    historical row stores no targets (target hit-rate accrues forward via the live collector)."""
    spot = _safe_num(spot)
    if spot <= 0:
        return None
    fl = quantdata_flow(ticker, session_date=date)
    oic = quantdata_oi_change(ticker, session_date=date)
    oimap = oic.get("map") if isinstance(oic, dict) else None
    conv = _qd_conviction(fl, oi_change_map=oimap) if fl else None
    ex = quantdata_exposure(ticker, "GAMMA", session_date=date)
    walls = _qd_gex_walls(ex, spot) if ex else None
    # Antes se tiraba el gamma_flip que _qd_gex_walls ya calcula y net_gex quedaba en None, dejando
    # el voto GEX en "neutral" salvo ruptura de wall. Ahora los reconstruimos desde la MISMA
    # exposición histórica (sessionDate), igual que en vivo → el histórico se vuelve un test justo.
    _flip_hist = (walls or {}).get("gamma_flip")
    _net_gex_hist = None
    try:
        _rows = (ex.get("by_strike") if isinstance(ex, dict) else None) or []
        if _rows:
            _net_gex_hist = round(sum(_safe_num(r.get("net")) for r in _rows if r.get("strike")), 0)
    except Exception:
        _net_gex_hist = None
    gexd = ({"ok": True, "spot": spot, "call_wall": (walls or {}).get("call_wall"),
             "put_wall": (walls or {}).get("put_wall"), "gamma_flip": _flip_hist, "net_gex": _net_gex_hist}
            if walls else None)
    dpf = quantdata_dark_prints(ticker, session_date=date)
    npm = quantdata_net_premium(ticker, session_date=date)
    confl = _qd_confluence(conv, gexd, None, spot, dp_flow=dpf)
    return {"ticker": ticker.upper(), "snap_date": str(date)[:10], "spot": round(spot, 2),
            "confl_verdict": confl.get("verdict"), "confl_direction": confl.get("direction"),
            "confl_score": confl.get("score"),
            "conv_bias": (conv or {}).get("bias"), "conv_strength": (conv or {}).get("strength_pct"),
            "net_premium": (_safe_num(npm.get("net_premium")) if isinstance(npm, dict) else None),
            "dark_bias": (dpf or {}).get("bias"),
            "call_wall": (walls or {}).get("call_wall"), "put_wall": (walls or {}).get("put_wall"),
            "gamma_flip": _flip_hist, "targets_json": "[]",
            "created_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S") + " (backfill)"}


def _run_backfill(ticker, sample_every=3, lookback_days=365, throttle=0.4):
    st = _BACKFILL_STATE[ticker]
    try:
        hist = yf.Ticker(ticker).history(period="1y")
        all_dates = [d.strftime("%Y-%m-%d") for d in hist.index]
        closes = {d.strftime("%Y-%m-%d"): float(c) for d, c in hist["Close"].items()}
        if not all_dates:
            st.update(running=False, error="Sin precios históricos."); return
        sampled = all_dates[::max(int(sample_every), 1)]
        # leave a forward tail so the 10-day direction window can be evaluated
        cutoff_idx = len(all_dates) - 11
        sampled = [d for d in sampled if all_dates.index(d) <= cutoff_idx]
        st["total"] = len(sampled)
        for d in sampled:
            if st.get("cancel"):
                break
            try:
                snap = _reconstruct_confluence_snapshot(ticker, d, closes.get(d))
                if snap:
                    _store_signal_snapshot(snap); st["stored"] += 1
            except Exception:
                st["errors"] = st.get("errors", 0) + 1
            st["done"] += 1
            time.sleep(throttle)
        st.update(running=False, finished=True)
    except Exception as e:
        st.update(running=False, error=str(e))


@app.post("/api/backfill/start")
def backfill_start_endpoint(ticker: str, sample_every: int = 3):
    """Kick off a background historical backfill of confluence snapshots (Quant Data sessionDate).

    POST, like every other route that starts work: a GET is defined as safe
    and browsers may reissue it on their own."""
    ticker = ticker.upper().strip()
    if not _quantdata_ready():
        return {"ok": False, "error": _quantdata_reason()}
    cur = _BACKFILL_STATE.get(ticker)
    if cur and cur.get("running"):
        return {"ok": True, "already_running": True, **{k: cur.get(k) for k in ("done", "total", "stored")}}
    _BACKFILL_STATE[ticker] = {"running": True, "done": 0, "total": 0, "stored": 0, "errors": 0,
                               "finished": False, "cancel": False, "started": datetime.now().strftime("%H:%M:%S")}
    threading.Thread(target=_run_backfill, args=(ticker, max(int(sample_every), 1)), daemon=True).start()
    return {"ok": True, "started": True, "ticker": ticker,
            "note": "Reconstruyendo histórico en segundo plano. Consulta el progreso."}


@app.get("/api/backfill/status")
def backfill_status_endpoint(ticker: str):
    ticker = ticker.upper().strip()
    st = _BACKFILL_STATE.get(ticker)
    if not st:
        return {"ok": True, "running": False, "total": 0, "done": 0, "stored": 0}
    return _json_safe({"ok": True, **st})


def _horizon_targets_prompt_block(ticker, conviction=None, net_premium=None):
    """#4 — feed the SAME gamma/flow target engine that Proyecciones uses into the agent prompt,
    so its narrative and Proyecciones agree (magnet, conflicts, earnings, calibrated confidence)."""
    try:
        wallsfn = (lambda e, s: _qd_exposure_walls(ticker, e, s)) if _quantdata_ready() else None
        t = compute_horizon_targets(ticker, net_premium=net_premium, flow=None, ai_12m=0.0,
                                    qd_walls_fn=wallsfn, conviction=conviction, calibrate=True)
    except Exception:
        return ""
    tgs = (t.get("targets") if isinstance(t, dict) else None) or []
    rows = [x for x in tgs if x.get("horizon_days", 0) < 365 and x.get("level")]
    if not rows:
        return ""
    parts = []
    for x in rows:
        arrow = "↑" if x["direction"] == "up" else "↓"
        tag = []
        if x.get("conflict"):
            tag.append("CONFLICTO flujo-vs-gamma")
        if x.get("earnings_soon"):
            tag.append("earnings en el rango")
        if x.get("capped"):
            tag.append("acotado al mov. esperado")
        extra = (" [" + "; ".join(tag) + "]") if tag else ""
        parts.append(f"{x['label']} {arrow}${x['level']} (conf {x['confidence']}, base {x.get('basis')}{extra})")
    return ("\nTARGETS DE GAMMA/FLUJO (MISMO motor que Proyecciones — niveles-imán de dealers + flujo "
            f"institucional, corto plazo; spot ${t.get('spot')}): " + " · ".join(parts) +
            ". USO: son niveles-imán (gamma) y de flujo, NO el target fundamental. Concílialos con tus "
            "targets de σ/DCF: si coinciden, refuerza la convicción; si un horizonte marca CONFLICTO "
            "flujo-vs-gamma o earnings en el rango, explícalo en tesis_riesgos. No los cambies de número.")


def _ledger_current_oi(ticker, trades, max_expiries=15):
    """Enriquece los trades mostrados con el OI ACTUAL del contrato y su prima vigente. Baja la cadena
    yfinance una vez por cada vencimiento único NO vencido. Agrega oi_now, price_now y
    premium_oi_now = oi_now × price_now × 100. Deja None si no hay (p.ej. contrato ya vencido)."""
    today = datetime.now().date()
    exps = []
    for t in trades:
        e = t.get("exp")
        if not e or e in exps:
            continue
        try:
            if datetime.strptime(e, "%Y-%m-%d").date() < today:
                continue   # contrato vencido → no hay OI actual
        except Exception:
            continue
        exps.append(e)
    exps = exps[:max_expiries]
    if not exps:
        return
    tk = yf.Ticker(ticker)
    cmap = {}
    for e in exps:
        try:
            ch = tk.option_chain(e)
        except Exception:
            continue
        for df, cp in ((getattr(ch, "calls", None), "CALL"), (getattr(ch, "puts", None), "PUT")):
            if df is None or df.empty:
                continue
            for _, r in df.iterrows():
                K = _safe_num(r.get("strike"))
                if K <= 0:
                    continue
                bid = _safe_num(r.get("bid")); ask = _safe_num(r.get("ask")); last = _safe_num(r.get("lastPrice"))
                price = round((bid + ask) / 2, 2) if (bid > 0 and ask > 0) else (last if last > 0 else None)
                cmap[(e, cp, round(K, 2))] = {"oi": int(_safe_num(r.get("openInterest"))), "price": price}
    for t in trades:
        info = cmap.get((t.get("exp"), t.get("cp"), round(_safe_num(t.get("strike")), 2)))
        if info:
            t["oi_now"] = info["oi"] or None
            t["price_now"] = info["price"]
            t["premium_oi_now"] = (round(info["oi"] * info["price"] * 100, 0)
                                   if (info["oi"] and info["price"]) else None)
        else:
            t["oi_now"] = t["price_now"] = t["premium_oi_now"] = None


@app.get("/api/options-ledger")
def options_ledger_endpoint(ticker: str, limit: int = 60, days: int = 120,
                            min_premium: float = 5_000_000, with_oi: bool = True,
                            only_active: bool = True):
    """Libro de transacciones institucionales de los ÚLTIMOS `days` días (default 120), solo trades de
    `min_premium`+ (default $5M) y solo lado direccional (ASK/ABOVE_ASK = compra · BID/BELOW_BID = venta).
    Con only_active=True (default) OCULTA contratos cuya fecha de vencimiento ya pasó: solo muestra flujo de
    contratos vigentes. Por trade: contrato, DTE (entero), nº de contratos, precio (optionPrice), total
    invertido. Más un resumen comprado-vs-vendido. Todo filtrado server-side por Quant Data (timeRange+premiumRange+sides)."""
    ticker = ticker.upper().strip()
    if not _quantdata_ready():
        return {"ok": False, "error": _quantdata_reason()}
    _today = datetime.utcnow().strftime("%Y-%m-%d")
    rows = quantdata_flow_window(ticker, days=int(days), min_premium=float(min_premium))
    if not rows:
        # fallback: sesión más reciente, filtrada del lado del cliente
        base = quantdata_flow(ticker, limit=100) or []
        rows = [r for r in base
                if _safe_num(r.get("premium")) >= float(min_premium)
                and (r.get("side") or "").upper() in ("ASK", "ABOVE_ASK", "BID", "BELOW_BID")]
        for r in rows:
            r["tradeTime"] = None
            r["price"] = (round(_safe_num(r.get("premium")) / (int(_safe_num(r.get("size"))) * 100), 2)
                          if int(_safe_num(r.get("size"))) > 0 else None)
    if not rows:
        return {"ok": True, "ticker": ticker, "trades": [], "summary": None,
                "note": f"Sin trades de ${float(min_premium):,.0f}+ direccionales en {int(days)} días."}

    def lean(side):
        s = (side or "").upper()
        if s in ("ASK", "ABOVE_ASK"):
            return "compra"
        if s in ("BID", "BELOW_BID"):
            return "venta"
        return "neutral"

    trades, summ = [], {"call_buy": 0.0, "call_sell": 0.0, "put_buy": 0.0, "put_sell": 0.0}
    n_expired = 0
    for r in rows:
        _exp = (r.get("exp") or "")[:10]
        if only_active and _exp and _exp < _today:      # contrato ya vencido → no mostrar su flujo
            n_expired += 1
            continue
        prem = _safe_num(r.get("premium"))
        size = int(_safe_num(r.get("size")))
        price = r.get("price")
        if price is None and size > 0 and prem:
            price = round(prem / (size * 100), 2)
        bs = lean(r.get("side"))
        cp = (r.get("cp") or "").upper()
        if prem:
            if cp == "CALL" and bs == "compra":
                summ["call_buy"] += prem
            elif cp == "CALL" and bs == "venta":
                summ["call_sell"] += prem
            elif cp == "PUT" and bs == "compra":
                summ["put_buy"] += prem
            elif cp == "PUT" and bs == "venta":
                summ["put_sell"] += prem
        # fecha legible del trade (la ventana abarca 120 días)
        tt = r.get("tradeTime")
        when = None
        if tt:
            try:
                when = datetime.fromtimestamp(int(tt) / 1000).strftime("%m-%d %H:%M")
            except Exception:
                when = None
        oi = int(_safe_num(r.get("oi")))
        premium_oi = round(oi * price * 100, 0) if (oi > 0 and price) else None   # prima total comprometida en el OI actual
        trades.append({"when": when, "_ts": (int(tt) if tt else 0),
                       "cp": cp, "strike": r.get("strike"), "exp": r.get("exp"),
                       "dte": (int(round(_safe_num(r.get("dte")))) if r.get("dte") is not None else None),
                       "size": size, "price": price, "side": r.get("side"), "buy_sell": bs,
                       "premium": round(prem, 0) if prem else None,
                       "oi": oi or None, "premium_oi": premium_oi, "kind": r.get("kind"),
                       "opening": r.get("opening"), "unusual": r.get("unusual"),
                       "golden": r.get("golden"), "delta": r.get("delta")})
    trades.sort(key=lambda t: (t.get("_ts") or 0), reverse=True)   # más reciente → más viejo
    bull = summ["call_buy"] + summ["put_sell"]
    bear = summ["put_buy"] + summ["call_sell"]
    bias = "alcista" if bull > bear else ("bajista" if bear > bull else "neutral")
    summ = {k: round(v, 0) for k, v in summ.items()}
    summ.update({"net_call": round(summ["call_buy"] - summ["call_sell"], 0),
                 "net_put": round(summ["put_buy"] - summ["put_sell"], 0),
                 "bull_notional": round(bull, 0), "bear_notional": round(bear, 0), "bias": bias})
    shown = trades[:int(limit)]
    if with_oi:
        try:
            _ledger_current_oi(ticker, shown)
        except Exception:
            pass
    return _json_safe({"ok": True, "ticker": ticker, "n": len(trades), "days": int(days),
                       "min_premium": float(min_premium), "only_active": only_active,
                       "n_expired_hidden": n_expired, "trades": shown, "summary": summ})


    def lean(side):
        s = (side or "").upper()
        if s in ("ASK", "ABOVE_ASK"):
            return "compra"
        if s in ("BID", "BELOW_BID"):
            return "venta"
        return "neutral"


@app.get("/api/options-gex")
def options_gex(ticker: str, refresh: bool = False):
    """GEX + key levels for one ticker. When a Quant Data API key is configured, augments
    tape_flow / pro-exposure y el dark pool COMPLETO (todos los niveles) de HOY y de los últimos
    30 días. refresh=1 salta el cache de GEX para recalcular en vivo (botón Refrescar / auto-refresh)."""
    g = get_gex_cached(ticker, force=bool(refresh))
    if not g:
        return {"ok": False, "error": "Cadena de opciones no disponible para este ticker."}
    try:
        g = dict(g)
        g["integrity"] = _integrity_checks(g)
    except Exception as _e:
        print(f"[integrity] skip: {_e}")
    if _quantdata_ready():
        try:
            g = dict(g)
            g["tape_flow"] = quantdata_net_premium(ticker)
            g["dark_pool"] = quantdata_darkpool(ticker, limit=500, lookback_days=30)   # todos, 30 días
            g["dark_pool_today"] = quantdata_darkpool(ticker, limit=500, lookback_days=0)  # todos, hoy
            g["exposure_pro"] = quantdata_exposure(ticker)
        except Exception as e:
            print(f"[QuantData] augment skip: {e}")
    return _json_safe(g)


def _quantdata_ready():
    return bool(QUANTDATA_API_KEY)


def _quantdata_reason():
    if not QUANTDATA_API_KEY:
        return "QUANTDATA_API_KEY no configurada. Pega tu API key de Quant Data para activar flow + dark pool."
    return None


#: Rutas de Quant Data que la cuenta NO tiene derecho a usar (401/402/403),
#: recordadas para no volver a pedirlas. Un rechazo de ENTITLEMENT no cambia
#: dentro de la misma corrida: reintentarlo sólo gasta tiempo de pared.
#: Medido en `/api/analyze`: 25 peticiones, todas 403, ~8 s tirados.
_QD_SIN_DERECHO: dict[str, int] = {}
_QD_ENTITLEMENT_STATUSES = frozenset({401, 402, 403})


def _quantdata_request(path, payload=None, method="POST", timeout=12):
    """Bearer-auth call to the Quant Data API. Returns parsed JSON, or a dict with
    '_error' on failure. Never raises — keeps the agent resilient if the feed is down."""
    if not QUANTDATA_API_KEY:
        return None
    ya = _QD_SIN_DERECHO.get(path)
    if ya:
        # Mismo cuerpo de error que habría devuelto la llamada, sin hacerla.
        return {"_error": f"HTTP {ya}", "_body": "endpoint fuera del plan (no se reintenta)"}
    url = QUANTDATA_BASE.rstrip("/") + path
    headers = {"Authorization": f"Bearer {QUANTDATA_API_KEY}", "Content-Type": "application/json"}
    try:
        if method.upper() == "POST":
            r = requests.post(url, json=(payload or {}), headers=headers, timeout=timeout)
        else:
            r = requests.get(url, params=(payload or {}), headers=headers, timeout=timeout)
        if r.status_code != 200:
            if r.status_code in _QD_ENTITLEMENT_STATUSES:
                _QD_SIN_DERECHO[path] = r.status_code
            return {"_error": f"HTTP {r.status_code}", "_body": (r.text or "")[:300]}
        return r.json()
    except Exception as e:
        return {"_error": str(e)}


def quantdata_net_premium(ticker, session_date=None):
    """Net call/put premium (endpoint /options/tool/net-drift).
    Response: {"data": {ts: {netCallPremium, netPutPremium, stockPrice}}}.
    session_date (YYYY-MM-DD) pulls a historical session for backtesting."""
    _p = {"filter": {"ticker": ticker.upper()}}
    if session_date:
        _p["sessionDate"] = str(session_date)[:10]
    d = _quantdata_request(QUANTDATA_ENDPOINTS["net_premium"], _p)
    if not d or not isinstance(d, dict) or "_error" in d:
        return None
    data = d.get("data") or {}
    if not isinstance(data, dict) or not data:
        return None
    try:
        last_ts = max(data.keys(), key=lambda k: int(k))
        row = data[last_ts] or {}
        ncp = _safe_num(row.get("netCallPremium")); npp = _safe_num(row.get("netPutPremium"))
        # Convención Quant Data: netPutPremium > 0 = COMPRA de puts (bajista); < 0 = venta (alcista).
        # Bullishness = call premium MENOS put premium (no sumar: el signo de puts ya codifica bajista).
        net = ncp - npp
        return {"net_call_premium": round(ncp, 0), "net_put_premium": round(npp, 0),
                "net_premium": round(net, 0), "stock_price": _safe_num(row.get("stockPrice")) or None,
                "bias": "alcista" if net > 0 else ("bajista" if net < 0 else "neutral")}
    except Exception:
        return None


def quantdata_darkpool(ticker, limit=15, lookback_days=30):
    """Off-exchange print activity aggregated BY PRICE LEVEL via /equities/tool/dark-pool-levels.
    Request uses sessionDateRange (startDate required; endDate defaults to tomorrow NY).
    Default lookback = 30 days (accumulation/distribution zones over the last month).
    Response: {"latestStockPrice":x, "data": {"215.00": {notionalValue, size, tradeCount}}}."""
    start = (datetime.now() - timedelta(days=lookback_days)).strftime("%Y-%m-%d")
    d = _quantdata_request(QUANTDATA_ENDPOINTS["darkpool"],
                           {"sessionDateRange": {"startDate": start}, "filter": {"ticker": ticker.upper()}})
    if not d or not isinstance(d, dict) or "_error" in d:
        return None
    data = d.get("data") or {}
    if not isinstance(data, dict) or not data:
        return None
    out = []
    for lvl, cell in data.items():
        if not isinstance(cell, dict):
            continue
        try:
            price = float(lvl)
        except Exception:
            price = _safe_num(cell.get("price"))
        out.append({
            "price": price or None,
            "value": _safe_num(cell.get("notionalValue")) or None,
            "size": int(_safe_num(cell.get("size"))),
            "trade_count": int(_safe_num(cell.get("tradeCount"))),
        })
    out = [x for x in out if x["price"] and x["value"]]
    out.sort(key=lambda x: x["value"], reverse=True)
    return out[:limit] or None


def quantdata_exposure(ticker, greek="GAMMA", representation="PER_ONE_PERCENT_MOVE", expiration=None, session_date=None):
    """Per-strike dealer exposure via /options/tool/exposure-by-strike. greekMode +
    representationMode are REQUIRED by the API (GAMMA/DELTA/VANNA/CHARM). Aggregates the
    exposureMap (expiration -> strike -> {callExposure,putExposure}) into net per strike.
    If `expiration` (YYYY-MM-DD) is given, restricts exposure to that expiry.
    session_date (YYYY-MM-DD) pulls a historical session for backtesting."""
    _filter = {"ticker": ticker.upper()}
    if expiration:
        _filter["expirationDate"] = str(expiration)[:10]
    _payload = {"greekMode": greek, "representationMode": representation, "filter": _filter}
    if session_date:
        _payload["sessionDate"] = str(session_date)[:10]
    d = _quantdata_request(QUANTDATA_ENDPOINTS["exposure"], _payload)
    if not d or not isinstance(d, dict) or "_error" in d:
        return None
    data = d.get("data") or {}
    if not isinstance(data, dict) or not data:
        return None
    tk = data.get(ticker.upper()) or next(iter(data.values()), {})
    if not isinstance(tk, dict):
        return None
    emap = tk.get("exposureMap") or {}
    by_strike = {}
    for _exp, strikes in (emap.items() if isinstance(emap, dict) else []):
        if not isinstance(strikes, dict):
            continue
        for k, cell in strikes.items():
            if not isinstance(cell, dict):
                continue
            K = _safe_num(k)
            if K <= 0:
                continue
            ce = _safe_num(cell.get("callExposure")); pe = _safe_num(cell.get("putExposure"))
            agg = by_strike.setdefault(K, {"call": 0.0, "put": 0.0})
            agg["call"] += ce; agg["put"] += pe
    rows = [{"strike": K, "call": round(v["call"], 0), "put": round(v["put"], 0),
             "net": round(v["call"] + v["put"], 0)} for K, v in sorted(by_strike.items())]
    if not rows:
        return None
    exps_avail = sorted({str(e)[:10] for e in emap.keys()}) if isinstance(emap, dict) else []
    return {"greek": greek, "representation": representation,
            "stock_price": _safe_num(tk.get("stockPrice")) or None,
            "by_strike": rows, "expirations": exps_avail}


def _extract_max_pain(d, ticker, expiration=None):
    """Extrae el strike de Max Pain de la respuesta QD sin asumir un único formato (defensivo)."""
    if not d or not isinstance(d, dict) or "_error" in d:
        return None

    def _mp_from(obj):
        if not isinstance(obj, dict):
            return None
        for k in ("maxPain", "max_pain", "maxPainStrike", "maxPainPrice", "maxpain"):
            if obj.get(k) is not None:
                v = _safe_num(obj.get(k))
                return v if v > 0 else None
        return None

    data = d.get("data", d)
    v = _mp_from(data)                                   # caso 1: maxPain directo
    if v:
        return v
    if isinstance(data, dict):
        v = _mp_from(data.get(ticker.upper()))           # caso 2: keyed por ticker
        if v:
            return v
        try:                                             # caso 3: keyed por timestamp → último bucket
            num_keys = [k for k in data.keys() if str(k).isdigit()]
            if num_keys:
                last = data[max(num_keys, key=lambda k: int(k))]
                v = _mp_from(last) if isinstance(last, dict) else (_safe_num(last) or None)
                if v and v > 0:
                    return v
        except Exception:
            pass
        exp_keys = [k for k in data.keys() if isinstance(k, str) and re.match(r"\d{4}-\d{2}-\d{2}", k)]
        if exp_keys:                                     # caso 4: keyed por expiración
            pick = str(expiration)[:10] if (expiration and str(expiration)[:10] in exp_keys) else sorted(exp_keys)[0]
            cell = data.get(pick)
            v = _mp_from(cell) if isinstance(cell, dict) else (_safe_num(cell) or None)
            if v and v > 0:
                return v
        first = next(iter(data.values()), None)          # caso 5: primer valor
        v = _mp_from(first) if isinstance(first, dict) else (_safe_num(first) or None)
        if v and v > 0:
            return v
    return None


    def _mp_from(obj):
        if not isinstance(obj, dict):
            return None
        for k in ("maxPain", "max_pain", "maxPainStrike", "maxPainPrice", "maxpain"):
            if obj.get(k) is not None:
                v = _safe_num(obj.get(k))
                return v if v > 0 else None
        return None


_QD_MAXPAIN_CACHE = {}


def quantdata_max_pain(ticker, expiration=None, session_date=None, ttl=300):
    """Max Pain NATIVO de Quant Data (/options/tool/max-pain), computado server-side sobre el OI
    completo de 18 exchanges. Devuelve el strike (float) o None. Si el endpoint/schema difieren,
    degrada a None y el caller cae a yfinance (sin romper nada)."""
    key = f"{ticker.upper()}|{expiration or ''}|{session_date or ''}"
    nowt = time.time()
    ent = _QD_MAXPAIN_CACHE.get(key)
    if ent and nowt - ent[0] < ttl:
        return ent[1]
    val = None
    try:
        _filter = {"ticker": ticker.upper()}
        if expiration:
            _filter["expirationDate"] = str(expiration)[:10]
        payload = {"filter": _filter}
        if session_date:
            payload["sessionDate"] = str(session_date)[:10]
        val = _extract_max_pain(_quantdata_request(QUANTDATA_ENDPOINTS["max_pain"], payload), ticker, expiration)
    except Exception:
        val = None
    _QD_MAXPAIN_CACHE[key] = (nowt, val)
    return val


def _max_pain_best(ticker, expiration=None, call_oi=None, put_oi=None):
    """Prefiere el Max Pain NATIVO de Quant Data (OI completo, server-side); si no hay, cae al cálculo
    sobre el OI de la cadena yfinance. Devuelve (valor|None, fuente)."""
    if _quantdata_ready():
        qd = quantdata_max_pain(ticker, expiration)
        if qd:
            return qd, "quantdata"
    if call_oi and put_oi:
        mp = _max_pain(call_oi, put_oi)
        if mp:
            return mp, "yfinance"
    return None, None


def _max_pain_per_expiry_best(ticker, expiration, call_oi, put_oi):
    """#7 — Max Pain POR VENCIMIENTO. Prefiere el nativo de Quant Data (OI completo de 18 exchanges,
    server-side) PERO solo si QD realmente honra el filtro `expirationDate`. Lo AUTODETECTA: si el
    max-pain QD del vencimiento concreto difiere del agregado QD (None), entonces QD está respetando
    el filtro → se usa. Si es idéntico (lo ignora) o nulo, cae al cálculo por-vencimiento de yfinance,
    que SÍ varía por expiración. Así nunca degradamos: o mejora con QD real, o queda igual que antes.
    Kill-switch: env QD_MAXPAIN_PER_EXPIRY=0. Devuelve (valor|None, fuente)."""
    yf_mp = _max_pain(call_oi, put_oi) if (call_oi and put_oi) else None
    try:
        if os.environ.get("QD_MAXPAIN_PER_EXPIRY", "1") != "0" and expiration and _quantdata_ready():
            qd_exp = quantdata_max_pain(ticker, expiration)
            if qd_exp:
                qd_agg = quantdata_max_pain(ticker, None)          # cacheado; 1 sola vez por ticker
                if qd_agg is None or abs(float(qd_exp) - float(qd_agg)) > 1e-6:
                    return qd_exp, "quantdata"                     # QD honró expirationDate
    except Exception:
        pass
    return (yf_mp, "yfinance") if yf_mp else (None, None)


def quantdata_flow(ticker, limit=20, session_date=None):
    """Per-trade options flow via /options/tool/order-flow/consolidated (blocks, sweeps, splits).
    Field names match the real schema; delta comes from each row's nested `greeks`.
    session_date (YYYY-MM-DD) pulls a historical session for backtesting."""
    _p = {"filter": {"ticker": ticker.upper()}, "size": max(limit, 50)}
    if session_date:
        _p["sessionDate"] = str(session_date)[:10]
    d = _quantdata_request(QUANTDATA_ENDPOINTS["flow"], _p)
    if not d or not isinstance(d, dict) or "_error" in d:
        return None
    rows = d.get("data") or []
    if isinstance(rows, dict):
        rows = list(rows.values())
    out = []
    for r in (rows or []):
        if not isinstance(r, dict):
            continue
        g = r.get("greeks") or {}
        dlt = g.get("delta")
        vol = _safe_num(r.get("volume"))
        oi_raw = r.get("openInterest", r.get("open_interest", r.get("oi")))
        oi = _safe_num(oi_raw) if oi_raw is not None else None
        unusual = bool(r.get("isUnusual"))
        # "Adds to open interest" = an OPENING trade. Direct proof: the day's volume exceeds
        # the prior open interest (you can't close more contracts than exist → must be opening).
        # Falls back to isUnusual (Quant Data's vol>OI flag) when OI isn't in the payload.
        if oi is not None and vol > 0:
            opening = vol > oi
        else:
            opening = unusual
        out.append({
            "time": r.get("tradeTime"),
            "kind": r.get("tradeConsolidationType") or r.get("tradeType"),   # SWEEP / BLOCK / SPLIT
            "side": r.get("tradeSideCode"),                                   # ABOVE_ASK / BELOW_BID ...
            "cp": r.get("contractType"),                                      # CALL / PUT
            "exp": r.get("expirationDate"),
            "dte": _safe_num(r.get("dte")) or None,
            "strike": _safe_num(r.get("strikePrice")) or None,
            "size": int(_safe_num(r.get("size") or r.get("volume"))),
            "volume": int(vol),
            "open_interest": (int(oi) if oi is not None else None),
            "vol_oi": (round(vol / oi, 2) if (oi and oi > 0) else None),
            "opening": opening,                                               # adds to OI
            "premium": _safe_num(r.get("premium")) or None,
            "delta": (_safe_num(dlt) if dlt is not None else None),
            "spot": _safe_num(r.get("stockPrice")) or None,
            "unusual": unusual,
            "golden": bool(r.get("isGoldenSweep")),
        })
        if len(out) >= limit:
            break
    return out or None


def quantdata_flow_window(ticker, days=120, min_premium=1_000_000, max_rows=300):
    """Order-flow over a multi-day window, FILTERED SERVER-SIDE (one paginated query, no per-day loop):
    timeRange (last `days`), premiumRange ≥ min_premium, and tradeSideCodes restricted to the four
    directional sides (ASK/ABOVE_ASK/BID/BELOW_BID — excludes MID). Uses optionPrice + isOpeningPosition
    straight from the schema. Sorted by tradeTime DESC (most recent first); cursor-paginated up to max_rows."""
    if not _quantdata_ready():
        return []
    end = datetime.utcnow()
    start = end - timedelta(days=int(days))
    iso = lambda d: d.strftime("%Y-%m-%dT%H:%M:%SZ")
    base = {"filter": {"ticker": ticker.upper(),
                       "premiumRange": {"min": float(min_premium)},
                       "tradeSideCodes": ["ASK", "ABOVE_ASK", "BID", "BELOW_BID"]},
            "timeRange": {"startTime": iso(start), "endTime": iso(end)},
            "size": 100, "sort": {"field": "tradeTime", "direction": "DESCENDING"}}
    out, after = [], None
    for _ in range(max(1, max_rows // 100 + 1)):
        body = dict(base)
        if after:
            body["searchAfter"] = after
        d = _quantdata_request(QUANTDATA_ENDPOINTS["flow"], body)
        if not d or not isinstance(d, dict) or "_error" in d:
            break
        rows = d.get("data") or []
        for r in rows:
            g = r.get("greeks") or {}
            dlt = g.get("delta") if isinstance(g, dict) else None
            out.append({
                "tradeTime": r.get("tradeTime"),
                "kind": r.get("tradeConsolidationType") or r.get("tradeType"),
                "side": r.get("tradeSideCode"),
                "cp": r.get("contractType"),
                "exp": r.get("expirationDate"),
                "dte": _safe_num(r.get("dte")) or None,
                "strike": _safe_num(r.get("strikePrice")) or None,
                "size": int(_safe_num(r.get("size") or r.get("volume"))),
                "oi": int(_safe_num(r.get("openInterest"))),
                "price": _safe_num(r.get("optionPrice")) or None,
                "premium": _safe_num(r.get("premium")) or None,
                "opening": (bool(r.get("isOpeningPosition")) if r.get("isOpeningPosition") is not None
                            else bool(r.get("isVolumeGreaterThanOpenInterest"))),
                "unusual": bool(r.get("isUnusual")),
                "golden": bool(r.get("isGoldenSweep")),
                "delta": (_safe_num(dlt) if dlt is not None else None),
                "spot": _safe_num(r.get("stockPrice")) or None,
            })
        after = d.get("nextSearchAfter")
        if not after or len(out) >= max_rows:
            break
    return out[:max_rows]


def quantdata_oi_change(ticker, limit=100, session_date=None):
    """Per-contract daily open-interest delta via /options/tool/open-interest-change.
    THIS is the real 'added to OI' signal Kevin wants: it compares the open interest the day
    AFTER the trades (currentOpenInterest) vs the day OF the trades (previousOpenInterest) and
    returns the signed changeInOpenInterest. Solves multi-day accumulation: even with 70k already
    open, adding 30k more shows changeInOpenInterest = +30k (vol>OI would have missed it).
    Returns {'map': {key->{change,current,previous,pct}}, 'builds': [top OI builds]} or None.
    key = 'CALL|220.0|2026-05-16' (contractType|strike|expiration)."""
    payload = {
        "filter": {"tickers": [ticker.upper()],
                   "changeInOpenInterestRange": {"min": 1, "max": None}},  # positive deltas = additions
        "size": min(max(int(limit), 1), 100),
        "sort": {"field": "changeInOpenInterest", "direction": "DESCENDING"},
    }
    if session_date:
        payload["sessionDate"] = str(session_date)[:10]
    d = _quantdata_request(QUANTDATA_ENDPOINTS["oi_change"], payload)
    if not d or not isinstance(d, dict) or "_error" in d:
        return None
    rows = d.get("data") or []
    if isinstance(rows, dict):
        rows = list(rows.values())
    omap, builds = {}, []
    for r in (rows or []):
        if not isinstance(r, dict):
            continue
        cp = str(r.get("contractType") or "").upper()
        K = _safe_num(r.get("strikePrice"))
        exp = str(r.get("expirationDate") or "")[:10]
        chg = int(_safe_num(r.get("changeInOpenInterest")))
        cur = int(_safe_num(r.get("currentOpenInterest")))
        prev = int(_safe_num(r.get("previousOpenInterest")))
        pct = _safe_num(r.get("percentChangeInOpenInterest"))
        if not cp or not exp:
            continue
        key = f"{cp}|{round(K, 2)}|{exp}"
        omap[key] = {"change": chg, "current": cur, "previous": prev, "pct": pct}
        builds.append({"cp": cp, "strike": K, "exp": exp, "change": chg,
                       "current": cur, "pct": round(pct * 100, 1) if pct else None})
    if not omap:
        return None
    builds.sort(key=lambda b: b["change"], reverse=True)
    return {"map": omap, "builds": builds[:15]}


def quantdata_dark_prints(ticker, limit=100, session_date=None):
    """Dark-pool BUY/SELL proxy via /equities/tool/equity-prints (printType DARK_POOL).
    tradeSide ASK/ABOVE_ASK = comprador (levanta la oferta, alcista); BID/BELOW_BID = vendedor
    (golpea el bid, bajista); MID_MARKET = neutral. Agrega notional por lado → el dark pool deja de
    ser solo posicional y pasa a ser DIRECCIONAL. Honesto: muchos prints dark imprimen al mid.
    session_date (YYYY-MM-DD) pulls a historical session for backtesting."""
    payload = {"filter": {"ticker": ticker.upper(), "equityPrintTypes": ["DARK_POOL"]},
               "size": min(max(int(limit), 1), 100),
               "sort": {"field": "notionalValue", "direction": "DESCENDING"}}
    if session_date:
        payload["sessionDate"] = str(session_date)[:10]
    d = _quantdata_request(QUANTDATA_ENDPOINTS["equity_prints"], payload)
    if not d or not isinstance(d, dict) or "_error" in d:
        return None
    rows = d.get("data") or []
    if isinstance(rows, dict):
        rows = list(rows.values())
    buy = sell = mid = 0.0
    prints = []
    for r in (rows or []):
        if not isinstance(r, dict):
            continue
        notl = _safe_num(r.get("notionalValue"))
        if notl <= 0:
            continue
        side = str(r.get("tradeSide") or "").upper()
        if side in ("ASK", "ABOVE_ASK"):
            buy += notl; lab = "compra"
        elif side in ("BID", "BELOW_BID"):
            sell += notl; lab = "venta"
        else:
            mid += notl; lab = "mid"
        if len(prints) < 12:
            prints.append({"price": _safe_num(r.get("price")) or None,
                           "size": int(_safe_num(r.get("size"))), "notional": round(notl, 0),
                           "side": side, "lab": lab, "time": r.get("tradeTime")})
    tot = buy + sell + mid
    if tot <= 0:
        return None
    directional = buy + sell
    lean = round((buy - sell) / directional * 100, 0) if directional > 0 else None
    bias = "alcista" if buy > sell * 1.15 else ("bajista" if sell > buy * 1.15 else "neutral")
    return {"buy_notional": round(buy, 0), "sell_notional": round(sell, 0), "mid_notional": round(mid, 0),
            "total_notional": round(tot, 0), "bias": bias, "lean_pct": lean, "prints": prints}


def quantdata_net_flow(ticker, window="today", expiration=None):
    """Net call vs put premium OVER TIME via /options/tool/net-flow (dataMode NET_PREMIUM, en centavos).
    window: 'today' (última sesión, buckets intradía) o '7d'/'30d'/'90d' (timeRange multi-día).
    expiration: 'YYYY-MM-DD' restringe el flujo a los contratos de esa expiración (0DTE = la del día).
    Devuelve serie + neto acumulado + tendencia (acelerando/desvaneciéndose/revirtiendo)."""
    _filter = {"ticker": ticker.upper()}
    if expiration and str(expiration).strip().lower() not in ("", "all", "todas"):
        _filter["expirationDate"] = str(expiration)[:10]
    payload = {"dataMode": "NET_PREMIUM", "filter": _filter}
    wd = {"7d": 7, "30d": 30, "90d": 90}.get(window)
    if wd:
        end = datetime.utcnow()
        start = end - timedelta(days=wd)
        payload["timeRange"] = {"startTime": start.strftime("%Y-%m-%dT%H:%M:%SZ"),
                                "endTime": end.strftime("%Y-%m-%dT%H:%M:%SZ")}
    d = _quantdata_request(QUANTDATA_ENDPOINTS["net_flow"], payload)
    if not d or not isinstance(d, dict) or "_error" in d:
        return None
    data = d.get("data") or {}
    if not isinstance(data, dict) or not data:
        return None
    series = []
    for ts, cell in data.items():
        if not isinstance(cell, dict):
            continue
        try:
            t = int(ts)
        except Exception:
            continue
        call = _safe_num(cell.get("callSum")) / 100.0      # cents → dollars
        put = _safe_num(cell.get("putSum")) / 100.0
        series.append({"t": t, "call": round(call, 0), "put": round(put, 0),
                       "net": round(call - put, 0), "spot": _safe_num(cell.get("stockPrice")) or None})
    if not series:
        return None
    series.sort(key=lambda x: x["t"])
    call_total = sum(s["call"] for s in series)
    put_total = sum(s["put"] for s in series)
    cum_net = round(call_total - put_total, 0)
    n = len(series)

    def _dir(x):
        return "alcista" if x > 0 else ("bajista" if x < 0 else "neutral")
    if n < 3:
        trend = "datos insuficientes"
    else:
        k = max(n // 3, 1)
        first = sum(s["net"] for s in series[:k]) / k
        last = sum(s["net"] for s in series[-k:]) / k
        if first != 0 and last * first < 0:
            trend = f"revirtiendo a {_dir(last)}"
        elif abs(last) > abs(first) * 1.15:
            trend = f"acelerando ({_dir(last)})"
        elif abs(last) < abs(first) * 0.6:
            trend = "desvaneciéndose"
        else:
            trend = f"estable ({_dir(last)})"
    return {"window": window, "expiration": (expiration or "all"), "series": series, "n": n, "cum_net": cum_net,
            "call_total": round(call_total, 0), "put_total": round(put_total, 0),
            "bias": _dir(cum_net), "trend": trend}


    def _dir(x):
        return "alcista" if x > 0 else ("bajista" if x < 0 else "neutral")


def quantdata_big_prints(ticker, window="today", expiration=None, a_min=5_000_000, b_min=1_000_000, oi_map=None):
    """Prints institucionales grandes para MARCAR sobre la línea de Net Drift, alineados a la ventana.
    Tipo A = transacción ÚNICA ≥ $5M (convicción de un solo golpe). Tipo B = ≥2 transacciones ≥ $1M en el
    MISMO contrato (cp/strike/exp) → acumulación repetida. Devuelve [{t(ms), premium, cp, side, strike, exp,
    type, golden, spot, oi_confirm, oi_change, b_count, b_span_min, b_density}] ordenado por tiempo.
    oi_map (de quantdata_oi_change) confirma si el print ABRIÓ posición (ΔOI>0) o probablemente la cerró (ΔOI<0).
    Tipo B trae densidad temporal: 3 golpes en 20min (density 'alta') pesan mucho más que 3 en 3 días ('baja')."""
    days = {"today": 1, "7d": 7, "30d": 30, "90d": 90}.get(window, 1)
    flow = quantdata_flow_window(ticker, days=days, min_premium=b_min, max_rows=400)
    if not flow:
        return []
    exp_f = str(expiration)[:10] if (expiration and str(expiration).strip().lower() not in ("", "all", "todas")) else None

    def _to_ms(ts):
        if ts is None:
            return None
        try:
            v = float(ts)
            return int(v) if v > 1e11 else int(v * 1000)   # ≥1e11 ya viene en ms; si no, seg→ms
        except Exception:
            pass
        s = str(ts).replace("Z", "").replace("T", " ")[:19]
        import calendar
        for fmt in ("%Y-%m-%d %H:%M:%S", "%Y-%m-%d %H:%M"):
            try:
                return int(calendar.timegm(time.strptime(s, fmt)) * 1000)   # ISO tratado como UTC (igual que los buckets QD)
            except Exception:
                continue
        return None

    # 1er paso: por contrato, junta los timestamps de trades ≥ b_min → define Tipo B + su densidad temporal
    times = {}
    for t in flow:
        if exp_f and str(t.get("exp") or "")[:10] != exp_f:
            continue
        if abs(_safe_num(t.get("premium"))) >= b_min:
            ms0 = _to_ms(t.get("tradeTime"))
            if ms0 is None:
                continue
            times.setdefault((t.get("cp"), t.get("strike"), t.get("exp")), []).append(ms0)
    bmeta = {}
    for key, ts in times.items():
        if len(ts) >= 2:
            span_min = (max(ts) - min(ts)) / 60000.0
            dens = "alta" if span_min <= 30 else ("media" if span_min <= 1440 else "baja")
            bmeta[key] = {"count": len(ts), "span_min": round(span_min, 1), "density": dens}

    out = []
    for t in flow:
        if exp_f and str(t.get("exp") or "")[:10] != exp_f:
            continue
        prem = abs(_safe_num(t.get("premium")))
        key = (t.get("cp"), t.get("strike"), t.get("exp"))
        typ = "A" if prem >= a_min else ("B" if (prem >= b_min and key in bmeta) else None)
        if not typ:
            continue
        ms = _to_ms(t.get("tradeTime"))
        if ms is None:
            continue
        rec = {"t": ms, "premium": round(prem, 0), "cp": t.get("cp"), "side": t.get("side"),
               "strike": t.get("strike"), "exp": t.get("exp"), "type": typ,
               "golden": bool(t.get("golden")), "spot": t.get("spot")}
        # Confirmación de OI: ¿el print ABRIÓ posición (ΔOI>0) o probablemente la cerró (ΔOI<0)?
        if isinstance(oi_map, dict) and oi_map:
            _cp = str(t.get("cp") or "").upper()
            _cpf = "CALL" if _cp.startswith("C") else ("PUT" if _cp.startswith("P") else _cp)
            oi = oi_map.get(f"{_cpf}|{round(_safe_num(t.get('strike')), 2)}|{str(t.get('exp') or '')[:10]}")
            if oi is not None:
                ch = int(_safe_num(oi.get("change")))
                rec["oi_change"] = ch
                rec["oi_confirm"] = True if ch > 0 else (False if ch < 0 else None)
            else:
                rec["oi_confirm"] = None
        # Densidad temporal del Tipo B
        if typ == "B" and key in bmeta:
            bm = bmeta[key]
            rec["b_count"] = bm["count"]
            rec["b_span_min"] = bm["span_min"]
            rec["b_density"] = bm["density"]
        out.append(rec)
    out.sort(key=lambda x: x["t"])
    return out[:60]


    def _to_ms(ts):
        if ts is None:
            return None
        try:
            v = float(ts)
            return int(v) if v > 1e11 else int(v * 1000)   # ≥1e11 ya viene en ms; si no, seg→ms
        except Exception:
            pass
        s = str(ts).replace("Z", "").replace("T", " ")[:19]
        import calendar
        for fmt in ("%Y-%m-%d %H:%M:%S", "%Y-%m-%d %H:%M"):
            try:
                return int(calendar.timegm(time.strptime(s, fmt)) * 1000)   # ISO tratado como UTC (igual que los buckets QD)
            except Exception:
                continue
        return None


@app.get("/api/quantdata/status")
def quantdata_status():
    """Configured? + a live probe that SURFACES the real HTTP error so failures are diagnosable."""
    if not _quantdata_ready():
        return {"ok": False, "configured": False, "error": _quantdata_reason()}
    probe = _quantdata_request(QUANTDATA_ENDPOINTS["net_premium"], {"filter": {"ticker": "AAPL"}})
    err = probe.get("_error") if isinstance(probe, dict) else None
    body = probe.get("_body") if isinstance(probe, dict) else None
    has_data = bool(isinstance(probe, dict) and probe.get("data"))
    if has_data:
        hint = "Conexión OK · datos recibidos de la última sesión cerrada."
    elif err and "401" in str(err):
        hint = "HTTP 401: API key inválida o no autorizada. Verifica que copiaste bien la key."
    elif err and ("403" in str(err) or "402" in str(err)):
        hint = "HTTP 403/402: la key es válida pero el PLAN API no está activo. Activa el plan API ($149.99/mo) en quantdata.us/pricing?planType=API."
    elif err and "404" in str(err):
        hint = "HTTP 404: ruta incorrecta. Revisa QUANTDATA_ENDPOINTS."
    elif err and ("422" in str(err) or "400" in str(err)):
        hint = ("HTTP 422/400: request mal formado o sin datos para esa sesión. "
                "Al omitir sessionDate debería usar la última sesión cerrada.")
    elif err and "429" in str(err):
        hint = "HTTP 429: límite de tasa (240 req/min). Espera y reintenta."
    else:
        # Antes esta rama culpaba al "fin de semana/feriado" SIEMPRE, y traía
        # "Juneteenth" escrito a mano de cuando se redactó. En un jueves de
        # mercado abierto eso manda a buscar el problema donde no está: la API
        # respondió sin error y devolvió `data` vacío, que es otra cosa. Se dice
        # el día real y se separan los dos casos.
        _hoy = datetime.now()
        _fin_de_semana = _hoy.weekday() >= 5
        hint = (
            f"La API respondió SIN error HTTP pero con `data` vacío "
            f"(hoy {_hoy.strftime('%A %Y-%m-%d')})."
            + (" Es fin de semana: sin sesión reciente es lo esperado."
               if _fin_de_semana else
               " Es día de semana, así que NO es falta de sesión: revisa que el"
               " plan API cubra /options/tool/net-drift y que la forma del"
               " request (filter/sessionDate) siga siendo la que espera la API.")
        )
    return {"ok": True, "configured": True, "base": QUANTDATA_BASE,
            "endpoints": QUANTDATA_ENDPOINTS, "live_ping": has_data,
            "error": err, "error_body": body, "hint": hint}


@app.get("/api/quantdata/flow")
def quantdata_flow_endpoint(ticker: str):
    """Quant Data flow + dark pool + net premium for one ticker (for the agent / UI)."""
    if not _quantdata_ready():
        return {"ok": False, "configured": False, "error": _quantdata_reason()}
    return _json_safe({"ok": True, "ticker": ticker.upper(),
                       "net_premium": quantdata_net_premium(ticker),
                       "dark_pool": quantdata_darkpool(ticker),
                       "flow": quantdata_flow(ticker)})


def compute_options_analytics(options, equity_positions=None):
    """Book-level Greeks from option positions. Pulls spot + IV from yfinance, prices
    each contract with Black-Scholes, aggregates net Δ/Γ/Θ/ν, expiry ladder, by-underlying
    breakdown, per-position detail, and alerts. Greeks are model estimates."""
    if not options:
        return {"ok": True, "n_options": 0,
                "note": "No se detectaron posiciones de opciones en la cuenta conectada. "
                        "(Plaid solo expone opciones si el broker las reporta; si no, cárgalas con /api/portfolio/import.)"}
    RF = 0.043
    now = datetime.now()
    spot_cache, iv_cache = {}, {}
    enriched = []
    net_delta_dollar = net_gamma = net_theta = net_vega = 0.0
    by_underlying, ladder = {}, {}

    for o in options:
        u, exp, otype = o["underlying"], o["expiry"], o["option_type"]
        strike = float(o["strike"]); contracts = float(o["contracts"])
        # Spot (cache per underlying)
        if u not in spot_cache:
            try:
                h = yf.Ticker(u).history(period="5d")
                spot_cache[u] = _safe_num(h["Close"].iloc[-1], 0.0) or None if not h.empty else None
            except Exception:
                spot_cache[u] = None
        S = spot_cache[u]
        # IV from the option chain (cache per underlying+expiry)
        ivkey = (u, exp)
        if ivkey not in iv_cache:
            chain_iv = {}
            try:
                ch = yf.Ticker(u).option_chain(exp)
                for df, typ in ((ch.calls, "call"), (ch.puts, "put")):
                    for _, row in df.iterrows():
                        chain_iv[(typ, round(_safe_num(row["strike"]), 2))] = _safe_num(row.get("impliedVolatility"))
            except Exception:
                pass
            iv_cache[ivkey] = chain_iv
        iv = iv_cache[ivkey].get((otype, round(strike, 2)))
        if not iv or iv <= 0:  # fallback: avg IV of same type on that expiry, else 0.5
            cands = [v for (t, _k), v in iv_cache[ivkey].items() if t == otype and v > 0]
            iv = (sum(cands) / len(cands)) if cands else 0.5
        # Time to expiry in years
        try:
            ed = datetime.strptime(exp, "%Y-%m-%d")
            T = max((ed - now).total_seconds() / (365.0 * 86400.0), 0.0)
            dte = max(int(round((ed - now).total_seconds() / 86400.0)), 0)
        except Exception:
            T, dte = 0.0, None
        g = _bs_greeks(S, strike, T, iv, RF, otype)
        mult = contracts * 100.0
        if S:
            d_dollar = g["delta"] * mult * S
            gm = g["gamma"] * mult
            th = g["theta_day"] * mult
            vg = g["vega_1pct"] * mult
            net_delta_dollar += d_dollar; net_gamma += gm; net_theta += th; net_vega += vg
            bu = by_underlying.setdefault(u, {"delta_dollar": 0.0, "theta_day": 0.0,
                                              "vega": 0.0, "value": 0.0, "contracts": 0.0})
            bu["delta_dollar"] += d_dollar; bu["theta_day"] += th
            bu["vega"] += vg; bu["value"] += o["value"]; bu["contracts"] += contracts
            lad = ladder.setdefault(exp, {"expiry": exp, "dte": dte, "contracts": 0.0,
                                          "value": 0.0, "theta_day": 0.0, "delta_dollar": 0.0})
            lad["contracts"] += contracts; lad["value"] += o["value"]
            lad["theta_day"] += th; lad["delta_dollar"] += d_dollar
        enriched.append({
            "underlying": u, "option_type": otype, "strike": strike, "expiry": exp, "dte": dte,
            "contracts": contracts, "value": o["value"], "spot": round(S, 2) if S else None,
            "iv": round(iv * 100, 1), "delta": round(g["delta"], 3), "gamma": round(g["gamma"], 4),
            "theta_day_$": round(g["theta_day"] * mult, 2) if S else None,
            "vega_$": round(g["vega_1pct"] * mult, 2) if S else None,
            "delta_$": round(g["delta"] * mult * S, 0) if S else None})

    # Total directional delta of the whole book: options Δ$ + stock value (stock delta = 1)
    stock_delta = sum(float(p.get("value") or 0) for p in (equity_positions or []))
    total_book_delta = net_delta_dollar + stock_delta

    for v in by_underlying.values():
        for k in ("delta_dollar", "theta_day", "vega", "value"):
            v[k] = round(v[k], 2)
    ladder_list = sorted(ladder.values(), key=lambda x: x["expiry"])
    for l in ladder_list:
        for k in ("value", "theta_day", "delta_dollar"):
            l[k] = round(l[k], 2)

    # Alerts
    alerts = []
    soon = [e for e in enriched if e.get("dte") is not None and e["dte"] <= 7]
    if soon:
        alerts.append({"level": "warn",
                       "msg": f"{len(soon)} posición(es) vencen en ≤7 días — el theta se acelera y el gamma se dispara."})
    if net_theta < 0:
        alerts.append({"level": "info",
                       "msg": f"El libro de opciones pierde ${abs(round(net_theta,0)):,.0f}/día por decaimiento temporal (theta neto)."})
    elif net_theta > 0:
        alerts.append({"level": "info",
                       "msg": f"El libro de opciones cobra ${round(net_theta,0):,.0f}/día de theta neto (el decaimiento juega a tu favor)."})
    if by_underlying:
        top_u = max(by_underlying.items(), key=lambda kv: abs(kv[1]["delta_dollar"]))
        tot_abs = sum(abs(v["delta_dollar"]) for v in by_underlying.values()) or 1
        share = abs(top_u[1]["delta_dollar"]) / tot_abs
        if share >= 0.6 and len(by_underlying) > 1:
            alerts.append({"level": "warn",
                           "msg": f"{top_u[0]} concentra {share*100:.0f}% del delta de opciones — riesgo direccional poco diversificado."})

    return _json_safe({
        "ok": True, "n_options": len(options),
        "greeks": {"net_delta_dollar": round(net_delta_dollar, 0),
                   "net_delta_shares": round(net_delta_dollar / spot_cache[options[0]["underlying"]], 0)
                   if spot_cache.get(options[0]["underlying"]) else None,
                   "net_gamma": round(net_gamma, 2),
                   "net_theta_day": round(net_theta, 2),
                   "net_vega_1pct": round(net_vega, 2)},
        "total_book_delta_dollar": round(total_book_delta, 0),
        "options_delta_dollar": round(net_delta_dollar, 0),
        "stock_delta_dollar": round(stock_delta, 0),
        "by_underlying": by_underlying, "ladder": ladder_list,
        "positions": sorted(enriched, key=lambda x: (x["expiry"], x["underlying"])),
        "alerts": alerts, "rf": RF,
        "note": "Las griegas son estimaciones del modelo Black-Scholes con IV de yfinance; "
                "pueden diferir de las de tu broker.",
        "generated_at": now.strftime('%m/%d/%Y, %I:%M:%S %p')})


@app.get("/api/portfolio-options")
def portfolio_options():
    """Book-level options Greeks panel. Reads the stored option snapshot (Plaid o import)
    + equity snapshot (for total directional delta) and prices everything with BSM."""
    try:
        opts = get_options_snapshot()
        eq = get_portfolio_snapshot()
        return compute_options_analytics(opts, eq)
    except Exception as e:
        return {"ok": False, "error": f"{e}"}


def _scheduler_tickers():
    """Universo a snapshotear: primarios + holdings persistidos + tickers analizados recientemente."""
    s = set(VERTEX_PRIMARY_TICKERS)
    try:
        for h in (get_portfolio_snapshot() or []):
            t = str(h.get("ticker") or "").upper().strip()
            if t and t.replace(".", "").isalnum() and len(t) <= 6:
                s.add(t)
    except Exception:
        pass
    try:
        conn = _db()
        rows = conn.execute("SELECT DISTINCT ticker FROM reports ORDER BY created_ts DESC LIMIT 40").fetchall()
        conn.close()
        for r in rows:
            t = str(r["ticker"] or "").upper().strip()
            if t:
                s.add(t)
    except Exception:
        pass
    return sorted(s)


def _run_daily_collection(throttle=1.0):
    """Captura el snapshot de hoy para cada ticker del universo. Devuelve cuántos capturó."""
    if _SCHED_STATE["running"]:
        return 0
    _SCHED_STATE["running"] = True
    n = 0
    tickers = _scheduler_tickers()
    try:
        for tk in tickers:
            try:
                _collect_signal_snapshot(tk)
                n += 1
                time.sleep(throttle)            # throttle suave para Quant Data
            except Exception:
                pass
    finally:
        _SCHED_STATE.update({"running": False, "last_run": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
                             "last_count": n, "last_tickers": tickers})
    return n


_SCHED_STATE = {"started": False, "last_run": None, "last_count": 0,
                "last_tickers": [], "running": False, "next_run": None}


def _seconds_until_next_run():
    """Próxima corrida ~21:30 UTC (post-cierre US; ~4:30pm EST / 5:30pm EDT)."""
    now = datetime.now(timezone.utc)
    target = now.replace(hour=21, minute=30, second=0, microsecond=0)
    if now >= target:
        target += timedelta(days=1)
    # saltar fines de semana
    while target.weekday() >= 5:
        target += timedelta(days=1)
    _SCHED_STATE["next_run"] = target.strftime("%Y-%m-%d %H:%M UTC")
    return max(60.0, (target - now).total_seconds())


def _scheduler_loop():
    while True:
        try:
            time.sleep(_seconds_until_next_run())
            if datetime.now(timezone.utc).weekday() < 5:    # solo días de mercado
                _run_daily_collection()
        except Exception:
            time.sleep(3600)


def _start_scheduler():
    if _SCHED_STATE["started"] or os.environ.get("VERTEX_SCHEDULER", "1") == "0":
        return
    threading.Thread(target=_scheduler_loop, daemon=True, name="vertex-collector").start()
    _SCHED_STATE["started"] = True


@app.get("/api/scheduler/status")
def scheduler_status():
    _seconds_until_next_run()
    return {"started": _SCHED_STATE["started"], "running": _SCHED_STATE["running"],
            "last_run": _SCHED_STATE["last_run"], "last_count": _SCHED_STATE["last_count"],
            "next_run": _SCHED_STATE["next_run"], "universe": _scheduler_tickers(),
            "primary": VERTEX_PRIMARY_TICKERS}


@app.post("/api/scheduler/run-now")
def scheduler_run_now():
    """Dispara la colección de snapshots de inmediato (en un hilo, para no bloquear).

    POST porque arranca trabajo. Un GET puede reemitirlo un prefetch o la
    caché de ida/vuelta del navegador, y aquí eso significa lanzar una
    colección entera contra los proveedores sin que nadie la pidiera."""
    if _SCHED_STATE["running"]:
        return {"ok": False, "note": "Ya hay una colección en curso."}
    threading.Thread(target=_run_daily_collection, daemon=True, name="vertex-collect-now").start()
    return {"ok": True, "note": "Colección disparada en segundo plano.", "universe": _scheduler_tickers()}


