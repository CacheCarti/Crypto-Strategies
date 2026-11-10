from domains.strategy_contract import Strategy, BarContext, Signal
from typing import Optional, Dict, Any
import math

class SentimentVolConfirmation(Strategy):
    METADATA = {
        "name": "Sentiment Volatility Confirmation",
        "domain": "btc_usdc",
        "declared_sl_bps": 300.0,
        "declared_tp_bps": 600.0,
        "declared_hold_seconds": 7200, # 2 hours avg hold
        "warmup_bars": 50,
        "required_features": ["fear_greed_index"],
    }

    def initialize(self, ctx: BarContext) -> None:
        self.fast_ema_period = 10
        self.slow_ema_period = 30
        self.rsi_period = 14
        self.vol_period = 20 # for ATR
        self.cooldown_bars = 5
        self.last_signal_bar = -self.cooldown_bars

        # Sentiment thresholds
        self.extreme_fear_threshold = 20
        self.extreme_greed_threshold = 75
        self.neutral_zone_min = 30
        self.neutral_zone_max = 70

        # Volatility thresholds (multiples of ATR)
        # Entry requires recent high volatility to confirm panic/euphoria
        self.high_volatility_multiplier = 1.5
        # Exit if volatility drops significantly, suggesting exhaustion
        self.low_volatility_multiplier = 0.5

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
        
        # Calculate initial average gain/loss
        avg_gain = sum(gains[:period]) / period
        avg_loss = sum(losses[:period]) / period

        # Apply Wilder's smoothing for subsequent periods
        for i in range(period, len(gains)):
            avg_gain = (avg_gain * (period - 1) + gains[i]) / period
            avg_loss = (avg_loss * (period - 1) + losses[i]) / period

        if avg_loss == 0:
            return 100.0
        rs = avg_gain / avg_loss
        return 100.0 - (100.0 / (1.0 + rs))

    def _atr(self, highs, lows, closes, period=14):
        if len(closes) < period + 1:
            return None
        trs = []
        for i in range(1, len(closes)):
            tr = max(highs[i]-lows[i], abs(highs[i]-closes[i-1]), abs(lows[i]-closes[i-1]))
            trs.append(tr)
        
        # Simple moving average of TR
        if len(trs) < period:
            return None
        return sum(trs[-period:]) / period

    def on_bar(self, ctx: BarContext) -> Optional[Signal]:
        closes = ctx.closes(self.warmup_bars)
        highs = ctx.highs(self.warmup_bars)
        lows = ctx.lows(self.warmup_bars)
        volumes = ctx.volumes(self.warmup_bars)

        if len(closes) < self.warmup_bars:
            return None

        # Cooldown check
        if ctx.bar_index - self.last_signal_bar < self.cooldown_bars:
            return None

        current_price = ctx.bar.close
        
        # Indicators
        fast_ema = self._ema(closes, self.fast_ema_period)
        slow_ema = self._ema(closes, self.slow_ema_period)
        rsi = self._rsi(closes, self.rsi_period)
        atr = self._atr(highs, lows, closes, self.vol_period)

        if fast_ema is None or slow_ema is None or rsi is None or atr is None:
            return None

        # Feature: Fear & Greed Index
        fear_greed_index = ctx.features.get("fear_greed_index", 50)
        
        # Calculate recent average true range to gauge volatility
        # We need more bars for a reliable average ATR over the vol_period
        recent_atrs = []
        for i in range(self.vol_period, len(closes)):
            current_atr = self._atr(highs[:i+1], lows[:i+1], closes[:i+1], self.vol_period)
            if current_atr is not None:
                recent_atrs.append(current_atr)

        if len(recent_atrs) < self.vol_period: # Need enough ATR values to average
            return None
        
        avg_atr_recent = sum(recent_atrs[-self.vol_period:]) / self.vol_period
        current_atr = atr # The latest ATR value

        # Volatility confirmation
        high_volatility_confirmed = current_atr > (avg_atr_recent * self.high_volatility_multiplier)
        low_volatility_confirmed = current_atr < (avg_atr_recent * self.low_volatility_multiplier)

        # EXIT LOGIC
        if ctx.has_position():
            pos = ctx.position
            # Exit if volatility drops significantly, suggesting momentum loss
            if low_volatility_confirmed:
                self.last_signal_bar = ctx.bar_index
                return ctx.signal("flat", confidence=0.7, metadata={
                    "reason": "volatility_exhaustion_exit",
                    "current_atr": current_atr,
                    "avg_atr_recent": avg_atr_recent,
                    "low_volatility_threshold": avg_atr_recent * self.low_volatility_multiplier,
                    "fear_greed_index": fear_greed_index,
                    "rsi": rsi
                })
            
            # Exit if RSI turns against position significantly
            if pos["direction"] == "long" and rsi > 70 and rsi < 60: # Exiting overbought
                 self.last_signal_bar = ctx.bar_index
                 return ctx.signal("flat", confidence=0.6, metadata={
                    "reason": "long_exit_rsi_fading",
                    "rsi": rsi,
                    "fear_greed_index": fear_greed_index,
                    "current_atr": current_atr
                })
            if pos["direction"] == "short" and rsi < 30 and rsi > 40: # Exiting oversold
                 self.last_signal_bar = ctx.bar_index
                 return ctx.signal("flat", confidence=0.6, metadata={
                    "reason": "short_exit_rsi_fading",
                    "rsi": rsi,
                    "fear_greed_index": fear_greed_index,
                    "current_atr": current_atr
                })

            return None # Hold position if no exit condition met

        # ENTRY LOGIC
        # Long Entry: Extreme Fear + Price Bounce + Volatility Confirmation
        is_extreme_fear = fear_greed_index <= self.extreme_fear_threshold
        is_bullish_cross = fast_ema > slow_ema and closes[-2] < slow_ema # Price bounce
        is_rsi_oversold = rsi < 30

        if is_extreme_fear and is_bullish_cross and is_rsi_oversold and high_volatility_confirmed:
            self.last_signal_bar = ctx.bar_index
            return ctx.signal("long", confidence=0.8, metadata={
                "reason": "extreme_fear_bounce_vol_confirm",
                "fear_greed_index": fear_greed_index,
                "rsi": rsi,
                "fast_ema": fast_ema,
                "slow_ema": slow_ema,
                "current_atr": current_atr,
                "avg_atr_recent": avg_atr_recent,
                "high_volatility_threshold": avg_atr_recent * self.high_volatility_multiplier
            })

        # Short Entry: Extreme Greed + Price Rejection + Volatility Confirmation
        is_extreme_greed = fear_greed_index >= self.extreme_greed_threshold
        is_bearish_cross = fast_ema < slow_ema and closes[-2] > slow_ema # Price rejection
        is_rsi_overbought = rsi > 70

        if is_extreme_greed and is_bearish_cross and is_rsi_overbought and high_volatility_confirmed:
            self.last_signal_bar = ctx.bar_index
            return ctx.signal("short", confidence=0.8, metadata={
                "reason": "extreme_greed_rejection_vol_confirm",
                "fear_greed_index": fear_greed_index,
                "rsi": rsi,
                "fast_ema": fast_ema,
                "slow_ema": slow_ema,
                "current_atr": current_atr,
                "avg_atr_recent": avg_atr_recent,
                "high_volatility_threshold": avg_atr_recent * self.high_volatility_multiplier
            })
            
        return None

    def on_position_close(self, ctx: BarContext, position: Dict[str, Any]) -> None:
        # Optional: Log position close details or adjust internal state
        pass