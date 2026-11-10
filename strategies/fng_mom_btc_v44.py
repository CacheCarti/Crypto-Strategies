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
        "warmup_bars": 40,
        "required_features": ["fear_greed_index"],
    }

    def initialize(self, ctx: BarContext) -> None:
        self.ema_period = 20
        self.cooldown_bars = 6
        self.last_trade_bar = -100

    def _ema(self, values, period):
        if len(values) < period:
            return None
        k = 2.0 / (period + 1)
        ema = sum(values[:period]) / period
        for v in values[period:]:
            ema = v * k + ema * (1.0 - k)
        return ema

    def on_position_close(self, ctx: BarContext, position: Dict[str, Any]) -> None:
        self.last_trade_bar = ctx.bar_index

    def on_bar(self, ctx: BarContext) -> Optional[Signal]:
        closes = ctx.closes(self.ema_period + 5)
        if len(closes) < self.ema_period + 3:
            return None

        # Cooldown guard: prevent overtrading and repetitive firing
        if ctx.bar_index - self.last_trade_bar < self.cooldown_bars:
            return None

        # Fetch sentiment filter: avoid violent reversal extremes (<25 or >75)
        fng = ctx.features.get("fear_greed_index", 50.0)
        if fng is None or fng < 25.0 or fng > 75.0:
            return None

        # Market regime guard: avoid crisis or meltdown
        market_regime = ctx.market.get("regime", "NORMAL")
        if market_regime in ("CRISIS", "MELTDOWN"):
            return None

        # Compute current and prior EMA20 to check trend slope
        ema_curr = self._ema(closes, self.ema_period)
        ema_prev = self._ema(closes[:-1], self.ema_period)
        if ema_curr is None or ema_prev is None:
            return None

        curr_close = closes[-1]
        prev_close = closes[-2]

        # Event boundary: Price must crossover EMA20 or confirm breakout bounce
        bull_cross = (prev_close <= ema_prev) and (curr_close > ema_curr)
        bear_cross = (prev_close >= ema_prev) and (curr_close < ema_curr)
        ema_rising = ema_curr > ema_prev
        ema_falling = ema_curr < ema_prev

        # Long entry: Bullish crossover event while EMA is rising in non-extreme sentiment
        if bull_cross and ema_rising:
            if not ctx.has_position():
                self.last_trade_bar = ctx.bar_index
                conf = 0.65 + min(0.25, max(0.0, (fng - 25.0) / 100.0))
                return ctx.signal(
                    "long",
                    confidence=conf,
                    stop_loss_bps=self.METADATA["declared_sl_bps"],
                    take_profit_bps=self.METADATA["declared_tp_bps"],
                    horizon_seconds=self.METADATA["declared_hold_seconds"],
                    metadata={
                        "reason": "bull_ema_cross_sentiment_filtered",
                        "price": curr_close,
                        "ema20": ema_curr,
                        "fng": fng,
                    },
                )

        # Short entry: Bearish crossover event while EMA is falling in non-extreme sentiment
        if bear_cross and ema_falling:
            if not ctx.has_position():
                self.last_trade_bar = ctx.bar_index
                conf = 0.65 + min(0.25, max(0.0, (75.0 - fng) / 100.0))
                return ctx.signal(
                    "short",
                    confidence=conf,
                    stop_loss_bps=self.METADATA["declared_sl_bps"],
                    take_profit_bps=self.METADATA["declared_tp_bps"],
                    horizon_seconds=self.METADATA["declared_hold_seconds"],
                    metadata={
                        "reason": "bear_ema_cross_sentiment_filtered",
                        "price": curr_close,
                        "ema20": ema_curr,
                        "fng": fng,
                    },
                )

        # Exit existing positions if slope reverses
        if ctx.has_position():
            pos_dir = ctx.position_direction()
            if pos_dir == "long" and curr_close < ema_curr:
                self.last_trade_bar = ctx.bar_index
                return ctx.signal(
                    "flat",
                    confidence=0.6,
                    metadata={"reason": "exit_long_below_ema20", "price": curr_close, "ema20": ema_curr},
                )
            elif pos_dir == "short" and curr_close > ema_curr:
                self.last_trade_bar = ctx.bar_index
                return ctx.signal(
                    "flat",
                    confidence=0.6,
                    metadata={"reason": "exit_short_above_ema20", "price": curr_close, "ema20": ema_curr},
                )

        return None