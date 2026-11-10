from domains.strategy_contract import Strategy, BarContext, Signal
from typing import Optional, Dict, Any
import math

class SolVolumeBurstMomentum(Strategy):
    METADATA = {
        "name": "SolVolumeBurstMomentum",
        "domain": "sol_usdc",
        "declared_sl_bps": 280.0,
        "declared_tp_bps": 480.0,
        "declared_hold_seconds": 18000,  # ~5 hours target
        "warmup_bars": 35,
        "required_features": ["fear_greed_index"],
    }

    def initialize(self, ctx: BarContext) -> None:
        self.vol_period = 20
        self.atr_period = 14
        self.trend_period = 30
        self.max_hold_bars = 6
        self.cooldown_bars = 5
        self.last_exit_bar = -999
        self.entry_bar = -999

    def _sma(self, values, period: int) -> Optional[float]:
        if len(values) < period:
            return None
        return sum(values[-period:]) / period

    def _atr(self, highs, lows, closes, period: int = 14) -> Optional[float]:
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
        closes = ctx.closes(self.warmup_bars)
        if len(closes) < self.warmup_bars:
            return None

        # Position management
        if ctx.has_position():
            bars_held = ctx.bar_index - self.entry_bar
            pos_dir = ctx.position_direction()
            
            # Time exit
            if bars_held >= self.max_hold_bars:
                return ctx.signal("flat", confidence=0.7, metadata={"reason": "max_bars_exceeded", "bars_held": bars_held})
            
            # Momentum stall exit: counter-bar against direction with high volume
            curr_bar = ctx.bar
            bar_dir = 1 if curr_bar.close > curr_bar.open else -1
            if pos_dir == "long" and bar_dir == -1 and curr_bar.close < ctx.lows(2)[0]:
                return ctx.signal("flat", confidence=0.6, metadata={"reason": "long_momentum_stalled", "close": curr_bar.close})
            if pos_dir == "short" and bar_dir == 1 and curr_bar.close > ctx.highs(2)[0]:
                return ctx.signal("flat", confidence=0.6, metadata={"reason": "short_momentum_stalled", "close": curr_bar.close})
            return None

        # Cooldown guard
        if (ctx.bar_index - self.last_exit_bar) < self.cooldown_bars:
            return None

        # Regime and market condition check
        market_regime = ctx.market.get("regime", "NORMAL")
        if market_regime in ("CRISIS", "MELTDOWN"):
            return None

        volumes = ctx.volumes(self.vol_period + 1)
        highs = ctx.highs(self.warmup_bars)
        lows = ctx.lows(self.warmup_bars)

        avg_vol = self._sma(volumes[:-1], self.vol_period)
        atr = self._atr(highs, lows, closes, self.atr_period)
        trend_ma = self._sma(closes, self.trend_period)

        if avg_vol is None or atr is None or trend_ma is None or avg_vol <= 0 or atr <= 0:
            return None

        curr_bar = ctx.bar
        curr_vol = curr_bar.volume
        vol_ratio = curr_vol / avg_vol
        bar_range = curr_bar.high - curr_bar.low
        range_ratio = bar_range / atr
        body = abs(curr_bar.close - curr_bar.open)

        # Trigger conditions: Volume > 2.1x 20-bar avg, Range > 1.6x ATR, Body >= 60% of Range (conviction candle)
        is_burst = (vol_ratio >= 2.1) and (range_ratio >= 1.6) and (bar_range > 0 and (body / bar_range) >= 0.60)
        if not is_burst:
            return None

        fg_index = ctx.features.get("fear_greed_index", 50)

        # Long burst: strong bullish candle breaking above trend MA
        if curr_bar.close > curr_bar.open and curr_bar.close > trend_ma and fg_index > 25:
            self.entry_bar = ctx.bar_index
            conf = min(0.9, 0.55 + 0.1 * min(vol_ratio - 2.0, 2.0))
            return ctx.signal(
                "long",
                confidence=conf,
                stop_loss_bps=self.METADATA["declared_sl_bps"],
                take_profit_bps=self.METADATA["declared_tp_bps"],
                horizon_seconds=self.METADATA["declared_hold_seconds"],
                metadata={
                    "reason": "bullish_vol_burst_expansion",
                    "vol_ratio": round(vol_ratio, 2),
                    "range_ratio": round(range_ratio, 2),
                    "fg_index": fg_index,
                    "trend_ma": round(trend_ma, 2),
                }
            )

        # Short burst: strong bearish candle breaking below trend MA
        if curr_bar.close < curr_bar.open and curr_bar.close < trend_ma and fg_index < 75:
            self.entry_bar = ctx.bar_index
            conf = min(0.9, 0.55 + 0.1 * min(vol_ratio - 2.0, 2.0))
            return ctx.signal(
                "short",
                confidence=conf,
                stop_loss_bps=self.METADATA["declared_sl_bps"],
                take_profit_bps=self.METADATA["declared_tp_bps"],
                horizon_seconds=self.METADATA["declared_hold_seconds"],
                metadata={
                    "reason": "bearish_vol_burst_expansion",
                    "vol_ratio": round(vol_ratio, 2),
                    "range_ratio": round(range_ratio, 2),
                    "fg_index": fg_index,
                    "trend_ma": round(trend_ma, 2),
                }
            )

        return None