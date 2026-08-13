"""El mapa del mercado por sectores — datos, no opinión.

Esto es la pantalla de arranque del panel: once sectores del S&P más tres
referencias (SPY, RSP, QQQ), cada uno con su precio, su cambio del día y su
RSI. Sirve para lo que sirve un mapa: ver de un golpe dónde está entrando el
dinero antes de elegir un ticker.

**Por qué RSP al lado de SPY.** No es un tercer índice de adorno. SPY pesa por
capitalización y RSP pesa a partes iguales, así que la distancia entre los dos
dice si sube el mercado o suben cinco empresas. Un SPY verde con un RSP rojo es
un mercado estrecho, y eso cambia cómo se lee todo lo demás.

Todo lo de aquí es **puro**: recibe cierres, devuelve números. Ni red ni disco.
Lo que sale por pantalla lo arma `vertex_api.py`.
"""

from __future__ import annotations

__all__ = [
    "RSI_PERIODO",
    "rsi",
    "cambio_pct",
    "REFERENCIAS",
    "SECTORES",
    "INDUSTRIAS",
    "CATEGORIAS",
    "CATEGORIA_DE",
    "ES_REFERENCIA",
    "universo",
    "nombre_de",
    "industrias_de",
    "categoria_de",
    # ── rotación ──
    "VENTANAS_ROC",
    "UMBRAL_VOLUMEN",
    "roc",
    "serie_rs",
    "media",
    "cuadrante",
    "CUADRANTES",
    "flujo_de_capital",
    "dispersion",
    "salud_del_mercado",
    "clasifica_sector",
    "lideres_del_dia_rojo",
    "diagnostico",
]

#: Periodos del RSI. 14 es el de Wilder, que es lo que enseña cualquier
#: plataforma cuando dice «RSI» a secas. Cambiarlo aquí lo cambia en el panel,
#: en la etiqueta y en los tests a la vez.
RSI_PERIODO = 14


def rsi(cierres, periodo: int = RSI_PERIODO):
    """RSI de Wilder sobre una serie de cierres, del más VIEJO al más nuevo.

    Devuelve `None` cuando no hay suficiente historia — que no es lo mismo que
    50. Un RSI inventado en el arranque de un ETF recién listado se leería como
    «ni sobrecomprado ni sobrevendido», que es una afirmación; `None` se pinta
    como «—», que es la verdad.

    Wilder suaviza, no promedia: la primera media es simple sobre `periodo`
    valores y a partir de ahí cada una arrastra la anterior con peso
    `(n-1)/n`. Un promedio móvil normal da otro número —parecido, y distinto—,
    y el que enseñan las plataformas es este.

    Sin pérdidas en toda la ventana el RSI es 100 por definición (no hay
    división por cero que salvar: el límite existe y vale 100). Sin ganancias,
    0.
    """
    try:
        serie = [float(c) for c in cierres if c is not None]
    except (TypeError, ValueError):
        return None
    n = int(periodo)
    if n < 2 or len(serie) < n + 1:
        return None

    subidas, bajadas = [], []
    for anterior, actual in zip(serie, serie[1:]):
        d = actual - anterior
        subidas.append(max(d, 0.0))
        bajadas.append(max(-d, 0.0))

    media_sube = sum(subidas[:n]) / n
    media_baja = sum(bajadas[:n]) / n
    for s, b in zip(subidas[n:], bajadas[n:]):
        media_sube = (media_sube * (n - 1) + s) / n
        media_baja = (media_baja * (n - 1) + b) / n

    if media_baja == 0:
        return 100.0 if media_sube > 0 else 50.0
    rs = media_sube / media_baja
    return 100.0 - (100.0 / (1.0 + rs))


