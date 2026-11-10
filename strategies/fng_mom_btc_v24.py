from domains.strategy_contract import Strategy, BarContext, Signal
from typing import Optional, Dict, Any
import math

class SentimentConditionedMomentum(Strategy):
    METADATA = {
        "name": "SentimentConditionedMomentum",
        "domain": "btc_usdc",
        "declared_sl_bps": 250.0,
        "declared_tp_bps": 500.0,
        "declared_hold_seconds": 18000,  # ~5 hours average hold
        "warmup_bars": 35,
        "required_features": ["fear_greed_index"],
    }

    def initialize(self, ctx: BarContext) -> None:
        self.ema_period = 20
        self.cooldown_bars = 6
        self.last_exit_bar = -999
        self.prev_ema = None
        self.prev_close = None

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
            tr = max(highs[i] - lows[i], abs(highs[i] - closes[i - 1]), abs(lows[i] - closes[i - 1]))
            trs.append(tr)
        return sum(trs[-period:]) / period

    def on_position_close(self, ctx: BarContext, position: Dict[str, Any]) -> None:
        self.last_exit_bar = ctx.bar_index

    def on_bar(self, ctx: BarContext) -> Optional[Signal]:
        closes = ctx.closes(self.METADATA["warmup_bars"])
        if len(closes) < self.METADATA["warmup_bars"]:
            return None

        # Cooldown guard after trade exit
        if ctx.bar_index - self.last_exit_bar < self.cooldown_bars:
            return None

        highs = ctx.highs(20)
        lows = ctx.lows(20)
        atr = self._atr(highs, lows, closes, period=14)
        if atr is None or atr == 0:
            return None

        curr_close = closes[-1]
        curr_ema = self._ema(closes, self.ema_period)
        if curr_ema is None:
            return None

        # Compute prior bar EMA to detect cross and slope cleanly
        prev_ema = self._ema(closes[:-1], self.ema_period)
        prev_close = closes[-2]
        if prev_ema is None:
            return None

        # Check sentiment filter: only trade when not at violent sentiment extremes
        fng = ctx.features.get("fear_greed_index", 50.0)
        if fng is None:
            fng = 50.0

        sentiment_ok = 25.0 <= fng <= 75.0
        regime = ctx.market.get("regime", "NORMAL")
        crisis_score = ctx.market.get("crisis_score", 0.0)
        market_safe = (regime != "CRISIS" and regime != "MELTDOWN") and (crisis_score < 0.7)

        # In-position management
        if ctx.has_position():
            pos_dir = ctx.position_direction()
            # Exit if trend breaks or market shifts to extreme sentiment
            if pos_dir == "long" and curr_close < curr_ema:
                return ctx.signal("flat", confidence=0.7, metadata={
                    "reason": "long_trend_break_below_ema",
                    "close": curr_close,
                    "ema": curr_ema,
                    "fng": fng
                })
            elif pos_dir == "short" and curr_close > curr_ema:
                return ctx.signal("flat", confidence=0.7, metadata={
                    "reason": "short_trend_break_above_ema",
                    "close": curr_close,
                    "ema": curr_ema,
                    "fng": fng
                })
            return None

        # Only trigger entry on new transition / breakout event
        if not (sentiment_ok and market_safe):
            return None

        ema_rising = curr_ema > prev_ema
        ema_falling = curr_ema < prev_ema

        # Event trigger: fresh cross over/under EMA with slope confirmation
        crossed_above = (prev_close <= prev_ema) and (curr_close > curr_ema)
        crossed_below = (prev_close >= prev_ema) and (curr_close < curr_ema)

        # Normalised ATR distance filter to avoid choppy micro-crosses
        atr_pct = (atr / curr_close) * 10000.0
        sl_bps = max(180.0, min(350.0, atr_pct * 1.5))
        tp_bps = sl_bps * 2.0

        # Long Entry: Bullish breakout through rising EMA20
        if crossed_above and ema_rising:
            return ctx.signal(
                "long",
                confidence=0.75,
                stop_loss_bps=sl_bps,
                take_profit_bps=tp_bps,
                horizon_seconds=18000,
                metadata={
                    "reason": "sentiment_gated_ema_cross_long",
                    "fng": fng,
                    "curr_close": curr_close,
                    "curr_ema": curr_ema,
                    "prev_ema": prev_ema,
                    "atr_bps": atr_pct
                }
            )

        # Short Entry: Bearish breakout through falling EMA20
        if crossed_below and ema_falling:
            return ctx.signal(
                "short",
                confidence=0.75,
                stop_loss_bps=sl_bps,
                take_profit_bps=tp_bps,
                horizon_seconds=18000,
                metadata={
                    "reason": "sentiment_gated_ema_cross_short",
                    "fng": fng,
                    "curr_close": curr_close,
                    "curr_ema": curr_ema,
                    "prev_ema": prev_ema,
                    "atr_bps": atr_pct
                }
            )

        return None