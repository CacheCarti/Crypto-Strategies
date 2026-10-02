from domains.strategy_contract import Strategy, BarContext, Signal
from typing import Optional, Dict, Any
import math


class FearAccumulationSwing(Strategy):
    METADATA = {
        "name": "Fear Accumulation Swing",
        "domain": "btc_usdc",
        "declared_sl_bps": 400.0,
        "declared_tp_bps": 700.0,
        "declared_hold_seconds": 43200,
        "warmup_bars": 30,
        "required_features": ["fear_greed_index"],
    }

    def initialize(self, ctx: BarContext) -> None:
        self.fear_threshold = 45.0
        self.exit_fg_threshold = 55.0
        self.rsi_period = 14
        self.ema_period = 20
        self.rsi_entry_threshold = 46.0
        self.rsi_exit_threshold = 68.0
        self.cooldown_bars = 4
        self.last_exit_bar = -100

    def _rsi(self, closes: list, period: int = 14) -> Optional[float]:
        if len(closes) < period + 1:
            return None
        gains, losses = [], []
        for i in range(1, len(closes)):
            diff = closes[i] - closes[i - 1]
            gains.append(max(diff, 0.0))
            losses.append(max(-diff, 0.0))
        avg_gain = sum(gains[-period:]) / period
        avg_loss = sum(losses[-period:]) / period
        if avg_loss == 0.0:
            return 100.0
        rs = avg_gain / avg_loss
        return 100.0 - (100.0 / (1.0 + rs))

    def _ema(self, values: list, period: int) -> Optional[float]:
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
        closes = ctx.closes(self.ema_period + 15)
        if len(closes) < self.METADATA["warmup_bars"]:
            return None

        fg_index = float(ctx.features.get("fear_greed_index", 50.0))
        rsi = self._rsi(closes, self.rsi_period)
        ema = self._ema(closes, self.ema_period)
        current_close = ctx.bar.close

        if rsi is None or ema is None:
            return None

        # Position Management & Exits
        if ctx.has_position():
            # Sentiment recovered above neutral discount zone
            if fg_index >= self.exit_fg_threshold:
                self.last_exit_bar = ctx.bar_index
                return ctx.signal(
                    "flat",
                    confidence=0.7,
                    metadata={
                        "reason": "sentiment_recovered_to_neutral",
                        "fear_greed": fg_index,
                        "rsi": round(rsi, 2),
                        "close": current_close,
                        "ema": round(ema, 2),
                    },
                )
            # Technical overbought exit
            if rsi >= self.rsi_exit_threshold:
                self.last_exit_bar = ctx.bar_index
                return ctx.signal(
                    "flat",
                    confidence=0.65,
                    metadata={
                        "reason": "rsi_momentum_take_profit",
                        "fear_greed": fg_index,
                        "rsi": round(rsi, 2),
                        "close": current_close,
                        "ema": round(ema, 2),
                    },
                )
            return None

        # Cooldown Gate
        if ctx.bar_index - self.last_exit_bar < self.cooldown_bars:
            return None

        # Loosened Entry Logic: Market in Fear territory (FG <= 45) combined with local dip (RSI <= 46 or below EMA)
        in_fear = fg_index <= self.fear_threshold
        local_dip = (rsi <= self.rsi_entry_threshold) or (current_close < ema and rsi < 50.0)

        if in_fear and local_dip:
            # Scale confidence based on fear depth
            confidence = 0.65
            if fg_index <= 25.0:
                confidence = 0.85
            elif fg_index <= 35.0:
                confidence = 0.75

            return ctx.signal(
                "long",
                confidence=confidence,
                stop_loss_bps=self.METADATA["declared_sl_bps"],
                take_profit_bps=self.METADATA["declared_tp_bps"],
                horizon_seconds=self.METADATA["declared_hold_seconds"],
                metadata={
                    "reason": "fear_accumulation_dip_entry",
                    "fear_greed": fg_index,
                    "rsi": round(rsi, 2),
                    "ema": round(ema, 2),
                    "close": current_close,
                },
            )

        return None