def cambio_pct(precio, cierre_previo):
    """Cambio del día en %, o `None` si falta cualquiera de los dos.

    Un cierre previo de 0 no da «infinito por ciento»: da `None`. Es el caso de
    un dato corrupto, y pintarlo como un número enorme sería inventarse una
    sesión histórica.
    """
    try:
        p, q = float(precio), float(cierre_previo)
    except (TypeError, ValueError):
        return None
    if q == 0:
        return None
    return (p - q) / q * 100.0


#: Las tres referencias del mercado. NO tienen industrias dentro: son índices,
#: no sectores, y desplegarlos sería inventar un desglose que no existe.
REFERENCIAS = (
    ("SPY", "S&P 500"),
    ("RSP", "S&P 500 equiponderado"),
    ("QQQ", "Nasdaq 100"),
)

#: Los once sectores GICS con su ETF de SPDR — los de referencia del mercado.
#: El orden es el que se pinta, de más a menos cíclico dentro de lo razonable;
#: no significa nada más que eso.
SECTORES = (
    ("XLK", "Tecnología"),
    ("XLF", "Financiero"),
    ("XLV", "Salud"),
    ("XLY", "Consumo discrecional"),
    ("XLC", "Comunicaciones"),
    ("XLI", "Industrial"),
    ("XLP", "Consumo básico"),
    ("XLE", "Energía"),
    ("XLU", "Servicios públicos"),
    ("XLRE", "Inmobiliario"),
    ("XLB", "Materiales"),
)

#: Las industrias DENTRO de cada sector, con el ETF que mejor la representa.
#:
#: Esta lista es curada, y conviene decirlo: no existe un desglose oficial de
#: «industrias de XLE» en formato ETF. Se eligieron los más líquidos y más
#: puros de cada industria, evitando los apalancados y los de menos de ~$100M
#: bajo gestión —un ETF que casi no se negocia da un RSI que no representa a
#: nadie—. Añadir o quitar uno es una línea de aquí y nada más.
#:
#: `XLU` no aparece: los ETF que se venden como «industrias de utilities» son
#: en realidad temáticos de energía limpia, que es otra cosa. Antes que meter
#: un desglose falso, el sector se queda sin desplegar y la pantalla lo dice.
INDUSTRIAS = {
    "XLK": (
        ("SMH", "Semiconductores"),
        ("IGV", "Software"),
        ("CIBR", "Ciberseguridad"),
        ("SKYY", "Nube"),
        ("XSD", "Semiconductores equiponderado"),
    ),
    "XLF": (
        ("KRE", "Bancos regionales"),
        ("KBE", "Bancos"),
        ("KCE", "Mercados de capitales"),
        ("IAK", "Seguros"),
    ),
    "XLV": (
        ("XBI", "Biotecnología equiponderado"),
        ("IBB", "Biotecnología"),
        ("IHI", "Dispositivos médicos"),
        ("IHF", "Proveedores de salud"),
        ("PPH", "Farmacéuticas"),
    ),
    "XLY": (
        ("XRT", "Minoristas"),
        ("XHB", "Construcción de vivienda"),
        ("ITB", "Constructoras"),
        ("PEJ", "Ocio y viajes"),
    ),
    "XLC": (
        ("FDN", "Internet"),
        ("SOCL", "Redes sociales"),
        ("ESPO", "Videojuegos"),
        ("PBS", "Medios y entretenimiento"),
    ),
    "XLI": (
        ("ITA", "Aeroespacial y defensa"),
        ("IYT", "Transporte"),
        ("PAVE", "Infraestructura"),
        ("JETS", "Aerolíneas"),
    ),
    "XLP": (
        ("PBJ", "Alimentos y bebidas"),
        ("FTXG", "Alimentación"),
    ),
    "XLE": (
        ("XOP", "Exploración y producción"),
        ("OIH", "Servicios petroleros"),
        ("AMLP", "Midstream y oleoductos"),
        ("FCG", "Gas natural"),
    ),
    "XLRE": (
        ("REZ", "Residencial"),
        ("REM", "Hipotecarios"),
        ("INDS", "Industrial y logística"),
    ),
    "XLB": (
        ("GDX", "Mineras de oro"),
        ("COPX", "Cobre"),
        ("LIT", "Litio y baterías"),
        ("WOOD", "Madera y papel"),
    ),
}

