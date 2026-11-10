from domains.strategy_contract import Strategy, BarContext, Signal
from typing import Optional, Dict, Any
import math

class FridayPositioningBTC(Strategy):
    METADATA = {
        "name": "Friday Positioning BTC",
        "domain": "btc_usdc",
        "declared_sl_bps": 200.0,
        "declared_tp_bps": 400.0,
        "declared_hold_seconds": 3600 * 6, # Aim for 6-hour hold
        "warmup_bars": 50,
        "required_features": ["fear_greed_index", "funding_rate_btcusdt"],
    }

    def initialize(self, ctx: BarContext) -> None:
        self.cooldown_bars = 12 # Cooldown for 12 hours after a trade
        self.last_trade_bar = -self.cooldown_bars
        self.ema_period_fast = 12
        self.ema_period_slow = 26
        self.rsi_period = 14
        self.volume_ma_period = 20

    def _ema(self, values, period):
        if len(values) < period:
            return None
        k = 2.0 / (period + 1)
        ema = sum(values[:period]) / period # Simple average for initial EMA
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
        
        # Calculate initial average gain/loss
        avg_gain = sum(gains[-period:]) / period
        avg_loss = sum(losses[-period:]) / period

        if avg_loss == 0:
            return 100.0
        rs = avg_gain / avg_loss
        return 100.0 - (100.0 / (1.0 + rs))

    def _sma(self, values, period):
        if len(values) < period:
            return None
        return sum(values[-period:]) / period

    def on_bar(self, ctx: BarContext) -> Optional[Signal]:
        if ctx.bar_index < self.METADATA["warmup_bars"]:
            return None

        closes = ctx.closes(self.METADATA["warmup_bars"])
        highs = ctx.highs(self.METADATA["warmup_bars"])
        lows = ctx.lows(self.METADATA["warmup_bars"])
        volumes = ctx.volumes(self.METADATA["warmup_bars"])

        if len(closes) < self.METADATA["warmup_bars"]:
            return None

        # Cooldown logic
        if ctx.bar_index - self.last_trade_bar < self.cooldown_bars:
            return None

        current_hour = ctx.bar.timestamp.hour
        current_weekday = ctx.bar.timestamp.weekday() # Monday is 0, Sunday is 6

        # Friday positioning logic: Look for entries on Friday UTC late hours (e.g., 18:00 UTC to 23:00 UTC)
        # or early Sunday UTC for positioning for the new week (e.g., 22:00 UTC to 23:00 UTC)
        
        # Condition 1: Time Window
        is_friday_window = (current_weekday == 4 and current_hour >= 18 and current_hour <= 23)
        is_sunday_window = (current_weekday == 6 and current_hour >= 22 and current_hour <= 23)

        if not (is_friday_window or is_sunday_window):
            return None

        # Indicator calculations
        rsi_val = self._rsi(closes, self.rsi_period)
        ema_fast = self._ema(closes, self.ema_period_fast)
        ema_slow = self._ema(closes, self.ema_period_slow)
        avg_volume = self._sma(volumes, self.volume_ma_period)

        if rsi_val is None or ema_fast is None or ema_slow is None or avg_volume is None:
            return None

        # Regime filters
        fear_greed = ctx.features.get("fear_greed_index", 50)
        funding_rate = ctx.features.get("funding_rate_btcusdt", 0.0)
        market_trend = ctx.market.get("trend_regime", "neutral")
        market_regime = ctx.market.get("regime", "NORMAL")

        signal_direction = None
        confidence = 0.0
        reason = ""
        
        # Exit logic
        if ctx.has_position():
            # If the market becomes too volatile, consider exiting
            if ctx.regime == "crisis" or ctx.market.get("crisis_score", 0.0) > 0.5:
                signal_direction = "flat"
                confidence = 0.6
                reason = "crisis_regime_exit"
            # If a position is held past the weekend, try to close it before Monday open
            elif current_weekday == 0 and current_hour >= 0 and current_hour <= 2: # Monday 00-02 UTC
                signal_direction = "flat"
                confidence = 0.7
                reason = "monday_open_exit"
            # If funding rate turns strongly against us
            elif (ctx.position_direction() == "long" and funding_rate < -0.0002) or \
                 (ctx.position_direction() == "short" and funding_rate > 0.0002):
                signal_direction = "flat"
                confidence = 0.5
                reason = "unfavorable_funding_rate_exit"

            if signal_direction:
                self.last_trade_bar = ctx.bar_index
                return ctx.signal(
                    signal_direction,
                    confidence=confidence,
                    metadata={
                        "reason": reason,
                        "current_price": ctx.bar.close,
                        "rsi": rsi_val,
                        "ema_fast": ema_fast,
                        "ema_slow": ema_slow,
                        "fear_greed": fear_greed,
                        "funding_rate": funding_rate,
                        "market_trend": market_trend,
                        "market_regime": market_regime,
                    }
                )
            return None # Hold position if no exit signal

        # Entry logic
        # Long condition: Bullish trend, RSI oversold, price above EMAs, positive funding rate or neutral
        if (is_friday_window or is_sunday_window) and \
           market_trend in ["bull", "neutral"] and \
           market_regime not in ["CRISIS", "MELTDOWN"] and \
           fear_greed > 30 and \
           rsi_val < 40 and \
           ctx.bar.close > ema_fast and \
           ema_fast > ema_slow and \
           funding_rate >= -0.0001 and \
           ctx.bar.volume > avg_volume * 1.2: # Volume confirmation
            
            signal_direction = "long"
            confidence = 0.75 if market_trend == "bull" else 0.65
            reason = "friday_sunday_long_positioning_rsi_ema_volume_confirm"

        # Short condition: Bearish trend, RSI overbought, price below EMAs, negative funding rate or neutral
        elif (is_friday_window or is_sunday_window) and \
             market_trend in ["bear", "neutral"] and \
             market_regime not in ["CRISIS", "MELTDOWN"] and \
             fear_greed < 70 and \
             rsi_val > 60 and \
             ctx.bar.close < ema_fast and \
             ema_fast < ema_slow and \
             funding_rate <= 0.0001 and \
             ctx.bar.volume > avg_volume * 1.2: # Volume confirmation
            
            signal_direction = "short"
            confidence = 0.75 if market_trend == "bear" else 0.65
            reason = "friday_sunday_short_positioning_rsi_ema_volume_confirm"

        if signal_direction:
            self.last_trade_bar = ctx.bar_index
            return ctx.signal(
                signal_direction,
                confidence=confidence,
                stop_loss_bps=self.METADATA["declared_sl_bps"],
                take_profit_bps=self.METADATA["declared_tp_bps"],
                horizon_seconds=self.METADATA["declared_hold_seconds"],
                metadata={
                    "reason": reason,
                    "current_price": ctx.bar.close,
                    "rsi": rsi_val,
                    "ema_fast": ema_fast,
                    "ema_slow": ema_slow,
                    "fear_greed": fear_greed,
                    "funding_rate": funding_rate,
                    "market_trend": market_trend,
                    "market_regime": market_regime,
                    "current_hour": current_hour,
                    "current_weekday": current_weekday,
                    "volume_ratio": ctx.bar.volume / avg_volume,
                }
            )
        return None

    def on_position_close(self, ctx: BarContext, position: Dict[str, Any]) -> None:
        # Reset cooldown on position close, allowing a new trade after cooldown_bars
        self.last_trade_bar = ctx.bar_index