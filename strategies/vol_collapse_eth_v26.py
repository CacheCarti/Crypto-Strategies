from domains.strategy_contract import Strategy, BarContext, Signal
from typing import Optional, Dict, Any
import math

class PostSpikeCoilBreakout(Strategy):
    METADATA = {
        "name": "PostSpikeCoilBreakout",
        "domain": "eth_usdc",
        "declared_sl_bps": 250.0,
        "declared_tp_bps": 500.0,
        "declared_hold_seconds": 14400,
        "warmup_bars": 175,
    }

    def initialize(self, ctx: BarContext) -> None:
        self.median_period = 168
        self.breakout_period = 12
        self.cooldown_bars = 6
        self.last_exit_bar = -999
        self.spike_seen = False
        self.coil_armed = False

    def _returns_std(self, closes: list, window: int = 12) -> float:
        if len(closes) < window + 1:
            return 0.0
        rets = [abs(closes[i] / closes[i - 1] - 1.0) for i in range(len(closes) - window, len(closes))]
        mean_ret = sum(rets) / window
        var = sum((r - mean_ret) ** 2 for r in rets) / window
        return math.sqrt(var)

    def on_position_close(self, ctx: BarContext, position: Dict[str, Any]) -> None:
        self.last_exit_bar = ctx.bar_index
        self.coil_armed = False
        self.spike_seen = False

    def on_bar(self, ctx: BarContext) -> Optional[Signal]:
        closes = ctx.closes(self.median_period + 15)
        if len(closes) < self.median_period + 13:
            return None

        # Cooldown guard to avoid churn and friction bleed
        if ctx.bar_index - self.last_exit_bar < self.cooldown_bars:
            return None

        # Calculate current short-term volatility (12-bar realized vol of returns)
        current_vol = self._returns_std(closes, 12)
        if current_vol == 0.0:
            return None

        # Calculate historical 12-bar realized vols over the last 168 bars for median baseline
        # Sample every 6 bars to remain fast within compute budget
        vol_samples = []
        for offset in range(12, self.median_period + 12, 6):
            sub_closes = closes[-offset - 12 : -offset]
            if len(sub_closes) >= 12:
                vol_samples.append(self._returns_std(sub_closes, 12))

        if len(vol_samples) < 10:
            return None

        sorted_vols = sorted(vol_samples)
        median_vol = sorted_vols[len(sorted_vols) // 2]
        if median_vol <= 0.0:
            return None

        # Track chaotic spike transition into compression coil
        if current_vol > 1.8 * median_vol:
            self.spike_seen = True
            self.coil_armed = False

        if self.spike_seen and current_vol < 1.05 * median_vol:
            self.coil_armed = True
            self.spike_seen = False

        # If holding a position, check exit via time or reversal without re-entering
        if ctx.has_position():
            return None

        # Entry logic: break of 12-bar high/low only when coil is armed
        if self.coil_armed:
            highs = ctx.highs(self.breakout_period + 1)
            lows = ctx.lows(self.breakout_period + 1)
            
            recent_high = max(highs[-self.breakout_period - 1 : -1])
            recent_low = min(lows[-self.breakout_period - 1 : -1])
            curr_close = ctx.bar.close

            # Release long
            if curr_close > recent_high:
                self.coil_armed = False
                return ctx.signal(
                    "long",
                    confidence=0.8,
                    stop_loss_bps=self.METADATA["declared_sl_bps"],
                    take_profit_bps=self.METADATA["declared_tp_bps"],
                    metadata={
                        "reason": "coil_expansion_breakout_high",
                        "current_vol": round(current_vol, 6),
                        "median_vol": round(median_vol, 6),
                        "breakout_level": round(recent_high, 2),
                        "close": round(curr_close, 2),
                    },
                )
            # Release short
            elif curr_close < recent_low:
                self.coil_armed = False
                return ctx.signal(
                    "short",
                    confidence=0.8,
                    stop_loss_bps=self.METADATA["declared_sl_bps"],
                    take_profit_bps=self.METADATA["declared_tp_bps"],
                    metadata={
                        "reason": "coil_expansion_breakout_low",
                        "current_vol": round(current_vol, 6),
                        "median_vol": round(median_vol, 6),
                        "breakout_level": round(recent_low, 2),
                        "close": round(curr_close, 2),
                    },
                )

        return None