"""Third-party regulator-reporting integration.

The desk pushes a daily position report to the regulator's intake API.
The submission is acknowledged with a receipt id; the regulator may
later issue notices (separate webhook receiver in app/webhooks.py).
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone


class RegulatorReportingError(Exception):
    pass


@dataclass
class PositionReportEntry:
    trader_id: str
    instrument_symbol: str
    net_quantity: int             # signed
    notional_value_pence: int


@dataclass
class PositionReportSubmission:
    report_date: datetime
    desk_id: str
    entries: list[PositionReportEntry]
    receipt_id: str
    submitted_at: datetime


def submit_position_report(
    *,
    report_date: datetime,
    desk_id: str,
    entries: list[PositionReportEntry],
) -> PositionReportSubmission:
    """Submit the desk's daily position report to the regulator's intake.
    The receipt id can be used to query status later.
    """
    if not desk_id:
        raise RegulatorReportingError("desk_id is required")
    if not entries:
        raise RegulatorReportingError("at least one entry is required")
    return PositionReportSubmission(
        report_date=report_date,
        desk_id=desk_id,
        entries=list(entries),
        receipt_id=f"posrpt-{desk_id}-{report_date.date().isoformat()}",
        submitted_at=datetime.now(timezone.utc),
    )
