from domains.strategy_contract import Strategy, BarContext, Signal
from typing import Optional, Dict, Any
import math

class VolatilityEventStructure(Strategy):
    METADATA = {
        "name": "VolatilityEventStructureETH",
        "domain": "eth_usdc",
        "declared_sl_bps": 200.0,
        "declared_tp_bps": 400.0,
        "declared_hold_seconds": 3600,
        "warmup_bars": 50,
    }

    def initialize(self, ctx: BarContext) -> None:
        self.fast_period = 10
        self.slow_period = 30
        self.vol_period = 20
        self.cooldown_bars = 5
        self.last_signal_bar = -self.cooldown_bars

    def _ema(self, values, period):
        if len(values) < period:
            return None
        k = 2.0 / (period + 1)
        ema = sum(values[:period]) / period
        for v in values[period:]:
            ema = v * k + ema * (1 - k)
        return ema

    def _atr(self, highs, lows, closes, period=14):
        if len(closes) < period + 1:
            return None
        trs = []
        for i in range(1, len(closes)):
            tr = max(highs[i]-lows[i], abs(highs[i]-closes[i-1]), abs(lows[i]-closes[i-1]))
            trs.append(tr)
        return sum(trs[-period:]) / period

    def on_bar(self, ctx: BarContext) -> Optional[Signal]:
        closes = ctx.closes(self.warmup_bars)
        highs = ctx.highs(self.warmup_bars)
        lows = ctx.lows(self.warmup_bars)

        if len(closes) < self.warmup_bars:
            return None

        # Cooldown logic
        if ctx.bar_index - self.last_signal_bar < self.cooldown_bars:
            return None

        current_close = ctx.bar.close
        prev_close = closes[-2] if len(closes) >= 2 else None
        current_high = ctx.bar.high
        prev_high = highs[-2] if len(highs) >= 2 else None
        current_low = ctx.bar.low
        prev_low = lows[-2] if len(lows) >= 2 else None

        if prev_close is None or prev_high is None or prev_low is None:
            return None

        fast_ema = self._ema(closes, self.fast_period)
        slow_ema = self._ema(closes, self.slow_period)
        atr = self._atr(highs, lows, closes, self.vol_period)

        if fast_ema is None or slow_ema is None or atr is None:
            return None

        # Volatility structure detection: look for pullbacks within a trend
        # We need at least 3 bars for HH/HL or LH/LL detection
        if len(closes) < 3:
            return None

        # Bullish setup: Higher Highs and Higher Lows, followed by a pullback above slow EMA
        is_hh = current_high > prev_high and prev_high > highs[-3]
        is_hl = current_low > prev_low and prev_low > lows[-3]
        is_bullish_trend = is_hh and is_hl

        # Bearish setup: Lower Highs and Lower Lows, followed by a pullback below slow EMA
        is_lh = current_high < prev_high and prev_high < highs[-3]
        is_ll = current_low < prev_low and prev_low < lows[-3]
        is_bearish_trend = is_lh and is_ll

        # Entry logic
        signal_direction = None
        confidence = 0.0
        reason = ""

        # Long entry: Bullish trend, fast EMA above slow EMA, and a pullback (current close below fast EMA but above slow EMA)
        if is_bullish_trend and fast_ema > slow_ema and current_close < fast_ema and current_close > slow_ema:
            if not ctx.has_position():
                signal_direction = "long"
                confidence = 0.7 + ctx.rng.uniform(-0.1, 0.1) # Add slight randomness to confidence
                reason = "bullish_trend_pullback_ema_support"
        
        # Short entry: Bearish trend, fast EMA below slow EMA, and a pullback (current close above fast EMA but below slow EMA)
        elif is_bearish_trend and fast_ema < slow_ema and current_close > fast_ema and current_close < slow_ema:
            if not ctx.has_position():
                signal_direction = "short"
                confidence = 0.7 + ctx.rng.uniform(-0.1, 0.1) # Add slight randomness to confidence
                reason = "bearish_trend_pullback_ema_resistance"

        # Exit logic
        if ctx.has_position():
            if ctx.position_direction() == "long":
                # Exit long if fast EMA crosses below slow EMA or price breaks below slow EMA
                if fast_ema < slow_ema or current_close < slow_ema:
                    signal_direction = "flat"
                    confidence = 0.5
                    reason = "long_exit_ema_cross_or_breakdown"
            elif ctx.position_direction() == "short":
                # Exit short if fast EMA crosses above slow EMA or price breaks above slow EMA
                if fast_ema > slow_ema or current_close > slow_ema:
                    signal_direction = "flat"
                    confidence = 0.5
                    reason = "short_exit_ema_cross_or_breakout"

        if signal_direction:
            self.last_signal_bar = ctx.bar_index
            return ctx.signal(
                signal_direction,
                confidence=min(1.0, max(0.0, confidence)),
                metadata={
                    "reason": reason,
                    "current_close": current_close,
                    "fast_ema": fast_ema,
                    "slow_ema": slow_ema,
                    "atr": atr,
                    "is_bullish_trend": is_bullish_trend,
                    "is_bearish_trend": is_bearish_trend,
                }
            )

        return None

    def on_position_close(self, ctx: BarContext, position: Dict[str, Any]) -> None:
        # Optional: Log or adapt based on position close
        pass