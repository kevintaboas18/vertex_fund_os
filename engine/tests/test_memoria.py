"""Tests for the prediction-tracking memory loop."""

import json
from datetime import date

import pytest

from wbj.memoria import evaluate, load_predictions, save_prediction, track

TARGETS = {
    "status": "ok", "price": 100.0, "growth_base": 0.15, "pe_now": 25.0,
    "horizon": "12 meses",
    "scenarios": [
        {"key": "bear", "target": 80.0, "upside": -0.20, "assumptions": "x"},
        {"key": "base", "target": 120.0, "upside": 0.20, "assumptions": "x"},
        {"key": "bull", "target": 150.0, "upside": 0.50, "assumptions": "x"},
    ],
}
SCORECARD = {"overall_10": 7.5, "evidence_points_covered": 50}


def test_save_and_load_roundtrip(tmp_path):
    p = save_prediction(tmp_path, "nvda", date(2026, 1, 15), SCORECARD, TARGETS)
    assert p is not None and p.name == "prediccion.json"
    preds = load_predictions(tmp_path)
    assert len(preds) == 1
    assert preds[0]["ticker"] == "NVDA" and preds[0]["base"] == 120.0
    assert preds[0]["score10"] == 7.5


def test_save_skips_not_scorable(tmp_path):
    assert save_prediction(tmp_path, "X", date(2026, 1, 1), SCORECARD,
                           {"status": "not_scorable"}) is None


def test_evaluate_in_progress_deviation():
    pred = {"ticker": "T", "date": "2026-01-01", "price": 100.0,
            "bear": 80.0, "base": 120.0, "bull": 150.0, "horizon_days": 365}
    # ~half the horizon elapsed; base implies +20%/yr -> prorated ~ +10%
    r = evaluate(pred, price_now=115.0, today=date(2026, 7, 2))
    assert r["outcome"] == "en_curso" and not r["mature"]
    assert abs(r["base_prorated"] - 0.20 * (182 / 365)) < 1e-3
    assert abs(r["deviation"] - (0.15 - 0.20 * 182 / 365)) < 1e-3


def test_evaluate_mature_outcomes():
    """A matured prediction is judged at its maturity price, not today's."""
    pred = {"ticker": "T", "date": "2025-01-01", "price": 100.0,
            "bear": 80.0, "base": 120.0, "bull": 150.0, "horizon_days": 365}
    today = date(2026, 7, 1)
    # price_now is whatever it is today; the verdict follows the maturity price.
    assert evaluate(pred, 999.0, today, price_at_maturity=130.0)["outcome"] == "en_rango"
    assert evaluate(pred, 1.0, today, price_at_maturity=160.0)["outcome"] == "supero_bull"
    assert evaluate(pred, 999.0, today, price_at_maturity=70.0)["outcome"] == "rompio_bear"


def test_a_matured_prediction_is_not_judged_by_a_later_price():
    """BACKTESTING_AND_CALIBRATION.md's anti-bias rules forbid look-ahead.
    A 12-month call that landed inside its range on schedule was being
    recorded as "superó Bull" purely because the stock kept climbing for two
    more years after the horizon closed."""
    pred = {"ticker": "T", "date": "2023-07-24", "price": 100.0,
            "bear": 80.0, "base": 110.0, "bull": 130.0, "horizon_days": 365}
    r = evaluate(pred, price_now=200.0, today=date(2026, 7, 24),
                 price_at_maturity=115.0)
    assert r["outcome"] == "en_rango"      # 115 was inside [80, 130] on the day
    assert r["judged_price"] == 115.0
    assert r["judged_on"] == "2024-07-23"


def test_a_maturity_price_we_cannot_get_withholds_the_verdict():
    """Deciding on the wrong day is worse than not deciding."""
    pred = {"ticker": "T", "date": "2023-07-24", "price": 100.0,
            "bear": 80.0, "base": 110.0, "bull": 130.0, "horizon_days": 365}
    r = evaluate(pred, price_now=200.0, today=date(2026, 7, 24))
    assert r["outcome"] == "sin_precio_al_vencimiento"
    assert r["judged_price"] is None


def test_track_writes_calibracion(tmp_path):
    reports, memoria = tmp_path / "Reportes", tmp_path / "Memoria"
    save_prediction(reports, "AAA", date(2025, 1, 1), SCORECARD, TARGETS)
    save_prediction(reports, "BBB", date(2026, 6, 1), SCORECARD, TARGETS)
    prices = {"AAA": 130.0, "BBB": 105.0}

    def price_fn(t, on=None):
        # Same close today and at maturity, so the verdict is unambiguous.
        return prices.get(t)

    s = track(reports, memoria, price_fn, today=date(2026, 7, 17))
    assert s["total"] == 2 and s["maduras"] == 1
    assert s["hit_rate"] == 1.0  # AAA at 130 is inside [80, 150]
    text = (memoria / "calibracion.md").read_text(encoding="utf-8")
    assert "AAA" in text and "en rango" in text and "en curso" in text


