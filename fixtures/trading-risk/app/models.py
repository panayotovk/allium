"""Domain entities for the trading-risk app.

Seven core entities plus two external entities (MarketTickEvent and
RegulatoryNotice) that arrive via webhook from third-party feeds.
Status fields are modelled as Enums; derived properties live as @property
methods on the entity they describe.

Money is in pence (integer); FX is converted to a notional pence amount in
the firm's base currency (assumed GBP throughout the spec).
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from enum import Enum
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from app import Store


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


# Hardcoded FX rates for the fixture. A real system would query an FX feed.
# All rates expressed as: 1 unit of the foreign currency in pence.
FX_TO_PENCE: dict[str, int] = {
    "GBP": 100,
    "USD": 80,
    "EUR": 87,
    "JPY": 0,  # negligible per-yen; aggregated separately in real systems
    "CHF": 92,
}

# Position is "stalled flat" if it has been at status flat with no
# activity for this long. Drives the stale-position-closer job.
STALE_FLAT_AFTER = timedelta(days=30)

# Margin calls default to T+1 — issued at time T, defaults at T + 1 day
# if not posted.
MARGIN_CALL_DEADLINE = timedelta(days=1)

# Risk aggregation cadence: jobs run every N minutes intraday.
RISK_AGG_WINDOW = timedelta(minutes=15)

# When a position's notional fraction of the trader's book exceeds this,
# the position is "concentrated". Implicit state — no enum value.
CONCENTRATION_PCT = 0.25


# ---------------------------------------------------------------------------
# Status enums
# ---------------------------------------------------------------------------

class DeskStatus(str, Enum):
    ACTIVE = "active"
    WINDING_DOWN = "winding_down"
    SHUTTERED = "shuttered"


class TraderStatus(str, Enum):
    ACTIVE = "active"
    SUSPENDED = "suspended"
    OFFBOARDED = "offboarded"


class OrderStatus(str, Enum):
    PENDING = "pending"
    PARTIALLY_FILLED = "partially_filled"
    FILLED = "filled"
    CANCELLED = "cancelled"
    REJECTED = "rejected"


class OrderSide(str, Enum):
    BUY = "buy"
    SELL = "sell"


class PositionStatus(str, Enum):
    OPEN = "open"
    FLAT = "flat"
    CLOSED = "closed"


class RiskLimitScope(str, Enum):
    TRADER = "trader"
    DESK = "desk"
    FIRM = "firm"


class RiskLimitMetric(str, Enum):
    NOTIONAL = "notional"
    VAR = "var"
    CONCENTRATION = "concentration"


class RiskLimitStatus(str, Enum):
    ACTIVE = "active"
    BREACHED = "breached"
    OVERRIDDEN = "overridden"


class MarginCallStatus(str, Enum):
    ISSUED = "issued"
    POSTED = "posted"
    DEFAULTED = "defaulted"
    CLEARED = "cleared"


class AssetClass(str, Enum):
    EQUITY = "equity"
    BOND = "bond"
    DERIVATIVE = "derivative"


# ---------------------------------------------------------------------------
# Entities
# ---------------------------------------------------------------------------

@dataclass
class TradingDesk:
    desk_id: str
    name: str
    desk_head: str
    status: DeskStatus = DeskStatus.ACTIVE
    risk_appetite_pence: int = 0   # firm-allocated headroom for the desk

    def active_traders(self, store: "Store") -> list["Trader"]:
        return [
            t for t in store.traders.values()
            if t.desk_id == self.desk_id and t.status == TraderStatus.ACTIVE
        ]


@dataclass
class Trader:
    trader_id: str
    name: str
    desk_id: str
    status: TraderStatus = TraderStatus.ACTIVE
    authorised_asset_classes: set[str] = field(default_factory=set)
    last_activity_at: datetime = field(default_factory=_utcnow)

    def open_positions(self, store: "Store") -> list["Position"]:
        return [
            p for p in store.positions.values()
            if p.trader_id == self.trader_id and p.status == PositionStatus.OPEN
        ]

    def total_notional_exposure(self, store: "Store") -> int:
        return sum(p.notional_value(store) for p in self.open_positions(store))

    def has_open_margin_calls(self, store: "Store") -> bool:
        return any(
            mc.trader_id == self.trader_id and mc.status == MarginCallStatus.ISSUED
            for mc in store.margin_calls.values()
        )

    def is_at_limit(self, store: "Store") -> bool:
        """Implicit state: any active limit at TRADER scope is currently breached."""
        for limit in store.risk_limits:
            if limit.scope == RiskLimitScope.TRADER and limit.scope_id == self.trader_id:
                if limit.is_breached(store):
                    return True
        return False

    def touch(self) -> None:
        self.last_activity_at = _utcnow()


@dataclass
class Instrument:
    symbol: str
    asset_class: AssetClass
    currency: str
    sector: str
    annualised_volatility: float   # 0.0–1.0 as a decimal
    lot_size: int = 1


@dataclass
class Order:
    order_id: str
    trader_id: str
    instrument_symbol: str
    side: OrderSide
    quantity: int
    limit_price_pence: int        # 0 = market order
    status: OrderStatus = OrderStatus.PENDING
    quantity_filled: int = 0
    submitted_at: datetime = field(default_factory=_utcnow)
    last_activity_at: datetime = field(default_factory=_utcnow)
    rejection_reason: str | None = None

    @property
    def remaining_quantity(self) -> int:
        return max(0, self.quantity - self.quantity_filled)

    @property
    def notional_value_pence(self) -> int:
        # Best-effort: uses limit_price_pence; a real system would use
        # the matched price (which we don't track here).
        return self.quantity * self.limit_price_pence

    def touch(self) -> None:
        self.last_activity_at = _utcnow()


@dataclass
class Position:
    position_id: str
    trader_id: str
    instrument_symbol: str
    quantity: int                 # signed: positive = long, negative = short
    avg_entry_price_pence: int
    status: PositionStatus = PositionStatus.OPEN
    opened_at: datetime = field(default_factory=_utcnow)
    last_activity_at: datetime = field(default_factory=_utcnow)
    closed_at: datetime | None = None

    def mark_to_market_value(self, store: "Store") -> int:
        """Current value in pence using the most recent mark."""
        last_mark = store.last_mark_by_symbol.get(self.instrument_symbol)
        if last_mark is None:
            # No mark yet — fall back to entry price (avoids divide-by-zero
            # in a freshly-booked system).
            last_mark = self.avg_entry_price_pence
        instrument = store.instruments.get(self.instrument_symbol)
        if instrument is None:
            raise ValueError(f"unknown instrument {self.instrument_symbol}")
        fx_to_pence = FX_TO_PENCE.get(instrument.currency, 100)
        return abs(self.quantity) * last_mark * fx_to_pence // 100

    def notional_value(self, store: "Store") -> int:
        return self.mark_to_market_value(store)

    def is_concentrated(self, store: "Store") -> bool:
        """Implicit state: this position alone exceeds CONCENTRATION_PCT of the
        trader's total exposure. There is deliberately no `concentrated`
        enum value — concentration is a derived observation.
        """
        trader = store.traders.get(self.trader_id)
        if trader is None:
            return False
        total = trader.total_notional_exposure(store)
        if total == 0:
            return False
        return self.notional_value(store) / total > CONCENTRATION_PCT

    def touch(self) -> None:
        self.last_activity_at = _utcnow()


@dataclass
class RiskLimit:
    limit_id: str
    scope: RiskLimitScope
    scope_id: str                 # trader_id, desk_id, or "firm"
    metric: RiskLimitMetric
    threshold_pence: int          # interpreted per metric (notional in pence;
                                  # var in pence; concentration as bps × 100)
    status: RiskLimitStatus = RiskLimitStatus.ACTIVE
    breached_at: datetime | None = None

    def is_breached(self, store: "Store") -> bool:
        """Cheap re-evaluation using current state. Distinct from the
        persisted `status` which is the *recorded* breach state."""
        observed = self._observed_value(store)
        return observed > self.threshold_pence

    def _observed_value(self, store: "Store") -> int:
        if self.scope == RiskLimitScope.TRADER:
            trader = store.traders.get(self.scope_id)
            return trader.total_notional_exposure(store) if trader else 0
        if self.scope == RiskLimitScope.DESK:
            desk = store.desks.get(self.scope_id)
            if desk is None:
                return 0
            return sum(t.total_notional_exposure(store) for t in desk.active_traders(store))
        # firm scope
        return sum(t.total_notional_exposure(store) for t in store.traders.values())


@dataclass
class MarginCall:
    margin_call_id: str
    trader_id: str
    risk_limit_id: str            # FK — the limit whose breach triggered this
    required_amount_pence: int
    status: MarginCallStatus = MarginCallStatus.ISSUED
    issued_at: datetime = field(default_factory=_utcnow)
    deadline: datetime = field(default_factory=lambda: _utcnow() + MARGIN_CALL_DEADLINE)
    posted_at: datetime | None = None
    posted_amount_pence: int = 0
    defaulted_at: datetime | None = None
    cleared_at: datetime | None = None

    @property
    def is_overdue(self) -> bool:
        return self.status == MarginCallStatus.ISSUED and _utcnow() > self.deadline


@dataclass
class MarketTickEvent:
    """External entity — arrives via webhook from the market-data feed.

    Each tick carries the last traded price for one instrument. We persist
    it and update store.last_mark_by_symbol for the corresponding symbol.
    """
    tick_id: str
    instrument_symbol: str
    last_price_pence: int
    venue: str                    # e.g. "LSE", "NYSE", "XETRA"
    tick_time: datetime
    received_at: datetime = field(default_factory=_utcnow)


@dataclass
class RegulatoryNotice:
    """External entity — arrives via webhook from the regulator.

    A notice may be a trading halt, position limit, or other directive.
    The app records it but does not itself decide which traders are
    affected; the routing happens downstream.
    """
    notice_id: str
    notice_kind: str              # "trading_halt" | "position_limit" | "other"
    instrument_symbol: str | None
    description: str
    effective_from: datetime
    received_at: datetime = field(default_factory=_utcnow)
