"""Call metrics tracker."""

from __future__ import annotations

import math
import time
from dataclasses import dataclass, field

from unpod.models.session import CallMetrics, CostBreakdown, TokenUsage


@dataclass
class _TurnRecord:
    """Raw data for a single turn."""

    stt_ms: int
    llm_ms: int
    tts_ms: int
    cost_voice: float
    cost_llm: float
    tokens_in: int
    tokens_out: int
    llm: str


def _p95(values: list[int]) -> int:
    """Return the P95 value from a sorted list of ints."""
    if not values:
        return 0
    sorted_vals = sorted(values)
    idx = int(math.ceil(0.95 * len(sorted_vals))) - 1
    return sorted_vals[max(idx, 0)]


@dataclass
class MetricsTracker:
    """Accumulates per-turn metrics and produces snapshots."""

    _turns: list[_TurnRecord] = field(default_factory=list)
    _start_time: float = field(default_factory=time.monotonic)

    def record_turn(
        self,
        *,
        stt_ms: int,
        llm_ms: int,
        tts_ms: int,
        cost_voice: float,
        cost_llm: float,
        tokens_in: int,
        tokens_out: int,
        llm: str,
    ) -> None:
        """Record one turn's metrics."""
        self._turns.append(
            _TurnRecord(
                stt_ms=stt_ms,
                llm_ms=llm_ms,
                tts_ms=tts_ms,
                cost_voice=cost_voice,
                cost_llm=cost_llm,
                tokens_in=tokens_in,
                tokens_out=tokens_out,
                llm=llm,
            )
        )

    def live(self) -> CallMetrics:
        """Return a current snapshot of accumulated metrics."""
        total_voice = sum(t.cost_voice for t in self._turns)
        total_llm = sum(t.cost_llm for t in self._turns)
        total_tokens_in = sum(t.tokens_in for t in self._turns)
        total_tokens_out = sum(t.tokens_out for t in self._turns)
        active_llm = self._turns[-1].llm if self._turns else ""

        return CallMetrics(
            duration_s=round(time.monotonic() - self._start_time, 2),
            turns=len(self._turns),
            stt_p95_ms=_p95([t.stt_ms for t in self._turns]),
            llm_p95_ms=_p95([t.llm_ms for t in self._turns]),
            tts_p95_ms=_p95([t.tts_ms for t in self._turns]),
            cost=CostBreakdown(
                voice=total_voice,
                llm=total_llm,
                total=total_voice + total_llm,
            ),
            tokens=TokenUsage(
                input=total_tokens_in,
                output=total_tokens_out,
            ),
            active_llm=active_llm,
        )
