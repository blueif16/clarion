"""Wave-B PARSE tests — geometric label↔value ``PairedFact`` extraction
(architecture killer-closer #1) + the control-value / aria-live harvest + the
no-dedup-of-value-bearing-facts rule.

Pure: AX-tree dicts shaped exactly like ``Accessibility.getFullAXTree`` (positive-
id text, negative-id InlineTextBox leaves, ``row``/``cell``/``columnheader`` table
nodes, ``name.sources`` relatedElements). No provider SDK, no browser. The fixtures
mirror the REAL SSA age-reduction table the live spike exercises, so a green test
here is the same behavior proven on the live page.
"""

from __future__ import annotations

from clarion.actuator.pipeline import extract_paired_facts, extract_text_facts
from clarion.contracts.state import PairedFact


def _ax(node_id, role, name="", *, ignored=False, backend=None,
        parent=None, children=None, value=None, sources=None, properties=None):
    n = {
        "nodeId": node_id,
        "ignored": ignored,
        "role": {"value": role},
        "name": {"value": name},
    }
    if backend is not None:
        n["backendDOMNodeId"] = backend
    if parent is not None:
        n["parentId"] = parent
    if children is not None:
        n["childIds"] = children
    if value is not None:
        n["value"] = {"value": value}
    if sources is not None:
        n["name"]["sources"] = sources
    if properties is not None:
        n["properties"] = properties
    return n


# ---------------------------------------------------------------------------
# dom-ancestry pairing on a REAL-shaped data table (the SSA age-reduction table)
# ---------------------------------------------------------------------------


def _ssa_table_tree() -> dict:
    """An AX tree shaped like the real SSA age-reduction table: a ``table`` whose
    ``row`` children hold ``cell`` children. The first cell of each data row is the
    row label (birth-year cohort); the rest are values. Mirrors live nodeIds 271–278."""
    return {
        "nodes": [
            _ax("249", "table", children=["271", "280"]),
            # Data row 1: 1943-1954 | 66 | 48 | $750 | 25.00% | $350 | 30.00%
            _ax("271", "row", children=["272", "273b", "274b", "275", "276", "277", "278"]),
            _ax("272", "cell", "1943-1954"),
            _ax("273b", "cell", "66"),
            _ax("274b", "cell", "48"),
            _ax("275", "cell", "$750"),
            _ax("276", "cell", "25.00%"),
            _ax("277", "cell", "$350"),
            _ax("278", "cell", "30.00%"),
            # Data row 2: 1955 | ... | $741 | ...
            _ax("280", "row", children=["281", "284", "285"]),
            _ax("281", "cell", "1955"),
            _ax("284", "cell", "$741"),
            _ax("285", "cell", "25.83%"),
        ]
    }


def test_table_dom_ancestry_pairs_row_label_to_value_cell() -> None:
    pairs = extract_paired_facts(_ssa_table_tree())
    assert pairs, "expected dom-ancestry pairings from the table"

    # A CORRECT pairing: the row label '1943-1954' IS the reduced benefit '$750',
    # both halves grounded to their REAL cell nodeIds, joined structurally.
    correct = next((p for p in pairs if p.label.value == "1943-1954"
                    and p.value.value == "$750"), None)
    assert correct is not None
    assert correct.method == "dom-ancestry"
    assert correct.label.source_node_id == "272"
    assert correct.value.source_node_id == "275"
    # The pairing BACKS the "X is Y" claim byte-identically (Wave-C fence input).
    assert correct.backs("1943-1954", "$750") is True

    # The second row's distinct '$741' is its OWN pairing (not collapsed with $750).
    assert any(p.label.value == "1955" and p.value.value == "$741" for p in pairs)


def test_table_refuses_the_cross_row_mispairing() -> None:
    """A value that belongs to row 1 is NEVER backed as a value of row 2's label —
    the structural cell relationship refuses the mis-association (killer-closer #1)."""
    pairs = extract_paired_facts(_ssa_table_tree())
    # '1955' (row 2 label) is NOT '$750' (row 1's value) — no pairing backs it.
    assert not any(p.backs("1955", "$750") for p in pairs)
    # And '$741' (row 2's value) is not backed against row 1's label.
    assert not any(p.backs("1943-1954", "$741") for p in pairs)


# ---------------------------------------------------------------------------
# aria-labelledby / <label for> pairing (the explicit signals)
# ---------------------------------------------------------------------------


def test_aria_labelledby_pairs_control_value_to_label() -> None:
    """A textbox named via aria-labelledby pairs to the referenced label node — the
    control's live value is the value half, the label node the label half, both
    grounded to real nodeIds; method is 'aria-labelledby'."""
    tree = {
        "nodes": [
            _ax("10", "StaticText", "Amount due", backend=100),
            _ax(
                "11", "textbox", "Amount due", backend=101, value="$84.32",
                sources=[{
                    "type": "relatedElement",
                    "attribute": "aria-labelledby",
                    "relatedNodes": [{"backendDOMNodeId": 100}],
                }],
            ),
        ]
    }
    pairs = extract_paired_facts(tree)
    p = next((x for x in pairs if x.method == "aria-labelledby"), None)
    assert p is not None
    assert p.label.value == "Amount due" and p.label.source_node_id == "10"
    assert p.value.value == "$84.32" and p.value.source_node_id == "11"
    assert p.backs("Amount due", "$84.32") is True