def test_a_one_argument_price_fn_withholds_matured_verdicts(tmp_path):
    """The older injected shape has no history to give, so a matured
    prediction cannot be judged on its own date -- and is not judged at all
    rather than judged on today's price."""
    reports, memoria = tmp_path / "Reportes", tmp_path / "Memoria"
    save_prediction(reports, "AAA", date(2025, 1, 1), SCORECARD, TARGETS)
    s = track(reports, memoria, lambda t: 130.0, today=date(2026, 7, 17))
    assert s["maduras"] == 1
    assert s["juzgadas"] == 0
    assert s["hit_rate"] is None


def test_a_ticker_without_a_price_stays_in_the_sample(tmp_path):
    """BACKTESTING_AND_CALIBRATION.md's anti-bias rules: "no survivorship
    bias" and "delisted securities remain in the sample". Dropping the row
    was survivorship bias in its purest form -- a name that went to zero, was
    acquired, or stopped trading has no quote, and those are precisely the
    predictions that went worst. Deleting them flatters the record."""
    reports, memoria = tmp_path / "Reportes", tmp_path / "Memoria"
    save_prediction(reports, "AAA", date(2026, 1, 1), SCORECARD, TARGETS)
    s = track(reports, memoria, lambda t: None, today=date(2026, 7, 17))
    assert s["total"] == 1           # kept, not deleted
    assert s["sin_medir"] == 1       # and visibly unmeasured
    assert s["hit_rate"] is None
    assert s["rows"][0]["outcome"] == "sin_precio_actual"
    text = (memoria / "calibracion.md").read_text(encoding="utf-8")
    assert "AAA" in text             # visible to a reader


def test_a_delisted_name_does_not_drag_the_bias(tmp_path):
    """It stays in the sample, but an unmeasurable row cannot be averaged
    into a bias -- it is reported as unmeasured instead."""
    reports, memoria = tmp_path / "Reportes", tmp_path / "Memoria"
    save_prediction(reports, "LIVE", date(2026, 6, 1), SCORECARD, TARGETS)
    save_prediction(reports, "GONE", date(2026, 6, 1), SCORECARD, TARGETS)
    s = track(reports, memoria,
              lambda t, on=None: 105.0 if t == "LIVE" else None,
              today=date(2026, 7, 1))
    assert s["total"] == 2 and s["sin_medir"] == 1
    assert {r["ticker"] for r in s["rows"]} == {"LIVE", "GONE"}
    # The bias reflects only LIVE, the one row with a measurable outcome.
    assert s["sesgo_medio"] is not None


def test_load_ignores_malformed(tmp_path):
    d = tmp_path / "XXX" / "2026-01-01"
    d.mkdir(parents=True)
    (d / "prediccion.json").write_text("{corrupted")
    assert load_predictions(tmp_path) == []


# ============================================================================
# Two analyses, two track records
# ============================================================================


def test_quick_and_cerebro_predictions_do_not_overwrite_each_other():
    """The quick scorecard and the strict Cerebro profile score the same
    company very differently (NVDA on the same day: 8.0/10 quick vs 4.74/10
    strict). Both wrote `prediccion.json`, so whichever ran second erased the
    other and the calibration measured a blend of two systems."""
    import tempfile
    from pathlib import Path

    from wbj.memoria import (SOURCE_CEREBRO, SOURCE_QUICK, load_predictions,
                             save_prediction)

    with tempfile.TemporaryDirectory() as tmp:
        reports = Path(tmp)
        save_prediction(reports, "AAA", date(2026, 1, 1), SCORECARD, TARGETS,
                        source=SOURCE_QUICK)
        save_prediction(reports, "AAA", date(2026, 1, 1), SCORECARD, TARGETS,
                        source=SOURCE_CEREBRO)
        preds = load_predictions(reports)
        assert len(preds) == 2
        assert {p["source"] for p in preds} == {SOURCE_QUICK, SOURCE_CEREBRO}


def test_predictions_written_before_the_split_are_read_as_quick():
    """Every prediction already on disk came from the quick scorecard."""
    import json
    import tempfile
    from pathlib import Path

    from wbj.memoria import SOURCE_QUICK, load_predictions

    with tempfile.TemporaryDirectory() as tmp:
        d = Path(tmp) / "AAA" / "2026-01-01"
        d.mkdir(parents=True)
        (d / "prediccion.json").write_text(json.dumps({
            "ticker": "AAA", "date": "2026-01-01", "price": 100.0,
            "bear": 80.0, "base": 120.0, "bull": 150.0,
        }), encoding="utf-8")
        preds = load_predictions(Path(tmp))
        assert len(preds) == 1
        assert preds[0]["source"] == SOURCE_QUICK


