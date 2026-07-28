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
        if field.startswith("FRED "):
            # A legitimate third route: business.py records `recession_years`
            # as "FRED USREC (NBER recession indicator)" because the NBER
            # recession calendar BUS-STAB-009 needs is not a company field at
            # all. Naming the provider is more honest than forcing it into a
            # DATASET.md row it does not belong to.
            continue
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
    # Only the keys the skeleton actually offers. The mapping is wider: it
    # merges business.py's lineage, which also covers overlay keys the engine
    # computes for itself (`wacc`, `peer_roic`, ...) and no analyst fills.
    for key in SKELETON_KEYS:
        declared = VICTOR_DATASET_FIELD[key]
        where = declared if declared.startswith("FORMULAS.md") else f"DATASET.md {declared}"
        assert f"`{key}` -> {where}" in text


def test_the_skeleton_still_loads_to_nothing_but_nulls(tmp_path):
    """Annotation must not have made the file score."""
    write_skeleton(tmp_path, "TEST")
    out = _manual_overlay(_S(tmp_path), "TEST")
    assert all(v is None for k, v in out.items() if k != "judgments")
    assert not any(k.startswith("_") for k in out)


# --- one table, and it points where Victor puts the figure -----------------

def test_there_is_only_one_lineage_table_and_it_does_not_contradict_itself():
    """`business.py` keeps `_OVERLAY_LINEAGE` for the keys it reads, and this
    module once kept a parallel table that disagreed with it on
    `tam_source_tier`. Two tables disagreeing about one key is worse than
    either answer: whichever an auditor opens, they cannot tell if it is the
    one the engine believes."""
    from wbj.specialists.business import _OVERLAY_LINEAGE

    for key, field in _OVERLAY_LINEAGE.items():
        assert VICTOR_DATASET_FIELD[key].startswith(field), (
            f"{key}: business.py dice '{field}', entradas.py dice "
            f"'{VICTOR_DATASET_FIELD[key]}'"
        )


def test_the_tam_source_tier_is_filed_under_the_tam():
    """Victor defines the tiers in `03_market_analysis/DECISION_RULES.md`
    under "Source-quality tiers for TAM", and his disclosure rule reads
    "1. exact TAM definition and source tier". The graded field is
    `tam_sam_som_sources`.

    The lineage previously named `market_share_company_industry_3y` — what the
    tier *affects* in business (SCORING.md: "Cannot score above 8 if market
    definition is low confidence"), not where the figure comes from. A lineage
    table records provenance."""
    assert VICTOR_DATASET_FIELD["tam_source_tier"].startswith("tam_sam_som_sources")


def test_each_field_is_labelled_with_the_document_that_declares_it():
    """The parenthetical must name the DATASET.md that actually holds the row.
    Tagging every business-read key `01_business_analysis` sent the auditor to
    a table without the row: `tam_sam_som_sources` is declared in
    `03_market_analysis`."""
    victor_by_doc = {}
    for path in _CEREBRO.glob("0[1-6]_*/DATASET.md"):
        for line in path.read_text(encoding="utf-8").splitlines():
            m = re.match(r"\|\s*([a-z][a-z0-9_]+)\s*\|", line)
            if m:
                victor_by_doc.setdefault(m.group(1), set()).add(path.parent.name)

    for key, declared in VICTOR_DATASET_FIELD.items():
        if "(" not in declared or declared.startswith("FORMULAS.md") \
                or declared.startswith("FRED "):
            continue
        field = declared.split(" (")[0]
        doc = declared[declared.index("(") + 1:declared.rindex(")")]
        assert doc in victor_by_doc.get(field, set()), (
            f"{key}: '{field}' no esta declarado en {doc}"
        )


def test_the_three_keys_the_strengthened_guard_found_are_documented():
    """`capitalized_rd_adjustment`, `contract_protection` and the amount form
    of `recurring_revenue` are read through `_overlay_number(overlay, "key")`,
    so their warnings never say "set `key`" and the first drift guard missed
    all three."""
    for key in ("capitalized_rd_adjustment", "contract_protection",
                "recurring_revenue"):
        assert key in SKELETON_KEYS, f"{key} sigue sin documentarse"
        assert key in VICTOR_DATASET_FIELD