#: Los que NO se despliegan, y por qué se pregunta con un `in` y no con una
#: lista suelta en el panel: si mañana entra un cuarto índice, el sitio donde
#: se declara es este.
ES_REFERENCIA = frozenset(t for t, _ in REFERENCIAS)

#: Nombre por ticker, para no repetir la tabla en tres sitios.
_NOMBRES = {t: n for t, n in REFERENCIAS + SECTORES}
for _sector, _filas in INDUSTRIAS.items():
    for _t, _n in _filas:
        _NOMBRES.setdefault(_t, _n)


def universo() -> tuple[str, ...]:
    """Los catorce de la parrilla, en el orden en que se pintan."""
    return tuple(t for t, _ in REFERENCIAS) + tuple(t for t, _ in SECTORES)


def nombre_de(ticker: str) -> str:
    """«XLE» → «Energía». El propio ticker si no se conoce."""
    return _NOMBRES.get(str(ticker).upper().strip(), str(ticker).upper().strip())


def industrias_de(ticker: str) -> tuple[tuple[str, str], ...]:
    """Las industrias de un sector. Vacío para SPY/RSP/QQQ y para lo que no sea
    uno de los once — preguntarlo no es un error, y devolver vacío deja que la
    pantalla diga «este no se despliega» en vez de reventar."""
    return tuple(INDUSTRIAS.get(str(ticker).upper().strip(), ()))


# ═══════════════════════════════════════════════════════════════════════════
#  ROTACIÓN SECTORIAL
#
#  Tres capas, y el orden importa porque cada una responde a algo distinto:
#
#   1. **Salud** — SPY contra RSP. ¿Sube el mercado o suben cinco empresas?
#   2. **Fuerza relativa** — cada sector contra el índice, y hacia dónde va esa
#      fuerza. De ahí salen los cuatro cuadrantes.
#   3. **Flujo** — precio Y volumen. Sin volumen, un sector que sube es ruido;
#      con volumen por encima de su media, es alguien grande moviéndose.
#
#  Todo son funciones puras sobre listas de cierres y volúmenes. La decisión de
#  qué es «alto» está en constantes con nombre, no repartida por el código.
# ═══════════════════════════════════════════════════════════════════════════

#: Las tres ventanas de la pendiente, en sesiones. Corta para el impulso de
#: ahora, media para la tendencia del mes, larga para saber si esto lleva
#: pasando un trimestre o empezó el martes.
VENTANAS_ROC = (5, 20, 50)

#: Cuánto volumen es «institucional». 1,2 veces la media de 20 sesiones: por
#: debajo de eso el movimiento cabe dentro de un día normal y no prueba nada.
UMBRAL_VOLUMEN = 1.2

#: Cuánto tiene que caer el SPY para que el día cuente como día rojo de verdad.
#: Un -0,2% no separa a los fuertes de los débiles: casi todo cierra plano.
CAIDA_DIA_ROJO = -1.0

#: Los cuatro cuadrantes, con lo que significan. El orden es el del ciclo:
#: mejora → lidera → se agota → se rezaga → vuelve a mejorar.
CUADRANTES = {
    "improving": "Mejorando — fuerza baja pero girando al alza (acumulación temprana)",
    "leading": "Liderando — fuerza alta y subiendo (el dinero está entrando)",
    "weakening": "Agotándose — fuerza alta pero perdiendo impulso",
    "lagging": "Rezagado — fuerza baja y cayendo (sin interés o venta activa)",
}

