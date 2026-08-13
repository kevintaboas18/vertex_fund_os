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
