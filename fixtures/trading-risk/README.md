# Trading-risk fixture

A sell-side trading-desk risk management mini-system. The desk takes
orders, fills them, keeps positions, evaluates risk limits in real time
and end-of-day, issues margin calls when limits are breached, ingests
market-data ticks via webhook, and reports daily positions to the
regulator.

About 1,370 LOC across 11 Python files. Built only against the standard
library so the package is importable without third-party dependencies.
It is *not* meant to run end-to-end; it exists to be read by the
`distill` skill.

## Domain

Seven core entities plus two external entities:

| Entity              | File                       | Notes                                                      |
|---------------------|----------------------------|------------------------------------------------------------|
| `TradingDesk`       | `app/models.py:123`        | groups traders; firm-allocated risk appetite                |
| `Trader`            | `app/models.py:138`        | status, desk, authorised asset classes                      |
| `Instrument`        | `app/models.py:174`        | symbol, asset_class, currency, sector, volatility, lot_size |
| `Order`             | `app/models.py:184`        | side, quantity, fills, status                               |
| `Position`          | `app/models.py:212`        | trader+instrument net position, MTM, concentration          |
| `RiskLimit`         | `app/models.py:257`        | scoped trader/desk/firm; metric notional/var/concentration  |
| `MarginCall`        | `app/models.py:287`        | issued on breach, T+1 deadline                              |
| `MarketTickEvent`   | `app/models.py:306`        | **External** — last-price tick from market-data feed        |
| `RegulatoryNotice`  | `app/models.py:321`        | **External** — trading halts / position limits / etc.       |

## File layout

```
app/
├── __init__.py             # Router + Store + package wiring
├── models.py               # entities, status enums, temporal constants
├── routes.py               # 10 HTTP endpoints (trader-facing API)
├── webhooks.py             # 2 inbound webhooks (market-data, regulator)
├── jobs.py                 # 5 scheduled jobs
├── services/
│   ├── orders.py           # order lifecycle: submit/cancel/fill/reject
│   ├── positions.py        # position update on fill, MTM, valuation
│   └── risk.py             # limit evaluation, breach handling, margin call lifecycle
└── integrations/
    ├── market_data.py      # Bloomberg-shaped subscribe + snapshot client
    ├── clearing_house.py   # post margin, request collateral release
    └── regulator_reporting.py  # submit daily position report
```

## Patterns exercised

Each row maps a pattern the distill skill has to handle to a specific
site in the fixture. Reviewers can use this table to audit a distilled
spec — every pattern should be reflected.

| #  | Pattern                       | Where to find it                                                                                                                              |
|----|-------------------------------|-----------------------------------------------------------------------------------------------------------------------------------------------|
| 1  | Status enums / state machines | 8 enums in `app/models.py:56-115` — DeskStatus, TraderStatus, OrderStatus, OrderSide, PositionStatus, RiskLimitScope/Metric/Status, MarginCallStatus, AssetClass |
| 2  | Guarded transitions           | `app/services/orders.py:81` (`apply_fill` — 3 guards), `app/services/risk.py:109` (`post_margin` — 4 guards), `app/services/risk.py:161` (`default_margin_call`) |
| 3  | Temporal rules                | 5 jobs in `app/jobs.py`: `:39` 15-min risk aggregation, `:54` 17:00 UTC EOD mark, `:72` hourly margin deadline, `:88` 30-day stale-flat closer, `:104` 18:00 UTC regulator report |
| 4  | External entity (via webhook) | `app/models.py:306` `MarketTickEvent` + `:321` `RegulatoryNotice`; receivers at `app/webhooks.py:18`, `:34`                                    |
| 5  | Third-party integration       | `app/integrations/market_data.py` (Bloomberg-shaped subscribe/snapshot), `app/integrations/clearing_house.py` (margin posting), `app/integrations/regulator_reporting.py` (daily report submission) |
| 6  | Implicit state machine        | `app/models.py:248` `Position.is_concentrated` — derived from `notional / trader.total_notional > CONCENTRATION_PCT`; `app/models.py:154` `Trader.is_at_limit` — derived from active limits; **no `concentrated` enum value** (see constant at `app/models.py:49`) |
| 7  | Scattered logic               | `evaluate_limit_breach` (`app/services/risk.py:56`) called from **two sites**: `app/services/orders.py:115` (real-time inside `apply_fill`) and `app/jobs.py:39` (`intraday_risk_aggregator`) |
| 8  | Derived properties            | `app/models.py:147` `Trader.open_positions`, `:151` `total_notional_exposure`, `:155` `has_open_margin_calls`, `:158` `is_at_limit`; `app/models.py:201` `Order.remaining_quantity`, `:205` `notional_value_pence`; `app/models.py:223` `Position.mark_to_market_value`, `:240` `notional_value`, `:243` `is_concentrated`; `app/models.py:299` `MarginCall.is_overdue` |
| 9  | FK → relationship             | `app/models.py:140` `Trader.desk_id: str` should distil to `desk: TradingDesk`. Similarly `Order.trader_id` → `trader: Trader`, `Order.instrument_symbol` → `instrument: Instrument`, `Position.trader_id`/`instrument_symbol`, `MarginCall.trader_id`/`risk_limit_id` |

## Sanity check

```sh
cd fixtures/trading-risk
python3 -c "import app; print(len(app.app.routes), 'routes')"
# expected: 12 routes
```