#: Las tres familias de la tabla, que es como se lee la rotación de un vistazo:
#: si el dinero sale de la primera y entra en la tercera, es miedo.
CATEGORIAS = (
    ("crecimiento", "Crecimiento / Beta alta", ("XLK", "XLC", "XLY")),
    ("ciclicos", "Cíclicos / Sensibles", ("XLF", "XLI", "XLB", "XLE")),
    ("defensivos", "Defensivos / Refugio", ("XLP", "XLV", "XLU", "XLRE")),
)

#: Sector → clave de su familia.
CATEGORIA_DE = {t: clave for clave, _, ts in CATEGORIAS for t in ts}


def categoria_de(ticker: str) -> str:
    """«XLK» → «crecimiento». Cadena vacía para lo que no sea uno de los once."""
    return CATEGORIA_DE.get(str(ticker).upper().strip(), "")


def media(serie, n: int):
    """Media simple de las últimas `n`, o `None` si no llegan."""
    try:
        s = [float(x) for x in serie if x is not None]
    except (TypeError, ValueError):
        return None
    n = int(n)
    if n < 1 or len(s) < n:
        return None
    return sum(s[-n:]) / n


def roc(serie, n: int):
    """Cambio porcentual entre el último valor y el de hace `n` posiciones.

    Es la PENDIENTE de la especificación: positiva, el sector va ganando fuerza
    contra el índice; negativa, la va perdiendo. Se mide sobre la serie del RS
    Ratio, no sobre el precio — un sector puede subir y aun así quedarse atrás.
    """
    try:
        s = [float(x) for x in serie if x is not None]
    except (TypeError, ValueError):
        return None
    n = int(n)
    if n < 1 or len(s) < n + 1:
        return None
    base = s[-1 - n]
    if base == 0:
        return None
    return (s[-1] - base) / abs(base) * 100.0


def serie_rs(cierres_etf, cierres_bench):
    """RS Ratio = precio del ETF / precio del índice, sesión a sesión.

    Las dos series se alinean por el FINAL. Si una tiene más historia que la
    otra —un ETF más nuevo, un festivo que un proveedor cuenta y el otro no—,
    emparejar por el principio desplazaría todo un día y la pendiente saldría
    de comparar el martes con el miércoles.

    Es una aproximación consciente: lo correcto sería casar por fecha. Con las
    dos series del mismo proveedor y el mismo rango, alinear por el final da lo
    mismo; y si algún día no lo diera, el error se ve —la pendiente cambia de
    signo— en vez de esconderse en un promedio.
    """
    try:
        a = [float(x) for x in cierres_etf if x is not None]
        b = [float(x) for x in cierres_bench if x is not None]
    except (TypeError, ValueError):
        return []
    n = min(len(a), len(b))
    if n == 0:
        return []
    a, b = a[-n:], b[-n:]
    return [x / y for x, y in zip(a, b) if y != 0]


def cuadrante(fuerza, impulso):
    """Los cuatro cuadrantes del RRG a partir de fuerza e impulso.

    `fuerza` es la pendiente larga (50 sesiones): dónde está el sector.
    `impulso` es la corta (5): hacia dónde va.

    Un sector fuerte que pierde impulso NO es lo mismo que uno débil que lo
    gana, aunque los dos estén «a medio camino»: el primero se está agotando y
    el segundo despertando. Por eso son cuatro casillas y no un ranking.
    """
    if fuerza is None or impulso is None:
        return None
    if fuerza >= 0:
        return "leading" if impulso >= 0 else "weakening"
    return "improving" if impulso >= 0 else "lagging"


def flujo_de_capital(ret_etf, ret_bench, volumen, volumen_medio,
                     umbral: float = UMBRAL_VOLUMEN):
    """`entrada`, `salida` o `None` — precio Y volumen, nunca uno solo.

    La regla de la especificación, literal: para que cuente como flujo, el
    sector tiene que batir (o quedarse detrás de) al índice **y** hacerlo con
    volumen por encima de `umbral` veces su media de 20 sesiones.

    Sin la condición de volumen esto sería un ranking de rendimiento con otro
    nombre: un sector puede subir más que el mercado en un día flojo sin que
    haya entrado un dólar institucional.
    """
    try:
        re_, rb = float(ret_etf), float(ret_bench)
        v, vm = float(volumen), float(volumen_medio)
    except (TypeError, ValueError):
        return None
    if vm <= 0 or v / vm < umbral:
        return None
    return "entrada" if re_ > rb else ("salida" if re_ < rb else None)


