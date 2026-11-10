from domains.strategy_contract import Strategy, BarContext, Signal
from typing import Optional, Dict, Any
import math

class CrossAssetBetaConvergence(Strategy):
    METADATA = {
        "name": "Cross Asset Beta Convergence (SOL-BTC)",
        "domain": "btc_usdc",
        "declared_sl_bps": 200.0,
        "declared_tp_bps": 400.0,
        "declared_hold_seconds": 7200, # 2 hours avg hold
        "warmup_bars": 50,
        "required_features": ["sol_return_pct", "eth_return_pct"],
    }

    def initialize(self, ctx: BarContext) -> None:
        self.lookback_window = 24  # Look back 24 bars for recent returns
        self.cooldown_bars = 5   # Cooldown period after a trade
        self.last_trade_bar = -self.cooldown_bars - 1 # Initialize to allow immediate trade

    def _sma(self, values, period):
        if len(values) < period:
            return None
        return sum(values[-period:]) / period

    def on_bar(self, ctx: BarContext) -> Optional[Signal]:
        closes = ctx.closes(self.lookback_window + 1)
        if len(closes) < self.METADATA["warmup_bars"]:
            return None

        # Cooldown logic
        if ctx.bar_index <= self.last_trade_bar + self.cooldown_bars:
            return None

        sol_returns = ctx.features.get("sol_return_pct", 0.0)
        btc_returns = ctx.features.get("eth_return_pct", 0.0) # Using ETH as a proxy for BTC's *own* momentum, as sol_return_pct is the driver. We want BTC to be lagging relative to ETH.

        # Regime filters
        trend_regime = ctx.market.get("trend_regime", "neutral")
        regime = ctx.market.get("regime", "NORMAL")

        # Only trade in normal or volatile regimes, avoid crisis
        if regime in ["CRISIS", "MELTDOWN"]:
            return None

        # Check for significant recent move in SOL (the leader)
        # We want a fresh, decisive move, not stale returns
        sol_recent_move_threshold = 0.5 # %
        btc_lag_threshold = 0.2 # %
        
        # Calculate recent BTC price change
        if len(closes) < 2:
            return None
        btc_current_price = closes[-1]
        btc_prev_price = closes[-2]
        btc_price_change_pct = ((btc_current_price - btc_prev_price) / btc_prev_price) * 100.0

        signal_direction = None
        reason = ""
        confidence = 0.0

        # Long condition: SOL makes a strong positive move, but BTC has not caught up (or even slightly negative)
        if sol_returns > sol_recent_move_threshold and btc_price_change_pct < sol_returns - btc_lag_threshold:
            if not ctx.has_position():
                signal_direction = "long"
                reason = "sol_leading_btc_lagging_bullish"
                confidence = min(0.8, abs(sol_returns - btc_price_change_pct) / 2.0) # Confidence scales with divergence
            elif ctx.position_direction() == "short": # Flip from short to long
                signal_direction = "long"
                reason = "sol_leading_btc_lagging_bullish_flip"
                confidence = min(0.8, abs(sol_returns - btc_price_change_pct) / 2.0)

        # Short condition: SOL makes a strong negative move, but BTC has not caught up (or even slightly positive)
        elif sol_returns < -sol_recent_move_threshold and btc_price_change_pct > sol_returns + btc_lag_threshold:
            if not ctx.has_position():
                signal_direction = "short"
                reason = "sol_leading_btc_lagging_bearish"
                confidence = min(0.8, abs(sol_returns - btc_price_change_pct) / 2.0)
            elif ctx.position_direction() == "long": # Flip from long to short
                signal_direction = "short"
                reason = "sol_leading_btc_lagging_bearish_flip"
                confidence = min(0.8, abs(sol_returns - btc_price_change_pct) / 2.0)

        # Exit condition: If in a position and the divergence normalizes or reverses
        if ctx.has_position():
            if ctx.position_direction() == "long":
                # Exit long if SOL momentum weakens or BTC catches up
                if sol_returns < 0.1 or btc_price_change_pct >= sol_returns - btc_lag_threshold / 2:
                    signal_direction = "flat"
                    reason = "long_convergence_or_sol_momentum_loss"
                    confidence = 0.5
            elif ctx.position_direction() == "short":
                # Exit short if SOL momentum weakens or BTC catches up
                if sol_returns > -0.1 or btc_price_change_pct <= sol_returns + btc_lag_threshold / 2:
                    signal_direction = "flat"
                    reason = "short_convergence_or_sol_momentum_loss"
                    confidence = 0.5

        if signal_direction:
            self.last_trade_bar = ctx.bar_index
            return ctx.signal(
                signal_direction,
                confidence=confidence,
                metadata={
                    "reason": reason,
                    "sol_return_pct": sol_returns,
                    "btc_price_change_pct": btc_price_change_pct,
                    "regime": regime,
                    "trend_regime": trend_regime,
                }
            )

        return None

    def on_position_close(self, ctx: BarContext, position: Dict[str, Any]) -> None:
        # Optional: Log position close details or adapt future behavior
        pass