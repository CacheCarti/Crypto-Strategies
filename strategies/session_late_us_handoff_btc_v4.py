from domains.strategy_contract import Strategy, BarContext, Signal
from typing import Optional, Dict, Any
import math

class BtcLateUsSessionHandoff(Strategy):
    METADATA = {
        "name": "BTC Late US Session Handoff",
        "domain": "btc_usdc",
        "declared_sl_bps": 250.0,
        "declared_tp_bps": 500.0,
        "declared_hold_seconds": 3600 * 6, # Roughly 6 hours
        "warmup_bars": 50,
        "required_features": ["fear_greed_index", "funding_rate_btcusdt"],
    }

    def initialize(self, ctx: BarContext) -> None:
        self.entry_hour_start = 18 # UTC 18:00 (late US session)
        self.entry_hour_end = 24   # UTC 00:00 (end of US session)
        self.exit_hour = 0         # UTC 00:00
        self.min_session_impulse_bars = 4 # Minimum bars for session impulse
        self.min_impulse_pct = 0.002 # 0.2% move in session to consider impulse
        self.cooldown_bars = 10 # Cooldown after a trade
        self.last_trade_bar = -self.cooldown_bars

        self.fast_ema_period = 10
        self.slow_ema_period = 30
        self.rsi_period = 14
        self.atr_period = 14
        
        self.max_crisis_score = 0.5 # Max crisis score to trade
        self.min_fear_greed_index = 20 # Avoid extreme fear
        self.max_fear_greed_index = 80 # Avoid extreme greed

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
            return 100.0 if avg_gain > 0 else 50.0
        
        rs = avg_gain / avg_loss
        return 100.0 - (100.0 / (1.0 + rs))

    def on_bar(self, ctx: BarContext) -> Optional[Signal]:
        closes = ctx.closes(self.warmup_bars)
        if len(closes) < self.warmup_bars:
            return None

        current_hour = ctx.bar.timestamp.hour

        # Exit logic first
        if ctx.has_position():
            # Exit at 00:00 UTC regardless
            if current_hour == self.exit_hour:
                return ctx.signal("flat", confidence=0.8, metadata={
                    "reason": "session_boundary_exit",
                    "exit_hour": self.exit_hour,
                    "entry_hour": ctx.position["metadata"].get("entry_hour", "N/A")
                })
            return None # Hold position if no exit condition met

        # Cooldown period
        if ctx.bar_index - self.last_trade_bar < self.cooldown_bars:
            return None

        # Entry logic
        if current_hour >= self.entry_hour_start and current_hour < self.entry_hour_end:
            # Check market regime filters
            crisis_score = ctx.market.get("crisis_score", 0.0)
            fear_greed_index = ctx.features.get("fear_greed_index", 50)
            trend_regime = ctx.market.get("trend_regime", "neutral")
            funding_rate = ctx.features.get("funding_rate_btcusdt", 0.0)

            if crisis_score > self.max_crisis_score:
                return None
            if fear_greed_index < self.min_fear_greed_index or fear_greed_index > self.max_fear_greed_index:
                return None

            # Calculate session impulse
            # Look back from current bar to the start of the entry window (self.entry_hour_start)
            # Find the closes within the current session window
            session_closes = []
            session_opens = []
            
            # Find the start of the current session entry window
            session_start_bar_index = -1
            for i in range(ctx.bar_index - self.warmup_bars + 1, ctx.bar_index + 1):
                if ctx.bar.timestamp.replace(hour=current_hour - (ctx.bar_index - i)).hour == self.entry_hour_start:
                    session_start_bar_index = i
                    break
            
            if session_start_bar_index == -1: # Not enough history for current session
                return None

            # Collect data for the current session up to the current bar
            temp_closes = ctx.closes(ctx.bar_index - session_start_bar_index + 1)
            temp_opens = ctx.opens(ctx.bar_index - session_start_bar_index + 1)
            
            # Ensure we have enough bars to form an impulse
            if len(temp_closes) < self.min_session_impulse_bars:
                return None

            session_open_price = temp_opens[0]
            session_close_price = temp_closes[-1]
            
            impulse_pct = (session_close_price - session_open_price) / session_open_price

            # Technical indicators for confirmation
            fast_ema = self._ema(closes, self.fast_ema_period)
            slow_ema = self._ema(closes, self.slow_ema_period)
            rsi = self._rsi(closes, self.rsi_period)
            
            if fast_ema is None or slow_ema is None or rsi is None:
                return None

            # Long signal conditions
            if impulse_pct > self.min_impulse_pct: # Bullish impulse
                if fast_ema > slow_ema and rsi > 50: # Bullish EMA crossover and RSI momentum
                    self.last_trade_bar = ctx.bar_index
                    return ctx.signal("long", confidence=0.75, metadata={
                        "reason": "late_us_session_bullish_handoff",
                        "session_impulse_pct": impulse_pct,
                        "fast_ema": fast_ema,
                        "slow_ema": slow_ema,
                        "rsi": rsi,
                        "entry_hour": current_hour,
                        "regime": ctx.regime,
                        "trend_regime": trend_regime,
                        "fear_greed_index": fear_greed_index,
                        "funding_rate": funding_rate
                    })

            # Short signal conditions
            elif impulse_pct < -self.min_impulse_pct: # Bearish impulse
                if fast_ema < slow_ema and rsi < 50: # Bearish EMA crossover and RSI momentum
                    self.last_trade_bar = ctx.bar_index
                    return ctx.signal("short", confidence=0.75, metadata={
                        "reason": "late_us_session_bearish_handoff",
                        "session_impulse_pct": impulse_pct,
                        "fast_ema": fast_ema,
                        "slow_ema": slow_ema,
                        "rsi": rsi,
                        "entry_hour": current_hour,
                        "regime": ctx.regime,
                        "trend_regime": trend_regime,
                        "fear_greed_index": fear_greed_index,
                        "funding_rate": funding_rate
                    })

        return None

    def on_position_close(self, ctx: BarContext, position: Dict[str, Any]) -> None:
        # Optional: Log or adapt strategy based on closed position
        # For example, reset cooldown or adjust confidence for next trade
        pass