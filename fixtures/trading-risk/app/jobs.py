"""Scheduled jobs.

In a real deployment these run on cron / APScheduler / similar. Each
job iterates the store and applies time-based business rules.

Temporal thresholds:
  - intraday risk aggregation: every 15 minutes
  - end-of-day mark-to-market: 17:00 UTC daily
  - margin call deadline check: hourly
  - stale-position closer: daily (FLAT positions inactive for 30 days)
  - daily regulator report: 18:00 UTC daily
"""
from __future__ import annotations

from datetime import datetime, time, timezone

from app import Store
from app.integrations.regulator_reporting import (
    PositionReportEntry,
    submit_position_report,
)
from app.models import (
    MarginCallStatus,
    PositionStatus,
    STALE_FLAT_AFTER,
    TraderStatus,
)
from app.services.positions import close_flat_position
from app.services.risk import (
    default_margin_call,
    evaluate_limit_breach,
)


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


def intraday_risk_aggregator(store: Store) -> list[str]:
    """Every 15 minutes during the trading day. Walks every active trader
    and runs evaluate_limit_breach for each. Returns the trader_ids that
    saw at least one limit breach this pass.
    """
    breached: list[str] = []
    for trader in store.traders.values():
        if trader.status != TraderStatus.ACTIVE:
            continue
        issued = evaluate_limit_breach(store, trader_id=trader.trader_id)
        if issued:
            breached.append(trader.trader_id)
    return breached


def end_of_day_mark_to_market(store: Store, now: datetime | None = None) -> int:
    """Daily 17:00 UTC. Re-marks every open position against the latest
    last-price snapshot for its instrument. Returns the count of positions
    revalued.
    """
    now = now or _utcnow()
    revalued = 0
    for position in store.positions.values():
        if position.status != PositionStatus.OPEN:
            continue
        # Force a revaluation by calling mark_to_market_value (in real
        # systems this would persist the new MTM; here it's a no-op since
        # MTM is always computed from the latest last_mark_by_symbol).
        _ = position.mark_to_market_value(store)
        revalued += 1
    return revalued


def margin_call_deadline_check(store: Store, now: datetime | None = None) -> list[str]:
    """Hourly. Any ISSUED margin call past its deadline transitions to
    DEFAULTED. Returns the margin_call_ids defaulted in this pass.
    """
    now = now or _utcnow()
    defaulted: list[str] = []
    for call in list(store.margin_calls.values()):
        if call.status != MarginCallStatus.ISSUED:
            continue
        if not call.is_overdue:
            continue
        default_margin_call(store, call.margin_call_id)
        defaulted.append(call.margin_call_id)
    return defaulted


def stale_position_closer(store: Store, now: datetime | None = None) -> list[str]:
    """Daily. FLAT positions that have been inactive for 30 days
    transition to CLOSED. Returns position_ids closed in this pass.
    """
    now = now or _utcnow()
    closed: list[str] = []
    for position in list(store.positions.values()):
        if position.status != PositionStatus.FLAT:
            continue
        if (now - position.last_activity_at) < STALE_FLAT_AFTER:
            continue
        close_flat_position(store, position.position_id)
        closed.append(position.position_id)
    return closed


def daily_regulator_report(store: Store, now: datetime | None = None) -> dict[str, str]:
    """Daily 18:00 UTC. Builds one position report per desk and submits
    it to the regulator. Returns a map of desk_id -> receipt_id.
    """
    now = now or _utcnow()
    receipts: dict[str, str] = {}
    for desk in store.desks.values():
        entries = _build_report_entries_for_desk(store, desk.desk_id)
        if not entries:
            continue
        submission = submit_position_report(
            report_date=now,
            desk_id=desk.desk_id,
            entries=entries,
        )
        receipts[desk.desk_id] = submission.receipt_id
    return receipts


def _build_report_entries_for_desk(store: Store, desk_id: str) -> list[PositionReportEntry]:
    out: list[PositionReportEntry] = []
    for trader in store.traders.values():
        if trader.desk_id != desk_id:
            continue
        for position in trader.open_positions(store):
            out.append(PositionReportEntry(
                trader_id=trader.trader_id,
                instrument_symbol=position.instrument_symbol,
                net_quantity=position.quantity,
                notional_value_pence=position.notional_value(store),
            ))
    return out


def is_in_trading_hours(now: datetime | None = None) -> bool:
    """Helper used by the scheduler infrastructure to know when to run
    the intraday aggregator. London hours assumed: 07:00–17:30 UTC.
    """
    now = now or _utcnow()
    cutoff_open = time(7, 0)
    cutoff_close = time(17, 30)
    return cutoff_open <= now.timetz().replace(tzinfo=None) <= cutoff_close
