from domains.strategy_contract import Strategy, BarContext, Signal
from typing import Optional, Dict, Any
import math

class VolumeBurstMomentum(Strategy):
    METADATA = {
        "name": "VolumeBurstMomentum",
        "domain": "sol_usdc",
        "declared_sl_bps": 320.0,
        "declared_tp_bps": 540.0,
        "declared_hold_seconds": 14400,
        "warmup_bars": 35,
    }

    def initialize(self, ctx: BarContext) -> None:
        self.period = 20
        self.max_hold_bars = 4
        self.cooldown_period = 4
        self.last_exit_bar = -100
        self.entry_bar = 0

    def _sma(self, values, period: int):
        if len(values) < period:
            return None
        return sum(values[-period:]) / period

    def _atr(self, highs, lows, closes, period: int = 14):
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
        closes = ctx.closes(self.period + 15)
        highs = ctx.highs(self.period + 15)
        lows = ctx.lows(self.period + 15)
        volumes = ctx.volumes(self.period + 5)

        if len(closes) < self.period + 10 or len(volumes) < self.period + 1:
            return None

        # Position management
        if ctx.has_position():
            bars_held = ctx.bar_index - self.entry_bar
            pos_dir = ctx.position_direction()

            # Exit on hold duration expiry
            if bars_held >= self.max_hold_bars:
                return ctx.signal(
                    "flat",
                    confidence=0.5,
                    metadata={"reason": "time_exit_reached", "bars_held": bars_held, "price": ctx.bar.close}
                )

            # Exit if momentum stalls or reverses hard against the burst
            bar_body = ctx.bar.close - ctx.bar.open
            if pos_dir == "long" and bar_body < 0 and abs(bar_body) > (ctx.bar.high - ctx.bar.low) * 0.7:
                return ctx.signal(
                    "flat",
                    confidence=0.6,
                    metadata={"reason": "long_momentum_stalled", "bars_held": bars_held, "price": ctx.bar.close}
                )
            elif pos_dir == "short" and bar_body > 0 and bar_body > (ctx.bar.high - ctx.bar.low) * 0.7:
                return ctx.signal(
                    "flat",
                    confidence=0.6,
                    metadata={"reason": "short_momentum_stalled", "bars_held": bars_held, "price": ctx.bar.close}
                )

            return None

        # Cooldown guard after recent exit
        if ctx.bar_index - self.last_exit_bar < self.cooldown_period:
            return None

        # Market regime filter: avoid entering bursts in chaotic meltdown environments
        market_regime = ctx.market.get("regime", "NORMAL")
        if market_regime in ("CRISIS", "MELTDOWN"):
            return None

        # Baseline metrics calculation
        baseline_vol = self._sma(volumes[:-1], self.period)
        atr_val = self._atr(highs, lows, closes, 14)

        if not baseline_vol or not atr_val or baseline_vol <= 0 or atr_val <= 0:
            return None

        curr_volume = ctx.bar.volume
        curr_range = ctx.bar.high - ctx.bar.low
        curr_body = ctx.bar.close - ctx.bar.open
        vol_ratio = curr_volume / baseline_vol
        range_ratio = curr_range / atr_val

        # Require significant volume surge and wide candle expansion
        if vol_ratio >= 2.1 and range_ratio >= 1.6:
            # Check directional conviction of the bar (body accounts for bulk of the total range)
            body_dominance = abs(curr_body) / curr_range if curr_range > 0 else 0.0

            if body_dominance >= 0.55:
                # Upward expansion burst -> Long
                if curr_body > 0:
                    self.entry_bar = ctx.bar_index
                    conf = min(0.85, 0.55 + (vol_ratio - 2.0) * 0.08)
                    return ctx.signal(
                        "long",
                        confidence=conf,
                        stop_loss_bps=self.METADATA["declared_sl_bps"],
                        take_profit_bps=self.METADATA["declared_tp_bps"],
                        horizon_seconds=self.METADATA["declared_hold_seconds"],
                        metadata={
                            "reason": "bullish_vol_range_burst",
                            "vol_ratio": round(vol_ratio, 2),
                            "range_ratio": round(range_ratio, 2),
                            "body_dominance": round(body_dominance, 2),
                            "price": ctx.bar.close
                        }
                    )
                # Downward expansion burst -> Short
                elif curr_body < 0:
                    self.entry_bar = ctx.bar_index
                    conf = min(0.85, 0.55 + (vol_ratio - 2.0) * 0.08)
                    return ctx.signal(
                        "short",
                        confidence=conf,
                        stop_loss_bps=self.METADATA["declared_sl_bps"],
                        take_profit_bps=self.METADATA["declared_tp_bps"],
                        horizon_seconds=self.METADATA["declared_hold_seconds"],
                        metadata={
                            "reason": "bearish_vol_range_burst",
                            "vol_ratio": round(vol_ratio, 2),
                            "range_ratio": round(range_ratio, 2),
                            "body_dominance": round(body_dominance, 2),
                            "price": ctx.bar.close
                        }
                    )

        return None