def dispersion(retornos):
    """Desviación estándar de los retornos del día entre sectores.

    Alta: unos suben fuerte y otros caen fuerte — hay rotación de verdad y
    elegir el sector correcto paga. Baja: todo se mueve en bloque, que es lo
    que pasa en un pánico macro o en un rally de pura liquidez, y ahí elegir
    sector no aporta nada.

    Se usa la desviación POBLACIONAL (n, no n-1): los once sectores no son una
    muestra de una población mayor, son la población entera.
    """
    try:
        s = [float(x) for x in retornos if x is not None]
    except (TypeError, ValueError):
        return None
    if len(s) < 2:
        return None
    m = sum(s) / len(s)
    return (sum((x - m) ** 2 for x in s) / len(s)) ** 0.5


def salud_del_mercado(rs_rsp_spy, ret_spy, ventana: int = 20):
    """¿Sube el mercado o suben cinco empresas?

    `rs_rsp_spy` es la serie de RSP/SPY. Su pendiente dice si el mercado ancho
    le está ganando al de las mega-caps. Cruzada con lo que hace el SPY, salen
    los tres escenarios de la especificación:

    · RSP/SPY sube  → amplitud sólida: la mayoría participa.
    · RSP/SPY baja y SPY sube → rally estrecho: un puñado de gigantes lo
      sostiene y el resto está plano o cayendo.
    · RSP/SPY sube y SPY no  → rotación activa fuera de las mega-caps.

    Devuelve `(clave, frase, pendiente)`. `None` en la clave cuando no hay
    serie suficiente: sin pendiente no hay diagnóstico, y adivinarlo aquí sería
    poner una etiqueta de régimen sobre nada.
    """
    p = roc(rs_rsp_spy, ventana)
    if p is None:
        return (None, "Sin historia suficiente para medir la amplitud.", None)
    sube_spy = ret_spy is not None and float(ret_spy) > 0
    if p > 0:
        if sube_spy:
            return ("amplia", "Amplitud sólida: la mayoría del mercado participa "
                              "de la subida.", p)
        return ("rotacion", "Rotación activa: el mercado ancho aguanta mejor que "
                            "las mega-caps.", p)
    if sube_spy:
        return ("estrecho", "Rally estrecho: un puñado de mega-caps sostiene al "
                            "SPY mientras el resto se queda atrás.", p)
    return ("debil", "Debilidad amplia: ni las mega-caps ni el mercado ancho "
                     "tiran.", p)


def clasifica_sector(cierres_etf, volumenes_etf, cierres_bench,
                     ret_etf=None, ret_bench=None):
    """Todo lo de UN sector: RS, las tres pendientes, cuadrante y flujo.

    Devuelve un dict con `None` en lo que no se pueda calcular. No lanza: un
    sector sin historia deja el resto del mapa intacto.
    """
    rs = serie_rs(cierres_etf, cierres_bench)
    pendientes = {f"roc_{n}": roc(rs, n) for n in VENTANAS_ROC}
    fuerza = pendientes.get(f"roc_{VENTANAS_ROC[-1]}")
    impulso = pendientes.get(f"roc_{VENTANAS_ROC[0]}")
    # Sin la ventana larga, la media manda: con 60 sesiones de historia y un
    # ROC de 50 en blanco, decir "sin cuadrante" desperdicia lo que sí hay.
    if fuerza is None:
        fuerza = pendientes.get(f"roc_{VENTANAS_ROC[1]}")
    vol_actual = None
    for v in reversed(list(volumenes_etf or [])):
        if v is not None:
            vol_actual = v
            break
    salida = {
        "rs": rs[-1] if rs else None,
        "cuadrante": cuadrante(fuerza, impulso),
        "fuerza": fuerza,
        "impulso": impulso,
        "volumen_rel": None,
        "flujo": None,
        **pendientes,
    }
    vm = media(volumenes_etf, 20)
    if vm and vol_actual:
        salida["volumen_rel"] = float(vol_actual) / vm
    if ret_etf is not None and ret_bench is not None:
        salida["flujo"] = flujo_de_capital(ret_etf, ret_bench, vol_actual, vm)
    return salida


