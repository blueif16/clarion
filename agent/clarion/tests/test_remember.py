"""The "remember?" gate (no-network): nominate reusable preference candidates,
NEVER offer a secret, and write only on approval (no memory without a yes)."""

from __future__ import annotations

from clarion.app.remember import (
    RememberCandidate,
    is_sensitive_field,
    nominate_remember_candidates,
    remember_candidates,
)
from clarion.contracts.state import AxNode, SelectorMap
from clarion.fakes.adapters import FakeMemory


def _tree() -> SelectorMap:
    return SelectorMap(
        nodes={
            0: AxNode(index=0, role="textbox", name="Payee", node_id="n0"),
            1: AxNode(index=1, role="textbox", name="Password", node_id="n1"),
            2: AxNode(index=2, role="textbox", name="Card number", node_id="n2"),
            3: AxNode(index=3, role="textbox", name="Mailing address", node_id="n3"),
            4: AxNode(index=4, role="textbox", name="CVV", node_id="n4"),
            5: AxNode(index=5, role="textbox", name="Shipping speed", node_id="n5"),
        }
    )


def test_secrets_are_suppressed_reusables_kept():
    filled = {
        "n0": "Northwind Electric",
        "n1": "hunter2",
        "n2": "4111 1111 1111 1111",
        "n3": "1 Main St",
        "n4": "123",
        "n5": "overnight",
    }
    keys = {c.key for c in nominate_remember_candidates(filled, _tree())}
    assert "Payee" in keys and "Mailing address" in keys
    assert "Shipping speed" in keys  # "pin"-substring must NOT false-trip on shipping
    assert "Password" not in keys and "Card number" not in keys and "CVV" not in keys


def test_structural_password_signal_suppresses():
    assert is_sensitive_field("Login", role="password")
    assert is_sensitive_field("Field", state={"password": True})
    assert not is_sensitive_field("Payee", role="textbox")


def test_blank_and_duplicate_values_skipped():
    sm = SelectorMap(nodes={0: AxNode(index=0, role="textbox", name="Note", node_id="n0")})
    assert nominate_remember_candidates({"n0": "   "}, sm) == []
    sm2 = SelectorMap(
        nodes={
            0: AxNode(index=0, role="textbox", name="Note", node_id="n0"),
            1: AxNode(index=1, role="textbox", name="Note", node_id="n1"),
        }
    )
    cands = nominate_remember_candidates({"n0": "x", "n1": "x"}, sm2)
    assert len(cands) == 1  # same (key, value) deduped


async def test_remember_writes_only_approved():
    m = FakeMemory()
    n = await remember_candidates(
        m, "default", [RememberCandidate(key="Payee", value="Northwind")]
    )
    assert n == 1
    prof = await m.read_profile("default")
    assert prof.preferences["Payee"] == "Northwind"
