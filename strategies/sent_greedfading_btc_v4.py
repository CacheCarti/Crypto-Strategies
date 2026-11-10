from domains.strategy_contract import Strategy, BarContext, Signal
from typing import Optional, Dict, Any
import math

class SentimentFadeBTC(Strategy):
    METADATA = {
        "name": "SentimentFadeBTC",
        "domain": "btc_usdc",
        "declared_sl_bps": 250.0,
        "declared_tp_bps": 500.0,
        "declared_hold_seconds": 7200, # 2 hours avg hold
        "warmup_bars": 50,
        "required_features": ["fear_greed_index"],
    }

    def initialize(self, ctx: BarContext) -> None:
        self.rsi_period = 14
        self.ema_period = 20
        self.cooldown_bars = 5
        self.last_trade_bar = -self.cooldown_bars

    def _sma(self, values, period):
        if len(values) < period:
            return None
        return sum(values[-period:]) / period

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
        avg_gain = self._sma(gains[-period:], period)
        avg_loss = self._sma(losses[-period:], period)

        if avg_loss == 0:
            return 100.0
        if avg_gain == 0:
            return 0.0

        rs = avg_gain / avg_loss
        return 100.0 - (100.0 / (1.0 + rs))

    def on_bar(self, ctx: BarContext) -> Optional[Signal]:
        closes = ctx.closes(self.rsi_period + 1)
        if len(closes) < self.METADATA["warmup_bars"]:
            return None

        current_price = ctx.bar.close
        rsi = self._rsi(closes, self.rsi_period)
        ema = self._ema(closes, self.ema_period)

        if rsi is None or ema is None:
            return None

        # Cooldown period
        if ctx.bar_index - self.last_trade_bar < self.cooldown_bars:
            return None

        # Regime filters
        fear_greed_index = ctx.features.get("fear_greed_index", 50)
        trend_regime = ctx.market.get("trend_regime", "neutral")
        regime = ctx.market.get("regime", "NORMAL")

        signal_direction = None
        signal_confidence = 0.0
        signal_metadata = {}

        # Long: Fade extreme fear (oversold RSI + price above EMA + calm/neutral trend)
        if (fear_greed_index <= 25 and # Extreme Fear or Fear
            rsi < 35 and # Oversold
            current_price > ema and # Price above EMA (bullish bias or recovery)
            trend_regime in ["neutral", "bull"] and # Not strong bearish trend
            regime in ["calm", "normal"]): # Not overly volatile
            
            if not ctx.has_position() or ctx.position_direction() == "short":
                signal_direction = "long"
                signal_confidence = 0.75
                signal_metadata = {
                    "reason": "extreme_fear_rsi_oversold_ema_support",
                    "fear_greed": fear_greed_index,
                    "rsi": rsi,
                    "ema": ema,
                    "price": current_price,
                    "trend_regime": trend_regime,
                    "market_regime": regime
                }

        # Short: Fade extreme greed (overbought RSI + price below EMA + calm/neutral trend)
        elif (fear_greed_index >= 75 and # Extreme Greed or Greed
              rsi > 65 and # Overbought
              current_price < ema and # Price below EMA (bearish bias or reversal)
              trend_regime in ["neutral", "bear"] and # Not strong bullish trend
              regime in ["calm", "normal"]): # Not overly volatile
            
            if not ctx.has_position() or ctx.position_direction() == "long":
                signal_direction = "short"
                signal_confidence = 0.75
                signal_metadata = {
                    "reason": "extreme_greed_rsi_overbought_ema_resistance",
                    "fear_greed": fear_greed_index,
                    "rsi": rsi,
                    "ema": ema,
                    "price": current_price,
                    "trend_regime": trend_regime,
                    "market_regime": regime
                }
        
        # Exit logic: if conditions reverse strongly while in position
        if ctx.has_position():
            if ctx.position_direction() == "long" and (rsi > 70 or current_price < ema * 0.98): # Overbought or significant drop
                signal_direction = "flat"
                signal_confidence = 0.6
                signal_metadata = {
                    "reason": "long_exit_rsi_overbought_or_ema_break",
                    "rsi": rsi,
                    "ema": ema,
                    "price": current_price
                }
            elif ctx.position_direction() == "short" and (rsi < 30 or current_price > ema * 1.02): # Oversold or significant rally
                signal_direction = "flat"
                signal_confidence = 0.6
                signal_metadata = {
                    "reason": "short_exit_rsi_oversold_or_ema_break",
                    "rsi": rsi,
                    "ema": ema,
                    "price": current_price
                }


        if signal_direction:
            self.last_trade_bar = ctx.bar_index
            return ctx.signal(
                signal_direction,
                confidence=signal_confidence,
                stop_loss_bps=self.METADATA["declared_sl_bps"],
                take_profit_bps=self.METADATA["declared_tp_bps"],
                horizon_seconds=self.METADATA["declared_hold_seconds"],
                metadata=signal_metadata
            )

        return None

    def on_position_close(self, ctx: BarContext, position: Dict[str, Any]) -> None:
        # Optionally log or adjust parameters based on performance
        pass