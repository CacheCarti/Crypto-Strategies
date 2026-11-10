from domains.strategy_contract import Strategy, BarContext, Signal
from typing import Optional, Dict, Any
import math

class TokyoHandoffStrategy(Strategy):
    METADATA = {
        "name": "Tokyo Handoff BTC",
        "domain": "btc_usdc",
        "declared_sl_bps": 250.0,
        "declared_tp_bps": 500.0,
        "declared_hold_seconds": 7200, # 2-3 hours for handoff
        "warmup_bars": 50,
        "required_features": ["fear_greed_index"],
    }

    def initialize(self, ctx: BarContext) -> None:
        self.tokyo_period = 8 # 0-8 UTC
        self.min_move_pct = 0.3 # Minimum move percentage for a decisive move
        self.max_move_pct = 1.5 # Maximum move percentage to avoid exhaustion
        self.max_rsi = 70 # RSI threshold for exhaustion
        self.min_rsi = 30 # RSI threshold for exhaustion
        self.cooldown_bars = 12 # Cooldown after a trade (12 hours)
        self.last_trade_bar = -self.cooldown_bars

        self.ema_short_period = 10
        self.ema_long_period = 20

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
            return 100.0 if avg_gain > 0 else 50.0 # Handle flat or only gains
        
        rs = avg_gain / avg_loss
        return 100.0 - (100.0 / (1.0 + rs))

    def on_bar(self, ctx: BarContext) -> Optional[Signal]:
        if ctx.bar_index < self.METADATA["warmup_bars"]:
            return None

        closes = ctx.closes(self.tokyo_period + self.ema_long_period + 1)
        if len(closes) < self.tokyo_period + self.ema_long_period + 1:
            return None

        # Cooldown logic
        if ctx.bar_index < self.last_trade_bar + self.cooldown_bars:
            return None
        
        current_hour = ctx.bar.timestamp.hour

        # Tokyo session handoff logic (UTC 8:00 for Europe/US handoff)
        if current_hour == self.tokyo_period: # End of Tokyo session, start of Europe session
            # Check if we already have a position
            if ctx.has_position():
                return None # Already in a trade, wait for it to close

            # Get Tokyo session bars (0-8 UTC, so 8 bars including the current one)
            # We need closes from 0 to 7 to measure the move *ending* at 8
            tokyo_session_closes = ctx.closes(self.tokyo_period + 1) # Including current bar's close
            if len(tokyo_session_closes) < self.tokyo_period + 1:
                return None

            tokyo_open = tokyo_session_closes[0]
            tokyo_close = tokyo_session_closes[-1] # This is the close of the 8th hour (0-7)
            
            # Calculate the move during Tokyo session
            tokyo_move_pct = ((tokyo_close - tokyo_open) / tokyo_open) * 100.0

            # Get recent RSI to check for exhaustion
            recent_rsi = self._rsi(closes, period=14)
            if recent_rsi is None:
                return None

            # Get EMA values for trend filtering
            ema_short = self._ema(closes, self.ema_short_period)
            ema_long = self._ema(closes, self.ema_long_period)
            if ema_short is None or ema_long is None:
                return None

            # Regime filter: Stand down in crisis or extreme fear/greed
            fear_greed_index = ctx.features.get("fear_greed_index", 50)
            if ctx.market.get("regime") in ["CRISIS", "MELTDOWN"] or fear_greed_index < 20 or fear_greed_index > 80:
                return None # Avoid trading in extreme market conditions

            signal_direction = None
            confidence = 0.0

            # Bullish scenario: Tokyo up move, not exhausted, short EMA above long EMA
            if (tokyo_move_pct > self.min_move_pct and 
                tokyo_move_pct < self.max_move_pct and
                recent_rsi < self.max_rsi and # Not overbought
                ema_short > ema_long):

                signal_direction = "long"
                confidence = min(1.0, 0.5 + (tokyo_move_pct / self.max_move_pct) * 0.5)

            # Bearish scenario: Tokyo down move, not exhausted, short EMA below long EMA
            elif (tokyo_move_pct < -self.min_move_pct and 
                  tokyo_move_pct > -self.max_move_pct and
                  recent_rsi > self.min_rsi and # Not oversold
                  ema_short < ema_long):
                
                signal_direction = "short"
                confidence = min(1.0, 0.5 + (abs(tokyo_move_pct) / self.max_move_pct) * 0.5)

            if signal_direction:
                self.last_trade_bar = ctx.bar_index
                return ctx.signal(
                    signal_direction,
                    confidence=confidence,
                    metadata={
                        "reason": f"tokyo_handoff_continuation_{signal_direction}",
                        "tokyo_move_pct": tokyo_move_pct,
                        "recent_rsi": recent_rsi,
                        "ema_short": ema_short,
                        "ema_long": ema_long,
                        "fear_greed_index": fear_greed_index,
                        "market_regime": ctx.market.get("regime")
                    }
                )
        
        # Exit logic: If we are in a position, check if it's nearing the next Asia session
        # Exit before 23:00 UTC (start of next Asia session for most of market)
        if ctx.has_position() and current_hour >= 22:
            return ctx.signal(
                "flat",
                confidence=0.5,
                metadata={
                    "reason": "exit_before_next_asia_session",
                    "current_hour": current_hour
                }
            )

        return None

    def on_position_close(self, ctx: BarContext, position: Dict[str, Any]) -> None:
        # Optional: Log or adapt based on position close.
        # For this strategy, the cooldown is handled in on_bar.
        pass