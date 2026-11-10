from domains.strategy_contract import Strategy, BarContext, Signal
from typing import Optional, Dict, Any
import math

class SolBtcSpreadFade(Strategy):
    METADATA = {
        "name": "SOL BTC Spread Fade",
        "domain": "sol_usdc",
        "declared_sl_bps": 200.0,
        "declared_tp_bps": 400.0,
        "declared_hold_seconds": 3600 * 4,  # Aim for 4-hour hold
        "warmup_bars": 60,  # Need enough bars for 20-period Z-score
        "required_features": ["btc_return_pct", "sol_return_pct"],
    }

    def initialize(self, ctx: BarContext) -> None:
        self.spread_history = []
        self.zscore_period = 20
        self.entry_zscore_threshold = 1.5  # Z-score for entry
        self.exit_zscore_threshold = 0.5  # Z-score for exit
        self.cooldown_bars = 12  # Cooldown after a trade
        self.last_trade_bar = -self.cooldown_bars

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
        mean = sum(values[-period:]) / period
        std = self._stddev(values, period)
        if std is None or std == 0:
            return 0.0
        return (values[-1] - mean) / std

    def on_bar(self, ctx: BarContext) -> Optional[Signal]:
        if ctx.bar_index < self.METADATA["warmup_bars"]:
            return None

        btc_return = ctx.features.get("btc_return_pct", 0.0)
        sol_return = ctx.features.get("sol_return_pct", 0.0)

        # Calculate spread: BTC's 24h return minus SOL's 24h return
        # A positive spread means BTC outperformed SOL; a negative spread means SOL outperformed BTC
        spread = btc_return - sol_return
        self.spread_history.append(spread)

        # Ensure we have enough data for Z-score calculation
        if len(self.spread_history) < self.zscore_period:
            return None

        current_zscore = self._zscore(self.spread_history, self.zscore_period)
        
        # Cooldown logic
        if ctx.bar_index - self.last_trade_bar < self.cooldown_bars:
            return None

        if not ctx.has_position():
            # Entry logic: Fade extreme spread dislocations
            # If BTC significantly outperformed SOL (large positive Z-score), expect SOL to catch up
            if current_zscore > self.entry_zscore_threshold:
                self.last_trade_bar = ctx.bar_index
                return ctx.signal(
                    "long",
                    confidence=min(1.0, (current_zscore - self.entry_zscore_threshold) / 1.0), # Scale confidence
                    stop_loss_bps=self.METADATA["declared_sl_bps"],
                    take_profit_bps=self.METADATA["declared_tp_bps"],
                    horizon_seconds=self.METADATA["declared_hold_seconds"],
                    metadata={
                        "reason": "sol_btc_spread_fade_long",
                        "btc_return_pct": btc_return,
                        "sol_return_pct": sol_return,
                        "spread": spread,
                        "spread_zscore": current_zscore,
                        "entry_threshold": self.entry_zscore_threshold
                    }
                )
            # If SOL significantly outperformed BTC (large negative Z-score), expect SOL to revert
            elif current_zscore < -self.entry_zscore_threshold:
                self.last_trade_bar = ctx.bar_index
                return ctx.signal(
                    "short",
                    confidence=min(1.0, (-current_zscore - self.entry_zscore_threshold) / 1.0), # Scale confidence
                    stop_loss_bps=self.METADATA["declared_sl_bps"],
                    take_profit_bps=self.METADATA["declared_tp_bps"],
                    horizon_seconds=self.METADATA["declared_hold_seconds"],
                    metadata={
                        "reason": "sol_btc_spread_fade_short",
                        "btc_return_pct": btc_return,
                        "sol_return_pct": sol_return,
                        "spread": spread,
                        "spread_zscore": current_zscore,
                        "entry_threshold": -self.entry_zscore_threshold
                    }
                )
        else:
            # Exit logic: When the spread normalizes (Z-score returns closer to zero)
            if ctx.position_direction() == "long" and current_zscore < self.exit_zscore_threshold:
                return ctx.signal(
                    "flat",
                    confidence=0.5,
                    metadata={
                        "reason": "spread_normalized_long_exit",
                        "btc_return_pct": btc_return,
                        "sol_return_pct": sol_return,
                        "spread": spread,
                        "spread_zscore": current_zscore,
                        "exit_threshold": self.exit_zscore_threshold
                    }
                )
            elif ctx.position_direction() == "short" and current_zscore > -self.exit_zscore_threshold:
                return ctx.signal(
                    "flat",
                    confidence=0.5,
                    metadata={
                        "reason": "spread_normalized_short_exit",
                        "btc_return_pct": btc_return,
                        "sol_return_pct": sol_return,
                        "spread": spread,
                        "spread_zscore": current_zscore,
                        "exit_threshold": -self.exit_zscore_threshold
                    }
                )
        return None

    def on_position_close(self, ctx: BarContext, position: Dict[str, Any]) -> None:
        # Reset cooldown after a position closes
        self.last_trade_bar = ctx.bar_index