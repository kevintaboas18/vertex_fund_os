"""Un estado financiero es INMUTABLE; lo que caduca es saber si hay uno nuevo.

Con un solo `_MAX_AGE_STATEMENT` de 30 dias para todo, una empresa presentaba
su Q2 y el motor seguia sirviendo el Q1 durante un mes -- justo en temporada
de resultados.

Medido el 2026-08-13 contra FMP en vivo: tenia el Q2 de AMD (2026-06-27),
PLTR y JPM (2026-06-30), y el packet entregaba el trimestre anterior. AMD a
138 dias, PLTR y JPM a 135, por encima del limite de 120 que
`DATA_POLICY.md` fija para marcar el packet financiero rancio.

Tras separar las vidas: AMD 47, PLTR 44, JPM 44.
"""
from wbj.providers import fmp


def test_el_trimestral_caduca_en_un_dia():
    """Aparece uno nuevo cada ~90 dias y hay que verlo el dia que sale."""
    assert fmp._MAX_AGE_STATEMENT_QUARTER == 1


def test_el_anual_puede_esperar_una_semana():
    """Aparece uno al ano; una semana sobre ese ritmo no mueve una metrica."""
    assert fmp._MAX_AGE_STATEMENT_ANNUAL == 7
    assert fmp._MAX_AGE_STATEMENT_ANNUAL > fmp._MAX_AGE_STATEMENT_QUARTER


def test_ningun_estado_se_cachea_ya_un_mes():
    """El valor de 30 dias es el que causaba el trimestre perdido."""
    import inspect

    src = inspect.getsource(fmp)
    for k in ("income_quarterly", "balance_quarterly", "cashflow_quarterly"):
        i = src.index(f'"{k}"')
        assert "_MAX_AGE_STATEMENT_QUARTER" in src[i:i + 140], k
    for k in ("income_annual", "balance_annual", "cashflow_annual"):
        i = src.index(f'"{k}"')
        assert "_MAX_AGE_STATEMENT_ANNUAL" in src[i:i + 140], k


def test_la_vida_del_trimestral_respeta_el_limite_del_Cerebro():
    """`DATA_POLICY.md`: "Quarterly fundamentals | Next required filing or 120
    days". Una cache de 30 dias podia sumar hasta 30 al retraso natural del
    calendario de presentacion y cruzar ese limite; una de 1 no."""
    assert fmp._MAX_AGE_STATEMENT_QUARTER <= 1
