from domains.strategy_contract import Strategy, BarContext, Signal
from typing import Optional, Dict, Any
import math

class SentimentVolGateSOL(Strategy):
    METADATA = {
        "name": "Sentiment Volatility Gate SOL",
        "domain": "sol_usdc",
        "declared_sl_bps": 250.0,
        "declared_tp_bps": 500.0,
        "declared_hold_seconds": 7200, # 2 hours hold time for 1h bars
        "warmup_bars": 60, # Need enough bars for 20-period indicators
        "required_features": ["fear_greed_index", "funding_rate_solusdt"],
    }

    def initialize(self, ctx: BarContext) -> None:
        self.fast_ema_period = 10
        self.slow_ema_period = 30
        self.rsi_period = 14
        self.atr_period = 14
        self.volatility_threshold = 1.5 # Multiplier for ATR to define "high" volatility
        self.cooldown_bars = 5 # Cooldown after a trade
        self.last_trade_bar = -self.cooldown_bars

    def _ema(self, values, period):
        if len(values) < period:
            return None
        k = 2.0 / (period + 1)
        ema = sum(values[:period]) / period
        for v in values[period:]:
            ema = v * k + ema * (1 - k)
        return ema

    def _rsi(self, closes, period):
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
            return 100.0 if avg_gain > 0 else 50.0 # Avoid division by zero
        rs = avg_gain / avg_loss
        return 100.0 - (100.0 / (1.0 + rs))

    def _atr(self, highs, lows, closes, period):
        if len(closes) < period + 1:
            return None
        trs = []
        for i in range(1, len(closes)):
            tr = max(highs[i]-lows[i], abs(highs[i]-closes[i-1]), abs(lows[i]-closes[i-1]))
            trs.append(tr)
        return sum(trs[-period:]) / period

    def on_bar(self, ctx: BarContext) -> Optional[Signal]:
        closes = ctx.closes(self.warmup_bars)
        highs = ctx.highs(self.warmup_bars)
        lows = ctx.lows(self.warmup_bars)

        if len(closes) < self.warmup_bars:
            return None

        # Cooldown logic
        if ctx.bar_index - self.last_trade_bar < self.cooldown_bars:
            return None

        current_price = ctx.bar.close

        # Indicators
        fast_ema = self._ema(closes, self.fast_ema_period)
        slow_ema = self._ema(closes, self.slow_ema_period)
        rsi = self._rsi(closes, self.rsi_period)
        atr = self._atr(highs, lows, closes, self.atr_period)

        if any(x is None for x in [fast_ema, slow_ema, rsi, atr]):
            return None

        # Market Context & Features
        fear_greed_index = ctx.features.get("fear_greed_index", 50)
        funding_rate = ctx.features.get("funding_rate_solusdt", 0.0)

        # Volatility check: High volatility is when ATR is significantly above its recent average
        # We'll use a simple threshold for current ATR vs a small multiple of a recent low ATR
        # For simplicity, we'll compare current ATR to a fixed multiple of a "normal" ATR
        # (A more advanced version would use rolling ATR average or std dev)
        is_high_volatility = atr > (ctx.market.get("avg_daily_atr", 0.005) * self.volatility_threshold * current_price) # avg_daily_atr is a placeholder, need a better reference

        # Simplified volatility check: is current ATR significantly higher than the average of the last few ATRs?
        # Requires more history for ATRs, so let's use a simpler proxy for "panic/euphoria confirmed by vol"
        # For this strategy, we define high volatility as just a high absolute ATR
        # A simple check for significant price movement within the last few bars
        recent_price_range = max(highs[-5:]) - min(lows[-5:])
        is_high_volatility = recent_price_range / current_price > 0.015 # 1.5% range in last 5 bars

        signal_direction = "hold"
        confidence = 0.0
        reason = "no_signal"
        signal_metadata = {}

        # Condition 1: Extreme Fear (Greed Index < 30) with High Volatility
        # Look for long opportunities
        if fear_greed_index < 30 and is_high_volatility:
            # Price action confirmation: RSI oversold and EMA crossover
            if rsi < 35 and fast_ema > slow_ema:
                signal_direction = "long"
                confidence = 0.75
                reason = "extreme_fear_high_vol_rsi_oversold_ema_cross"
                signal_metadata = {
                    "fear_greed_index": fear_greed_index,
                    "is_high_volatility": is_high_volatility,
                    "rsi": rsi,
                    "fast_ema": fast_ema,
                    "slow_ema": slow_ema,
                    "current_price": current_price
                }

        # Condition 2: Extreme Greed (Greed Index > 70) with High Volatility
        # Look for short opportunities
        elif fear_greed_index > 70 and is_high_volatility:
            # Price action confirmation: RSI overbought and EMA crossover
            if rsi > 65 and fast_ema < slow_ema:
                signal_direction = "short"
                confidence = 0.75
                reason = "extreme_greed_high_vol_rsi_overbought_ema_cross"
                signal_metadata = {
                    "fear_greed_index": fear_greed_index,
                    "is_high_volatility": is_high_volatility,
                    "rsi": rsi,
                    "fast_ema": fast_ema,
                    "slow_ema": slow_ema,
                    "current_price": current_price
                }

        # Exit logic:
        if ctx.has_position():
            pos_dir = ctx.position_direction()
            entry_price = ctx.position["entry_price"]

            # Exit if sentiment reverses significantly OR volatility drops
            if pos_dir == "long":
                # If fear subsides or turns to greed, or volatility drops
                if fear_greed_index > 50 or not is_high_volatility:
                    signal_direction = "flat"
                    confidence = 0.6
                    reason = "long_exit_sentiment_vol_reversal"
                    signal_metadata = {
                        "fear_greed_index": fear_greed_index,
                        "is_high_volatility": is_high_volatility,
                        "current_price": current_price,
                        "entry_price": entry_price
                    }
            elif pos_dir == "short":
                # If greed subsides or turns to fear, or volatility drops
                if fear_greed_index < 50 or not is_high_volatility:
                    signal_direction = "flat"
                    confidence = 0.6
                    reason = "short_exit_sentiment_vol_reversal"
                    signal_metadata = {
                        "fear_greed_index": fear_greed_index,
                        "is_high_volatility": is_high_volatility,
                        "current_price": current_price,
                        "entry_price": entry_price
                    }

        if signal_direction != "hold" and confidence > 0:
            self.last_trade_bar = ctx.bar_index
            return ctx.signal(
                signal_direction,
                confidence=confidence,
                metadata=signal_metadata
            )

        return None

    def on_position_close(self, ctx: BarContext, position: Dict[str, Any]) -> None:
        # Not strictly needed for this strategy, but good practice to include
        # for potential adaptive logic or logging.
        pass