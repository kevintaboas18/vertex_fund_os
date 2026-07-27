"""The input file must be auditable against Victor's `DATASET.md` contract.

`CLAUDE.md` step 2 validates the packet against the shared layer and each
specialist's dataset contract. The engine grew its own overlay vocabulary and
only two keys ever matched what Victor wrote:

    Victor                              engine
    customer_revenue_shares             customer_shares
    management_guidance_history         guidance_history
    industry_revenue_history            tam_history
    company_relevant_revenue            company_relevant_revenue   (matched)
    debt_schedule                       debt_schedule              (matched)

Someone comparing `Entradas/<TICKER>.json` against `DATASET.md` could not map
the fields. Two things fix that without inventing names Victor never wrote:
every key declares its parent field, and the three exact correspondences are
accepted under Victor's own name.

The rest are *elements* of a bundled field — `tam_sam_som_sources` is "market
size, scope, geography, product definition, and forecast", not one number — so
they keep their key and declare the parent. Renaming those 1:1 would collapse
distinct inputs or invent sub-names, which is the error this guards against.
"""

import json
import re
from pathlib import Path

from wbj.entradas import (SKELETON_KEYS, VICTOR_DATASET_FIELD,
                          VICTOR_FIELD_ALIASES, render_skeleton, write_skeleton)
from wbj.overlay.from_packet import _manual_overlay

_CEREBRO = Path(__file__).parent.parent.parent / "Cerebro"


class _S:
    def __init__(self, path):
        self.inputs_dir = path


def _victor_fields() -> set[str]:
    """Every field name in every specialist's DATASET.md table."""
    found: set[str] = set()
    for path in _CEREBRO.glob("*/DATASET.md"):
        for line in path.read_text(encoding="utf-8").splitlines():
            m = re.match(r"\|\s*([a-z][a-z0-9_]+)\s*\|", line)
            if m:
                found.add(m.group(1))
    return found


# --- the mapping points at fields Victor actually wrote ---------------------

def _formula_ids() -> set[str]:
    """Every formula id in the valuation registry."""
    text = (_CEREBRO / "06_valuation_analysis" / "FORMULAS.md").read_text(encoding="utf-8")
    return set(re.findall(r"VAL-[A-Z]+-\d+", text))


def test_every_declared_field_exists_in_a_dataset_contract():
    """A citation pointing at something Victor never wrote is worse than none:
    it looks authoritative and sends the auditor hunting.

    Four keys have no DATASET.md row and cite FORMULAS.md's "inputs" column
    instead, which is where Victor does name them. Citing a stretched dataset
    field for those would be the invention this guards against —
    `forecast_drivers` is "units/users, pricing, share, revenue, margins,
    reinvestment, ROIC, taxes" and contains none of them.
    """
    victor, formulas = _victor_fields(), _formula_ids()
    assert victor, "no DATASET.md tables were read"
    for key, declared in VICTOR_DATASET_FIELD.items():
        if declared.startswith("FORMULAS.md "):
            fid = declared.split()[1]
            assert fid in formulas, f"{key} -> '{fid}' no existe en FORMULAS.md"
            continue
        field = declared.split(" (")[0]
        assert field in victor, f"{key} -> '{field}' no existe en ningun DATASET.md"


def test_the_formula_citations_quote_victors_own_input_list():
    """The parenthetical must be his wording, not a paraphrase that drifts."""
    text = (_CEREBRO / "06_valuation_analysis" / "FORMULAS.md").read_text(encoding="utf-8")
    for key, declared in VICTOR_DATASET_FIELD.items():
        if not declared.startswith("FORMULAS.md "):
            continue
        row = next(l for l in text.splitlines() if declared.split()[1] in l)
        # `| id | name | formula | inputs | period | caveat |` -> leading empty
        # cell makes `inputs` index 4. Index 5 is the period column, which is
        # where this first pointed.
        inputs = row.split("|")[4].strip().lower()
        cited = declared[declared.index("(") + 1:declared.rindex(")")].lower()
        for token in cited.split(", "):
            assert token in inputs, f"{key}: '{token}' no esta en los inputs de Victor"


