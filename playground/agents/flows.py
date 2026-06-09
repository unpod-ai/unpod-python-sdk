"""Flow registry — discover ConversationFlow JSONs and build a FlowSet machine.

Every ``*.json`` in this package's ``flows/`` directory is a superdialog
ConversationFlow. The playground worker runs a single :class:`DialogMachine`
bound to a :class:`FlowSet` of **all** of them, so the UI can switch flows live
via ``DialogMachine.switch_flow`` (routed through the harness control plane).

Flows are keyed by filename stem — that id is what the UI sends on the wire.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any

from superdialog import DialogMachine, Flow, FlowSet

_FLOWS_DIR = Path(__file__).parent / "flows"

# Flow JSON is static config; parse each file once and reuse across calls.
_flows_cache: dict[str, Flow] | None = None


@dataclass(frozen=True)
class FlowInfo:
    """Display metadata for one flow (consumed by the UI sidebar)."""

    id: str
    label: str
    nodes: int
    initial_node: str
    description: str


def _label(stem: str) -> str:
    """Turn a filename stem into a human-readable label."""
    words = stem.replace("flow_", "").replace("_", " ").split()
    return " ".join(w.capitalize() for w in words) or stem


def _load_flows() -> dict[str, Flow]:
    """Load (and cache) every flow JSON, keyed by id (filename stem)."""
    global _flows_cache
    if _flows_cache is None:
        loaded = {p.stem: Flow.load(p) for p in sorted(_FLOWS_DIR.glob("*.json"))}
        if not loaded:
            raise RuntimeError(f"no flow JSON files found in {_FLOWS_DIR}")
        _flows_cache = loaded
    return _flows_cache


def load_flowset() -> FlowSet:
    """Return a FlowSet over all flows (fresh wrapper, shared Flow objects)."""
    return FlowSet(dict(_load_flows()))


def flow_registry() -> list[FlowInfo]:
    """Return display metadata for every discovered flow, in id order."""
    infos: list[FlowInfo] = []
    for fid, flow in _load_flows().items():
        prompt = " ".join((getattr(flow, "system_prompt", "") or "").split())
        infos.append(
            FlowInfo(
                id=fid,
                label=_label(fid),
                nodes=len(getattr(flow, "nodes", []) or []),
                initial_node=getattr(flow, "initial_node", "") or "",
                description=prompt[:140],
            )
        )
    return infos


def build(llm: str) -> Any:
    """Build the playground DialogMachine bound to a FlowSet of all flows."""
    return DialogMachine(flow=load_flowset(), llm=llm)
