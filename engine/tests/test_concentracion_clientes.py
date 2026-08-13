"""La concentracion de clientes sale del XBRL, no de una captura a mano.

`BUS-CONC-003` y `BUS-HHI-004` fallaban en 10 de 12 tickers, y NVDA daba 100%
de cobertura en business SOLO porque alguien lleno su `Entradas/NVDA.json`.
El resto del mercado no lo tiene.

El dato SI existe --la SEC obliga a divulgar cualquier cliente >=10% de los
ingresos-- pero no llega por los caminos de siempre: `companyfacts` no
devuelve hechos dimensionales, y este lo es. Lo que se prueba aqui es lo
unico que hace peligroso leerlo: que el eje lleva AGREGADOS junto a los
clientes sueltos.
"""
import pytest

from wbj.providers.edgar import EdgarProvider


UN_CLIENTE = [
    "CustomerOneMember", "CustomerAMember", "CustomerNumberOneMember",
    "OneCustomerMember", "LargestCustomerMember", "SecondLargestCustomerMember",
    "MajorCustomerOneMember", "DistributorAMember", "Distributor1Member",
    "ResellerBMember", "OneBottlerMember", "USFederalGovernmentMember",
    "CardinalHealthIncMember", "DellIncMember", "TMobileMember",
    "UndisclosedCustomerMember", "PipelineCompanyMember",
]

UN_GRUPO = [
    # El que rompia NVDA: 0,76 para un grupo geografico. Su mayor cliente
    # real es 22%, asi que puntuar 0,76 disparaba el CONCENTRATION_RED_FLAG
    # de >30% sobre una concentracion que no existe.
    "UnitedStatesAndEuropeBasedEndCustomersMember",
    # El que rompia LLY: tres mayoristas sumados leidos como un cliente.
    "ThreeLargestWholesalersMember",
    "ThreeLargestCustomersMember", "FiveLargestCustomersMember",
    "TenLargestCustomersMember", "TopTenCustomersMember",
    "ThreeCustomersMember", "FourCustomersMember", "WholesaleCustomersMember",
    "ManufacturingCustomersMember", "U.S.GovernmentAgenciesMember",
    "GroupPurchasingOrganizationsMember", "AEPSubsidiariesMember",
    # Un subtotal, no un cliente: es el 100% de la base.
    "TotalAsPercentageOfTotalRevenueMember",
    # El residuo, tampoco.
    "OtherCustomersMember", "OtherCustomerMember",
]


@pytest.mark.parametrize("miembro", UN_CLIENTE)
def test_un_cliente_solo_se_acepta(miembro):
    assert EdgarProvider._miembro_es_un_cliente(miembro) is True


@pytest.mark.parametrize("miembro", UN_GRUPO)
def test_un_agregado_se_rechaza(miembro):
    assert EdgarProvider._miembro_es_un_cliente(miembro) is False


def test_la_regla_salio_del_vocabulario_real_no_de_tres_ejemplos():
    """82 nombres distintos en 66 emisores de las 260 mayores del mercado, y
    el discriminante que aparecio ahi es el PLURAL: "Customer" es uno,
    "Customers" son varios. Este test fija que la lista cubre las dos
    familias con volumen suficiente para que la regla no este ajustada a un
    puñado de casos."""
    assert len(UN_CLIENTE) >= 15
    assert len(UN_GRUPO) >= 15


def test_el_benchmark_de_INGRESOS_no_incluye_cuentas_por_cobrar():
    """FORMULAS.md define BUS-CONC-003 sobre INGRESOS. El mismo emisor
    publica las dos bases: Palantir declara 25% sobre cuentas por cobrar y
    nada sobre ingresos, y confundirlas pondria una concentracion de clientes
    donde la empresa no declaro ninguna."""
    b = EdgarProvider._BENCHMARK_INGRESOS
    assert "AccountsReceivableBenchmarkMember" not in b
    assert "SalesRevenueNetMember" in b
    assert "RevenueFromContractWithCustomerExcludingAssessedTaxMember" in b


# ============================================================================
# Doble señal: sin etiqueta Y sin desmentido en el texto
# ============================================================================

_NIEGAN_SOBRE_INGRESOS = [
    "For the years ended December 31, 2025, no customer represented 10% or "
    "more of total revenue.",
    "No bottlers or customers represented 10% or more of our net operating "
    "revenues for the years ended December 31, 2024 and 2023.",
    "No sales to an individual customer or country other than the United "
    "States accounted for more than 10% of revenue.",
]

_NO_NIEGAN = [
    # El que rompia Eli Lilly: "no OTHER" presupone que ya hay uno que si
    # llega -- y sus tres mayores mayoristas estan al 24%. Leerlo como
    # negacion invierte el hecho.
    "No other customer accounted for more than 10 percent of our "
    "consolidated revenue in any of these years.",
    "No additional customer exceeded 10% of revenues.",
]


@pytest.mark.parametrize("frase", _NIEGAN_SOBRE_INGRESOS)
def test_una_negacion_expresa_sobre_ingresos_se_reconoce(frase):
    from wbj.providers.edgar import EdgarProvider as E

    assert E._NIEGA.search(frase)
    assert E._NIEGA_PARCIAL.search(frase) is None
    assert E._BASE_INGRESOS.search(frase)


@pytest.mark.parametrize("frase", _NO_NIEGAN)
def test_no_OTHER_customer_no_es_una_negacion(frase):
    from wbj.providers.edgar import EdgarProvider as E

    assert E._NIEGA_PARCIAL.search(frase), (
        "'no other' presupone un cliente que SI llega: tratarlo como negacion "
        "convierte una concentracion declarada en su contrario")


def test_una_frase_sobre_cuentas_por_cobrar_no_habla_de_ingresos():
    """Apple: "one customer that represented 10% or more of total trade
    receivables". No confirma ni desmiente nada sobre la base de BUS-CONC-003.
    """
    from wbj.providers.edgar import EdgarProvider as E

    frase = ("As of September 27, 2025, the Company had one customer that "
             "represented 10% or more of total trade receivables.")
    assert E._BASE_COBRAR.search(frase)
    assert E._BASE_INGRESOS.search(frase) is None


def test_la_cita_empieza_en_la_negacion_no_en_la_tabla_vecina():
    """El texto del 10-K llega sin puntos entre celdas, asi que la "oracion
    anterior" puede ser media cuenta de resultados. Una cita ilegible en el
    reporte vale menos que ninguna."""
    from wbj.providers.edgar import EdgarProvider as E

    sucio = ("331,839 $ 281,724 $ 245,122 Cost of revenue 106,374 87,831 "
             "No sales to an individual customer accounted for more than 10%.")
    assert E._recortar(sucio).startswith("No sales to an individual customer")


def test_la_cota_del_HHI_se_demuestra_no_se_estima():
    """Si toda cuota <= u y las cuotas suman 1, entonces
    HHI = sum(s^2) <= u * sum(s) = u. Con u = 0,10 el HHI no pasa de 0,10.
    """
    import random

    for _ in range(200):
        n = random.randint(10, 60)
        cuotas = [random.random() for _ in range(n)]
        total = sum(cuotas)
        cuotas = [c / total for c in cuotas]
        if max(cuotas) > 0.10:            # el supuesto de la cota
            continue
        assert sum(c * c for c in cuotas) <= 0.10 + 1e-12
