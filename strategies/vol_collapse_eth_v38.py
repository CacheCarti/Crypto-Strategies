from domains.strategy_contract import Strategy, BarContext, Signal
from typing import Optional, Dict, Any
import math

class PostSpikeCoilBreakout(Strategy):
    METADATA = {
        "name": "PostSpikeCoilBreakout",
        "domain": "eth_usdc",
        "declared_sl_bps": 220.0,
        "declared_tp_bps": 440.0,
        "declared_hold_seconds": 18000,  # ~5 hours target swing hold
        "warmup_bars": 200,
    }

    def initialize(self, ctx: BarContext) -> None:
        self.rv_window = 168
        self.breakout_window = 12
        self.cooldown_period = 18
        
        # State tracking for the post-spike event lifecycle
        self.spike_active = False
        self.coil_armed = False
        self.coil_armed_bar = 0
        self.coil_valid_window = 24  # Coil release must trigger within 24 bars
        self.last_exit_bar = -999

    def _realized_vol(self, closes, period=14):
        if len(closes) < period + 1:
            return 0.0
        rets = [abs(closes[i] - closes[i-1]) / closes[i-1] for i in range(len(closes) - period, len(closes))]
        return sum(rets) / period

    def on_position_close(self, ctx: BarContext, position: Dict[str, Any]) -> None:
        self.last_exit_bar = ctx.bar_index
        self.coil_armed = False
        self.spike_active = False

    def on_bar(self, ctx: BarContext) -> Optional[Signal]:
        closes = ctx.closes(self.rv_window + 16)
        highs = ctx.highs(self.breakout_window + 2)
        lows = ctx.lows(self.breakout_window + 2)

        if len(closes) < self.rv_window + 15:
            return None

        # Cooldown guard: prevent churn after positions close
        if ctx.bar_index - self.last_exit_bar < self.cooldown_period:
            return None

        # Compute rolling short-term realized volatility (14-bar) over past 168 bars to find median
        rv_series = []
        for i in range(14, len(closes)):
            sub = closes[i-14:i+1]
            rets = [abs(sub[j] - sub[j-1]) / sub[j-1] for j in range(1, len(sub))]
            rv_series.append(sum(rets) / 14.0)

        if len(rv_series) < self.rv_window:
            return None

        window_rv = sorted(rv_series[-self.rv_window:])
        median_rv = window_rv[len(window_rv) // 2]
        current_rv = rv_series[-1]

        # Stage 1: Volatility spike detection (> 1.85x median)
        if current_rv > 1.85 * median_rv:
            self.spike_active = True

        # Stage 2: Volatility collapse (chaos -> compression transition)
        if self.spike_active and current_rv < median_rv:
            self.spike_active = False
            self.coil_armed = True
            self.coil_armed_bar = ctx.bar_index

        # Expire stale armed coils if no release occurs
        if self.coil_armed and (ctx.bar_index - self.coil_armed_bar > self.coil_valid_window):
            self.coil_armed = False

        # If already holding a position, manage via engine stops or explicit trail
        if ctx.has_position():
            return None

        # Stage 3: Event trigger — Coil release breakout
        if self.coil_armed:
            curr_close = ctx.bar.close
            prior_high = max(highs[-self.breakout_window-1:-1])
            prior_low = min(lows[-self.breakout_window-1:-1])

            # Trend alignment check via 50-bar SMA
            sma50 = sum(closes[-50:]) / 50.0

            if curr_close > prior_high and curr_close > sma50:
                self.coil_armed = False
                return ctx.signal(
                    "long",
                    confidence=0.75,
                    stop_loss_bps=self.METADATA["declared_sl_bps"],
                    take_profit_bps=self.METADATA["declared_tp_bps"],
                    horizon_seconds=self.METADATA["declared_hold_seconds"],
                    metadata={
                        "reason": "coil_release_high_breakout",
                        "current_rv": round(current_rv, 5),
                        "median_rv": round(median_rv, 5),
                        "prior_high": round(prior_high, 2),
                        "price": round(curr_close, 2)
                    }
                )

            elif curr_close < prior_low and curr_close < sma50:
                self.coil_armed = False
                return ctx.signal(
                    "short",
                    confidence=0.75,
                    stop_loss_bps=self.METADATA["declared_sl_bps"],
                    take_profit_bps=self.METADATA["declared_tp_bps"],
                    horizon_seconds=self.METADATA["declared_hold_seconds"],
                    metadata={
                        "reason": "coil_release_low_breakout",
                        "current_rv": round(current_rv, 5),
                        "median_rv": round(median_rv, 5),
                        "prior_low": round(prior_low, 2),
                        "price": round(curr_close, 2)
                    }
                )

        return None