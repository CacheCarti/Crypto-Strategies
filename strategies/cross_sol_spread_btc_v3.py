from domains.strategy_contract import Strategy, BarContext, Signal
from typing import Optional, Dict, Any
import math

class BtcSolSpreadZScoreFade(Strategy):
    METADATA = {
        "name": "BTC-SOL Spread Z-Score Fade",
        "domain": "btc_usdc",
        "declared_sl_bps": 250.0,
        "declared_tp_bps": 500.0,
        "declared_hold_seconds": 3600 * 6, # Aim for up to 6 hours
        "warmup_bars": 100,
        "required_features": ["btc_return_pct", "sol_return_pct"],
    }

    def initialize(self, ctx: BarContext) -> None:
        self.spread_period = 60 # Lookback for spread calculation
        self.zscore_period = 60 # Lookback for z-score calculation
        self.zscore_threshold = 1.8 # Z-score deviation to trigger fade
        self.cooldown_bars = 12 # Cooldown period after a trade
        self.last_trade_bar = -self.cooldown_bars # Initialize to allow immediate trading

        # Store previous z-score to detect transitions
        self.prev_zscore = 0.0

    def _sma(self, values, period):
        if len(values) < period:
            return None
        return sum(values[-period:]) / period

    def _stddev(self, values, period):
        if len(values) < period:
            return None
        slice_v = values[-period:]
        mean = sum(slice_v) / period
        variance = sum((x - mean) ** 2 for x in slice_v) / period
        return math.sqrt(variance)

    def _zscore(self, values, period):
        if len(values) < period:
            return None
        mean = self._sma(values, period)
        std = self._stddev(values, period)
        if std is None or std == 0:
            return 0.0
        return (values[-1] - mean) / std

    def on_bar(self, ctx: BarContext) -> Optional[Signal]:
        if ctx.bar_index < self.METADATA["warmup_bars"]:
            return None

        btc_returns = [ctx.features.get("btc_return_pct", 0.0) for _ in range(self.spread_period)]
        sol_returns = [ctx.features.get("sol_return_pct", 0.0) for _ in range(self.spread_period)]

        # Ensure we have enough data for the spread calculation
        if len(btc_returns) < self.spread_period or len(sol_returns) < self.spread_period:
            return None

        # Calculate the spread: BTC return - SOL return
        # We assume the features provide current bar's return, so we build a history
        spreads = []
        for i in range(self.spread_period):
            if i < len(btc_returns) and i < len(sol_returns):
                spreads.append(btc_returns[i] - sol_returns[i])

        if len(spreads) < self.zscore_period:
            return None

        current_zscore = self._zscore(spreads, self.zscore_period)
        if current_zscore is None:
            return None

        # Cooldown logic
        if ctx.bar_index - self.last_trade_bar < self.cooldown_bars:
            self.prev_zscore = current_zscore
            return None

        signal_direction = None
        confidence = 0.0
        reason = "hold"
        metadata = {
            "current_zscore": current_zscore,
            "zscore_threshold": self.zscore_threshold,
            "btc_return_pct": ctx.features.get("btc_return_pct", 0.0),
            "sol_return_pct": ctx.features.get("sol_return_pct", 0.0),
        }

        # Fade extreme positive z-score (BTC outperformed SOL too much, expect BTC to underperform)
        if current_zscore > self.zscore_threshold and self.prev_zscore <= self.zscore_threshold:
            if not ctx.has_position():
                signal_direction = "short" # Short BTC, expecting it to revert relative to SOL
                confidence = min(1.0, (current_zscore - self.zscore_threshold) / 2.0) # Scale confidence
                reason = "spread_zscore_fade_short"
            elif ctx.position_direction() == "long":
                signal_direction = "flat" # Exit long to flip or avoid further deviation
                reason = "spread_zscore_fade_exit_long_for_reversion"

        # Fade extreme negative z-score (SOL outperformed BTC too much, expect BTC to outperform)
        elif current_zscore < -self.zscore_threshold and self.prev_zscore >= -self.zscore_threshold:
            if not ctx.has_position():
                signal_direction = "long" # Long BTC, expecting it to revert relative to SOL
                confidence = min(1.0, (-current_zscore - self.zscore_threshold) / 2.0) # Scale confidence
                reason = "spread_zscore_fade_long"
            elif ctx.position_direction() == "short":
                signal_direction = "flat" # Exit short to flip or avoid further deviation
                reason = "spread_zscore_fade_exit_short_for_reversion"

        # Exit conditions: Spread normalizes
        if ctx.has_position():
            # If z-score crosses back towards zero from an extreme
            if ctx.position_direction() == "short" and current_zscore < 0.5: # Short position, z-score moved back to normal
                signal_direction = "flat"
                reason = "spread_zscore_exit_short_normalized"
            elif ctx.position_direction() == "long" and current_zscore > -0.5: # Long position, z-score moved back to normal
                signal_direction = "flat"
                reason = "spread_zscore_exit_long_normalized"

        self.prev_zscore = current_zscore

        if signal_direction:
            self.last_trade_bar = ctx.bar_index
            return ctx.signal(
                signal_direction,
                confidence=confidence,
                stop_loss_bps=self.METADATA["declared_sl_bps"],
                take_profit_bps=self.METADATA["declared_tp_bps"],
                horizon_seconds=self.METADATA["declared_hold_seconds"],
                metadata={**metadata, "reason": reason}
            )

        return None

    def on_position_close(self, ctx: BarContext, position: Dict[str, Any]) -> None:
        # Reset last trade bar on close to enforce cooldown
        self.last_trade_bar = ctx.bar_index
        # Log position close for debugging/analysis
        ctx.log(f"Position closed: {position['direction']} at {position['exit_price']} (entry: {position['entry_price']}), return: {position['return_bps']} bps, held for {position['bars_held']} bars. Reason: {position.get('exit_reason', 'unknown')}")