def test_the_report_pipeline_records_its_own_prediction():
    """`wbj report` is the strict Cerebro path; it recorded nothing, so the
    only thing being calibrated was the quick scorecard.

    Reaching the call site for real costs a network packet and six
    specialist outputs, so this reads what the compiled function
    references instead of its text: `co_names` survives reformatting,
    comment edits and reordering, and drops the name only when the
    pipeline genuinely stops recording. What gets recorded, and that the
    two sources stay apart, is covered by the round-trip tests above.
    """
    from wbj import report

    referenced = report.run_report.__code__.co_names
    assert "save_prediction" in referenced
    assert "SOURCE_CEREBRO" in referenced


# ============================================================================
# Re-audit: the deviation must be measured over the same window as the verdict
# ============================================================================


def test_the_deviation_is_measured_at_maturity_not_today():
    """Fixing the verdict was not enough. `deviation` still compared the
    return to TODAY against an expectation pro-rated to the horizon, so a
    prediction three years past maturity reported a +90% deviation when the
    real one at maturity was +5% -- and that number is `sesgo_medio`, which
    CLAUDE.md's ±10% rule reads."""
    pred = {"ticker": "T", "date": "2023-07-24", "price": 100.0,
            "bear": 80.0, "base": 110.0, "bull": 130.0, "horizon_days": 365}
    r = evaluate(pred, price_now=200.0, today=date(2026, 7, 24),
                 price_at_maturity=115.0)
    assert r["change"] == pytest.approx(0.15)          # 115/100 at maturity
    assert r["base_prorated"] == pytest.approx(0.10)   # full base return
    assert r["deviation"] == pytest.approx(0.05)       # not +0.90


def test_an_in_flight_prediction_still_prorates_against_today():
    pred = {"ticker": "T", "date": "2026-01-01", "price": 100.0,
            "bear": 80.0, "base": 120.0, "bull": 150.0, "horizon_days": 365}
    r = evaluate(pred, price_now=105.0, today=date(2026, 7, 1))
    assert r["outcome"] == "en_curso"
    assert r["change"] == pytest.approx(0.05)
    assert 0 < r["base_prorated"] < 0.20   # part of the way to +20%


def test_the_prorating_respects_the_predictions_own_horizon():
    """`min(days, MATURITY_DAYS)/MATURITY_DAYS` ignored a record's own
    `horizon_days`, so a shorter-horizon prediction was pro-rated against
    the module default."""
    pred = {"ticker": "T", "date": "2026-01-01", "price": 100.0,
            "bear": 80.0, "base": 120.0, "bull": 150.0, "horizon_days": 180}
    r = evaluate(pred, price_now=100.0, today=date(2026, 3, 31))  # 89 of 180 days
    assert r["base_prorated"] == pytest.approx(0.20 * 89 / 180, abs=1e-4)


def test_a_zero_base_price_does_not_manufacture_an_infinity():
    pred = {"ticker": "T", "date": "2026-01-01", "price": 0.0,
            "bear": 80.0, "base": 120.0, "bull": 150.0}
    r = evaluate(pred, price_now=100.0, today=date(2026, 7, 1))
    assert r["outcome"] == "sin_precio_base"
    assert r["deviation"] is None


def test_bias_is_reported_per_source_not_blended(tmp_path):
    """Separating the files was not enough: `track` still averaged both
    systems into one `sesgo_medio`, which is the very thing that made a
    blended file wrong."""
    from wbj.memoria import SOURCE_CEREBRO, SOURCE_QUICK

    reports, memoria = tmp_path / "Reportes", tmp_path / "Memoria"
    save_prediction(reports, "AAA", date(2026, 6, 1), SCORECARD, TARGETS,
                    source=SOURCE_QUICK)
    save_prediction(reports, "AAA", date(2026, 6, 1), SCORECARD, TARGETS,
                    source=SOURCE_CEREBRO)
    s = track(reports, memoria, lambda t: 105.0, today=date(2026, 7, 1))
    by_source = s["sesgo_por_fuente"]
    assert set(by_source) == {SOURCE_QUICK, SOURCE_CEREBRO}
    assert by_source[SOURCE_QUICK]["total"] == 1
    assert by_source[SOURCE_CEREBRO]["total"] == 1


