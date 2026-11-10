from domains.strategy_contract import Strategy, BarContext, Signal
from typing import Optional, Dict, Any
import math

class SentimentRecoveryBTC(Strategy):
    METADATA = {
        "name": "SentimentRecoveryBTC",
        "domain": "btc_usdc",
        "declared_sl_bps": 250.0,
        "declared_tp_bps": 500.0,
        "declared_hold_seconds": 7200, # 2 hours
        "warmup_bars": 50,
        "required_features": ["fear_greed_index"],
    }

    def initialize(self, ctx: BarContext) -> None:
        self.ema_period = 20
        self.rsi_period = 14
        self.fear_threshold_extreme = 20
        self.fear_threshold_recovery = 30
        self.cooldown_bars = 12 # 12 hours cooldown
        self.last_trade_bar = -self.cooldown_bars - 1 # Allow trade on first bar

    def _sma(self, values, period):
        if len(values) < period:
            return None
        return sum(values[-period:]) / period

    def _ema(self, values, period):
        if len(values) < period:
            return None
        k = 2.0 / (period + 1)
        ema = self._sma(values[:period], period) # Use SMA for initial EMA
        if ema is None:
            return None
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

        avg_gain = self._sma(gains[-period:], period)
        avg_loss = self._sma(losses[-period:], period)

        if avg_loss == 0:
            return 100.0 if avg_gain > 0 else 50.0 # Avoid division by zero
        if avg_gain is None or avg_loss is None:
            return None

        rs = avg_gain / avg_loss
        return 100.0 - (100.0 / (1.0 + rs))

    def on_bar(self, ctx: BarContext) -> Optional[Signal]:
        closes = ctx.closes(self.ema_period * 2) # Need enough for EMA
        highs = ctx.highs(self.rsi_period + 1)
        lows = ctx.lows(self.rsi_period + 1)

        if len(closes) < self.METADATA["warmup_bars"]:
            return None

        current_price = ctx.bar.close
        ema = self._ema(closes, self.ema_period)
        rsi = self._rsi(closes, self.rsi_period)

        if ema is None or rsi is None:
            return None

        fear_greed_index = ctx.features.get("fear_greed_index", 50)

        # Cooldown check
        if ctx.bar_index - self.last_trade_bar < self.cooldown_bars:
            return None

        # --- Entry Logic ---
        # Long only: Sentiment recovering from extreme fear, price showing strength (above EMA, RSI not overbought)
        if not ctx.has_position():
            # Condition 1: Sentiment transitioning from extreme fear to recovery
            # We need to check previous fear_greed_index to detect transition
            # Since fear_greed_index is daily, we can assume it's stable for 24 bars (1h bars)
            # We need to look back at least one bar to see if it was in extreme fear.
            # Assuming ctx.features.get can give us previous values (not directly, so we use a proxy:
            # if current is recovering, and it was likely extreme before)
            
            # Simplified sentiment check: if currently recovering, and we were recently in extreme fear
            # This is a heuristic as we don't have historical feature values directly.
            # We assume if the current FGI is in recovery zone, and not too far from extreme, it's a recovery.
            
            sentiment_recovery_signal = (
                fear_greed_index > self.fear_threshold_extreme and
                fear_greed_index <= self.fear_threshold_recovery
            )

            # Condition 2: Price action confirmation (above EMA, RSI not overbought)
            price_confirmation = (
                current_price > ema and
                rsi < 60 # Not overbought, still room to run
            )
            
            # Condition 3: Recent price dip (e.g. current close is above previous low, but still below recent high)
            # This helps to ensure we are buying into strength after a dip, not just chasing.
            # Look for a hammer-like bar or strong close after a low
            if len(lows) > 2 and len(highs) > 2:
                recent_low = lows[-2]
                recent_high = highs[-2]
                price_dip_recovery = (
                    current_price > recent_low and # Recovering from a dip
                    ctx.bar.open < ctx.bar.close # Green candle
                )
            else:
                price_dip_recovery = False

            if sentiment_recovery_signal and price_confirmation and price_dip_recovery:
                self.last_trade_bar = ctx.bar_index
                return ctx.signal(
                    "long",
                    confidence=0.7,
                    stop_loss_bps=self.METADATA["declared_sl_bps"],
                    take_profit_bps=self.METADATA["declared_tp_bps"],
                    horizon_seconds=self.METADATA["declared_hold_seconds"],
                    metadata={
                        "reason": "fear_greed_recovery_ema_support_green_candle",
                        "fear_greed_index": fear_greed_index,
                        "current_price": current_price,
                        "ema_20": ema,
                        "rsi_14": rsi,
                    }
                )

        # --- Exit Logic (if already in position) ---
        # The engine handles SL/TP. We can add discretionary exits here if needed.
        # For this strategy, we rely on declared SL/TP and horizon for longer holds.
        return None

    def on_position_close(self, ctx: BarContext, position: Dict[str, Any]) -> None:
        # Reset last trade bar to enforce cooldown after any position closes
        self.last_trade_bar = ctx.bar_index
        ctx.log(f"Position closed. Direction: {position['direction']}, Return: {position['return_bps']} bps, Bars Held: {position['bars_held']}")