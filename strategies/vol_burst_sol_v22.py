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
        self.entry_bar = -1
        self.last_exit_bar = -999
        self.cooldown_bars = 10
        self.max_hold_bars = 5

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
        self.entry_bar = -1

    def on_bar(self, ctx: BarContext) -> Optional[Signal]:
        closes = ctx.closes(30)
        highs = ctx.highs(30)
        lows = ctx.lows(30)
        volumes = ctx.volumes(30)

        if len(closes) < 25 or len(volumes) < 25:
            return None

        bar = ctx.bar
        idx = ctx.bar_index

        # Position management
        if ctx.has_position():
            bars_held = idx - self.entry_bar if self.entry_bar > 0 else 1
            pos_dir = ctx.position_direction()

            # Time-based hard stop or momentum exhaustion
            if bars_held >= self.max_hold_bars:
                return ctx.signal(
                    "flat",
                    confidence=0.7,
                    metadata={"reason": "max_bars_reached", "bars_held": bars_held, "price": bar.close}
                )

            # Reversal bar exit: opposite close wiping out progress
            if pos_dir == "long" and bar.close < bar.open and (bar.open - bar.close) > (bar.high - bar.low) * 0.6:
                return ctx.signal(
                    "flat",
                    confidence=0.6,
                    metadata={"reason": "long_momentum_stall", "bars_held": bars_held, "price": bar.close}
                )
            elif pos_dir == "short" and bar.close > bar.open and (bar.close - bar.open) > (bar.high - bar.low) * 0.6:
                return ctx.signal(
                    "flat",
                    confidence=0.6,
                    metadata={"reason": "short_momentum_stall", "bars_held": bars_held, "price": bar.close}
                )
            return None

        # Mandatory cooldown between events
        if (idx - self.last_exit_bar) < self.cooldown_bars:
            return None

        # Check regime to avoid meltdowns
        market_regime = ctx.market.get("regime", "NORMAL")
        if market_regime in ("CRISIS", "MELTDOWN"):
            return None

        # Volume baseline
        vol_slice = volumes[-21:-1]
        avg_vol = sum(vol_slice) / len(vol_slice)
        if avg_vol <= 0:
            return None

        curr_vol = bar.volume
        vol_ratio = curr_vol / avg_vol

        # Range expansion calculation
        atr = self._atr(highs, lows, closes, 14)
        if atr is None or atr <= 0:
            return None

        bar_range = bar.high - bar.low
        body = abs(bar.close - bar.open)

        # Event detection: 2.2x volume surge + range > 1.8x ATR + strong decisive body (>= 60% of candle range)
        is_vol_burst = vol_ratio >= 2.2
        is_wide_range = bar_range >= (1.8 * atr)
        is_decisive_candle = body >= (0.60 * bar_range)

        if is_vol_burst and is_wide_range and is_decisive_candle:
            confidence = min(0.9, 0.55 + 0.1 * min(vol_ratio / 3.0, 1.0) + 0.1 * min(bar_range / (2.5 * atr), 1.0))
            if bar.close > bar.open:
                self.entry_bar = idx
                return ctx.signal(
                    "long",
                    confidence=confidence,
                    stop_loss_bps=self.METADATA["declared_sl_bps"],
                    take_profit_bps=self.METADATA["declared_tp_bps"],
                    horizon_seconds=self.METADATA["declared_hold_seconds"],
                    metadata={
                        "reason": "bullish_vol_burst_expansion",
                        "vol_ratio": round(vol_ratio, 2),
                        "range_atr_ratio": round(bar_range / atr, 2),
                        "close": bar.close,
                        "atr": round(atr, 4)
                    }
                )
            elif bar.close < bar.open:
                self.entry_bar = idx
                return ctx.signal(
                    "short",
                    confidence=confidence,
                    stop_loss_bps=self.METADATA["declared_sl_bps"],
                    take_profit_bps=self.METADATA["declared_tp_bps"],
                    horizon_seconds=self.METADATA["declared_hold_seconds"],
                    metadata={
                        "reason": "bearish_vol_burst_expansion",
                        "vol_ratio": round(vol_ratio, 2),
                        "range_atr_ratio": round(bar_range / atr, 2),
                        "close": bar.close,
                        "atr": round(atr, 4)
                    }
                )

        return None