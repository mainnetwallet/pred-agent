"""reports module — daily summary and calibration.

Grounded in kit/05_STRATEGY_AND_RISK.md section 8 (Calibration) and kit/06_TELEGRAM_BOT.md
(/report command).
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from decimal import Decimal


@dataclass
class CalibrationBucket:
    edge_bucket: str  # e.g. "0-3pp", "3-6pp"
    predicted_p: Decimal
    actual_win_rate: Decimal
    n: int
    brier_score: Decimal


@dataclass
class DailySummary:
    date: str
    mode: str  # "PAPER" or "LIVE"
    proposals_created: int
    proposals_approved: int
    proposals_rejected: int
    proposals_expired: int
    fills: int
    realized_pnl_usd: Decimal
    total_pnl_usd: Decimal
    open_positions: int
    brier_score: Decimal | None


class Reports:
    """Generates daily summary and calibration reports."""

    def __init__(self) -> None:
        self._proposals_created = 0
        self._proposals_approved = 0
        self._proposals_rejected = 0
        self._proposals_expired = 0
        self._fills = 0
        self._calibration_rows: list[dict] = []

    def record_proposal_created(self) -> None:
        self._proposals_created += 1

    def record_proposal_approved(self) -> None:
        self._proposals_approved += 1

    def record_proposal_rejected(self) -> None:
        self._proposals_rejected += 1

    def record_proposal_expired(self) -> None:
        self._proposals_expired += 1

    def record_fill(self, p_fair: Decimal, actual_won: bool) -> None:
        self._fills += 1
        self._calibration_rows.append({"p_fair": p_fair, "won": actual_won})

    def daily_summary(self, *, mode: str, realized_pnl_usd: Decimal,
                      total_pnl_usd: Decimal, open_positions: int) -> DailySummary:
        return DailySummary(
            date=datetime.now(timezone.utc).strftime("%Y-%m-%d"),
            mode=mode,
            proposals_created=self._proposals_created,
            proposals_approved=self._proposals_approved,
            proposals_rejected=self._proposals_rejected,
            proposals_expired=self._proposals_expired,
            fills=self._fills,
            realized_pnl_usd=realized_pnl_usd,
            total_pnl_usd=total_pnl_usd,
            open_positions=open_positions,
            brier_score=self.brier_score(),
        )

    def brier_score(self) -> Decimal | None:
        if not self._calibration_rows:
            return None
        total = sum((r["p_fair"] - (Decimal(1) if r["won"] else Decimal(0))) ** 2
                    for r in self._calibration_rows)
        return total / Decimal(len(self._calibration_rows))

    def calibration_table(self) -> list[CalibrationBucket]:
        """Bucket predictions and compute predicted vs actual win rate."""
        buckets: dict[str, list[dict]] = {}
        for r in self._calibration_rows:
            p = r["p_fair"]
            bucket = f"{int(p * 10 // 10) * 10}-{int(p * 10 // 10) * 10 + 10}%"
            buckets.setdefault(bucket, []).append(r)
        result = []
        for bucket, rows in sorted(buckets.items()):
            avg_p = sum(r["p_fair"] for r in rows) / Decimal(len(rows))
            win_rate = sum(Decimal(1) if r["won"] else Decimal(0) for r in rows) / Decimal(len(rows))
            result.append(CalibrationBucket(
                edge_bucket=bucket, predicted_p=avg_p, actual_win_rate=win_rate,
                n=Decimal(len(rows)), brier_score=Decimal("0"),
            ))
        return result
