from domains.strategy_contract import Strategy, BarContext, Signal
from typing import Optional, Dict, Any
import math

class OvernightFade(Strategy):
    METADATA = {
        "name": "OvernightFade",
        "domain": "eth_usdc",
        "declared_sl_bps": 200.0,
        "declared_tp_bps": 400.0,
        "declared_hold_seconds": 3600 * 4, # Up to 4 hours
        "warmup_bars": 50,
        "required_features": ["fear_greed_index", "funding_rate_ethusdt"],
    }

    def initialize(self, ctx: BarContext) -> None:
        self.session_start_hour_utc = 0
        self.session_end_hour_utc = 6 # End of overnight session
        self.mid_session_hour_utc = 3 # Midpoint for fade target
        self.cooldown_bars = 6 # Wait 6 bars (6 hours) after a trade
        self.last_trade_bar = -self.cooldown_bars # Initialize to allow immediate trade

        # Indicator periods
        self.ema_period = 20
        self.atr_period = 14

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
        closes = ctx.closes(self.ema_period + 1)
        highs = ctx.highs(self.atr_period + 1)
        lows = ctx.lows(self.atr_period + 1)
        
        if len(closes) < self.METADATA["warmup_bars"] or len(highs) < self.METADATA["warmup_bars"] or len(lows) < self.METADATA["warmup_bars"]:
            return None

        current_hour = ctx.bar.timestamp.hour
        current_close = ctx.bar.close

        # Cooldown check
        if ctx.bar_index - self.last_trade_bar < self.cooldown_bars:
            return None

        # Calculate indicators
        ema = self._ema(closes, self.ema_period)
        atr = self._atr(highs, lows, closes, self.atr_period)

        if ema is None or atr is None:
            return None

        # Regime filters
        fear_greed = ctx.features.get("fear_greed_index", 50)
        funding_rate = ctx.features.get("funding_rate_ethusdt", 0.0)
        market_regime = ctx.market.get("regime", "NORMAL")
        trend_regime = ctx.market.get("trend_regime", "neutral")

        # Avoid trading during crisis or strong confirmed trends
        if market_regime in ["CRISIS", "MELTDOWN"] or ctx.market.get("crisis_score", 0.0) > 0.5:
            return None
        if (trend_regime == "bull" and funding_rate > 0.0005) or (trend_regime == "bear" and funding_rate < -0.0005):
            return None # Avoid fading strong trends with high funding

        # Check for start of session to establish session open
        is_session_start_bar = current_hour == self.session_start_hour_utc

        if is_session_start_bar:
            # Store session open price
            self.session_open_price = ctx.bar.open
            self.session_high = ctx.bar.high
            self.session_low = ctx.bar.low
            self.session_move = 0.0 # Reset session move tracking
            self.triggered_fade = False # Reset fade trigger for the new session
            return None # Don't trade on the first bar of the session

        # Update session high/low and check for fade conditions within the session
        if hasattr(self, 'session_open_price') and not self.triggered_fade and \
           self.session_start_hour_utc < current_hour <= self.mid_session_hour_utc: # Only consider fades in first half
            self.session_high = max(self.session_high, ctx.bar.high)
            self.session_low = min(self.session_low, ctx.bar.low)

            # Calculate current session range and move from open
            session_range = self.session_high - self.session_low
            current_move_from_open = current_close - self.session_open_price

            # Define "stretched" based on a multiple of ATR or percentage of session range
            # Use 1.5x ATR as a threshold for a "stretched" move
            stretch_threshold = 1.5 * atr 

            # Long Fade: Price has moved significantly up, now stalls near high
            if current_move_from_open > stretch_threshold and current_close > ema:
                # Check for stalling: current close is below the open of the current bar (slight pullback)
                # and price is near the session high
                if ctx.bar.close < ctx.bar.open and (self.session_high - current_close) < (0.2 * session_range):
                    if not ctx.has_position():
                        self.last_trade_bar = ctx.bar_index
                        self.triggered_fade = True
                        return ctx.signal(
                            "short",
                            confidence=0.7,
                            stop_loss_bps=self.METADATA["declared_sl_bps"],
                            take_profit_bps=self.METADATA["declared_tp_bps"],
                            horizon_seconds=self.METADATA["declared_hold_seconds"],
                            metadata={
                                "reason": "overnight_long_fade",
                                "session_open": self.session_open_price,
                                "session_move_from_open": current_move_from_open,
                                "stretch_threshold": stretch_threshold,
                                "current_close": current_close,
                                "ema": ema,
                                "atr": atr,
                                "hour": current_hour,
                                "fear_greed": fear_greed
                            }
                        )

            # Short Fade: Price has moved significantly down, now stalls near low
            elif current_move_from_open < -stretch_threshold and current_close < ema:
                # Check for stalling: current close is above the open of the current bar (slight pullback)
                # and price is near the session low
                if ctx.bar.close > ctx.bar.open and (current_close - self.session_low) < (0.2 * session_range):
                    if not ctx.has_position():
                        self.last_trade_bar = ctx.bar_index
                        self.triggered_fade = True
                        return ctx.signal(
                            "long",
                            confidence=0.7,
                            stop_loss_bps=self.METADATA["declared_sl_bps"],
                            take_profit_bps=self.METADATA["declared_tp_bps"],
                            horizon_seconds=self.METADATA["declared_hold_seconds"],
                            metadata={
                                "reason": "overnight_short_fade",
                                "session_open": self.session_open_price,
                                "session_move_from_open": current_move_from_open,
                                "stretch_threshold": stretch_threshold,
                                "current_close": current_close,
                                "ema": ema,
                                "atr": atr,
                                "hour": current_hour,
                                "fear_greed": fear_greed
                            }
                        )

        # Exit existing position if we are nearing the end of the session
        # or if the trade has gone against us significantly (implicit in SL but good to have a time-based check)
        if ctx.has_position() and current_hour >= (self.session_end_hour_utc - 1): # Exit 1 hour before session end
            return ctx.signal(
                "flat",
                confidence=0.5,
                metadata={
                    "reason": "session_end_exit",
                    "current_hour": current_hour,
                    "session_end_hour": self.session_end_hour_utc,
                    "position_direction": ctx.position_direction()
                }
            )
        
        return None

    def on_position_close(self, ctx: BarContext, position: Dict[str, Any]) -> None:
        # Reset triggered_fade to allow a new trade in the next session
        # or potentially if the current session's trade closed early for some reason
        self.triggered_fade = False
        self.last_trade_bar = ctx.bar_index # Update last trade bar on close