from domains.strategy_contract import Strategy, BarContext, Signal
from typing import Optional, Dict, Any
import math

class EuropeanMorningImpulse(Strategy):
    METADATA = {
        "name": "European Morning Impulse",
        "domain": "eth_usdc",
        "declared_sl_bps": 200.0,
        "declared_tp_bps": 400.0,
        "declared_hold_seconds": 3600, # ~1 hour hold
        "warmup_bars": 50,
        "required_features": [],
    }

    def initialize(self, ctx: BarContext) -> None:
        self.ema_period_short = 10
        self.ema_period_long = 30
        self.rsi_period = 14
        self.cooldown_bars = 5 # Cooldown after a trade
        self.last_trade_bar = -self.cooldown_bars # Initialize to allow immediate trade

    def _ema(self, values, period):
        if len(values) < period:
            return None
        k = 2.0 / (period + 1)
        ema = sum(values[:period]) / period
        for v in values[period:]:
            ema = v * k + ema * (1 - k)
        return ema

    def _rsi(self, closes, period=14):
        if len(closes) < period + 1:
            return None
        gains, losses = [], []
        for i in range(1, len(closes)):
            diff = closes[i] - closes[i-1]
            gains.append(max(diff, 0))
            losses.append(max(-diff, 0))

        avg_gain = sum(gains[-period:]) / period
        avg_loss = sum(losses[-period:]) / period

        if avg_loss == 0:
            return 100.0
        rs = avg_gain / avg_loss
        return 100.0 - (100.0 / (1.0 + rs))

    def on_bar(self, ctx: BarContext) -> Optional[Signal]:
        closes = ctx.closes(self.METADATA["warmup_bars"])
        if len(closes) < self.METADATA["warmup_bars"]:
            return None

        current_hour = ctx.bar.timestamp.hour

        # Exit logic: close position by UTC 12:00
        if ctx.has_position():
            if current_hour == 12: # Close at the start of 12 UTC hour
                return ctx.signal("flat", confidence=0.8, metadata={"reason": "session_boundary_exit", "current_hour": current_hour})
            return None # Hold position

        # Cooldown period
        if ctx.bar_index - self.last_trade_bar < self.cooldown_bars:
            return None

        # Entry logic: European morning session (UTC 7-11)
        if 7 <= current_hour < 11:
            # Calculate indicators
            ema_short = self._ema(closes, self.ema_period_short)
            ema_long = self._ema(closes, self.ema_period_long)
            rsi = self._rsi(closes, self.rsi_period)

            if ema_short is None or ema_long is None or rsi is None:
                return None

            current_close = closes[-1]
            prev_close = closes[-2] if len(closes) >= 2 else None

            if prev_close is None:
                return None

            # Bullish conditions: EMA cross and RSI confirms
            if ema_short > ema_long and closes[-1] > ema_long and rsi < 70 and rsi > 40:
                # Check for recent EMA cross-up as an entry trigger
                prev_ema_short = self._ema(closes[:-1], self.ema_period_short)
                prev_ema_long = self._ema(closes[:-1], self.ema_period_long)

                if prev_ema_short is not None and prev_ema_long is not None and \
                   prev_ema_short <= prev_ema_long and ema_short > ema_long:
                    self.last_trade_bar = ctx.bar_index
                    return ctx.signal(
                        "long",
                        confidence=0.7,
                        metadata={
                            "reason": "ema_crossover_long_european_morning",
                            "ema_short": ema_short,
                            "ema_long": ema_long,
                            "rsi": rsi,
                            "current_hour": current_hour
                        }
                    )

            # Bearish conditions: EMA cross and RSI confirms
            elif ema_short < ema_long and closes[-1] < ema_long and rsi > 30 and rsi < 60:
                # Check for recent EMA cross-down as an entry trigger
                prev_ema_short = self._ema(closes[:-1], self.ema_period_short)
                prev_ema_long = self._ema(closes[:-1], self.ema_period_long)

                if prev_ema_short is not None and prev_ema_long is not None and \
                   prev_ema_short >= prev_ema_long and ema_short < ema_long:
                    self.last_trade_bar = ctx.bar_index
                    return ctx.signal(
                        "short",
                        confidence=0.7,
                        metadata={
                            "reason": "ema_crossover_short_european_morning",
                            "ema_short": ema_short,
                            "ema_long": ema_long,
                            "rsi": rsi,
                            "current_hour": current_hour
                        }
                    )
        return None

    def on_position_close(self, ctx: BarContext, position: Dict[str, Any]) -> None:
        # We track last_trade_bar in on_bar, so no specific action needed here
        pass