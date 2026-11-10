from domains.strategy_contract import Strategy, BarContext, Signal
from typing import Optional, Dict, Any
import math

class SentimentConditionedMomentum(Strategy):
    METADATA = {
        "name": "SentimentConditionedMomentum",
        "domain": "btc_usdc",
        "declared_sl_bps": 220.0,
        "declared_tp_bps": 440.0,
        "declared_hold_seconds": 18000,
        "warmup_bars": 55,
        "required_features": ["fear_greed_index"],
    }

    def initialize(self, ctx: BarContext) -> None:
        self.fast_period = 20
        self.slow_period = 50
        self.cooldown_bars = 6
        self.last_exit_bar = -999
        self.bars_in_position = 0

    def _ema(self, values, period: int) -> Optional[float]:
        if len(values) < period:
            return None
        k = 2.0 / (period + 1)
        ema = sum(values[:period]) / period
        for v in values[period:]:
            ema = v * k + ema * (1.0 - k)
        return ema

    def on_position_close(self, ctx: BarContext, position: Dict[str, Any]) -> None:
        self.last_exit_bar = ctx.bar_index
        self.bars_in_position = 0

    def on_bar(self, ctx: BarContext) -> Optional[Signal]:
        closes = ctx.closes(self.METADATA["warmup_bars"])
        if len(closes) < self.METADATA["warmup_bars"]:
            return None

        # Cooldown guard after position close
        if ctx.bar_index - self.last_exit_bar < self.cooldown_bars:
            return None

        ema20 = self._ema(closes, self.fast_period)
        ema20_prev = self._ema(closes[:-1], self.fast_period)
        ema50 = self._ema(closes, self.slow_period)
        if ema20 is None or ema20_prev is None or ema50 is None:
            return None

        fng = ctx.features.get("fear_greed_index", 50.0)
        curr_price = ctx.bar.close

        # Filter: Avoid violent reversal zones at sentiment extremes
        sentiment_ok = 25.0 <= fng <= 75.0

        # Market regime gate to avoid low-edge choppy or crisis phases
        market_regime = ctx.market.get("regime", "NORMAL")
        crisis_score = ctx.market.get("crisis_score", 0.0)
        regime_ok = market_regime in ("NORMAL", "HIGH_VOL") and crisis_score < 0.35

        has_pos = ctx.has_position()
        pos_dir = ctx.position_direction()

        if has_pos:
            self.bars_in_position += 1
            # Trend exhaustion or breakdown exit
            if pos_dir == "long":
                if curr_price < ema20 or ema20 < ema20_prev or fng > 80.0:
                    return ctx.signal(
                        "flat",
                        confidence=0.7,
                        metadata={
                            "reason": "long_trend_breakdown_or_sentiment_extreme",
                            "price": curr_price,
                            "ema20": ema20,
                            "fng": fng,
                            "bars_held": self.bars_in_position,
                        },
                    )
            elif pos_dir == "short":
                if curr_price > ema20 or ema20 > ema20_prev or fng < 20.0:
                    return ctx.signal(
                        "flat",
                        confidence=0.7,
                        metadata={
                            "reason": "short_trend_breakdown_or_sentiment_extreme",
                            "price": curr_price,
                            "ema20": ema20,
                            "fng": fng,
                            "bars_held": self.bars_in_position,
                        },
                    )
            return None

        # Entries only when sentiment & regime gates are passed
        if not (sentiment_ok and regime_ok):
            return None

        prev_price = closes[-2]
        ema20_rising = ema20 > ema20_prev
        ema20_falling = ema20 < ema20_prev

        # Long Trigger: Price pushes above EMA20 with EMA20 rising above EMA50 baseline
        long_cross = prev_price <= ema20_prev and curr_price > ema20
        if long_cross and ema20_rising and ema20 > ema50:
            return ctx.signal(
                "long",
                confidence=0.75,
                stop_loss_bps=self.METADATA["declared_sl_bps"],
                take_profit_bps=self.METADATA["declared_tp_bps"],
                horizon_seconds=self.METADATA["declared_hold_seconds"],
                metadata={
                    "reason": "ema20_bullish_cross_filtered",
                    "price": curr_price,
                    "ema20": ema20,
                    "ema50": ema50,
                    "fng": fng,
                },
            )

        # Short Trigger: Price breaks below EMA20 with EMA20 falling below EMA50 baseline
        short_cross = prev_price >= ema20_prev and curr_price < ema20
        if short_cross and ema20_falling and ema20 < ema50:
            return ctx.signal(
                "short",
                confidence=0.75,
                stop_loss_bps=self.METADATA["declared_sl_bps"],
                take_profit_bps=self.METADATA["declared_tp_bps"],
                horizon_seconds=self.METADATA["declared_hold_seconds"],
                metadata={
                    "reason": "ema20_bearish_cross_filtered",
                    "price": curr_price,
                    "ema20": ema20,
                    "ema50": ema50,
                    "fng": fng,
                },
            )

        return None