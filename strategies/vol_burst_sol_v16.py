from domains.strategy_contract import Strategy, BarContext, Signal
from typing import Optional, Dict, Any
import math

class SolVolBurstMomentum(Strategy):
    METADATA = {
        "name": "SolVolBurstMomentum",
        "domain": "sol_usdc",
        "declared_sl_bps": 280.0,
        "declared_tp_bps": 560.0,
        "declared_hold_seconds": 18000,  # ~5 hours
        "warmup_bars": 50,
        "required_features": ["fear_greed_index"],
    }

    def initialize(self, ctx: BarContext) -> None:
        self.vol_period = 20
        self.atr_period = 14
        self.trend_period = 50
        self.vol_mult = 2.2
        self.range_mult = 1.8
        self.max_hold_bars = 5
        self.cooldown_bars = 6
        self.last_exit_bar = -100
        self.entry_bar = -100

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
        if len(trs) < period:
            return None
        return sum(trs[-period:]) / period

    def on_position_close(self, ctx: BarContext, position: Dict[str, Any]) -> None:
        self.last_exit_bar = ctx.bar_index

    def on_bar(self, ctx: BarContext) -> Optional[Signal]:
        if ctx.bar_index < self.METADATA["warmup_bars"]:
            return None

        closes = ctx.closes(self.trend_period + 2)
        highs = ctx.highs(self.trend_period + 2)
        lows = ctx.lows(self.trend_period + 2)
        volumes = ctx.volumes(self.trend_period + 2)

        if len(closes) < self.trend_period:
            return None

        # Position management: time-based exit or momentum stalling
        if ctx.has_position():
            bars_held = ctx.bar_index - self.entry_bar
            curr_pos = ctx.position_direction()

            # Exit on max hold time
            if bars_held >= self.max_hold_bars:
                return ctx.signal("flat", confidence=0.7, metadata={
                    "reason": "max_bars_held_timeout",
                    "bars_held": bars_held,
                    "close": ctx.bar.close
                })

            # Exit if momentum immediately stalls or reverses
            curr_bar_range = ctx.bar.high - ctx.bar.low
            body = ctx.bar.close - ctx.bar.open
            if curr_pos == "long" and body < 0 and abs(body) > 0.6 * curr_bar_range:
                return ctx.signal("flat", confidence=0.6, metadata={
                    "reason": "long_momentum_stalled",
                    "bars_held": bars_held,
                    "close": ctx.bar.close
                })
            elif curr_pos == "short" and body > 0 and abs(body) > 0.6 * curr_bar_range:
                return ctx.signal("flat", confidence=0.6, metadata={
                    "reason": "short_momentum_stalled",
                    "bars_held": bars_held,
                    "close": ctx.bar.close
                })
            return None

        # Mandatory cooldown check
        if (ctx.bar_index - self.last_exit_bar) < self.cooldown_bars:
            return None

        # Regime gate to avoid prior OOS failures in crisis/meltdown
        market_regime = ctx.market.get("regime", "NORMAL")
        if market_regime in ("CRISIS", "MELTDOWN"):
            return None

        crisis_score = ctx.market.get("crisis_score", 0.0)
        if crisis_score > 0.45:
            return None

        # Calculate indicators
        avg_vol = self._sma(volumes[:-1], self.vol_period)
        atr = self._atr(highs[:-1], lows[:-1], closes[:-1], self.atr_period)
        trend_sma = self._sma(closes, self.trend_period)

        if avg_vol is None or atr is None or trend_sma is None or avg_vol == 0 or atr == 0:
            return None

        curr_vol = ctx.bar.volume
        curr_range = ctx.bar.high - ctx.bar.low
        body = ctx.bar.close - ctx.bar.open

        # Condition 1: Volume burst
        vol_ratio = curr_vol / avg_vol
        is_vol_burst = vol_ratio >= self.vol_mult

        # Condition 2: Unusually wide range
        range_ratio = curr_range / atr
        is_wide_range = range_ratio >= self.range_mult

        # Condition 3: Decisive directional body (>50% of the bar's range)
        is_strong_body = abs(body) > (0.50 * curr_range)

        if not (is_vol_burst and is_wide_range and is_strong_body):
            return None

        # Directional entry with trend regime filter
        fear_greed = ctx.features.get("fear_greed_index", 50)
        
        # Bullish burst: strong close above open, reinforced by trend or non-extreme fear
        if body > 0 and ctx.bar.close > trend_sma and fear_greed > 25:
            self.entry_bar = ctx.bar_index
            conf = min(0.9, 0.55 + (vol_ratio - 2.0) * 0.1)
            return ctx.signal("long", confidence=conf, metadata={
                "reason": "vol_burst_bullish_expansion",
                "vol_ratio": round(vol_ratio, 2),
                "range_ratio": round(range_ratio, 2),
                "trend_sma": round(trend_sma, 2),
                "close": round(ctx.bar.close, 2)
            })

        # Bearish burst: strong close below open, reinforced by downtrend or non-extreme greed
        if body < 0 and ctx.bar.close < trend_sma and fear_greed < 75:
            self.entry_bar = ctx.bar_index
            conf = min(0.9, 0.55 + (vol_ratio - 2.0) * 0.1)
            return ctx.signal("short", confidence=conf, metadata={
                "reason": "vol_burst_bearish_expansion",
                "vol_ratio": round(vol_ratio, 2),
                "range_ratio": round(range_ratio, 2),
                "trend_sma": round(trend_sma, 2),
                "close": round(ctx.bar.close, 2)
            })

        return None