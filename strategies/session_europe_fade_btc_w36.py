from domains.strategy_contract import Strategy, BarContext, Signal
from typing import Optional, Dict, Any
import math

class EuropeanMorningFade(Strategy):
    METADATA = {
        "name": "European Morning Fade",
        "domain": "btc_usdc",
        "declared_sl_bps": 200.0,
        "declared_tp_bps": 400.0,
        "declared_hold_seconds": 3600 * 3,  # Up to 3 hours, or until session close
        "warmup_bars": 50,
        "required_features": ["fear_greed_index"],
    }

    def initialize(self, ctx: BarContext) -> None:
        self.entry_hour_start = 7  # UTC 7:00
        self.entry_hour_end = 9   # UTC 9:00 (allowing entry up to this hour)
        self.exit_hour = 12       # UTC 12:00 (target exit by this hour)
        self.session_length_bars = self.exit_hour - self.entry_hour_start + 1 # 7, 8, 9, 10, 11, 12 = 6 bars
        self.ema_period = 20
        self.rsi_period = 14
        self.last_trade_bar = -1
        self.cooldown_bars = 5 # Cooldown after an exit

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
            return 100.0 if avg_gain > 0 else 50.0 # Handle flat market
        rs = avg_gain / avg_loss
        return 100.0 - (100.0 / (1.0 + rs))

    def on_bar(self, ctx: BarContext) -> Optional[Signal]:
        closes = ctx.closes(self.warmup_bars + 1)
        if len(closes) < self.warmup_bars:
            return None

        current_hour = ctx.bar.timestamp.hour
        
        # Cooldown logic
        if ctx.bar_index - self.last_trade_bar < self.cooldown_bars:
            return None

        # Exit logic for existing positions
        if ctx.has_position():
            if ctx.position_direction() == "long" and current_hour >= self.exit_hour:
                return ctx.signal("flat", confidence=0.8, metadata={"reason": "session_end_exit_long", "exit_hour": current_hour})
            elif ctx.position_direction() == "short" and current_hour >= self.exit_hour:
                return ctx.signal("flat", confidence=0.8, metadata={"reason": "session_end_exit_short", "exit_hour": current_hour})
            return None # Hold position if not time to exit

        # Entry logic
        if self.entry_hour_start <= current_hour <= self.entry_hour_end:
            # Check if we already traded this session (simple check using last_trade_bar and current_hour)
            if self.last_trade_bar != -1 and ctx.bar_index - self.last_trade_bar < self.session_length_bars:
                # Assuming 1-hour bars, if last_trade_bar was within this session's window, skip
                # A more robust check would involve checking the date of the last trade
                if ctx.bar.timestamp.date() == ctx.opens(self.cooldown_bars + 1)[0].date():
                    return None

            # Regime filters
            if ctx.market.get("trend_regime", "neutral") != "neutral" and ctx.market.get("trend_regime_confidence", 0.0) > 0.6:
                return None # Do not fade strong trends
            
            if ctx.regime == "crisis" or ctx.market.get("crisis_score", 0.0) > 0.5:
                return None # Do not fade in crisis regimes

            fear_greed = ctx.features.get("fear_greed_index", 50)
            if fear_greed < 30 or fear_greed > 70: # Avoid extreme sentiment
                return None

            # Calculate session move
            session_opens = ctx.opens(self.session_length_bars)
            session_closes = ctx.closes(self.session_length_bars)
            
            if len(session_opens) < current_hour - self.entry_hour_start + 1:
                return None # Not enough bars in session yet

            # Use the open of the first bar of the session as the session_start_price
            session_start_price = session_opens[0]
            current_price = ctx.bar.close

            # Calculate EMA for confirmation
            ema = self._ema(closes, self.ema_period)
            if ema is None:
                return None
            
            # Calculate RSI for overbought/oversold conditions
            rsi = self._rsi(closes, self.rsi_period)
            if rsi is None:
                return None

            # Determine if the session move is "stretched"
            # We define stretched as current price significantly away from session start price
            # and also away from a short-term EMA, coupled with RSI extremes.
            move_from_start_pct = ((current_price - session_start_price) / session_start_price) * 100
            
            # Thresholds for "stretched" (adjust based on domain volatility)
            stretch_threshold_long = 0.5 # 0.5% up from session start
            stretch_threshold_short = -0.5 # 0.5% down from session start
            
            # Fade long if stretched up
            if move_from_start_pct > stretch_threshold_long and current_price > ema and rsi > 65:
                self.last_trade_bar = ctx.bar_index
                return ctx.signal(
                    "short",
                    confidence=0.7,
                    stop_loss_bps=self.declared_sl_bps,
                    take_profit_bps=self.declared_tp_bps,
                    horizon_seconds=self.declared_hold_seconds,
                    metadata={
                        "reason": "euro_morning_fade_stretched_long",
                        "current_hour": current_hour,
                        "session_start_price": session_start_price,
                        "current_price": current_price,
                        "move_from_start_pct": move_from_start_pct,
                        "ema": ema,
                        "rsi": rsi,
                        "fear_greed_index": fear_greed,
                    }
                )
            
            # Fade short if stretched down
            if move_from_start_pct < stretch_threshold_short and current_price < ema and rsi < 35:
                self.last_trade_bar = ctx.bar_index
                return ctx.signal(
                    "long",
                    confidence=0.7,
                    stop_loss_bps=self.declared_sl_bps,
                    take_profit_bps=self.declared_tp_bps,
                    horizon_seconds=self.declared_hold_seconds,
                    metadata={
                        "reason": "euro_morning_fade_stretched_short",
                        "current_hour": current_hour,
                        "session_start_price": session_start_price,
                        "current_price": current_price,
                        "move_from_start_pct": move_from_start_pct,
                        "ema": ema,
                        "rsi": rsi,
                        "fear_greed_index": fear_greed,
                    }
                )
        
        return None

    def on_position_close(self, ctx: BarContext, position: Dict[str, Any]) -> None:
        self.last_trade_bar = ctx.bar_index
        # You could add adaptive logic here, e.g., adjust confidence based on recent performance
        # For simplicity, we just update the last_trade_bar for cooldown.