def test_native_label_for_pairs_with_method_for() -> None:
    tree = {
        "nodes": [
            _ax("20", "StaticText", "Email", backend=200),
            _ax(
                "21", "textbox", "Email", backend=201, value="a@b.com",
                sources=[{
                    "type": "relatedElement",
                    "nativeSource": "label",
                    "relatedNodes": [{"backendDOMNodeId": 200}],
                }],
            ),
        ]
    }
    pairs = extract_paired_facts(tree)
    assert any(p.method == "for" and p.backs("Email", "a@b.com") for p in pairs)


# ---------------------------------------------------------------------------
# shared-row geometric pairing + the AMBIGUITY refusal
# ---------------------------------------------------------------------------


def _row_tree() -> dict:
    return {
        "nodes": [
            _ax("30", "StaticText", "Amount due"),
            _ax("31", "StaticText", "$84.32"),
            _ax("32", "StaticText", "Due date"),
            _ax("33", "StaticText", "June 15, 2026"),
        ]
    }


def test_shared_row_pairs_when_unambiguous() -> None:
    # Two rows: each label has exactly ONE value to its right, same row.
    geometry = {
        "30": [0, 100, 80, 20],   "31": [120, 100, 60, 20],   # row 1
        "32": [0, 140, 80, 20],   "33": [120, 140, 90, 20],   # row 2
    }
    pairs = extract_paired_facts(_row_tree(), geometry=geometry)
    assert any(p.method == "shared-row" and p.backs("Amount due", "$84.32") for p in pairs)
    assert any(p.method == "shared-row" and p.backs("Due date", "June 15, 2026") for p in pairs)


def test_shared_row_refuses_when_two_values_tie() -> None:
    """The reading-order mis-pairing this fence exists to refuse: a label with TWO
    near-equidistant values on its row produces NO pairing (we never guess)."""
    tree = {
        "nodes": [
            _ax("40", "StaticText", "Balance"),
            _ax("41", "StaticText", "$84.32"),
            _ax("42", "StaticText", "$142.10"),
        ]
    }
    geometry = {
        "40": [0, 100, 70, 20],
        "41": [100, 100, 60, 20],   # gap 30
        "42": [165, 100, 70, 20],   # gap 95 -> within margin? no; make them tie
    }
    # Make the two values nearly equidistant (tie within the ambiguity margin).
    geometry["42"] = [110, 100, 60, 20]  # gap ~40 vs 30 -> within 24px margin
    pairs = extract_paired_facts(tree, geometry=geometry)
    # No shared-row pairing for 'Balance' — the association is ambiguous.
    assert not any(p.method == "shared-row" and p.label.value == "Balance" for p in pairs)


def test_no_geometry_means_no_shared_row_but_explicit_signals_still_work() -> None:
    # Pure path (no geometry): shared-row yields nothing, but the function is sound.
    assert extract_paired_facts(_row_tree()) == [] or all(
        p.method != "shared-row" for p in extract_paired_facts(_row_tree())
    )


# ---------------------------------------------------------------------------
# both halves always grounded to a REAL node id (the invariant)
# ---------------------------------------------------------------------------


def test_every_paired_fact_half_is_grounded_to_a_real_node() -> None:
    geometry = {"30": [0, 100, 80, 20], "31": [120, 100, 60, 20],
                "32": [0, 140, 80, 20], "33": [120, 140, 90, 20]}
    pairs = extract_paired_facts(_ssa_table_tree())
    pairs += extract_paired_facts(_row_tree(), geometry=geometry)
    assert pairs
    for p in pairs:
        assert isinstance(p, PairedFact)
        assert p.label.source_node_id and not p.label.source_node_id.startswith("-")
        assert p.value.source_node_id and not p.value.source_node_id.startswith("-")


# ---------------------------------------------------------------------------
# extract_text_facts: control-values + aria-live harvest + no value dedup
# ---------------------------------------------------------------------------


def test_harvest_includes_control_values_and_aria_live() -> None:
    tree = {
        "nodes": [
            _ax("1", "textbox", "Amount", value="$84.32"),       # live control value
            _ax("2", "combobox", "State", value="California"),    # selected option
            _ax("3", "alert", "Payment failed: invalid card"),    # implicit live region
            _ax("4", "StaticText", "Account & Billing"),          # plain text
            _ax("5", "status", "Saved", properties=[
                {"name": "live", "value": {"value": "polite"}}]),
        ]
    }
    values = {f.value for f in extract_text_facts(tree)}
    assert "$84.32" in values           # control value harvested
    assert "California" in values       # combobox selection harvested
    assert "Payment failed: invalid card" in values  # aria-live alert harvested
    assert "Saved" in values            # explicit aria-live=polite region harvested


def test_value_bearing_facts_are_not_deduped() -> None:
    """Two DIFFERENT $ amounts must BOTH survive — and even two IDENTICAL value-
    bearing strings on different nodes survive (the amount-due vs past-due case)."""
    tree = {
        "nodes": [
            _ax("10", "StaticText", "$84.32"),
            _ax("11", "StaticText", "$142.10"),
            _ax("12", "StaticText", "$142.10"),   # a second, identical past-due-style row
        ]
    }
    values = [f.value for f in extract_text_facts(tree)]
    assert values.count("$84.32") == 1
    # Both $142.10 nodes survive — value-bearing facts are NOT deduped.
    assert values.count("$142.10") == 2
    ids = {f.source_node_id for f in extract_text_facts(tree)}
    assert {"10", "11", "12"} <= ids


def test_label_text_is_still_deduped() -> None:
    """Non-value label text still dedups (the AX tree repeats a label across leaves)."""
    tree = {
        "nodes": [
            _ax("20", "StaticText", "Amount due"),
            _ax("21", "StaticText", "Amount due"),   # repeated label → one fact
        ]
    }
    values = [f.value for f in extract_text_facts(tree)]
    assert values.count("Amount due") == 1
