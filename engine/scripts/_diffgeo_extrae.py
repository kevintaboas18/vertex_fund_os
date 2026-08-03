"""Extrae las tres funciones de geometría del HTML del panel.

EXTRAE, no transcribe: es la misma regla que el resto de los diferenciales.
Si alguien edita el HTML, este script recoge la versión editada; si el bloque
cambia de nombre o desaparece, falla en vez de comparar contra una copia vieja.
"""
import pathlib
import re
import sys

HTML = pathlib.Path(__file__).resolve().parents[2] / "vertex_fund_os_platform.html"


def bloque(src: str, nombre: str) -> str:
    """El cuerpo completo de `function nombre(...) { … }`, contando llaves.

    La lista de parámetros se salta contando PARÉNTESIS antes de buscar la
    llave: estas tres funciones desestructuran su argumento, así que la primera
    `{` del texto es la del parámetro, no la del cuerpo.
    """
    i = src.index(f"function {nombre}(")
    par = src.index("(", i)
    prof = 0
    for k in range(par, len(src)):
        if src[k] == "(":
            prof += 1
        elif src[k] == ")":
            prof -= 1
            if prof == 0:
                par = k
                break
    j = src.index("{", par)
    prof = 0
    for k in range(j, len(src)):
        if src[k] == "{":
            prof += 1
        elif src[k] == "}":
            prof -= 1
            if prof == 0:
                return src[i:k + 1]
    raise SystemExit(f"no se cierra la función {nombre}")


def main() -> None:
    src = HTML.read_text(encoding="utf-8")
    vc = re.search(r"^const VC = \{.*?^\};", src, re.M | re.S)
    if not vc:
        raise SystemExit("no se encontró el bloque `const VC = {…}` del panel")
    partes = [vc.group(0)]
    for n in ("vcSmartDomain", "vcBuildScales", "vcPackLabels"):
        partes.append(bloque(src, n))
    partes.append("export { vcSmartDomain, vcBuildScales, vcPackLabels, VC };")
    pathlib.Path(sys.argv[1]).write_text("\n\n".join(partes), encoding="utf-8")


if __name__ == "__main__":
    main()
