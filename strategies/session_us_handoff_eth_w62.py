from domains.strategy_contract import Strategy, BarContext, Signal
from typing import Optional, Dict, Any
import math

class USSessionImpulseStrategy(Strategy):
    METADATA = {
        "name": "US Session Impulse",
        "domain": "eth_usdc",
        "declared_sl_bps": 200.0,
        "declared_tp_bps": 400.0,
        "declared_hold_seconds": 3600,  # Aim for 1-2 hour hold, exit by session close
        "warmup_bars": 50,
        "required_features": ["funding_rate_ethusdt", "fear_greed_index"]
    }

    def initialize(self, ctx: BarContext) -> None:
        self.entry_hour_start = 13  # UTC 13 (US session open)
        self.entry_hour_end = 15    # Allow entries for 2 hours
        self.exit_hour = 21         # UTC 21 (US session close)
        self.atr_period = 20
        self.ema_period = 20
        self.rsi_period = 14
        self.cooldown_bars = 5      # Cooldown after a trade
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
        
        if len(gains) < period or len(losses) < period:
            return None

        avg_gain = sum(gains[-period:]) / period
        avg_loss = sum(losses[-period:]) / period
        
        if avg_loss == 0:
            return 100.0 if avg_gain > 0 else 50.0 # Avoid division by zero, neutral if no movement
        
        rs = avg_gain / avg_loss
        return 100.0 - (100.0 / (1.0 + rs))

    def _atr(self, highs, lows, closes, period=14):
        if len(closes) < period + 1:
            return None
        trs = []
        for i in range(1, len(closes)):
            tr = max(highs[i]-lows[i], abs(highs[i]-closes[i-1]), abs(lows[i]-closes[i-1]))
            trs.append(tr)
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

        current_hour = ctx.bar.timestamp.hour
        current_close = ctx.bar.close

        # Calculate indicators
        ema_val = self._ema(closes, self.ema_period)
        rsi_val = self._rsi(closes, self.rsi_period)
        atr_val = self._atr(highs, lows, closes, self.atr_period)

        if ema_val is None or rsi_val is None or atr_val is None:
            return None

        # Exit logic
        if ctx.has_position():
            # Exit if past the exit hour
            if current_hour >= self.exit_hour:
                return ctx.signal("flat", confidence=0.7, metadata={"reason": "session_close", "exit_hour": self.exit_hour, "current_hour": current_hour})
            
            # Additional exit condition: price crosses back over EMA
            if ctx.position_direction() == "long" and current_close < ema_val:
                return ctx.signal("flat", confidence=0.6, metadata={"reason": "ema_cross_exit", "current_close": current_close, "ema_val": ema_val})
            if ctx.position_direction() == "short" and current_close > ema_val:
                return ctx.signal("flat", confidence=0.6, metadata={"reason": "ema_cross_exit", "current_close": current_close, "ema_val": ema_val})
            
            return None # Hold position if no exit conditions met

        # Entry logic
        if current_hour >= self.entry_hour_start and current_hour < self.entry_hour_end:
            # Check cooldown
            if ctx.bar_index - self.last_trade_bar < self.cooldown_bars:
                return None

            # Determine session impulse (simple: compare to session open or recent average)
            # Use the close price from the start of the entry window (ctx.bar.timestamp - 1 hour * (current_hour - self.entry_hour_start))
            # Or simpler: use the start of the current bar as a reference for impulse
            
            # Impulse based on current close vs previous EMA
            # Long condition: Price above EMA, RSI strong, and positive funding rate
            if current_close > ema_val and rsi_val > 55: # Not overbought, but strong
                funding_rate = ctx.features.get("funding_rate_ethusdt", 0.0)
                fear_greed = ctx.features.get("fear_greed_index", 50)
                
                if funding_rate > 0.0001 and fear_greed > 40: # Positive funding and not extreme fear
                    self.last_trade_bar = ctx.bar_index
                    # Set a wider SL/TP based on ATR for volatility
                    sl_bps = max(self.declared_sl_bps, atr_val / current_close * 10000 * 1.5) # ATR based SL, at least declared
                    tp_bps = max(self.declared_tp_bps, atr_val / current_close * 10000 * 2.5) # ATR based TP, at least declared
                    confidence = min(0.8, (rsi_val - 55) / 15 * 0.5 + 0.3) # Scale confidence
                    
                    return ctx.signal(
                        "long",
                        confidence=confidence,
                        stop_loss_bps=sl_bps,
                        take_profit_bps=tp_bps,
                        horizon_seconds=3600 * (self.exit_hour - current_hour), # Exit by session end
                        metadata={
                            "reason": "us_session_long_impulse",
                            "current_hour": current_hour,
                            "current_close": current_close,
                            "ema_val": ema_val,
                            "rsi_val": rsi_val,
                            "funding_rate": funding_rate,
                            "fear_greed": fear_greed,
                            "atr_val": atr_val
                        }
                    )
            
            # Short condition: Price below EMA, RSI weak, and negative funding rate
            elif current_close < ema_val and rsi_val < 45: # Not oversold, but weak
                funding_rate = ctx.features.get("funding_rate_ethusdt", 0.0)
                fear_greed = ctx.features.get("fear_greed_index", 50)
                
                if funding_rate < -0.0001 and fear_greed < 60: # Negative funding and not extreme greed
                    self.last_trade_bar = ctx.bar_index
                    sl_bps = max(self.declared_sl_bps, atr_val / current_close * 10000 * 1.5)
                    tp_bps = max(self.declared_tp_bps, atr_val / current_close * 10000 * 2.5)
                    confidence = min(0.8, (45 - rsi_val) / 15 * 0.5 + 0.3)
                    
                    return ctx.signal(
                        "short",
                        confidence=confidence,
                        stop_loss_bps=sl_bps,
                        take_profit_bps=tp_bps,
                        horizon_seconds=3600 * (self.exit_hour - current_hour),
                        metadata={
                            "reason": "us_session_short_impulse",
                            "current_hour": current_hour,
                            "current_close": current_close,
                            "ema_val": ema_val,
                            "rsi_val": rsi_val,
                            "funding_rate": funding_rate,
                            "fear_greed": fear_greed,
                            "atr_val": atr_val
                        }
                    )

        return None

    def on_position_close(self, ctx: BarContext, position: Dict[str, Any]) -> None:
        # Optional: Log position close for analysis or adaptive sizing
        pass