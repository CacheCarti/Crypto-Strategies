from domains.strategy_contract import Strategy, BarContext, Signal
from typing import Optional, Dict, Any
import math

class SessionCloseMomentum(Strategy):
    METADATA = {
        "name": "Session Close Momentum SOL",
        "domain": "sol_usdc",
        "declared_sl_bps": 250.0,
        "declared_tp_bps": 500.0,
        "declared_hold_seconds": 3 * 3600, # Intended hold until UTC 0 or stop
        "warmup_bars": 50,
        "required_features": [], # No external features used for this strategy
    }

    def initialize(self, ctx: BarContext) -> None:
        self.lookback_period = 4  # Hours before UTC 21
        self.cooldown_bars = 5
        self.last_trade_bar = -self.cooldown_bars

    def _sma(self, values, period):
        if len(values) < period:
            return None
        return sum(values[-period:]) / period

    def _atr(self, highs, lows, closes, period=14):
        if len(closes) < period + 1:
            return None
        trs = []
        for i in range(1, len(closes)):
            tr = max(highs[i]-lows[i], abs(highs[i]-closes[i-1]), abs(lows[i]-closes[i-1]))
            trs.append(tr)
        return sum(trs[-period:]) / period

    def on_bar(self, ctx: BarContext) -> Optional[Signal]:
        closes = ctx.closes(self.METADATA["warmup_bars"])
        highs = ctx.highs(self.METADATA["warmup_bars"])
        lows = ctx.lows(self.METADATA["warmup_bars"])
        volumes = ctx.volumes(self.METADATA["warmup_bars"])

        if len(closes) < self.METADATA["warmup_bars"]:
            return None

        current_hour = ctx.bar.timestamp.hour

        # Exit logic: Flat if it's UTC 0 or later, or if it's past our hold time and still open
        if ctx.has_position():
            if current_hour >= 0 and current_hour < 6: # Exit between UTC 0 and 6 AM
                return ctx.signal("flat", confidence=0.8, metadata={"reason": "session_exit_time", "current_hour": current_hour})

        # Entry logic: Only consider entries around UTC 21:00 (end of US session)
        if current_hour != 20: # We need data up to and including the 20:00 bar to make a decision at 21:00
            return None

        # Cooldown check
        if ctx.bar_index - self.last_trade_bar < self.cooldown_bars:
            return None

        # Ensure we have enough bars for the lookback period
        required_bars = self.lookback_period + 1
        if len(closes) < required_bars:
            return None

        # Calculate momentum over the last 'lookback_period' hours (e.g., 17:00 to 20:00)
        # We need closes from 20:00, 19:00, 18:00, 17:00
        lookback_closes = closes[-self.lookback_period-1:]
        lookback_highs = highs[-self.lookback_period-1:]
        lookback_lows = lows[-self.lookback_period-1:]
        lookback_volumes = volumes[-self.lookback_period-1:]

        # Calculate price change over the lookback period
        price_change_pct = (lookback_closes[-1] - lookback_closes[0]) / lookback_closes[0] * 100

        # Calculate average range and volume for comparison
        ranges = [h - l for h, l in zip(lookback_highs, lookback_lows)]
        avg_range = sum(ranges) / len(ranges)
        avg_volume = sum(lookback_volumes) / len(lookback_volumes)

        # Current bar's range and volume (the 20:00 bar)
        current_range = ctx.bar.high - ctx.bar.low
        current_volume = ctx.bar.volume

        # Median range and volume (using a longer period for better context)
        # Using a simple SMA of 10 bars for "median" proxy
        sma_range_10 = self._sma([h-l for h,l in zip(highs[-10:], lows[-10:])], 10)
        sma_volume_10 = self._sma(volumes[-10:], 10)

        # Check for strong close
        is_strong_close = False
        if price_change_pct > 0.5 and \
           current_range > (sma_range_10 * 1.2) and \
           current_volume > (sma_volume_10 * 1.5):
            is_strong_close = True

        # Check for weak close (bearish momentum)
        is_weak_close = False
        if price_change_pct < -0.5 and \
           current_range > (sma_range_10 * 1.2) and \
           current_volume > (sma_volume_10 * 1.5):
            is_weak_close = True

        signal_confidence = 0.7 # Base confidence

        if ctx.has_position():
            # Already in a position, do not re-enter or flip unless conditions are extreme
            return None

        if is_strong_close:
            self.last_trade_bar = ctx.bar_index
            return ctx.signal(
                "long",
                confidence=signal_confidence,
                stop_loss_bps=self.METADATA["declared_sl_bps"],
                take_profit_bps=self.METADATA["declared_tp_bps"],
                horizon_seconds=self.METADATA["declared_hold_seconds"],
                metadata={
                    "reason": "session_close_momentum_long",
                    "price_change_pct": round(price_change_pct, 2),
                    "current_range_ratio": round(current_range / (sma_range_10 + 1e-9), 2),
                    "current_volume_ratio": round(current_volume / (sma_volume_10 + 1e-9), 2),
                    "close_hour": current_hour
                }
            )
        elif is_weak_close:
            self.last_trade_bar = ctx.bar_index
            return ctx.signal(
                "short",
                confidence=signal_confidence,
                stop_loss_bps=self.METADATA["declared_sl_bps"],
                take_profit_bps=self.METADATA["declared_tp_bps"],
                horizon_seconds=self.METADATA["declared_hold_seconds"],
                metadata={
                    "reason": "session_close_momentum_short",
                    "price_change_pct": round(price_change_pct, 2),
                    "current_range_ratio": round(current_range / (sma_range_10 + 1e-9), 2),
                    "current_volume_ratio": round(current_volume / (sma_volume_10 + 1e-9), 2),
                    "close_hour": current_hour
                }
            )

        return None

    def on_position_close(self, ctx: BarContext, position: Dict[str, Any]) -> None:
        # We track last_trade_bar on entry, but also update it on close to ensure cooldown applies
        # even if an external close (SL/TP) happens
        self.last_trade_bar = ctx.bar_index
        pass