from domains.strategy_contract import Strategy, BarContext, Signal
from typing import Optional, Dict, Any
import math

class SolVolBurstMomentum(Strategy):
    METADATA = {
        "name": "SolVolBurstMomentum",
        "domain": "sol_usdc",
        "declared_sl_bps": 280.0,
        "declared_tp_bps": 560.0,
        "declared_hold_seconds": 18000,
        "warmup_bars": 35,
        "required_features": ["btc_return_pct"],
    }

    def initialize(self, ctx: BarContext) -> None:
        self.vol_period = 20
        self.atr_period = 14
        self.cooldown_bars = 6
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
        closes = ctx.closes(self.METADATA["warmup_bars"])
        highs = ctx.highs(self.METADATA["warmup_bars"])
        lows = ctx.lows(self.METADATA["warmup_bars"])
        volumes = ctx.volumes(self.METADATA["warmup_bars"])

        if len(closes) < self.METADATA["warmup_bars"]:
            return None

        current_close = ctx.bar.close
        current_open = ctx.bar.open
        current_high = ctx.bar.high
        current_low = ctx.bar.low
        current_vol = ctx.bar.volume

        # Manage open position
        if ctx.has_position():
            bars_held = ctx.bar_index - self.entry_bar if self.entry_bar > 0 else 1
            direction = ctx.position_direction()

            # Time-based exit (holding window elapsed)
            if bars_held >= self.max_hold_bars:
                return ctx.signal(
                    "flat",
                    confidence=0.6,
                    metadata={"reason": "max_bars_exhaustion", "bars_held": bars_held, "close": current_close}
                )

            # Momentum stall / reversal exit
            if direction == "long" and current_close < ctx.bar.open and bars_held >= 2:
                recent_high = max(highs[-bars_held:])
                if recent_high > 0 and (recent_high - current_close) / recent_high > 0.015:
                    return ctx.signal(
                        "flat",
                        confidence=0.7,
                        metadata={"reason": "long_momentum_stall", "bars_held": bars_held, "close": current_close}
                    )
            elif direction == "short" and current_close > ctx.bar.open and bars_held >= 2:
                recent_low = min(lows[-bars_held:])
                if recent_low > 0 and (current_close - recent_low) / recent_low > 0.015:
                    return ctx.signal(
                        "flat",
                        confidence=0.7,
                        metadata={"reason": "short_momentum_stall", "bars_held": bars_held, "close": current_close}
                    )
            return None

        # Cooldown guard after position exits
        if ctx.bar_index - self.last_exit_bar < self.cooldown_bars:
            return None

        # Market regime filter: avoid choppy/crisis collapses
        regime = ctx.market.get("regime", "NORMAL")
        if regime in ("CRISIS", "MELTDOWN"):
            return None

        # Indicator calculations
        avg_vol = sum(volumes[-self.vol_period - 1:-1]) / self.vol_period
        if avg_vol <= 0:
            return None
        vol_ratio = current_vol / avg_vol

        atr = self._atr(highs, lows, closes, self.atr_period)
        if atr is None or atr <= 0:
            return None

        bar_range = current_high - current_low
        body = abs(current_close - current_open)
        range_ratio = bar_range / atr

        # Cross-asset macro confirmation: BTC 24h momentum alignment
        btc_ret = ctx.features.get("btc_return_pct", 0.0)

        # Volume-burst conditions:
        # 1. Volume > 2.2x 20-bar SMA
        # 2. Bar range > 1.8x ATR (wide range expansion)
        # 3. Decisive candle body (> 60% of total bar range to avoid long-wick fakeouts)
        if vol_ratio >= 2.2 and range_ratio >= 1.8 and bar_range > 0 and (body / bar_range) >= 0.60:
            # Bullish expansion with aligned BTC
            if current_close > current_open and btc_ret > -1.5:
                self.entry_bar = ctx.bar_index
                self.entry_price = current_close
                return ctx.signal(
                    "long",
                    confidence=0.85,
                    stop_loss_bps=self.METADATA["declared_sl_bps"],
                    take_profit_bps=self.METADATA["declared_tp_bps"],
                    metadata={
                        "reason": "bullish_volume_range_expansion",
                        "vol_ratio": round(vol_ratio, 2),
                        "range_ratio": round(range_ratio, 2),
                        "btc_return_pct": round(btc_ret, 2),
                        "close": current_close
                    }
                )

            # Bearish expansion with aligned BTC
            elif current_close < current_open and btc_ret < 1.5:
                self.entry_bar = ctx.bar_index
                self.entry_price = current_close
                return ctx.signal(
                    "short",
                    confidence=0.85,
                    stop_loss_bps=self.METADATA["declared_sl_bps"],
                    take_profit_bps=self.METADATA["declared_tp_bps"],
                    metadata={
                        "reason": "bearish_volume_range_expansion",
                        "vol_ratio": round(vol_ratio, 2),
                        "range_ratio": round(range_ratio, 2),
                        "btc_return_pct": round(btc_ret, 2),
                        "close": current_close
                    }
                )

        return None