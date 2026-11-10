from domains.strategy_contract import Strategy, BarContext, Signal
from typing import Optional, Dict, Any
import math

class SolVolumeBurstMomentum(Strategy):
    METADATA = {
        "name": "SolVolumeBurstMomentum",
        "domain": "sol_usdc",
        "declared_sl_bps": 280.0,
        "declared_tp_bps": 520.0,
        "declared_hold_seconds": 18000,
        "warmup_bars": 35,
    }

    def initialize(self, ctx: BarContext) -> None:
        self.vol_period = 20
        self.atr_period = 14
        self.cooldown_bars = 4
        self.max_hold_bars = 5
        self.last_exit_bar = -100
        self.entry_bar = -100

    def _atr(self, highs, lows, closes, period=14):
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
        closes = ctx.closes(35)
        if len(closes) < 30:
            return None

        # Manage existing open position: time-based exit or momentum stall
        if ctx.has_position():
            bars_held = ctx.bar_index - self.entry_bar
            curr_bar = ctx.bar
            direction = ctx.position_direction()

            # Time horizon exit
            if bars_held >= self.max_hold_bars:
                self.last_exit_bar = ctx.bar_index
                return ctx.signal(
                    "flat",
                    confidence=0.6,
                    metadata={"reason": "max_bars_reached", "bars_held": bars_held, "price": curr_bar.close}
                )

            # Reversal / Stall exit
            if direction == "long" and curr_bar.close < curr_bar.open and bars_held >= 2:
                self.last_exit_bar = ctx.bar_index
                return ctx.signal(
                    "flat",
                    confidence=0.5,
                    metadata={"reason": "long_momentum_stalled", "bars_held": bars_held, "price": curr_bar.close}
                )
            elif direction == "short" and curr_bar.close > curr_bar.open and bars_held >= 2:
                self.last_exit_bar = ctx.bar_index
                return ctx.signal(
                    "flat",
                    confidence=0.5,
                    metadata={"reason": "short_momentum_stalled", "bars_held": bars_held, "price": curr_bar.close}
                )
            return None

        # Cooldown guard after recent exit
        if ctx.bar_index - self.last_exit_bar < self.cooldown_bars:
            return None

        # Crisis regime avoidance
        crisis_score = ctx.market.get("crisis_score", 0.0)
        if crisis_score > 0.65:
            return None

        volumes = ctx.volumes(self.vol_period + 1)
        highs = ctx.highs(self.atr_period + 2)
        lows = ctx.lows(self.atr_period + 2)

        if len(volumes) < self.vol_period + 1 or len(highs) < self.atr_period + 1:
            return None

        # Volume burst condition: current volume > 2.2x prior 20-bar average
        avg_vol = sum(volumes[-self.vol_period - 1:-1]) / self.vol_period
        if avg_vol <= 0:
            return None
        current_vol = volumes[-1]
        vol_ratio = current_vol / avg_vol

        if vol_ratio < 2.2:
            return None

        # Range expansion condition: high-low spread > 1.8x ATR
        atr = self._atr(highs, lows, closes, self.atr_period)
        if atr is None or atr <= 0:
            return None

        curr_range = ctx.bar.high - ctx.bar.low
        if curr_range < 1.8 * atr:
            return None

        # Strong directional displacement
        body = ctx.bar.close - ctx.bar.open
        body_abs = abs(body)
        if curr_range == 0 or (body_abs / curr_range) < 0.60:
            return None

        # Trend alignment filter (50-bar proxy via 25-bar SMA comparison)
        sma20 = sum(closes[-20:]) / 20.0

        if body > 0 and ctx.bar.close > sma20:
            self.entry_bar = ctx.bar_index
            return ctx.signal(
                "long",
                confidence=min(0.85, 0.55 + (vol_ratio - 2.0) * 0.1),
                stop_loss_bps=self.METADATA["declared_sl_bps"],
                take_profit_bps=self.METADATA["declared_tp_bps"],
                horizon_seconds=self.METADATA["declared_hold_seconds"],
                metadata={
                    "reason": "volume_burst_long_breakout",
                    "vol_ratio": round(vol_ratio, 2),
                    "range_atr_ratio": round(curr_range / atr, 2),
                    "body_range_ratio": round(body_abs / curr_range, 2),
                    "close": ctx.bar.close
                }
            )
        elif body < 0 and ctx.bar.close < sma20:
            self.entry_bar = ctx.bar_index
            return ctx.signal(
                "short",
                confidence=min(0.85, 0.55 + (vol_ratio - 2.0) * 0.1),
                stop_loss_bps=self.METADATA["declared_sl_bps"],
                take_profit_bps=self.METADATA["declared_tp_bps"],
                horizon_seconds=self.METADATA["declared_hold_seconds"],
                metadata={
                    "reason": "volume_burst_short_breakout",
                    "vol_ratio": round(vol_ratio, 2),
                    "range_atr_ratio": round(curr_range / atr, 2),
                    "body_range_ratio": round(body_abs / curr_range, 2),
                    "close": ctx.bar.close
                }
            )

        return None