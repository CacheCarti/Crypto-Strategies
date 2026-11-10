from domains.strategy_contract import Strategy, BarContext, Signal
from typing import Optional, Dict, Any
import math

class VolumeBurstMomentum(Strategy):
    METADATA = {
        "name": "VolumeBurstMomentum",
        "domain": "sol_usdc",
        "declared_sl_bps": 320.0,
        "declared_tp_bps": 550.0,
        "declared_hold_seconds": 18000,  # ~5 hours
        "warmup_bars": 35,
    }

    def initialize(self, ctx: BarContext) -> None:
        self.vol_period = 20
        self.atr_period = 14
        self.vol_mult = 2.2
        self.range_mult = 1.6
        self.max_hold_bars = 5
        self.cooldown_bars = 8
        
        self.last_exit_bar = -999
        self.entry_bar = -999
        self.bars_in_trade = 0

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
        self.bars_in_trade = 0

    def on_bar(self, ctx: BarContext) -> Optional[Signal]:
        closes = ctx.closes(self.vol_period + 5)
        highs = ctx.highs(self.vol_period + 5)
        lows = ctx.lows(self.vol_period + 5)
        volumes = ctx.volumes(self.vol_period + 5)

        if len(closes) < self.vol_period + 1:
            return None

        # Position management
        if ctx.has_position():
            self.bars_in_trade += 1
            direction = ctx.position_direction()

            # Time-based fixed boundary exit
            if self.bars_in_trade >= self.max_hold_bars:
                return ctx.signal(
                    "flat",
                    confidence=0.6,
                    metadata={"reason": "max_bars_exhaustion", "bars_held": self.bars_in_trade}
                )

            # Momentum stall / reversal exit
            curr_bar = ctx.bar
            if direction == "long" and curr_bar.close < curr_bar.open:
                # Bearish bar against long
                if curr_bar.high - curr_bar.close > (curr_bar.close - curr_bar.low):
                    return ctx.signal(
                        "flat",
                        confidence=0.55,
                        metadata={"reason": "momentum_stall_long", "bars_held": self.bars_in_trade}
                    )
            elif direction == "short" and curr_bar.close > curr_bar.open:
                # Bullish bar against short
                if curr_bar.close - curr_bar.low > (curr_bar.high - curr_bar.close):
                    return ctx.signal(
                        "flat",
                        confidence=0.55,
                        metadata={"reason": "momentum_stall_short", "bars_held": self.bars_in_trade}
                    )
            return None

        # Mandatory post-exit cooldown
        if ctx.bar_index - self.last_exit_bar < self.cooldown_bars:
            return None

        # Volume baseline calculation (exclude the current trigger bar)
        hist_vols = volumes[-(self.vol_period + 1):-1]
        if not hist_vols or sum(hist_vols) == 0:
            return None
        avg_vol = sum(hist_vols) / len(hist_vols)
        current_vol = volumes[-1]

        # ATR calculation for relative bar range check
        atr = self._atr(highs, lows, closes, self.atr_period)
        if atr is None or atr <= 0:
            return None

        current_range = highs[-1] - lows[-1]
        body = abs(closes[-1] - ctx.bar.open)

        # Event trigger conditions:
        # 1. Volume burst > 2.2x 20-bar baseline
        # 2. Wide price range > 1.6x ATR
        # 3. Decisive body (candle body makes up at least 60% of total bar range)
        vol_ratio = current_vol / avg_vol
        range_ratio = current_range / atr

        if vol_ratio >= self.vol_mult and range_ratio >= self.range_mult and current_range > 0:
            body_ratio = body / current_range
            if body_ratio < 0.60:
                return None

            is_bullish = closes[-1] > ctx.bar.open
            is_bearish = closes[-1] < ctx.bar.open

            conf = min(0.85, 0.55 + 0.05 * min(vol_ratio - self.vol_mult, 3.0))

            if is_bullish:
                self.entry_bar = ctx.bar_index
                self.bars_in_trade = 0
                return ctx.signal(
                    "long",
                    confidence=conf,
                    stop_loss_bps=self.METADATA["declared_sl_bps"],
                    take_profit_bps=self.METADATA["declared_tp_bps"],
                    metadata={
                        "reason": "vol_burst_bullish_expansion",
                        "vol_ratio": round(vol_ratio, 2),
                        "range_ratio": round(range_ratio, 2),
                        "body_ratio": round(body_ratio, 2),
                        "atr": round(atr, 4),
                        "close": closes[-1]
                    }
                )
            elif is_bearish:
                self.entry_bar = ctx.bar_index
                self.bars_in_trade = 0
                return ctx.signal(
                    "short",
                    confidence=conf,
                    stop_loss_bps=self.METADATA["declared_sl_bps"],
                    take_profit_bps=self.METADATA["declared_tp_bps"],
                    metadata={
                        "reason": "vol_burst_bearish_expansion",
                        "vol_ratio": round(vol_ratio, 2),
                        "range_ratio": round(range_ratio, 2),
                        "body_ratio": round(body_ratio, 2),
                        "atr": round(atr, 4),
                        "close": closes[-1]
                    }
                )

        return None