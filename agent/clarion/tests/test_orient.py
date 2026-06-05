"""ORIENT — the grounded screen-reader readout (foundation §1, §3 on-ramp).

These pin the pure extractor that turns a raw ``Accessibility.getFullAXTree`` into
a ``PageReadout`` the voice plane speaks. The load-bearing property is the
invariant: every item surfaced in a readout carries the real AX ``source_node_id``
it was read from — nothing is ungrounded, nothing is invented (no fixture). Pure;
imports zero provider SDKs.
"""

from __future__ import annotations

from clarion.actuator.pipeline import (
    readout_from_selector_map,
    summarize_ax_tree,
)
from clarion.contracts.state import AxNode, PageReadout, SelectorMap


def _ax(node_id: str, role: str, name: str, *, ignored: bool = False) -> dict:
    """A minimal CDP AX node in the shape ``Accessibility.getFullAXTree`` returns."""
    return {
        "nodeId": node_id,
        "ignored": ignored,
        "role": {"value": role},
        "name": {"value": name},
    }


def _usagov_tree() -> dict:
    """A realistic slice: a heading, a content link, a search box, plus nodes that
    MUST be filtered (hidden/ignored, unnamed, and non-interactive prose)."""
    return {
        "nodes": [
            _ax("1", "heading", "Government benefits"),
            _ax("2", "link", "Disability benefits"),
            _ax("3", "searchbox", "Search USAGov"),
            _ax("4", "button", "Pay now"),
            _ax("9", "button", "Cookie banner", ignored=True),  # hidden → drop
            _ax("10", "button", ""),  # unnamed → drop (nothing to announce)
            _ax("11", "StaticText", "Some body prose"),  # non-interactive → drop
        ]
    }


# ---------------------------------------------------------------------------
# summarize_ax_tree — grounded headings + affordances from the real AX tree
# ---------------------------------------------------------------------------


def test_summarize_extracts_grounded_headings_and_affordances() -> None:
    readout = summarize_ax_tree(
        _usagov_tree(), title="Government benefits | USAGov", url="https://www.usa.gov/benefits"
    )
    assert isinstance(readout, PageReadout)

    # Heading surfaced, grounded to its real nodeId.
    assert [h.value for h in readout.headings] == ["Government benefits"]
    assert readout.headings[0].source_node_id == "1"

    # Affordances: the link, the searchbox, and the button — each grounded.
    aff = {f.value: f.source_node_id for f in readout.affordances}
    assert aff == {
        "Disability benefits": "2",
        "Search USAGov": "3",
        "Pay now": "4",
    }
    # The invariant: NOTHING in a readout is ungrounded.
    for f in readout.headings + readout.affordances:
        assert f.source_node_id, f"ungrounded fact in readout: {f.value!r}"


def test_summarize_filters_hidden_unnamed_and_noninteractive() -> None:
    readout = summarize_ax_tree(_usagov_tree(), title="t")
    surfaced = {f.value for f in readout.headings + readout.affordances}
    assert "Cookie banner" not in surfaced  # ignored/hidden
    assert "Some body prose" not in surfaced  # non-interactive, non-heading
    assert "" not in surfaced  # unnamed control (nothing to announce)


def test_summary_is_spoken_ready_and_open_ended() -> None:
    readout = summarize_ax_tree(_usagov_tree(), title="Government benefits | USAGov")
    s = readout.summary
    assert "Government benefits | USAGov" in s  # where they are
    assert "Government benefits" in s  # the section heading
    # The grounded affordance NAMES are spoken back (robust to singular/plural).
    assert "Search USAGov" in s and "Pay now" in s and "Disability benefits" in s
    # Count labels are singular when there is exactly one of a kind.
    assert "1 field you can fill" in s and "1 button" in s and "1 link" in s
    # Ends on an open prompt → the user states a goal (which is then confirmed).
    assert s.strip().endswith("What would you like to do?")
    # No banned "assistant/helper/assist" language (persona rule).
    low = s.lower()
    assert "assistant" not in low and "helper" not in low and "assist" not in low


def test_summarize_empty_page_is_honest() -> None:
    readout = summarize_ax_tree({"nodes": []}, title="Blank")
    assert readout.headings == [] and readout.affordances == []
    assert "can't find any labeled headings or controls" in readout.summary


# ---------------------------------------------------------------------------
# readout_from_selector_map — the fallback (interactive map only, still grounded)
# ---------------------------------------------------------------------------


def test_readout_from_selector_map_is_grounded() -> None:
    sm = SelectorMap(
        nodes={
            0: AxNode(index=0, role="searchbox", name="Search", node_id="n-search"),
            1: AxNode(index=1, role="button", name="Pay bill", node_id="n-pay"),
            2: AxNode(index=2, role="textbox", name="", node_id="n-blank"),  # unnamed → drop
        }
    )
    readout = readout_from_selector_map(sm, title="Account")
    aff = {f.value: f.source_node_id for f in readout.affordances}
    assert aff == {"Search": "n-search", "Pay bill": "n-pay"}
    # Fallback has no page headings (the numbered map is action-only).
    assert readout.headings == []
    for f in readout.affordances:
        assert f.source_node_id, "ungrounded affordance in fallback readout"
