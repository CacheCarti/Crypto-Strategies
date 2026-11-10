from domains.strategy_contract import Strategy, BarContext, Signal
from typing import Optional, Dict, Any
import math

class BtcEuMorningImpulse(Strategy):
    METADATA = {
        "name": "BTC EU Morning Impulse",
        "domain": "btc_usdc",
        "declared_sl_bps": 200.0,
        "declared_tp_bps": 400.0,
        "declared_hold_seconds": 3600,  # Aim for ~1 hour hold, but can extend slightly
        "warmup_bars": 50,
        "required_features": ["fear_greed_index", "btc_return_pct"]
    }

    def initialize(self, ctx: BarContext) -> None:
        self.entry_hour_start = 7  # UTC 7 AM
        self.entry_hour_end = 10  # UTC 10 AM (allow entry up to and including 10 AM bar)
        self.exit_hour = 12       # UTC 12 PM (exit before or at the start of this bar)
        self.ema_fast_period = 12
        self.ema_slow_period = 26
        self.rsi_period = 14
        self.cooldown_bars = 5    # Cooldown after a position closes
        self.last_trade_bar_index = -self.cooldown_bars

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
            return 100.0 if avg_gain > 0 else 50.0 # Avoid division by zero, if no loss, RSI is 100
        rs = avg_gain / avg_loss
        return 100.0 - (100.0 / (1.0 + rs))

    def on_bar(self, ctx: BarContext) -> Optional[Signal]:
        closes = ctx.closes(self.warmup_bars)
        highs = ctx.highs(self.warmup_bars)
        lows = ctx.lows(self.warmup_bars)

        if len(closes) < self.METADATA["warmup_bars"]:
            return None

        current_hour = ctx.bar.timestamp.hour
        current_close = ctx.bar.close

        # Calculate indicators
        ema_fast = self._ema(closes, self.ema_fast_period)
        ema_slow = self._ema(closes, self.ema_slow_period)
        rsi = self._rsi(closes, self.rsi_period)

        # Check for sufficient data for indicators
        if ema_fast is None or ema_slow is None or rsi is None:
            return None

        # Regime filters
        fear_greed_index = ctx.features.get("fear_greed_index", 50)
        btc_return_pct = ctx.features.get("btc_return_pct", 0.0)
        regime = ctx.market.get("regime", "NORMAL")
        trend_regime = ctx.market.get("trend_regime", "neutral")

        # Exit logic
        if ctx.has_position():
            # Close by end of session (UTC 12 PM bar)
            if current_hour >= self.exit_hour:
                reason = "session_end_time"
                if ctx.position_direction() == "long":
                    reason += "_long"
                else:
                    reason += "_short"
                return ctx.signal("flat", confidence=0.8, metadata={"reason": reason, "current_hour": current_hour, "exit_hour": self.exit_hour})
            return None # Hold position if not time to exit

        # Entry logic - only consider entries during specific hours and outside cooldown
        if not (self.entry_hour_start <= current_hour <= self.entry_hour_end):
            return None

        if ctx.bar_index - self.last_trade_bar_index < self.cooldown_bars:
            return None

        # Avoid trading in highly volatile or crisis regimes
        if regime in ["CRISIS", "HIGH_VOL"]:
            return None

        # Entry Signal 1: Bullish Impulse (EMA Crossover + RSI Confirmation)
        if ema_fast > ema_slow and closes[-2] < ema_slow and current_close > ema_slow: # EMA fast crosses above slow, and price confirms above slow EMA
            if rsi < 70 and rsi > 45: # RSI not overbought, showing some strength
                # Further filter by overall market trend and sentiment
                if trend_regime == "bull" and fear_greed_index > 40:
                    self.last_trade_bar_index = ctx.bar_index
                    return ctx.signal(
                        "long",
                        confidence=0.75,
                        metadata={
                            "reason": "eu_morning_bull_impulse",
                            "entry_hour": current_hour,
                            "ema_fast": ema_fast,
                            "ema_slow": ema_slow,
                            "rsi": rsi,
                            "trend_regime": trend_regime,
                            "fear_greed_index": fear_greed_index,
                            "btc_return_pct": btc_return_pct
                        }
                    )

        # Entry Signal 2: Bearish Impulse (EMA Crossover + RSI Confirmation)
        if ema_fast < ema_slow and closes[-2] > ema_slow and current_close < ema_slow: # EMA fast crosses below slow, and price confirms below slow EMA
            if rsi > 30 and rsi < 55: # RSI not oversold, showing some weakness
                # Further filter by overall market trend and sentiment
                if trend_regime == "bear" and fear_greed_index < 60:
                    self.last_trade_bar_index = ctx.bar_index
                    return ctx.signal(
                        "short",
                        confidence=0.75,
                        metadata={
                            "reason": "eu_morning_bear_impulse",
                            "entry_hour": current_hour,
                            "ema_fast": ema_fast,
                            "ema_slow": ema_slow,
                            "rsi": rsi,
                            "trend_regime": trend_regime,
                            "fear_greed_index": fear_greed_index,
                            "btc_return_pct": btc_return_pct
                        }
                    )

        return None

    def on_position_close(self, ctx: BarContext, position: Dict[str, Any]) -> None:
        # Update last trade bar index to enforce cooldown
        self.last_trade_bar_index = ctx.bar_index