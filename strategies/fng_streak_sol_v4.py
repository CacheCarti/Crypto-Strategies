from domains.strategy_contract import Strategy, BarContext, Signal
from typing import Optional, Dict, Any
import math

class SolCapitulationStreak(Strategy):
    METADATA = {
        "name": "SOL Capitulation Streak Reversal",
        "domain": "sol_usdc",
        "declared_sl_bps": 450.0,
        "declared_tp_bps": 900.0,
        "declared_hold_seconds": 28800,
        "warmup_bars": 50,
        "required_features": ["fear_greed_index", "funding_rate_solusdt"],
    }

    def initialize(self, ctx: BarContext) -> None:
        self.rsi_period = 14
        self.ema_period = 21
        self.fear_threshold = 35.0
        self.recovery_threshold = 48.0
        self.cooldown_bars = 8
        self.last_exit_bar = -999
        self.fear_streak_count = 0
        self.last_fg = 50.0

    def _rsi(self, closes, period=14):
        if len(closes) < period + 1:
            return None
        gains, losses = [], []
        for i in range(1, len(closes)):
            diff = closes[i] - closes[i - 1]
            gains.append(max(diff, 0.0))
            losses.append(max(-diff, 0.0))
        avg_gain = sum(gains[-period:]) / period
        avg_loss = sum(losses[-period:]) / period
        if avg_loss == 0:
            return 100.0
        rs = avg_gain / avg_loss
        return 100.0 - (100.0 / (1.0 + rs))

    def _ema(self, values, period):
        if len(values) < period:
            return None
        k = 2.0 / (period + 1)
        ema = sum(values[:period]) / period
        for v in values[period:]:
            ema = v * k + ema * (1 - k)
        return ema

    def on_position_close(self, ctx: BarContext, position: Dict[str, Any]) -> None:
        self.last_exit_bar = ctx.bar_index

    def on_bar(self, ctx: BarContext) -> Optional[Signal]:
        closes = ctx.closes(self.METADATA["warmup_bars"])
        if len(closes) < self.METADATA["warmup_bars"]:
            return None

        fg_index = float(ctx.features.get("fear_greed_index", 50.0))
        funding_rate = float(ctx.features.get("funding_rate_solusdt", 0.0))

        # Track sustained fear persistence
        if fg_index <= self.fear_threshold:
            self.fear_streak_count += 1
        else:
            self.fear_streak_count = max(0, self.fear_streak_count - 1)
        self.last_fg = fg_index

        rsi = self._rsi(closes, self.rsi_period)
        ema21 = self._ema(closes, self.ema_period)
        if rsi is None or ema21 is None:
            return None

        current_price = ctx.bar.close
        has_pos = ctx.has_position()

        # Manage open position exit logic
        if has_pos:
            sentiment_recovered = fg_index >= self.recovery_threshold and rsi >= 55.0
            technical_overbought = rsi >= 72.0
            
            if sentiment_recovered or technical_overbought:
                reason = "sentiment_recovered" if sentiment_recovered else "rsi_overbought_exhaustion"
                return ctx.signal(
                    "flat",
                    confidence=0.7,
                    metadata={
                        "reason": reason,
                        "rsi": round(rsi, 2),
                        "fear_greed_index": fg_index,
                        "fear_streak_count": self.fear_streak_count,
                        "close": current_price
                    }
                )
            return None

        # Cooldown guard after prior trade
        if ctx.bar_index - self.last_exit_bar < self.cooldown_bars:
            return None

        # Long Entry Conditions:
        # 1. Depressed sentiment (capitulation / fear streak)
        # 2. Price oversold / inflection (RSI < 38 and green rebound bar, or deep RSI < 30)
        # 3. Funding rate not overly crowded long (<= 0.01%)
        is_fear_environment = fg_index <= self.fear_threshold or self.fear_streak_count >= 12
        rsi_rebound = (rsi < 38.0 and ctx.bar.close > ctx.bar.open) or (rsi < 30.0)
        funding_healthy = funding_rate <= 0.00015

        if is_fear_environment and rsi_rebound and funding_healthy:
            confidence = 0.65
            if self.fear_streak_count >= 24:
                confidence = min(0.90, confidence + 0.15)
            if rsi < 28.0:
                confidence = min(0.95, confidence + 0.10)

            return ctx.signal(
                "long",
                confidence=confidence,
                stop_loss_bps=self.METADATA["declared_sl_bps"],
                take_profit_bps=self.METADATA["declared_tp_bps"],
                horizon_seconds=self.METADATA["declared_hold_seconds"],
                metadata={
                    "reason": "capitulation_streak_rsi_rebound",
                    "fear_greed_index": fg_index,
                    "fear_streak_count": self.fear_streak_count,
                    "rsi": round(rsi, 2),
                    "ema21": round(ema21, 2),
                    "funding_rate": funding_rate,
                    "price": current_price
                }
            )

        return None