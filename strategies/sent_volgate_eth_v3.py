from domains.strategy_contract import Strategy, BarContext, Signal
from typing import Optional, Dict, Any
import math

class SentimentVolGateEth(Strategy):
    METADATA = {
        "name": "Sentiment Volatility Gated ETH",
        "domain": "eth_usdc",
        "declared_sl_bps": 250.0,
        "declared_tp_bps": 400.0,
        "declared_hold_seconds": 7200, # 2 hours avg hold
        "warmup_bars": 50,
        "required_features": ["fear_greed_index"],
    }

    def initialize(self, ctx: BarContext) -> None:
        self.ema_period_short = 10
        self.ema_period_long = 30
        self.rsi_period = 14
        self.vol_period = 20 # for ATR
        self.cooldown_bars = 5
        self.last_signal_bar = -self.cooldown_bars

        # Store previous values for transition detection
        self.prev_fear_greed_class = None
        self.prev_atr = 0.0

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
            return 100.0
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

        if len(closes) < self.METADATA["warmup_bars"]:
            return None

        # Cooldown period check
        if ctx.bar_index - self.last_signal_bar < self.cooldown_bars:
            return None

        # Calculate indicators
        ema_short = self._ema(closes, self.ema_period_short)
        ema_long = self._ema(closes, self.ema_period_long)
        rsi = self._rsi(closes, self.rsi_period)
        current_atr = self._atr(highs, lows, closes, self.vol_period)

        if ema_short is None or ema_long is None or rsi is None or current_atr is None:
            return None

        # Sentiment and Volatility Gating
        fear_greed_index = ctx.features.get("fear_greed_index", 50)
        
        # Volatility change detection
        atr_change = (current_atr - self.prev_atr) / self.prev_atr if self.prev_atr != 0 else 0
        self.prev_atr = current_atr # Update for next bar

        # Define volatility thresholds for "panic" and "euphoria" confirmation
        # These are relative to the ATR itself, indicating a sudden surge in volatility
        vol_surge_threshold = 0.25 # ATR increased by 25%

        signal_direction = None
        confidence = 0.0
        reason = ""
        
        # Long Entry Conditions
        # Fear (F&G < 35) + Volatility Surge + Price Action (EMA cross or RSI oversold)
        if (fear_greed_index < 35 and ctx.bar.close > ema_long and
            atr_change > vol_surge_threshold and ctx.bar.close > ema_short and ema_short > ema_long):
            
            # Additional check for RSI oversold to confirm 'panic buy' opportunity
            if rsi < 40 and not ctx.has_position():
                signal_direction = "long"
                confidence = 0.7 + (100 - fear_greed_index) / 100 * 0.2 # Higher confidence with more fear
                reason = "fear_vol_surge_ema_cross_rsi_oversold"

        # Short Entry Conditions
        # Greed (F&G > 65) + Volatility Surge + Price Action (EMA cross or RSI overbought)
        elif (fear_greed_index > 65 and ctx.bar.close < ema_long and
              atr_change > vol_surge_threshold and ctx.bar.close < ema_short and ema_short < ema_long):
            
            # Additional check for RSI overbought to confirm 'euphoria sell' opportunity
            if rsi > 60 and not ctx.has_position():
                signal_direction = "short"
                confidence = 0.7 + (fear_greed_index) / 100 * 0.2 # Higher confidence with more greed
                reason = "greed_vol_surge_ema_cross_rsi_overbought"

        # Exit logic (if holding a position and conditions reverse or weaken)
        if ctx.has_position():
            if ctx.position_direction() == "long":
                # Exit long if sentiment turns neutral/greedy or price drops below short EMA
                if fear_greed_index > 50 or ctx.bar.close < ema_short:
                    signal_direction = "flat"
                    confidence = 0.6
                    reason = "long_exit_sentiment_neutral_or_ema_break"
            elif ctx.position_direction() == "short":
                # Exit short if sentiment turns neutral/fearful or price rises above short EMA
                if fear_greed_index < 50 or ctx.bar.close > ema_short:
                    signal_direction = "flat"
                    confidence = 0.6
                    reason = "short_exit_sentiment_neutral_or_ema_break"
        
        # Avoid trading in ambiguous/neutral sentiment or quiet volatility
        if 40 <= fear_greed_index <= 60 or atr_change < 0.05: # Small volatility change
            if ctx.has_position():
                return None # Hold position if sentiment is ambiguous, don't exit unless price action dictates
            else:
                return None # Do not enter new positions in ambiguous conditions

        if signal_direction:
            self.last_signal_bar = ctx.bar_index
            return ctx.signal(
                signal_direction,
                confidence=min(1.0, confidence), # Cap confidence at 1.0
                stop_loss_bps=self.METADATA["declared_sl_bps"],
                take_profit_bps=self.METADATA["declared_tp_bps"],
                horizon_seconds=self.METADATA["declared_hold_seconds"],
                metadata={
                    "reason": reason,
                    "fear_greed_index": fear_greed_index,
                    "atr_change": atr_change,
                    "current_atr": current_atr,
                    "ema_short": ema_short,
                    "ema_long": ema_long,
                    "rsi": rsi,
                    "close": ctx.bar.close
                }
            )

        return None

    def on_position_close(self, ctx: BarContext, position: Dict[str, Any]) -> None:
        # Placeholder for adaptive logic, not implemented in this version
        pass