def test_every_alias_is_a_real_dataset_field():
    victor = _victor_fields()
    for victor_name in VICTOR_FIELD_ALIASES:
        assert victor_name in victor, f"'{victor_name}' no es un campo de Victor"


def test_every_alias_target_is_a_key_the_engine_reads():
    for engine_key in VICTOR_FIELD_ALIASES.values():
        assert engine_key in SKELETON_KEYS


def test_every_skeleton_key_declares_its_field():
    """No key may sit in the file without saying which contract row it serves."""
    undeclared = set(SKELETON_KEYS) - set(VICTOR_DATASET_FIELD)
    assert not undeclared, f"sin campo declarado: {sorted(undeclared)}"


def test_the_two_keys_that_already_matched_are_recorded_as_themselves():
    """`company_relevant_revenue` and `debt_schedule` are Victor's own names.
    The mapping must not quietly rename what was already right."""
    assert VICTOR_DATASET_FIELD["company_relevant_revenue"].startswith(
        "company_relevant_revenue")
    assert VICTOR_DATASET_FIELD["debt_schedule"].startswith("debt_schedule")


# --- Victor's vocabulary works in the file ----------------------------------

def test_a_file_written_in_victors_names_is_honoured(tmp_path):
    """The point of the whole change: his contract's names must work."""
    (tmp_path / "TEST.json").write_text(json.dumps({
        "management_guidance_history": [{"period": "2025Q4",
                                         "guidance_midpoint": 100, "actual": 110}],
        "customer_revenue_shares": [0.22, 0.14],
        "industry_revenue_history": [1, 2, 3],
    }), encoding="utf-8")

    out = _manual_overlay(_S(tmp_path), "TEST")
    assert out["guidance_history"][0]["actual"] == 110
    assert out["customer_shares"] == [0.22, 0.14]
    assert out["tam_history"] == [1, 2, 3]


def test_the_engine_key_still_works_so_captured_files_keep_loading(tmp_path):
    """`Entradas/NVDA.json` was written in the engine's vocabulary. Adding
    Victor's names must not strand it."""
    (tmp_path / "NVDA.json").write_text(json.dumps({
        "customer_shares": [0.22, 0.14], "guidance_history": [{"period": "x"}],
    }), encoding="utf-8")

    out = _manual_overlay(_S(tmp_path), "NVDA")
    assert out["customer_shares"] == [0.22, 0.14]
    assert out["guidance_history"] == [{"period": "x"}]


def test_a_collision_keeps_the_engine_key_and_does_not_lose_it(tmp_path):
    """Both names set is analyst error. Overwriting the key every consumer
    reads would be worse than ignoring the duplicate."""
    (tmp_path / "TEST.json").write_text(json.dumps({
        "customer_shares": [0.30], "customer_revenue_shares": [0.99],
    }), encoding="utf-8")

    assert _manual_overlay(_S(tmp_path), "TEST")["customer_shares"] == [0.30]


def test_a_null_under_victors_name_does_not_overwrite_a_real_value(tmp_path):
    """The skeleton ships every key null. A null alias must stay inert."""
    (tmp_path / "TEST.json").write_text(json.dumps({
        "customer_shares": [0.30], "customer_revenue_shares": None,
    }), encoding="utf-8")

    assert _manual_overlay(_S(tmp_path), "TEST")["customer_shares"] == [0.30]


# --- the skeleton says so ---------------------------------------------------

def test_the_skeleton_names_the_dataset_field_for_each_key():
    text = render_skeleton("TEST")
    for key, declared in VICTOR_DATASET_FIELD.items():
        where = declared if declared.startswith("FORMULAS.md") else f"DATASET.md {declared}"
        assert f"`{key}` -> {where}" in text


def test_the_skeleton_still_loads_to_nothing_but_nulls(tmp_path):
    """Annotation must not have made the file score."""
    write_skeleton(tmp_path, "TEST")
    out = _manual_overlay(_S(tmp_path), "TEST")
    assert all(v is None for k, v in out.items() if k != "judgments")
    assert not any(k.startswith("_") for k in out)
