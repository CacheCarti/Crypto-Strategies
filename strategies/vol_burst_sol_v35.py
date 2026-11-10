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
        self.period = 20
        self.cooldown_bars = 5
        self.max_hold_bars = 6
        self.last_exit_bar = -100
        self.entry_bar = -100
        self.entry_price = 0.0

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
        self.entry_bar = -100
        self.entry_price = 0.0

    def on_bar(self, ctx: BarContext) -> Optional[Signal]:
        closes = ctx.closes(35)
        highs = ctx.highs(35)
        lows = ctx.lows(35)
        volumes = ctx.volumes(35)

        if len(closes) < self.METADATA["warmup_bars"]:
            return None

        # Position Management
        if ctx.has_position():
            direction = ctx.position_direction()
            bars_held = ctx.bar_index - self.entry_bar
            current_close = ctx.bar.close

            # Fixed time horizon exit (5-6 bars ~ 5-6 hours)
            if bars_held >= self.max_hold_bars:
                return ctx.signal(
                    "flat",
                    confidence=0.6,
                    metadata={"reason": "time_stop_stall", "bars_held": bars_held, "close": current_close}
                )

            # Momentum stall / reversal exit: current bar opposing entry direction significantly
            bar_change_pct = (ctx.bar.close - ctx.bar.open) / ctx.bar.open if ctx.bar.open > 0 else 0.0
            if direction == "long" and bar_change_pct < -0.015:
                return ctx.signal(
                    "flat",
                    confidence=0.7,
                    metadata={"reason": "momentum_stalled_bearish_bar", "bar_change_pct": bar_change_pct}
                )
            elif direction == "short" and bar_change_pct > 0.015:
                return ctx.signal(
                    "flat",
                    confidence=0.7,
                    metadata={"reason": "momentum_stalled_bullish_bar", "bar_change_pct": bar_change_pct}
                )
            return None

        # Cooldown guard after exit to prevent overtrading and ensure independent trades
        if ctx.bar_index - self.last_exit_bar < self.cooldown_bars:
            return None

        # Market regime filter: avoid entering during extreme crisis or meltdowns
        crisis_score = ctx.market.get("crisis_score", 0.0)
        regime = ctx.market.get("regime", "NORMAL")
        if crisis_score > 0.75 or regime == "MELTDOWN":
            return None

        # Compute volume surge
        avg_vol = sum(volumes[-21:-1]) / 20.0
        if avg_vol <= 0:
            return None
        vol_ratio = volumes[-1] / avg_vol

        # Compute bar range vs ATR
        atr = self._atr(highs, lows, closes, 14)
        if atr is None or atr <= 0:
            return None

        current_range = highs[-1] - lows[-1]
        range_ratio = current_range / atr

        # Require strong volume burst (> 2.1x) and wide candle (> 1.6x ATR)
        if vol_ratio >= 2.1 and range_ratio >= 1.6:
            body = ctx.bar.close - ctx.bar.open
            abs_body = abs(body)

            # Ensure directionality: body should occupy at least 50% of the high-low range
            if current_range > 0 and (abs_body / current_range) >= 0.50:
                confidence = min(0.9, 0.5 + 0.1 * (vol_ratio - 2.0))

                if body > 0:
                    self.entry_bar = ctx.bar_index
                    self.entry_price = ctx.bar.close
                    return ctx.signal(
                        "long",
                        confidence=confidence,
                        stop_loss_bps=280.0,
                        take_profit_bps=520.0,
                        horizon_seconds=18000,
                        metadata={
                            "reason": "bullish_vol_burst_expansion",
                            "vol_ratio": round(vol_ratio, 2),
                            "range_ratio": round(range_ratio, 2),
                            "close": ctx.bar.close,
                        }
                    )
                elif body < 0:
                    self.entry_bar = ctx.bar_index
                    self.entry_price = ctx.bar.close
                    return ctx.signal(
                        "short",
                        confidence=confidence,
                        stop_loss_bps=280.0,
                        take_profit_bps=520.0,
                        horizon_seconds=18000,
                        metadata={
                            "reason": "bearish_vol_burst_expansion",
                            "vol_ratio": round(vol_ratio, 2),
                            "range_ratio": round(range_ratio, 2),
                            "close": ctx.bar.close,
                        }
                    )

        return None