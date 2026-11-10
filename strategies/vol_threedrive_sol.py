from domains.strategy_contract import Strategy, BarContext, Signal
from typing import Optional, Dict, Any
import math

class VolThreeDriveSol(Strategy):
    METADATA = {
        "name": "Vol Three Drive SOL",
        "domain": "sol_usdc",
        "declared_sl_bps": 200.0,
        "declared_tp_bps": 400.0,
        "declared_hold_seconds": 3600,
        "warmup_bars": 50,
        "required_features": [],
    }

    def initialize(self, ctx: BarContext) -> None:
        self.cooldown_bars = 10
        self.last_signal_bar = -self.cooldown_bars
        self.drive_period = 3
        self.momentum_period = 5
        self.min_drive_strength_pct = 0.5 # Minimum percentage move for a drive
        self.momentum_threshold = 0.0 # Momentum must be weakening (negative for long, positive for short)
        self.max_consecutive_drives = 3 # Max number of drives to look for

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

    def _momentum(self, closes, period):
        if len(closes) < period:
            return None
        return closes[-1] - closes[-period]

    def on_bar(self, ctx: BarContext) -> Optional[Signal]:
        closes = ctx.closes(self.warmup_bars)
        highs = ctx.highs(self.warmup_bars)
        lows = ctx.lows(self.warmup_bars)

        if len(closes) < self.warmup_bars:
            return None

        if ctx.bar_index < self.last_signal_bar + self.cooldown_bars:
            return None

        current_price = ctx.bar.close

        # Check for 3 successive drives up (potential short)
        # A "drive" is a sustained move in one direction followed by weakening momentum
        long_drives = []
        for i in range(1, self.max_consecutive_drives + 1):
            start_index = len(closes) - (self.drive_period * i)
            end_index = len(closes) - (self.drive_period * (i - 1))
            if start_index < 0:
                break

            drive_closes = closes[start_index:end_index]
            drive_highs = highs[start_index:end_index]
            drive_lows = lows[start_index:end_index]

            if len(drive_closes) < self.drive_period:
                break

            drive_start_price = drive_closes[0]
            drive_end_price = drive_closes[-1]
            drive_peak = max(drive_highs)
            drive_trough = min(drive_lows)

            # Check for a clear directional push
            if drive_end_price > drive_start_price * (1 + self.min_drive_strength_pct / 100):
                long_drives.append({"start": drive_start_price, "end": drive_end_price, "peak": drive_peak})
            else:
                break # Drive sequence broken

        # Check for 3 successive drives down (potential long)
        short_drives = []
        for i in range(1, self.max_consecutive_drives + 1):
            start_index = len(closes) - (self.drive_period * i)
            end_index = len(closes) - (self.drive_period * (i - 1))
            if start_index < 0:
                break

            drive_closes = closes[start_index:end_index]
            drive_highs = highs[start_index:end_index]
            drive_lows = lows[start_index:end_index]

            if len(drive_closes) < self.drive_period:
                break

            drive_start_price = drive_closes[0]
            drive_end_price = drive_closes[-1]
            drive_peak = max(drive_highs)
            drive_trough = min(drive_lows)

            # Check for a clear directional push
            if drive_end_price < drive_start_price * (1 - self.min_drive_strength_pct / 100):
                short_drives.append({"start": drive_start_price, "end": drive_end_price, "trough": drive_trough})
            else:
                break # Drive sequence broken

        # Short signal logic
        if len(long_drives) >= self.max_consecutive_drives:
            # Check for weakening momentum on the current bar or last drive
            current_momentum = self._momentum(closes, self.momentum_period)
            if current_momentum is not None and current_momentum < self.momentum_threshold:
                # Confirm with a failure to push higher on the current bar
                if ctx.bar.close < long_drives[0]["end"]: # Current close lower than end of last drive
                    self.last_signal_bar = ctx.bar_index
                    return ctx.signal(
                        "short",
                        confidence=0.7,
                        metadata={
                            "reason": "three_drive_up_momentum_weakening",
                            "current_momentum": current_momentum,
                            "last_drive_end_price": long_drives[0]["end"],
                            "current_close": current_price,
                            "regime": ctx.regime
                        },
                    )

        # Long signal logic
        if len(short_drives) >= self.max_consecutive_drives:
            # Check for weakening momentum on the current bar or last drive
            current_momentum = self._momentum(closes, self.momentum_period)
            if current_momentum is not None and current_momentum > self.momentum_threshold:
                # Confirm with a failure to push lower on the current bar
                if ctx.bar.close > short_drives[0]["end"]: # Current close higher than end of last drive
                    self.last_signal_bar = ctx.bar_index
                    return ctx.signal(
                        "long",
                        confidence=0.7,
                        metadata={
                            "reason": "three_drive_down_momentum_weakening",
                            "current_momentum": current_momentum,
                            "last_drive_end_price": short_drives[0]["end"],
                            "current_close": current_price,
                            "regime": ctx.regime
                        },
                    )

        # Exit logic
        if ctx.has_position():
            # If position is losing money and regime is volatile, consider exiting early
            if ctx.regime in ["volatile", "crisis"] and ctx.bar.close < ctx.position["entry_price"] * (1 - self.METADATA["declared_sl_bps"] / 10000 / 2): # half the SL
                return ctx.signal("flat", confidence=0.5, metadata={"reason": "regime_change_early_exit", "current_regime": ctx.regime, "loss_pct": (ctx.position["entry_price"] - ctx.bar.close) / ctx.position["entry_price"] * 100})

        return None

    def on_position_close(self, ctx: BarContext, position: Dict[str, Any]) -> None:
        # Reset cooldown after position closes
        self.last_signal_bar = ctx.bar_index