"""La caché de proveedores se escribe entera o no se escribe.

`Cache.put` usaba `path.write_text`, que abre TRUNCANDO: entre el truncado y
el volcado el archivo está vacío en disco. La aplicación web corre cuatro
hilos de fondo (el planificador, el backfill, el índice de FMP y la colección
bajo demanda) que escriben esta misma caché mientras las peticiones en vivo la
leen, así que la carrera era alcanzable en producción, no teórica.

El lector devuelve `None` ante un JSON roto, y por eso el defecto no se veía
como un fallo: se veía como una petición más a la API — cuota gastada para
recuperar algo que ya estaba en disco.

Estos tests no leen el código fuente: hacen correr hilos de verdad contra el
disco de verdad.
"""

from __future__ import annotations

import json
import threading

from wbj.providers.cache import Cache


def _payload(n: int) -> dict:
    """Un payload lo bastante grande como para no caber en una escritura."""
    return {"n": n, "filas": [{"i": i, "relleno": "x" * 200} for i in range(400)]}


def test_a_reader_never_sees_a_half_written_entry():
    """El invariante: quien lee ve SIEMPRE un payload entero y coherente.

    Con `write_text` este test falla con lecturas `None` (el archivo vacío o
    truncado que el lector descarta). Con `os.replace` no hay estado
    intermedio que observar.
    """
    import tempfile

    with tempfile.TemporaryDirectory() as d:
        cache = Cache(d)
        cache.put("NVDA", "perfil", _payload(0))     # que exista antes de la carrera

        parar = threading.Event()
        rotos: list[str] = []

        def escribir() -> None:
            n = 0
            while not parar.is_set():
                n += 1
                cache.put("NVDA", "perfil", _payload(n))

        def leer() -> None:
            while not parar.is_set():
                got = cache.get("NVDA", "perfil")
                if got is None:
                    rotos.append("el lector vio la entrada rota o ausente")
                elif len(got.get("filas", [])) != 400:
                    rotos.append(f"payload truncado: {len(got.get('filas', []))} filas")

        hilos = [threading.Thread(target=escribir) for _ in range(3)]
        hilos += [threading.Thread(target=leer) for _ in range(3)]
        for h in hilos:
            h.start()
        parar.wait(1.5)
        parar.set()
        for h in hilos:
            h.join(timeout=10)

        assert not rotos, f"{len(rotos)} lecturas rotas; primera: {rotos[0]}"


def test_concurrent_writers_leave_one_valid_entry():
    """Escritores simultáneos con payloads DISTINTOS: el archivo final tiene
    que ser el de uno de ellos, no una mezcla de dos."""
    import tempfile

    with tempfile.TemporaryDirectory() as d:
        cache = Cache(d)
        hilos = [
            threading.Thread(target=cache.put, args=("NVDA", "perfil", _payload(n)))
            for n in range(12)
        ]
        for h in hilos:
            h.start()
        for h in hilos:
            h.join(timeout=10)

        final = cache.get("NVDA", "perfil")
        assert final is not None, "la entrada quedó ilegible tras 12 escritores"
        assert final["n"] in range(12), "el payload final no es el de ningún escritor"
        assert len(final["filas"]) == 400, "el payload final quedó mezclado"
        assert cache.age_days("NVDA", "perfil") is not None, "la marca de tiempo se perdió"


def test_no_temporary_file_is_left_behind():
    """El temporal es un detalle de implementación: no puede quedar como
    basura en el directorio de caché ni confundirse con una entrada."""
    import tempfile
    from pathlib import Path

    with tempfile.TemporaryDirectory() as d:
        cache = Cache(d)
        for n in range(5):
            cache.put("NVDA", "perfil", _payload(n))
        sobrantes = [p.name for p in Path(d).rglob("*.tmp")]
        assert not sobrantes, f"temporales sin limpiar: {sobrantes}"
        assert [p.name for p in Path(d).rglob("*.json")] == ["perfil.json"]


def test_a_failed_write_never_breaks_the_analysis():
    """Cachear es una optimización: el dato ya se obtuvo. Si el disco falla
    (permisos, disco lleno), `put` no puede tumbar el análisis."""
    import tempfile
    from pathlib import Path

    with tempfile.TemporaryDirectory() as d:
        cache = Cache(d)
        # Un directorio donde debería ir el archivo: `os.replace` fallará.
        destino = Path(d) / "NVDA" / "perfil.json"
        destino.mkdir(parents=True)
        cache.put("NVDA", "perfil", _payload(1))          # no debe lanzar
        assert not list(Path(d).rglob("*.tmp")), "el temporal quedó tras el fallo"


def test_the_entry_survives_a_reader_holding_the_old_payload():
    """`os.replace` sustituye el nombre, no el contenido abierto. Un lector
    que ya tenía el payload viejo en memoria no se corrompe cuando otro
    escribe encima — que es justo lo que hace el planificador de fondo."""
    import tempfile

    with tempfile.TemporaryDirectory() as d:
        cache = Cache(d)
        cache.put("NVDA", "perfil", _payload(1))
        viejo = cache.get("NVDA", "perfil")
        cache.put("NVDA", "perfil", _payload(2))
        assert viejo["n"] == 1, "el payload ya leído cambió bajo los pies del lector"
        assert cache.get("NVDA", "perfil")["n"] == 2
        # Y sigue siendo JSON estricto, que es lo que exige el cliente HTTP.
        json.dumps(cache.get("NVDA", "perfil"), allow_nan=False)
