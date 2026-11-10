from domains.strategy_contract import Strategy, BarContext, Signal
from typing import Optional, Dict, Any
import math

class SentimentRecoveryEth(Strategy):
    METADATA = {
        "name": "Sentiment Recovery ETH",
        "domain": "eth_usdc",
        "declared_sl_bps": 250.0,
        "declared_tp_bps": 500.0,
        "declared_hold_seconds": 3600 * 6, # Aim for 6-hour holds
        "warmup_bars": 50,
        "required_features": ["fear_greed_index"],
    }

    def initialize(self, ctx: BarContext) -> None:
        self.ema_period_fast = 12
        self.ema_period_slow = 26
        self.rsi_period = 14
        self.cooldown_bars = 12 # 12 hours cooldown after a trade
        self.last_trade_bar = -self.cooldown_bars

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
            return 100.0 if avg_gain > 0 else 50.0 # Avoid division by zero, neutral if no change
        rs = avg_gain / avg_loss
        return 100.0 - (100.0 / (1.0 + rs))

    def on_bar(self, ctx: BarContext) -> Optional[Signal]:
        closes = ctx.closes(self.warmup_bars)
        if len(closes) < self.warmup_bars:
            return None

        if ctx.has_position():
            return None

        if ctx.bar_index < self.last_trade_bar + self.cooldown_bars:
            return None

        current_fear_greed = ctx.features.get("fear_greed_index", 50)
        previous_fear_greed = ctx.features.get("fear_greed_index", 50, lookback=1) # Get previous daily value

        # Calculate indicators
        ema_fast = self._ema(closes, self.ema_period_fast)
        ema_slow = self._ema(closes, self.ema_period_slow)
        rsi = self._rsi(closes, self.rsi_period)

        if ema_fast is None or ema_slow is None or rsi is None:
            return None

        # Sentiment Recovery Logic:
        # 1. Previous day was in "Extreme Fear" (FG < 20)
        # 2. Current day shows recovery (FG > previous_FG and no longer Extreme Fear)
        # 3. Market is not in a crisis regime
        is_sentiment_recovering = (
            previous_fear_greed < 20 and
            current_fear_greed >= 20 and
            current_fear_greed > previous_fear_greed
        )
        
        # Price Action Confirmation:
        # 1. Bullish EMA crossover (fast EMA crosses above slow EMA)
        # 2. RSI is not overbought (below 70) to allow for upside
        is_bullish_price_action = (
            ema_fast > ema_slow and
            ctx.closes(2)[-2] <= ctx.opens(2)[-2] and # Previous bar was red (potential capitulation)
            ctx.bar.close > ctx.bar.open and # Current bar is green (recovery)
            rsi < 70
        )

        # Regime filter: avoid trading during crisis
        is_market_calm = ctx.market.get("regime", "NORMAL") not in ["CRISIS", "MELTDOWN"]

        if is_sentiment_recovering and is_bullish_price_action and is_market_calm:
            confidence = min(0.7, (current_fear_greed - previous_fear_greed) / 20.0 + 0.3) # Scale confidence with recovery strength
            self.last_trade_bar = ctx.bar_index
            return ctx.signal(
                "long",
                confidence=confidence,
                metadata={
                    "reason": "sentiment_recovery_ema_cross_green_bar",
                    "fear_greed_current": current_fear_greed,
                    "fear_greed_previous": previous_fear_greed,
                    "ema_fast": ema_fast,
                    "ema_slow": ema_slow,
                    "rsi": rsi,
                    "close": ctx.bar.close,
                    "regime": ctx.market.get("regime")
                }
            )

        return None

    def on_position_close(self, ctx: BarContext, position: Dict[str, Any]) -> None:
        # Log closing event for analysis
        # self.last_trade_bar is already updated on signal, no need to update here
        pass