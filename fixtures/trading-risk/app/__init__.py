"""Trading-risk management app.

A sell-side trading desk that takes orders from traders, fills them, keeps
positions, evaluates risk limits in real time and at end-of-day, issues
margin calls when limits are breached, ingests market-data ticks via
webhook, and reports daily positions to the regulator.

Built with only the standard library — importable for the distill skill to
read; not intended to run end-to-end.
"""
from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any


@dataclass
class Route:
    method: str
    path: str
    handler: Callable[..., Any]


class Router:
    """Minimal stand-in for a Flask `app` object."""

    def __init__(self) -> None:
        self.routes: list[Route] = []

    def _register(self, method: str, path: str) -> Callable[[Callable[..., Any]], Callable[..., Any]]:
        def decorator(fn: Callable[..., Any]) -> Callable[..., Any]:
            self.routes.append(Route(method=method, path=path, handler=fn))
            return fn
        return decorator

    def get(self, path: str): return self._register("GET", path)
    def post(self, path: str): return self._register("POST", path)
    def put(self, path: str): return self._register("PUT", path)


@dataclass
class Store:
    """In-memory storage. A real desk would back this with kdb+/postgres."""
    desks: dict[str, "TradingDesk"] = field(default_factory=dict)
    traders: dict[str, "Trader"] = field(default_factory=dict)
    instruments: dict[str, "Instrument"] = field(default_factory=dict)
    orders: dict[str, "Order"] = field(default_factory=dict)
    positions: dict[str, "Position"] = field(default_factory=dict)
    risk_limits: list["RiskLimit"] = field(default_factory=list)
    margin_calls: dict[str, "MarginCall"] = field(default_factory=dict)
    market_tick_events: dict[str, "MarketTickEvent"] = field(default_factory=dict)
    regulatory_notices: dict[str, "RegulatoryNotice"] = field(default_factory=dict)
    last_mark_by_symbol: dict[str, int] = field(default_factory=dict)  # symbol → last mark in pence


app = Router()
store = Store()

# Side-effect imports register routes / webhooks on `app`.
from app import routes as _routes  # noqa: E402,F401
from app import webhooks as _webhooks  # noqa: E402,F401
from app.models import (  # noqa: E402  # re-exported for forward refs
    Instrument,
    MarginCall,
    MarketTickEvent,
    Order,
    Position,
    RegulatoryNotice,
    RiskLimit,
    Trader,
    TradingDesk,
)
