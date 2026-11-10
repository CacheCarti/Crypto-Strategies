from domains.strategy_contract import Strategy, BarContext, Signal
from typing import Optional, Dict, Any
import math

class EthEmaPullbackAdvanced(Strategy):
    METADATA = {
        "name": "EthEmaPullbackAdvanced",
        "domain": "eth_usdc",
        "declared_sl_bps": 220.0,
        "declared_tp_bps": 440.0,
        "declared_hold_seconds": 14400,
        "warmup_bars": 60,
    }

    def initialize(self, ctx: BarContext) -> None:
        self.fast_period = 20
        self.slow_period = 50
        self.cooldown_bars = 6
        self.last_exit_bar = -999

    def _ema(self, values, period):
        if len(values) < period:
            return None
        k = 2.0 / (period + 1)
        ema = sum(values[:period]) / period
        for v in values[period:]:
            ema = v * k + ema * (1.0 - k)
        return ema

    def on_position_close(self, ctx: BarContext, position: Dict[str, Any]) -> None:
        self.last_exit_bar = ctx.bar_index

    def on_bar(self, ctx: BarContext) -> Optional[Signal]:
        closes = ctx.closes(self.slow_period + 15)
        highs = ctx.highs(self.slow_period + 15)
        lows = ctx.lows(self.slow_period + 15)
        
        if len(closes) < self.slow_period + 10:
            return None

        # Calculate current and previous bar EMAs
        ema20_curr = self._ema(closes, self.fast_period)
        ema50_curr = self._ema(closes, self.slow_period)
        ema20_prev = self._ema(closes[:-1], self.fast_period)
        ema50_prev = self._ema(closes[:-1], self.slow_period)

        if ema20_curr is None or ema50_curr is None or ema20_prev is None or ema50_prev is None:
            return None

        price_curr = ctx.bar.close
        price_prev = closes[-2]
        low_prev = lows[-2]
        high_prev = highs[-2]

        # Handle active position management (exit on cross back against EMA20)
        if ctx.has_position():
            direction = ctx.position_direction()
            if direction == "long" and price_curr < ema20_curr:
                return ctx.signal(
                    "flat",
                    confidence=0.6,
                    metadata={
                        "reason": "ema20_loss_exit_long",
                        "close": price_curr,
                        "ema20": ema20_curr,
                        "ema50": ema50_curr
                    }
                )
            elif direction == "short" and price_curr > ema20_curr:
                return ctx.signal(
                    "flat",
                    confidence=0.6,
                    metadata={
                        "reason": "ema20_recapture_exit_short",
                        "close": price_curr,
                        "ema20": ema20_curr,
                        "ema50": ema50_curr
                    }
                )
            return None

        # Check entry cooldown
        if (ctx.bar_index - self.last_exit_bar) < self.cooldown_bars:
            return None

        # Avoid entries in extreme crisis or meltdown regimes
        market_regime = ctx.market.get("regime", "NORMAL")
        crisis_score = ctx.market.get("crisis_score", 0.0)
        if market_regime in ("CRISIS", "MELTDOWN") or crisis_score > 0.7:
            return None

        # Long Setup:
        # 1. Structural uptrend: EMA20 > EMA50, price > EMA50
        # 2. Pullback & reclaim: previous low dipped to/below EMA20, current close reclaimed above EMA20
        # 3. Trigger confirmation: previous bar closed below or touched EMA20, current bar closes solidly above
        long_trend = (ema20_curr > ema50_curr) and (price_curr > ema50_curr)
        long_pullback = (low_prev <= ema20_prev) or (price_prev <= ema20_prev)
        long_reclaim = (price_curr > ema20_curr) and (price_prev <= ema20_prev)

        if long_trend and long_pullback and long_reclaim:
            confidence = 0.7 if price_curr > ema20_curr * 1.001 else 0.55
            return ctx.signal(
                "long",
                confidence=confidence,
                stop_loss_bps=self.METADATA["declared_sl_bps"],
                take_profit_bps=self.METADATA["declared_tp_bps"],
                metadata={
                    "reason": "bullish_pullback_ema20_reclaim",
                    "close": price_curr,
                    "ema20": ema20_curr,
                    "ema50": ema50_curr,
                    "prev_low": low_prev
                }
            )

        # Short Setup:
        # 1. Structural downtrend: EMA20 < EMA50, price < EMA50
        # 2. Pullback & rejection: previous high tagged or exceeded EMA20, current close dropped back below EMA20
        # 3. Trigger confirmation: previous bar closed above or touched EMA20, current bar closes solidly below
        short_trend = (ema20_curr < ema50_curr) and (price_curr < ema50_curr)
        short_pullback = (high_prev >= ema20_prev) or (price_prev >= ema20_prev)
        short_rejection = (price_curr < ema20_curr) and (price_prev >= ema20_prev)

        if short_trend and short_pullback and short_rejection:
            confidence = 0.7 if price_curr < ema20_curr * 0.999 else 0.55
            return ctx.signal(
                "short",
                confidence=confidence,
                stop_loss_bps=self.METADATA["declared_sl_bps"],
                take_profit_bps=self.METADATA["declared_tp_bps"],
                metadata={
                    "reason": "bearish_pullback_ema20_rejection",
                    "close": price_curr,
                    "ema20": ema20_curr,
                    "ema50": ema50_curr,
                    "prev_high": high_prev
                }
            )

        return None