from domains.strategy_contract import Strategy, BarContext, Signal
from typing import Optional, Dict, Any
import math

class SentimentRecoveryBTC(Strategy):
    METADATA = {
        "name": "SentimentRecoveryBTC",
        "domain": "btc_usdc",
        "declared_sl_bps": 250.0,
        "declared_tp_bps": 500.0,
        "declared_hold_seconds": 7200,  # 2 hours average hold
        "warmup_bars": 60,  # Need enough bars for EMA and Fear & Greed history
        "required_features": ["fear_greed_index"],
    }

    def initialize(self, ctx: BarContext) -> None:
        self.ema_fast_period = 12
        self.ema_slow_period = 26
        self.rsi_period = 14
        self.cooldown_bars = 5  # Wait 5 bars after a trade before entering again
        self.last_trade_bar = -self.cooldown_bars - 1 # Initialize to allow immediate trade after warmup

        # Parameters for sentiment recovery
        self.extreme_fear_threshold = 20
        self.fear_recovery_threshold = 5

        # We need to track previous fear_greed_index to detect transitions
        self.prev_fear_greed_index: Optional[float] = None
        self.prev_fear_greed_class: Optional[str] = None

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
            return 100.0 if avg_gain > 0 else 50.0 # Prevent division by zero
        rs = avg_gain / avg_loss
        return 100.0 - (100.0 / (1.0 + rs))

    def on_bar(self, ctx: BarContext) -> Optional[Signal]:
        closes = ctx.closes(self.warmup_bars)
        highs = ctx.highs(self.warmup_bars)
        lows = ctx.lows(self.warmup_bars)

        if len(closes) < self.warmup_bars:
            return None

        # Cooldown logic
        if ctx.bar_index < self.last_trade_bar + self.cooldown_bars:
            return None

        current_price = ctx.bar.close

        ema_fast = self._ema(closes, self.ema_fast_period)
        ema_slow = self._ema(closes, self.ema_slow_period)
        rsi = self._rsi(closes, self.rsi_period)

        if ema_fast is None or ema_slow is None or rsi is None:
            return None

        fear_greed_index = ctx.features.get("fear_greed_index", 50.0)
        fear_greed_class = ctx.features.get("fear_greed_class", "Neutral")

        signal = None
        confidence = 0.0
        reason = ""
        metadata = {}

        # Long Entry Logic: Fear recovery and bullish price action
        if not ctx.has_position():
            # Condition 1: Sentiment Recovery
            # We need to ensure fear_greed_index is not the default value (50.0) from backtest
            # and that we have a previous value to compare for transition detection.
            if self.prev_fear_greed_index is not None and fear_greed_index != 50.0:
                # Entered extreme fear and is now recovering
                entered_extreme_fear = (self.prev_fear_greed_index <= self.extreme_fear_threshold and
                                        fear_greed_index > self.extreme_fear_threshold)
                # Or, was in fear and is now showing clear recovery
                was_in_fear = self.prev_fear_greed_index < 30
                is_recovering = fear_greed_index > self.prev_fear_greed_index + self.fear_recovery_threshold

                if (entered_extreme_fear or (was_in_fear and is_recovering)) and fear_greed_index < 50:
                    # Condition 2: Bullish Price Action (EMA crossover or price above EMAs)
                    if ema_fast > ema_slow and closes[-1] > ema_fast: # EMA cross + price confirmation
                        confidence = 0.75
                        reason = "fear_recovery_ema_bull_confirm"
                        metadata = {
                            "fear_greed_index": fear_greed_index,
                            "prev_fear_greed_index": self.prev_fear_greed_index,
                            "ema_fast": ema_fast,
                            "ema_slow": ema_slow,
                            "rsi": rsi,
                            "price": current_price
                        }
                        signal = ctx.signal("long", confidence=confidence, metadata=metadata)
                        self.last_trade_bar = ctx.bar_index

                    # Alternative bullish trigger: RSI oversold in a recovering sentiment
                    elif rsi < 35 and current_price > ema_slow: # RSI oversold and above slow EMA
                        confidence = 0.65
                        reason = "fear_recovery_rsi_oversold_above_ema_slow"
                        metadata = {
                            "fear_greed_index": fear_greed_index,
                            "prev_fear_greed_index": self.prev_fear_greed_index,
                            "ema_fast": ema_fast,
                            "ema_slow": ema_slow,
                            "rsi": rsi,
                            "price": current_price
                        }
                        signal = ctx.signal("long", confidence=confidence, metadata=metadata)
                        self.last_trade_bar = ctx.bar_index

            # Short Entry Logic: Extreme greed and bearish price action
            elif self.prev_fear_greed_index is not None and fear_greed_index != 50.0:
                # Entered extreme greed and is now showing signs of reversal
                entered_extreme_greed = (self.prev_fear_greed_index >= 75 and
                                         fear_greed_index < 75)
                # Or, was in greed and is now showing clear decline
                was_in_greed = self.prev_fear_greed_index > 60
                is_declining = fear_greed_index < self.prev_fear_greed_index - self.fear_recovery_threshold # Using same threshold for decline

                if (entered_extreme_greed or (was_in_greed and is_declining)) and fear_greed_index > 50:
                    # Condition 2: Bearish Price Action (EMA crossover or price below EMAs)
                    if ema_fast < ema_slow and closes[-1] < ema_fast: # EMA cross + price confirmation
                        confidence = 0.75
                        reason = "greed_decline_ema_bear_confirm"
                        metadata = {
                            "fear_greed_index": fear_greed_index,
                            "prev_fear_greed_index": self.prev_fear_greed_index,
                            "ema_fast": ema_fast,
                            "ema_slow": ema_slow,
                            "rsi": rsi,
                            "price": current_price
                        }
                        signal = ctx.signal("short", confidence=confidence, metadata=metadata)
                        self.last_trade_bar = ctx.bar_index

                    # Alternative bearish trigger: RSI overbought in a declining sentiment
                    elif rsi > 65 and current_price < ema_slow: # RSI overbought and below slow EMA
                        confidence = 0.65
                        reason = "greed_decline_rsi_overbought_below_ema_slow"
                        metadata = {
                            "fear_greed_index": fear_greed_index,
                            "prev_fear_greed_index": self.prev_fear_greed_index,
                            "ema_fast": ema_fast,
                            "ema_slow": ema_slow,
                            "rsi": rsi,
                            "price": current_price
                        }
                        signal = ctx.signal("short", confidence=confidence, metadata=metadata)
                        self.last_trade_bar = ctx.bar_index

        # Update previous fear_greed_index for the next bar's transition detection
        self.prev_fear_greed_index = fear_greed_index
        self.prev_fear_greed_class = fear_greed_class

        return signal

    def on_position_close(self, ctx: BarContext, position: Dict[str, Any]) -> None:
        # Optionally, reset state or log here
        pass