def test_calibration_bias_reads_only_its_own_systems_record(tmp_path):
    """A strict report must not declare the quick scorecard's bias as its
    own."""
    import json

    from wbj.memoria import SOURCE_CEREBRO, SOURCE_QUICK, calibration_bias

    memoria = tmp_path / "Memoria"
    memoria.mkdir()
    (memoria / "calibracion.json").write_text(json.dumps({
        "sesgo_medio": -0.30, "total": 40,
        "sesgo_por_fuente": {
            SOURCE_QUICK: {"sesgo_medio": -0.30, "total": 39},
            SOURCE_CEREBRO: {"sesgo_medio": -0.01, "total": 1},
        },
    }), encoding="utf-8")

    quick = calibration_bias(memoria, source=SOURCE_QUICK)
    assert quick["material"] is True            # -30% over 39 predictions
    cerebro = calibration_bias(memoria, source=SOURCE_CEREBRO)
    assert cerebro["material"] is False         # one prediction is not a record
    assert calibration_bias(memoria, source="nonexistent") is None


def test_a_withheld_verdict_withholds_its_deviation_too():
    """Third pass: the verdict was being withheld for want of the maturity
    close while the deviation quietly fell back to today's price -- putting
    the very look-ahead the verdict refuses straight into `sesgo_medio`."""
    pred = {"ticker": "T", "date": "2023-07-24", "price": 100.0,
            "bear": 80.0, "base": 110.0, "bull": 130.0, "horizon_days": 365}
    r = evaluate(pred, price_now=200.0, today=date(2026, 7, 24))
    assert r["outcome"] == "sin_precio_al_vencimiento"
    assert r["deviation"] is None
    assert r["change"] is None


def test_unjudgeable_rows_are_excluded_from_the_bias(tmp_path):
    reports, memoria = tmp_path / "Reportes", tmp_path / "Memoria"
    save_prediction(reports, "AAA", date(2024, 1, 1), SCORECARD, TARGETS)  # matured
    save_prediction(reports, "BBB", date(2026, 6, 1), SCORECARD, TARGETS)  # in flight
    # One-argument price_fn: no history, so AAA cannot be judged.
    s = track(reports, memoria, lambda t: 130.0, today=date(2026, 7, 1))
    assert s["maduras"] == 1 and s["juzgadas"] == 0
    # The bias reflects only the in-flight row that could be measured.
    assert s["sesgo_medio"] is not None
    assert all(r["deviation"] is not None
               for r in s["rows"] if r["outcome"] != "sin_precio_al_vencimiento")


def test_the_markdown_shows_the_bias_per_system(tmp_path):
    """A reader of calibracion.md was shown one blended number for two
    systems that are not comparable."""
    from wbj.memoria import SOURCE_CEREBRO, SOURCE_QUICK

    reports, memoria = tmp_path / "Reportes", tmp_path / "Memoria"
    save_prediction(reports, "AAA", date(2026, 6, 1), SCORECARD, TARGETS,
                    source=SOURCE_QUICK)
    save_prediction(reports, "AAA", date(2026, 6, 1), SCORECARD, TARGETS,
                    source=SOURCE_CEREBRO)
    track(reports, memoria, lambda t: 105.0, today=date(2026, 7, 1))
    text = (memoria / "calibracion.md").read_text(encoding="utf-8")
    assert "Sesgo por sistema" in text
    assert "`quick`" in text and "`cerebro`" in text


# ============================================================================
# Fourth pass: one corrupt file must not take the whole run down
# ============================================================================


def test_a_corrupt_record_does_not_kill_the_calibration_run(tmp_path):
    """`load_predictions` promises to ignore malformed files, but checked
    only that keys were PRESENT. A record with an unparseable `date` passed
    the filter and then raised out of `date.fromisoformat` inside `track`, so
    one bad file took every other prediction down with it."""
    reports, memoria = tmp_path / "Reportes", tmp_path / "Memoria"
    save_prediction(reports, "GOOD", date(2026, 6, 1), SCORECARD, TARGETS)
    bad = reports / "BAD" / "2026-01-01"
    bad.mkdir(parents=True)
    (bad / "prediccion.json").write_text(json.dumps({
        "ticker": "BAD", "date": "no-es-fecha", "price": 100.0,
        "bear": 80.0, "base": 120.0, "bull": 150.0,
    }), encoding="utf-8")

    s = track(reports, memoria, lambda t, on=None: 105.0, today=date(2026, 7, 1))
    assert s["total"] == 1                       # the good one survives
    assert s["rows"][0]["ticker"] == "GOOD"


@pytest.mark.parametrize("broken", [
    {"date": "not-a-date"},
    {"price": "cien"},
    {"bear": None},
    {"horizon_days": 0},
    {"horizon_days": "un año"},
])
def test_unusable_records_are_dropped(tmp_path, broken):
    rec = {"ticker": "X", "date": "2026-01-01", "price": 100.0,
           "bear": 80.0, "base": 120.0, "bull": 150.0}
    rec.update(broken)
    d = tmp_path / "X" / "2026-01-01"
    d.mkdir(parents=True)
    (d / "prediccion.json").write_text(json.dumps(rec), encoding="utf-8")
    assert load_predictions(tmp_path) == []
