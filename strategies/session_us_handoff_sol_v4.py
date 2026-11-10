from domains.strategy_contract import Strategy, BarContext, Signal
from typing import Optional, Dict, Any
import math

class SolUsSessionHandoff(Strategy):
    METADATA = {
        "name": "SOL US Session Handoff",
        "domain": "sol_usdc",
        "declared_sl_bps": 200.0,
        "declared_tp_bps": 400.0,
        "declared_hold_seconds": 3600, # Aim for 1-2 sessions
        "warmup_bars": 50, # For EMA and ATR
        "required_features": [], # No specific features required for this strategy
    }

    def initialize(self, ctx: BarContext) -> None:
        self.us_session_start_hour = 13 # UTC
        self.us_session_end_hour = 21 # UTC, exclusive (exit before this hour)
        self.session_lookback_bars = 6 # e.g., 13-19 to define impulse (6 hours)
        self.min_session_move_pct = 0.5 # Minimum % move in session to consider an impulse
        self.atr_period = 20
        self.ema_fast_period = 10
        self.ema_slow_period = 30
        self.last_trade_bar = -1
        self.cooldown_bars = 10 # Cooldown after a trade
        self.session_high = -math.inf
        self.session_low = math.inf
        self.session_open_price = None
        self.session_direction = None # "long", "short", or None
        self.session_start_bar_index = -1

    def _atr(self, highs, lows, closes, period):
        if len(closes) < period + 1:
            return None
        trs = []
        for i in range(1, len(closes)):
            tr = max(highs[i]-lows[i], abs(highs[i]-closes[i-1]), abs(lows[i]-closes[i-1]))
            trs.append(tr)
        return sum(trs[-period:]) / period

    def _ema(self, values, period):
        if len(values) < period:
            return None
        k = 2.0 / (period + 1)
        ema = sum(values[:period]) / period
        for v in values[period:]:
            ema = v * k + ema * (1 - k)
        return ema

    def on_bar(self, ctx: BarContext) -> Optional[Signal]:
        closes = ctx.closes(self.METADATA["warmup_bars"])
        highs = ctx.highs(self.METADATA["warmup_bars"])
        lows = ctx.lows(self.METADATA["warmup_bars"])
        current_hour = ctx.bar.timestamp.hour

        if len(closes) < self.METADATA["warmup_bars"]:
            return None

        # Regime filtering
        market_regime = ctx.market.get("regime", "NORMAL")
        trend_regime = ctx.market.get("trend_regime", "neutral")
        
        # Stand down in crisis or if trend is against us
        if market_regime == "CRISIS" or market_regime == "MELTDOWN":
            if ctx.has_position():
                return ctx.signal("flat", confidence=0.8, metadata={"reason": "crisis_regime_exit", "regime": market_regime})
            return None
        
        # Calculate indicators
        atr_val = self._atr(highs, lows, closes, self.atr_period)
        ema_fast = self._ema(closes, self.ema_fast_period)
        ema_slow = self._ema(closes, self.ema_slow_period)

        if atr_val is None or ema_fast is None or ema_slow is None:
            return None

        # Reset session data at the start of US session
        if current_hour == self.us_session_start_hour and ctx.bar_index != self.session_start_bar_index:
            self.session_open_price = ctx.bar.open
            self.session_high = ctx.bar.high
            self.session_low = ctx.bar.low
            self.session_direction = None
            self.session_start_bar_index = ctx.bar_index
            return None # Don't trade on the very first bar of the session

        # Update session high/low within the US session
        if self.us_session_start_hour <= current_hour < self.us_session_end_hour:
            self.session_high = max(self.session_high, ctx.bar.high)
            self.session_low = min(self.session_low, ctx.bar.low)

            # Determine session impulse after sufficient bars have passed
            if self.session_open_price is not None and \
               ctx.bar_index >= self.session_start_bar_index + self.session_lookback_bars - 1: # -1 because we count current bar
                
                session_range = self.session_high - self.session_low
                
                # Check for strong directional impulse
                if ctx.bar.close > self.session_open_price * (1 + self.min_session_move_pct / 100.0) and \
                   (ctx.bar.close - self.session_open_price) > 0.5 * session_range: # Price closed significantly above open and near session high
                    self.session_direction = "long"
                elif ctx.bar.close < self.session_open_price * (1 - self.min_session_move_pct / 100.0) and \
                     (self.session_open_price - ctx.bar.close) > 0.5 * session_range: # Price closed significantly below open and near session low
                    self.session_direction = "short"
                else:
                    self.session_direction = None # No clear impulse

        # --- Entry Logic ---
        # Enter at the end of the US session, carrying into the next handoff
        if current_hour == self.us_session_end_hour - 1 and \
           ctx.bar_index > self.last_trade_bar + self.cooldown_bars and \
           not ctx.has_position():
            
            # Additional confirmation: EMA crossover for trend alignment
            if self.session_direction == "long" and ema_fast > ema_slow and ctx.bar.close > ema_fast:
                confidence = 0.7
                if trend_regime == "bull": confidence += 0.1
                sl = self.METADATA["declared_sl_bps"]
                tp = self.METADATA["declared_tp_bps"]
                # Adjust SL/TP based on ATR for volatility
                sl_atr = atr_val * 1.5 / ctx.bar.close * 10000 # 1.5 ATR in bps
                tp_atr = atr_val * 3.0 / ctx.bar.close * 10000 # 3.0 ATR in bps
                
                return ctx.signal(
                    "long", 
                    confidence=min(1.0, confidence),
                    stop_loss_bps=max(sl, sl_atr),
                    take_profit_bps=max(tp, tp_atr),
                    horizon_seconds=7200, # Carry for ~2 hours into next session
                    metadata={
                        "reason": "us_session_bullish_handoff_ema_confirm",
                        "session_dir": self.session_direction,
                        "session_open": self.session_open_price,
                        "session_close": ctx.bar.close,
                        "ema_fast": ema_fast,
                        "ema_slow": ema_slow,
                        "atr": atr_val,
                        "trend_regime": trend_regime
                    }
                )
            elif self.session_direction == "short" and ema_fast < ema_slow and ctx.bar.close < ema_fast:
                confidence = 0.7
                if trend_regime == "bear": confidence += 0.1
                sl = self.METADATA["declared_sl_bps"]
                tp = self.METADATA["declared_tp_bps"]

                sl_atr = atr_val * 1.5 / ctx.bar.close * 10000
                tp_atr = atr_val * 3.0 / ctx.bar.close * 10000

                return ctx.signal(
                    "short", 
                    confidence=min(1.0, confidence),
                    stop_loss_bps=max(sl, sl_atr),
                    take_profit_bps=max(tp, tp_atr),
                    horizon_seconds=7200,
                    metadata={
                        "reason": "us_session_bearish_handoff_ema_confirm",
                        "session_dir": self.session_direction,
                        "session_open": self.session_open_price,
                        "session_close": ctx.bar.close,
                        "ema_fast": ema_fast,
                        "ema_slow": ema_slow,
                        "atr": atr_val,
                        "trend_regime": trend_regime
                    }
                )

        # --- Exit Logic ---
        # Force close all positions before the end of US session (or if conditions change)
        if ctx.has_position():
            # Exit if past the intended holding period (e.g., 2 hours past handoff)
            # Or if it's the very beginning of the next US session (don't hold across two US sessions)
            if current_hour >= self.us_session_end_hour + 2 or current_hour == self.us_session_start_hour:
                 return ctx.signal("flat", confidence=0.6, metadata={"reason": "time_based_exit", "current_hour": current_hour})

            # Exit if trend reverses sharply or EMA crossover happens against position
            if ctx.position_direction() == "long" and ema_fast < ema_slow:
                return ctx.signal("flat", confidence=0.7, metadata={"reason": "ema_bear_cross_exit", "ema_fast": ema_fast, "ema_slow": ema_slow})
            if ctx.position_direction() == "short" and ema_fast > ema_slow:
                return ctx.signal("flat", confidence=0.7, metadata={"reason": "ema_bull_cross_exit", "ema_fast": ema_fast, "ema_slow": ema_slow})
        
        return None

    def on_position_close(self, ctx: BarContext, position: Dict[str, Any]) -> None:
        self.last_trade_bar = ctx.bar_index