def lideres_del_dia_rojo(ret_spy, retornos_por_sector,
                         corte: float = CAIDA_DIA_ROJO):
    """En un día de venta generalizada, quién aguanta.

    Solo devuelve algo cuando el SPY cae de verdad (`corte`, -1% por defecto).
    Fuera de esos días la pregunta no tiene sentido: en una sesión plana el
    sector que menos baja es simplemente el que menos se movió.

    El que no cae cuando todo cae es el que tiene demanda debajo.
    """
    try:
        spy = float(ret_spy)
    except (TypeError, ValueError):
        return []
    if spy > corte:
        return []
    filas = [(t, float(r)) for t, r in (retornos_por_sector or {}).items()
             if r is not None]
    # Aguantar es batir al índice por un margen real, no por dos centésimas.
    aguantan = [(t, r) for t, r in filas if r > spy + 0.25]
    return sorted(aguantan, key=lambda x: -x[1])


def diagnostico(por_sector, salud_clave=None):
    """Las reglas de escenario, en palabras.

    `por_sector` es `{ticker: {"cuadrante": …, "flujo": …}}`. Devuelve una
    lista de frases; vacía si nada encaja, que es una respuesta legítima — la
    mayoría de los días el mercado no está haciendo nada citable, y llenar el
    hueco con una frase genérica es exactamente lo que hace que nadie vuelva a
    leer esta sección.
    """
    def _fuertes(ts):
        return [t for t in ts
                if (por_sector.get(t) or {}).get("cuadrante") in ("leading", "improving")]

    def _flojos(ts):
        return [t for t in ts
                if (por_sector.get(t) or {}).get("cuadrante") in ("lagging", "weakening")]

    crecimiento = ("XLK", "XLC", "XLY")
    ciclicos = ("XLF", "XLI", "XLB", "XLE")
    defensivos = ("XLP", "XLV", "XLU")

    frases = []
    if len(_flojos(crecimiento)) >= 2 and len(_fuertes(defensivos)) >= 2:
        frases.append(
            "El mercado está buscando protección: el crecimiento pierde fuerza "
            "y los defensivos la ganan. Precaución con posiciones largas de "
            "beta alta.")
    if len(_flojos(crecimiento)) >= 1 and len(_fuertes(ciclicos)) >= 2:
        frases.append(
            "Rotación saludable fuera de las mega-caps hacia la economía real "
            "(financieros, industriales, materiales, energía).")
    if len(_fuertes(crecimiento)) >= 2 and len(_flojos(defensivos)) >= 2:
        frases.append(
            "Apetito por riesgo: el dinero está en crecimiento y sale de los "
            "refugios.")
    if salud_clave == "estrecho":
        frases.append(
            "Ojo con la amplitud: el índice sube sostenido por pocas empresas, "
            "así que un giro en esas pocas se lleva al índice entero.")
    entrando = [t for t, d in por_sector.items() if (d or {}).get("flujo") == "entrada"]
    saliendo = [t for t, d in por_sector.items() if (d or {}).get("flujo") == "salida"]
    if entrando and saliendo:
        frases.append(
            f"Con volumen por encima de la media: entra en {', '.join(sorted(entrando))} "
            f"y sale de {', '.join(sorted(saliendo))}.")
    return frases
