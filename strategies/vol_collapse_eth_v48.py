from domains.strategy_contract import Strategy, BarContext, Signal
from typing import Optional, Dict, Any
import math

class PostSpikeCoilBreakout(Strategy):
    METADATA = {
        "name": "PostSpikeCoilBreakout",
        "domain": "eth_usdc",
        "declared_sl_bps": 250.0,
        "declared_tp_bps": 500.0,
        "declared_hold_seconds": 14400,  # 4 hours average hold
        "warmup_bars": 180,
    }

    def initialize(self, ctx: BarContext) -> None:
        self.vol_lookback = 168
        self.breakout_period = 12
        self.event_window = 24
        self.cooldown_period = 8

        self.spike_active = False
        self.coil_active = False
        self.coil_expiry_bar = 0
        self.cooldown_until_bar = 0

    def _realized_vol(self, closes, window=12):
        if len(closes) < window + 1:
            return 0.0
        log_rets = [math.log(closes[i] / closes[i - 1]) for i in range(len(closes) - window, len(closes))]
        mean = sum(log_rets) / window
        var = sum((r - mean) ** 2 for r in log_rets) / window
        return math.sqrt(var)

    def on_position_close(self, ctx: BarContext, position: Dict[str, Any]) -> None:
        self.cooldown_until_bar = ctx.bar_index + self.cooldown_period
        self.coil_active = False

    def on_bar(self, ctx: BarContext) -> Optional[Signal]:
        closes = ctx.closes(self.vol_lookback + 14)
        if len(closes) < self.vol_lookback + 13:
            return None

        # Calculate rolling short-term realized vol history over 168 bars
        rv_history = []
        for i in range(self.vol_lookback):
            idx = len(closes) - self.vol_lookback + i
            sub_c = closes[idx - 12: idx + 1]
            rv_history.append(self._realized_vol(sub_c, 12))

        current_rv = rv_history[-1]
        sorted_rv = sorted(rv_history)
        median_rv = sorted_rv[len(sorted_rv) // 2]

        if median_rv <= 1e-7:
            return None

        # State machine tracking chaos -> compression transition
        if current_rv > 2.0 * median_rv:
            self.spike_active = True
        elif self.spike_active and current_rv <= median_rv:
            # Transitioned: chaos subsided into coil
            self.spike_active = False
            self.coil_active = True
            self.coil_expiry_bar = ctx.bar_index + self.event_window

        if ctx.bar_index > self.coil_expiry_bar:
            self.coil_active = False

        if ctx.has_position():
            return None

        if ctx.bar_index < self.cooldown_until_bar:
            return None

        # Only trigger breakout while the coil regime is live
        if self.coil_active:
            highs = ctx.highs(self.breakout_period + 1)
            lows = ctx.lows(self.breakout_period + 1)
            if len(highs) < self.breakout_period + 1 or len(lows) < self.breakout_period + 1:
                return None

            recent_high = max(highs[-self.breakout_period - 1: -1])
            recent_low = min(lows[-self.breakout_period - 1: -1])
            curr_close = ctx.bar.close

            if curr_close > recent_high:
                self.coil_active = False
                return ctx.signal(
                    "long",
                    confidence=0.85,
                    stop_loss_bps=250.0,
                    take_profit_bps=500.0,
                    horizon_seconds=14400,
                    metadata={
                        "reason": "post_spike_coil_bullish_breakout",
                        "current_rv": round(current_rv, 6),
                        "median_rv": round(median_rv, 6),
                        "breakout_high": round(recent_high, 2),
                        "close": round(curr_close, 2),
                    }
                )
            elif curr_close < recent_low:
                self.coil_active = False
                return ctx.signal(
                    "short",
                    confidence=0.85,
                    stop_loss_bps=250.0,
                    take_profit_bps=500.0,
                    horizon_seconds=14400,
                    metadata={
                        "reason": "post_spike_coil_bearish_breakdown",
                        "current_rv": round(current_rv, 6),
                        "median_rv": round(median_rv, 6),
                        "breakdown_low": round(recent_low, 2),
                        "close": round(curr_close, 2),
                    }
                )

        return None