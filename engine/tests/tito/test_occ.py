"""Port de `web/lib/occ.test.ts` (6 casos)."""

from __future__ import annotations

from datetime import datetime, timezone

from wbj.tito.occ import OccInfo, days_to_expiration, parse_occ


def _utc(s: str) -> datetime:
    return datetime.fromisoformat(s).replace(tzinfo=timezone.utc)


class TestParseOcc:
    def test_parsea_un_put_occ_real(self):
        assert parse_occ("TSLA261120P00305000") == OccInfo(
            underlying="TSLA", expiration="2026-11-20", type="put", strike=305
        )

    def test_parsea_root_de_cuatro_o_mas_letras(self):
        assert parse_occ("SPXW260723P07400000") == OccInfo(
            underlying="SPXW", expiration="2026-07-23", type="put", strike=7400
        )

    def test_parsea_strike_con_decimales(self):
        info = parse_occ("TSLA260724P00352500")
        assert info is not None
        assert info.strike == 352.5

    def test_devuelve_none_para_simbolos_invalidos(self):
        assert parse_occ("") is None
        assert parse_occ("AAPL") is None
        assert parse_occ("TSLA261120X00305000") is None  # tipo invalido


class TestDaysToExpiration:
    def test_cuenta_dias_hasta_el_vencimiento(self):
        now = _utc("2026-07-22T15:00:00")  # 11:00 ET del 22
        assert days_to_expiration("2026-07-23", now) == 1
        assert days_to_expiration("2026-11-20", now) == 121
        assert days_to_expiration("2026-07-22", now) == 0

    def test_usa_el_dia_del_mercado_no_el_utc(self):
        # 01:00 UTC del 24 = todavia 21:00 ET del 23 -> el 24 vence "manana".
        noche_et = _utc("2026-07-24T01:00:00")
        assert days_to_expiration("2026-07-24", noche_et) == 1
        assert days_to_expiration("2026-07-23", noche_et) == 0
        assert days_to_expiration("2026-07-22", noche_et) == -1

    def test_fecha_no_parseable_devuelve_none(self):
        # Divergencia deliberada vs TS (que devolveria NaN): None es explicito y
        # el llamador no puede confundirlo con "vence hoy".
        assert days_to_expiration("no-es-fecha", _utc("2026-07-22T15:00:00")) is None
