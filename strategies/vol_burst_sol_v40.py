from domains.strategy_contract import Strategy, BarContext, Signal
from typing import Optional, Dict, Any
import math

class SolVolumeBurstMomentum(Strategy):
    METADATA = {
        "name": "SolVolumeBurstMomentum",
        "domain": "sol_usdc",
        "declared_sl_bps": 280.0,
        "declared_tp_bps": 560.0,
        "declared_hold_seconds": 18000,  # ~5 hours average hold
        "warmup_bars": 35,
    }

    def initialize(self, ctx: BarContext) -> None:
        self.vol_period = 20
        self.atr_period = 14
        self.vol_multiplier = 2.2
        self.range_multiplier = 1.8
        self.max_hold_bars = 6
        self.cooldown_period = 5
        self.last_exit_bar = -100
        self.entry_bar = 0

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
        closes = ctx.closes(self.METADATA["warmup_bars"])
        if len(closes) < self.METADATA["warmup_bars"]:
            return None

        highs = ctx.highs(self.METADATA["warmup_bars"])
        lows = ctx.lows(self.METADATA["warmup_bars"])
        volumes = ctx.volumes(self.METADATA["warmup_bars"])

        # Handle active position management
        if ctx.has_position():
            bars_held = ctx.bar_index - self.entry_bar
            pos_dir = ctx.position_direction()

            # Time-based exhaustion exit
            if bars_held >= self.max_hold_bars:
                return ctx.signal(
                    "flat",
                    confidence=0.6,
                    metadata={"reason": "max_bars_exhaustion", "bars_held": bars_held, "close": ctx.bar.close}
                )

            # Momentum stall: opposing candle wiping out prior bar
            if pos_dir == "long" and ctx.bar.close < ctx.bar.open and (ctx.bar.open - ctx.bar.close) > (highs[-2] - lows[-2]) * 0.5:
                return ctx.signal(
                    "flat",
                    confidence=0.55,
                    metadata={"reason": "momentum_stalled_bear_candle", "bars_held": bars_held, "close": ctx.bar.close}
                )
            elif pos_dir == "short" and ctx.bar.close > ctx.bar.open and (ctx.bar.close - ctx.bar.open) > (highs[-2] - lows[-2]) * 0.5:
                return ctx.signal(
                    "flat",
                    confidence=0.55,
                    metadata={"reason": "momentum_stalled_bull_candle", "bars_held": bars_held, "close": ctx.bar.close}
                )
            return None

        # Mandatory cooldown post-exit to prevent whipsawing and overtrading
        if ctx.bar_index - self.last_exit_bar < self.cooldown_period:
            return None

        # Volume baseline calculation (excluding current bar)
        avg_vol = sum(volumes[-self.vol_period - 1:-1]) / self.vol_period
        if avg_vol <= 0:
            return None
        vol_ratio = volumes[-1] / avg_vol

        # Volatility baseline (ATR)
        atr = self._atr(highs, lows, closes, self.atr_period)
        if atr is None or atr <= 0:
            return None

        current_range = ctx.bar.high - ctx.bar.low
        range_ratio = current_range / atr

        # Burst Condition: Unusually high volume AND exceptionally wide range expansion
        is_volume_burst = vol_ratio >= self.vol_multiplier
        is_range_expansion = range_ratio >= self.range_multiplier

        if not (is_volume_burst and is_range_expansion):
            return None

        body = abs(ctx.bar.close - ctx.bar.open)
        # Body dominance: ensure directional conviction, not an indecision doji
        if current_range == 0 or (body / current_range) < 0.55:
            return None

        # Market regime filter from engine context
        regime = ctx.market.get("regime", "NORMAL")
        if regime == "MELTDOWN":
            return None

        # Confidence scaled by volume burst strength
        conf = min(0.85, 0.55 + (vol_ratio - self.vol_multiplier) * 0.08)

        # Bullish burst expansion
        if ctx.bar.close > ctx.bar.open:
            self.entry_bar = ctx.bar_index
            return ctx.signal(
                "long",
                confidence=round(conf, 2),
                stop_loss_bps=self.METADATA["declared_sl_bps"],
                take_profit_bps=self.METADATA["declared_tp_bps"],
                metadata={
                    "reason": "bullish_volume_burst_expansion",
                    "vol_ratio": round(vol_ratio, 2),
                    "range_ratio": round(range_ratio, 2),
                    "atr": round(atr, 4),
                    "close": ctx.bar.close
                }
            )

        # Bearish burst expansion
        elif ctx.bar.close < ctx.bar.open:
            self.entry_bar = ctx.bar_index
            return ctx.signal(
                "short",
                confidence=round(conf, 2),
                stop_loss_bps=self.METADATA["declared_sl_bps"],
                take_profit_bps=self.METADATA["declared_tp_bps"],
                metadata={
                    "reason": "bearish_volume_burst_expansion",
                    "vol_ratio": round(vol_ratio, 2),
                    "range_ratio": round(range_ratio, 2),
                    "atr": round(atr, 4),
                    "close": ctx.bar.close
                }
            )

        return None