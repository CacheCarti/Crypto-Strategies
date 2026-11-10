from domains.strategy_contract import Strategy, BarContext, Signal
from typing import Optional, Dict, Any
import math

class EthAsiaSessionFade(Strategy):
    METADATA = {
        "name": "ETH Asia Session Fade",
        "domain": "eth_usdc",
        "declared_sl_bps": 200.0,
        "declared_tp_bps": 400.0,
        "declared_hold_seconds": 3600,
        "warmup_bars": 50,  # Need enough bars for EMA and ATR
        "required_features": ["fear_greed_index", "eth_return_pct"],
    }

    def initialize(self, ctx: BarContext) -> None:
        self.long_ema_period = 20
        self.short_ema_period = 5
        self.atr_period = 14
        self.cooldown_bars = 5
        self.last_trade_bar = -self.cooldown_bars
        self.session_midpoint = None
        self.session_high = -math.inf
        self.session_low = math.inf
        self.session_open = None
        self.session_close_price = None
        self.traded_this_session = False

    def _ema(self, values, period):
        if len(values) < period:
            return None
        k = 2.0 / (period + 1)
        ema = sum(values[:period]) / period
        for v in values[period:]:
            ema = v * k + ema * (1 - k)
        return ema

    def _atr(self, highs, lows, closes, period=14):
        if len(closes) < period + 1:
            return None
        trs = []
        for i in range(1, len(closes)):
            tr = max(highs[i]-lows[i], abs(highs[i]-closes[i-1]), abs(lows[i]-closes[i-1]))
            trs.append(tr)
        return sum(trs[-period:]) / period

    def on_bar(self, ctx: BarContext) -> Optional[Signal]:
        closes = ctx.closes(self.long_ema_period + 1)
        highs = ctx.highs(self.atr_period + 1)
        lows = ctx.lows(self.atr_period + 1)

        if len(closes) < self.METADATA["warmup_bars"] or len(highs) < self.METADATA["warmup_bars"] or len(lows) < self.METADATA["warmup_bars"]:
            return None

        current_hour = ctx.bar.timestamp.hour
        
        # Reset session data at UTC 0 (start of Asia session)
        if current_hour == 0 and not self.traded_this_session:
            self.session_midpoint = None
            self.session_high = -math.inf
            self.session_low = math.inf
            self.session_open = ctx.bar.open
            self.traded_this_session = False
        
        # Update session high/low
        if 0 <= current_hour < 8:
            self.session_high = max(self.session_high, ctx.bar.high)
            self.session_low = min(self.session_low, ctx.bar.low)
            if self.session_open is not None:
                self.session_midpoint = (self.session_high + self.session_low) / 2
            self.session_close_price = ctx.bar.close # Keep track of last price in session
        
        # Cooldown check
        if ctx.bar_index - self.last_trade_bar < self.cooldown_bars:
            return None

        # Regime filters
        fear_greed = ctx.features.get("fear_greed_index", 50)
        eth_return_pct = ctx.features.get("eth_return_pct", 0.0)
        market_regime = ctx.market.get("regime", "NORMAL")
        trend_regime = ctx.market.get("trend_regime", "neutral")

        # Do not trade in crisis or very strong trends
        if market_regime == "CRISIS" or abs(eth_return_pct) > 3.0 or trend_regime != "neutral":
            return None

        # Calculate EMAs
        short_ema = self._ema(closes, self.short_ema_period)
        long_ema = self._ema(closes, self.long_ema_period)
        
        if short_ema is None or long_ema is None:
            return None

        current_atr = self._atr(highs, lows, closes, self.atr_period)
        if current_atr is None:
            return None
        
        # Exit existing position before UTC 8 or if the session midpoint is reached
        if ctx.has_position():
            if current_hour >= 8: # Exit at end of Asia session
                self.traded_this_session = True # Mark as traded for the session
                return ctx.signal("flat", confidence=0.7, metadata={"reason": "session_end_exit", "exit_hour": current_hour, "session_midpoint": self.session_midpoint})
            
            # Trailing stop based on session midpoint (adaptive structure stop)
            pos_direction = ctx.position_direction()
            entry_price = ctx.position["entry_price"]
            
            if self.session_midpoint is not None:
                if pos_direction == "long" and ctx.bar.close < self.session_midpoint and ctx.bar.close < entry_price:
                    return ctx.signal("flat", confidence=0.6, metadata={"reason": "session_midpoint_stop_long", "current_price": ctx.bar.close, "session_midpoint": self.session_midpoint})
                elif pos_direction == "short" and ctx.bar.close > self.session_midpoint and ctx.bar.close > entry_price:
                    return ctx.signal("flat", confidence=0.6, metadata={"reason": "session_midpoint_stop_short", "current_price": ctx.bar.close, "session_midpoint": self.session_midpoint})
            
            return None # Hold existing position

        # Entry logic: Asia session fade (UTC 0-8)
        if 0 <= current_hour < 8 and not self.traded_this_session and self.session_open is not None and self.session_midpoint is not None:
            session_range = self.session_high - self.session_low
            
            # Avoid trading if session range is too small or too large (unusually stretched)
            # Use ATR to gauge typical volatility
            if session_range < 0.5 * current_atr or session_range > 3.0 * current_atr:
                return None

            # Fade stretched moves away from midpoint
            # Long entry: Price significantly below midpoint, showing signs of recovery (short EMA crossing above long EMA)
            if ctx.bar.close < self.session_midpoint - 0.5 * current_atr: # Stretched to the downside
                if short_ema > long_ema and closes[-2] < closes[-1]: # Recovery signal
                    self.last_trade_bar = ctx.bar_index
                    self.traded_this_session = True
                    return ctx.signal("long", confidence=0.75,
                                      stop_loss_bps=self.METADATA["declared_sl_bps"],
                                      take_profit_bps=self.METADATA["declared_tp_bps"],
                                      horizon_seconds=self.METADATA["declared_hold_seconds"],
                                      metadata={"reason": "asia_session_fade_long",
                                                "current_hour": current_hour,
                                                "session_midpoint": self.session_midpoint,
                                                "session_low": self.session_low,
                                                "current_price": ctx.bar.close,
                                                "short_ema": short_ema,
                                                "long_ema": long_ema,
                                                "atr": current_atr,
                                                "fear_greed": fear_greed})

            # Short entry: Price significantly above midpoint, showing signs of weakness (short EMA crossing below long EMA)
            elif ctx.bar.close > self.session_midpoint + 0.5 * current_atr: # Stretched to the upside
                if short_ema < long_ema and closes[-2] > closes[-1]: # Weakness signal
                    self.last_trade_bar = ctx.bar_index
                    self.traded_this_session = True
                    return ctx.signal("short", confidence=0.75,
                                      stop_loss_bps=self.METADATA["declared_sl_bps"],
                                      take_profit_bps=self.METADATA["declared_tp_bps"],
                                      horizon_seconds=self.METADATA["declared_hold_seconds"],
                                      metadata={"reason": "asia_session_fade_short",
                                                "current_hour": current_hour,
                                                "session_midpoint": self.session_midpoint,
                                                "session_high": self.session_high,
                                                "current_price": ctx.bar.close,
                                                "short_ema": short_ema,
                                                "long_ema": long_ema,
                                                "atr": current_atr,
                                                "fear_greed": fear_greed})
        
        # Reset traded_this_session flag at the end of the trading day
        if current_hour == 8 and self.traded_this_session:
            self.traded_this_session = False


        return None

    def on_position_close(self, ctx: BarContext, position: Dict[str, Any]) -> None:
        # Optional: Log position close details or adapt sizing.
        # For now, just reset the traded_this_session flag if it was closed by something other than session end.
        if position["bars_held"] > 0: # Ensure it was a real trade
             if position["metadata"].get("reason") != "session_end_exit":
                 self.traded_this_session = False