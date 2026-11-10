from domains.strategy_contract import Strategy, BarContext, Signal
from typing import Optional, Dict, Any
import math

class EuropeanSessionFadeETH(Strategy):
    METADATA = {
        "name": "European Session Fade ETH",
        "domain": "eth_usdc",
        "declared_sl_bps": 200.0,
        "declared_tp_bps": 400.0,
        "declared_hold_seconds": 3600 * 2,  # Up to 2 hours within the session
        "warmup_bars": 50,  # Need enough bars for EMA and recent high/low
        "required_features": ["fear_greed_index", "eth_return_pct"],
    }

    def initialize(self, ctx: BarContext) -> None:
        self.session_start_hour = 7  # UTC
        self.session_end_hour = 12  # UTC
        self.fade_threshold_pct = 0.5  # % move in session to consider stretched
        self.fade_period = 5 # bars to look for session move
        self.ema_period = 20 # for trend filter
        self.last_trade_bar_index = -self.METADATA["warmup_bars"] - 100 # Ensure it can trade initially
        self.cooldown_bars = 10 # Wait 10 bars after a trade

    def _ema(self, values, period):
        if len(values) < period:
            return None
        k = 2.0 / (period + 1)
        ema = sum(values[:period]) / period
        for v in values[period:]:
            ema = v * k + ema * (1 - k)
        return ema

    def on_bar(self, ctx: BarContext) -> Optional[Signal]:
        closes = ctx.closes(self.METADATA["warmup_bars"] + 1)
        highs = ctx.highs(self.METADATA["warmup_bars"] + 1)
        lows = ctx.lows(self.METADATA["warmup_bars"] + 1)

        if len(closes) < self.METADATA["warmup_bars"] + 1:
            return None

        current_hour = ctx.bar.timestamp.hour

        # Cooldown check
        if ctx.bar_index - self.cooldown_bars < self.last_trade_bar_index:
            return None

        # Exit logic: if we have a position and it's past session_end_hour
        if ctx.has_position() and current_hour >= self.session_end_hour:
            self.last_trade_bar_index = ctx.bar_index # Cooldown after exit
            return ctx.signal("flat", confidence=0.7, metadata={"reason": "session_end_exit", "current_hour": current_hour})

        # Do not enter if a position is already open
        if ctx.has_position():
            return None

        # --- Entry Conditions ---
        if not (self.session_start_hour <= current_hour < self.session_end_hour):
            return None

        # Regime filters
        if ctx.market.get("regime", "NORMAL") in ["CRISIS", "MELTDOWN"]:
            return None # Avoid trading in crisis regimes

        fear_greed = ctx.features.get("fear_greed_index", 50)
        if fear_greed <= 20 or fear_greed >= 80: # Avoid extreme F&G
            return None

        # Calculate session move
        # We need recent bars within the session to calculate the move
        session_opens = []
        session_closes = []
        session_highs = []
        session_lows = []
        session_volumes = []

        # Look back up to self.fade_period bars, ensuring they are within the current session
        for i in range(1, min(self.fade_period + 1, ctx.bar_index + 1)):
            if ctx.bar.timestamp.replace(hour=current_hour - i).hour >= self.session_start_hour:
                session_opens.append(ctx.opens(self.fade_period + 1)[-i])
                session_closes.append(ctx.closes(self.fade_period + 1)[-i])
                session_highs.append(ctx.highs(self.fade_period + 1)[-i])
                session_lows.append(ctx.lows(self.fade_period + 1)[-i])
                session_volumes.append(ctx.volumes(self.fade_period + 1)[-i])
            else:
                break # Stop if we go before session start

        if len(session_closes) < 2: # Need at least two bars to calculate a move
            return None

        session_start_price = session_opens[0]
        session_current_price = ctx.bar.close
        session_move_pct = ((session_current_price - session_start_price) / session_start_price) * 100.0

        # Trend filter using EMA
        ema_val = self._ema(closes, self.ema_period)
        if ema_val is None:
            return None

        is_uptrend = ctx.bar.close > ema_val
        is_downtrend = ctx.bar.close < ema_val

        signal_confidence = 0.6 # Base confidence

        # Fade Long logic: Price stretched up, but close is weakening
        if session_move_pct > self.fade_threshold_pct and not is_uptrend: # Avoid fading strong uptrends
            # Check for a stall or reversal signal within the last few bars
            # E.g., current close below previous high, or current close below open
            if ctx.bar.close < ctx.bar.open and ctx.bar.close < closes[-2]: # Current bar is red and lower than prev close
                self.last_trade_bar_index = ctx.bar_index
                return ctx.signal("short", confidence=signal_confidence,
                                  metadata={
                                      "reason": "session_europe_fade_long_stall",
                                      "session_start_price": session_start_price,
                                      "session_current_price": session_current_price,
                                      "session_move_pct": session_move_pct,
                                      "current_hour": current_hour,
                                      "ema_val": ema_val,
                                      "fear_greed": fear_greed,
                                  })

        # Fade Short logic: Price stretched down, but close is strengthening
        if session_move_pct < -self.fade_threshold_pct and not is_downtrend: # Avoid fading strong downtrends
            # Check for a stall or reversal signal
            # E.g., current close above previous low, or current close above open
            if ctx.bar.close > ctx.bar.open and ctx.bar.close > closes[-2]: # Current bar is green and higher than prev close
                self.last_trade_bar_index = ctx.bar_index
                return ctx.signal("long", confidence=signal_confidence,
                                  metadata={
                                      "reason": "session_europe_fade_short_stall",
                                      "session_start_price": session_start_price,
                                      "session_current_price": session_current_price,
                                      "session_move_pct": session_move_pct,
                                      "current_hour": current_hour,
                                      "ema_val": ema_val,
                                      "fear_greed": fear_greed,
                                  })

        return None

    def on_position_close(self, ctx: BarContext, position: Dict[str, Any]) -> None:
        # Optional: log position close for analysis or adaptive logic
        # self.last_trade_bar_index = ctx.bar_index # This is already handled in on_bar for session_end_exit
        pass