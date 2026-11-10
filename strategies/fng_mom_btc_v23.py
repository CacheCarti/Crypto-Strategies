from domains.strategy_contract import Strategy, BarContext, Signal
from typing import Optional, Dict, Any
import math

class SentimentConditionedMomentum(Strategy):
    METADATA = {
        "name": "SentimentConditionedMomentum",
        "domain": "btc_usdc",
        "declared_sl_bps": 280.0,
        "declared_tp_bps": 480.0,
        "declared_hold_seconds": 18000,
        "warmup_bars": 55,
        "required_features": ["fear_greed_index"],
    }

    def initialize(self, ctx: BarContext) -> None:
        self.fast_ema_period = 20
        self.slow_ema_period = 50
        self.atr_period = 14
        self.cooldown_bars = 6
        self.last_exit_bar = -100

    def _ema(self, values, period: int) -> Optional[float]:
        if len(values) < period:
            return None
        k = 2.0 / (period + 1)
        ema = sum(values[:period]) / period
        for v in values[period:]:
            ema = v * k + ema * (1 - k)
        return ema

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
        closes = ctx.closes(60)
        highs = ctx.highs(30)
        lows = ctx.lows(30)

        if len(closes) < 55:
            return None

        # Manage open positions: early exit on structural trend loss
        if ctx.has_position():
            pos_dir = ctx.position_direction()
            curr_close = closes[-1]
            ema20 = self._ema(closes, self.fast_ema_period)
            if ema20 is not None:
                if pos_dir == "long" and curr_close < ema20:
                    return ctx.signal(
                        "flat",
                        confidence=0.6,
                        metadata={"reason": "trend_break_below_ema20", "close": curr_close, "ema20": ema20}
                    )
                elif pos_dir == "short" and curr_close > ema20:
                    return ctx.signal(
                        "flat",
                        confidence=0.6,
                        metadata={"reason": "trend_break_above_ema20", "close": curr_close, "ema20": ema20}
                    )
            return None

        # Cooldown guard after trade exit to avoid overtrading
        if (ctx.bar_index - self.last_exit_bar) < self.cooldown_bars:
            return None

        # Sentiment gate: Stand down at extreme euphoria or panic
        fng = float(ctx.features.get("fear_greed_index", 50.0))
        if fng < 25.0 or fng > 75.0:
            return None

        # Skip high crisis market regime
        crisis_score = float(ctx.market.get("crisis_score", 0.0))
        if crisis_score > 0.6:
            return None

        # Calculate momentum indicators
        curr_ema20 = self._ema(closes, self.fast_ema_period)
        prev_ema20 = self._ema(closes[:-1], self.fast_ema_period)
        curr_ema50 = self._ema(closes, self.slow_ema_period)

        if curr_ema20 is None or prev_ema20 is None or curr_ema50 is None:
            return None

        close = closes[-1]
        prev_close = closes[-2]

        atr = self._atr(highs, lows, closes, self.atr_period)
        atr_bps = ((atr / close) * 10000.0) if (atr and close > 0) else 250.0

        # Dynamic volatility stop calibrated to ATR (clamped within reasonable swing bounds)
        dynamic_sl_bps = max(180.0, min(380.0, atr_bps * 1.5))
        dynamic_tp_bps = dynamic_sl_bps * 1.8

        # Fresh breakout trigger: Price crossed above/below EMA20 while EMA20 slope aligns with macro EMA50
        bullish_slope = curr_ema20 > prev_ema20
        bearish_slope = curr_ema20 < prev_ema20

        long_trigger = (
            close > curr_ema20
            and prev_close <= prev_ema20
            and bullish_slope
            and curr_ema20 > curr_ema50
        )

        short_trigger = (
            close < curr_ema20
            and prev_close >= prev_ema20
            and bearish_slope
            and curr_ema20 < curr_ema50
        )

        if long_trigger:
            conf = 0.70 if (35.0 <= fng <= 65.0) else 0.55
            return ctx.signal(
                "long",
                confidence=conf,
                stop_loss_bps=dynamic_sl_bps,
                take_profit_bps=dynamic_tp_bps,
                horizon_seconds=18000,
                metadata={
                    "reason": "ema20_cross_up_with_fng_filter",
                    "close": close,
                    "ema20": curr_ema20,
                    "ema50": curr_ema50,
                    "fng": fng,
                    "atr_bps": atr_bps,
                }
            )

        if short_trigger:
            conf = 0.70 if (35.0 <= fng <= 65.0) else 0.55
            return ctx.signal(
                "short",
                confidence=conf,
                stop_loss_bps=dynamic_sl_bps,
                take_profit_bps=dynamic_tp_bps,
                horizon_seconds=18000,
                metadata={
                    "reason": "ema20_cross_down_with_fng_filter",
                    "close": close,
                    "ema20": curr_ema20,
                    "ema50": curr_ema50,
                    "fng": fng,
                    "atr_bps": atr_bps,
                }
            )

        return None