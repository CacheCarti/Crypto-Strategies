from domains.strategy_contract import Strategy, BarContext, Signal
from typing import Optional, Dict, Any
import math

class VolumeBurstContinuationScalp(Strategy):
    METADATA = {
        "name": "VolumeBurstContinuationScalp",
        "domain": "btc_usdc_scalp",
        "declared_sl_bps": 85.0,
        "declared_tp_bps": 170.0,
        "declared_hold_seconds": 1200,
        "warmup_bars": 30,
    }

    def initialize(self, ctx: BarContext) -> None:
        self.vol_period = 24
        self.atr_period = 14
        self.vol_mult = 3.2
        self.max_hold_bars = 4
        self.cooldown_bars = 16
        self.entry_bar = -999
        self.last_exit_bar = -999

    def _atr(self, highs, lows, closes, period):
        if len(closes) < period + 1:
            return None
        trs = []
        for i in range(1, len(closes)):
            tr = max(highs[i] - lows[i], abs(highs[i] - closes[i - 1]), abs(lows[i] - closes[i - 1]))
            trs.append(tr)
        return sum(trs[-period:]) / period

    def on_position_close(self, ctx: BarContext, position: Dict[str, Any]) -> None:
        self.last_exit_bar = ctx.bar_index

    def on_bar(self, ctx: BarContext) -> Optional[Signal]:
        closes = ctx.closes(self.vol_period + 2)
        if len(closes) < self.vol_period + 1:
            return None

        # Position management: time-based exit for short-lived scalps
        if ctx.has_position():
            bars_held = ctx.bar_index - self.entry_bar
            if bars_held >= self.max_hold_bars:
                pos_dir = ctx.position_direction() or "unknown"
                return ctx.signal(
                    "flat",
                    confidence=0.8,
                    metadata={
                        "reason": "max_bars_timeout_scalp_exit",
                        "bars_held": bars_held,
                        "position_direction": pos_dir,
                        "close": ctx.bar.close
                    }
                )
            return None

        # Cooldown guard after prior trade exit
        if ctx.bar_index - self.last_exit_bar < self.cooldown_bars:
            return None

        volumes = ctx.volumes(self.vol_period + 1)
        highs = ctx.highs(self.vol_period + 1)
        lows = ctx.lows(self.vol_period + 1)

        # Baseline volume average (excluding current triggering bar)
        base_volumes = volumes[-self.vol_period - 1:-1]
        avg_vol = sum(base_volumes) / self.vol_period
        if avg_vol <= 0:
            return None

        curr_vol = volumes[-1]
        vol_ratio = curr_vol / avg_vol
        if vol_ratio < self.vol_mult:
            return None

        bar_high = highs[-1]
        bar_low = lows[-1]
        bar_close = closes[-1]
        bar_range = bar_high - bar_low

        if bar_range <= 0:
            return None

        # Ensure bar expansion is meaningful relative to recent volatility
        atr = self._atr(highs, lows, closes, self.atr_period)
        if atr is None or atr <= 0:
            return None

        if bar_range < 1.1 * atr:
            return None

        # Bar quartile position: 0.0 (closed at low) to 1.0 (closed at high)
        quartile_pos = (bar_close - bar_low) / bar_range

        # Long breakout continuation: closed in upper 22% of bar range
        if quartile_pos >= 0.78:
            self.entry_bar = ctx.bar_index
            conf = min(0.95, 0.65 + (vol_ratio - self.vol_mult) * 0.08)
            return ctx.signal(
                "long",
                confidence=conf,
                stop_loss_bps=self.METADATA["declared_sl_bps"],
                take_profit_bps=self.METADATA["declared_tp_bps"],
                horizon_seconds=self.METADATA["declared_hold_seconds"],
                metadata={
                    "reason": "volume_burst_bullish_quartile_break",
                    "vol_ratio": round(vol_ratio, 2),
                    "quartile_pos": round(quartile_pos, 3),
                    "bar_range": round(bar_range, 2),
                    "atr": round(atr, 2),
                    "close": bar_close
                }
            )

        # Short breakdown continuation: closed in lower 22% of bar range
        if quartile_pos <= 0.22:
            self.entry_bar = ctx.bar_index
            conf = min(0.95, 0.65 + (vol_ratio - self.vol_mult) * 0.08)
            return ctx.signal(
                "short",
                confidence=conf,
                stop_loss_bps=self.METADATA["declared_sl_bps"],
                take_profit_bps=self.METADATA["declared_tp_bps"],
                horizon_seconds=self.METADATA["declared_hold_seconds"],
                metadata={
                    "reason": "volume_burst_bearish_quartile_break",
                    "vol_ratio": round(vol_ratio, 2),
                    "quartile_pos": round(quartile_pos, 3),
                    "bar_range": round(bar_range, 2),
                    "atr": round(atr, 2),
                    "close": bar_close
                }
            )

        return None