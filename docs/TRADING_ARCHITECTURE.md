# Curie market analysis and trading architecture

## Current decision

Curie may gain read-only market analysis and paper-trading capabilities on its
current server. Live Polymarket execution must remain disabled there because the
official geoblock endpoint currently marks the host as blocked. Curie must never
use a VPN, proxy, relocated request, or another account to bypass a platform or
jurisdiction restriction.

Polymarket is a prediction-market venue, not a general crypto exchange. A
separate venue adapter is required for spot crypto trading.

## Phase 1: analysis and paper trading

Build this before accepting any wallet or exchange credential:

1. Venue-neutral market-data contracts for instruments, quotes, candles, order
   books, fees, liquidity, positions, and timestamps.
2. Polymarket public-data adapter for market discovery, resolution criteria,
   outcome prices, order-book depth, spread, volume, liquidity, and price history.
3. Crypto market-data adapter selected for the operator's eligible exchange.
4. Source provenance, freshness checks, clock-skew checks, rate-limit handling,
   caching, and disagreement detection across independent sources.
5. A paper ledger with realistic fees, partial fills, spread, slippage, latency,
   cancellation, settlement, and reproducible backtests.
6. Calibrated analysis that separates observations, assumptions, forecasts,
   uncertainty, counterarguments, catalysts, resolution risk, and invalidation
   conditions. Market prices are signals, not ground truth.
7. Evaluation gates for data freshness, probability calibration, expected-value
   calculation, portfolio exposure, drawdown, and simulated execution accuracy.

## Phase 2: approval-bound live execution

Only after jurisdiction/account eligibility and the paper gates pass:

- Keep private keys in an isolated signer or hardware-backed service. The LLM,
  prompts, memory, logs, and source tree must never receive signing material.
- Require a typed financial mandate: venue, assets/markets, starting capital,
  target, time horizon, maximum position, maximum daily/weekly loss, maximum
  drawdown, liquidity floor, spread/slippage ceiling, and expiration date.
- Default to limit orders, no leverage, no borrowing, no martingale, no averaging
  down outside the mandate, and no transfer/withdrawal capability.
- Generate a preview showing thesis, sources, probability/price, fees, worst-case
  loss, portfolio impact, and exact order before obtaining fresh user approval.
- Use single-use approval, idempotency keys, preflight balance/allowance checks,
  post-trade reconciliation, persistent receipts, and an immediate emergency stop.
- Recheck geoblocking and market eligibility immediately before every order.
- Halt automatically on stale data, provider disagreement, unexpected positions,
  rejected reconciliation, loss limits, authentication anomalies, or 403/429
  responses.
- Never let self-learning or model output edit risk limits, approval policy,
  signer configuration, or the live executor.

## Financial goals

A financial goal is a planning constraint, not a promised outcome. Curie should
reject targets that require breaching the loss mandate and report when the target
is statistically implausible. Optimization should maximize risk-adjusted expected
value under the mandate rather than chase a target after losses.

## Required operator inputs before live work

- Physical jurisdiction and the specific eligible venue/product.
- Account ownership and required KYC/KYB completion.
- Starting capital, target amount, deadline, and acceptable probability of loss.
- Maximum position, daily loss, weekly loss, total drawdown, and asset/market
  allowlist.
- Whether every order requires confirmation (recommended) or a narrowly scoped,
  time-limited pre-authorized mandate.
- A dedicated account/wallet with only the funds authorized for Curie.

Official references:

- https://docs.polymarket.com/trading/overview
- https://docs.polymarket.com/trading/quickstart
- https://docs.polymarket.com/api-reference/geoblock
- https://docs.polymarket.com/api-reference/trade/post-a-new-order
- https://help.polymarket.com/en/articles/13364163-geographic-restrictions
