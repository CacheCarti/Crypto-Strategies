from domains.strategy_contract import Strategy, BarContext, Signal
from typing import Optional, Dict, Any
import math

class EthOvernightFade(Strategy):
    METADATA = {
        "name": "ETH Overnight Fade",
        "domain": "eth_usdc",
        "declared_sl_bps": 200.0,
        "declared_tp_bps": 400.0,
        "declared_hold_seconds": 3600 * 4, # Max 4 hours hold, aiming for session midpoint
        "warmup_bars": 50, # Need enough for 20-bar SMA and 14-bar RSI
        "required_features": ["fear_greed_index", "funding_rate_ethusdt"],
    }

    def initialize(self, ctx: BarContext) -> None:
        self.overnight_start_hour = 0  # UTC 00:00
        self.overnight_end_hour = 6    # UTC 06:00
        self.fade_midpoint_hour = 3    # UTC 03:00 (midpoint of 0-6)
        self.cooldown_bars = 4         # Cooldown after a trade
        self.last_trade_bar = -self.cooldown_bars # Initialize to allow immediate trading

        self.overnight_high = -math.inf
        self.overnight_low = math.inf
        self.overnight_open = None
        self.session_active = False

        # Parameters for fading logic
        self.rsi_period = 14
        self.price_dev_multiplier = 0.005 # 0.5% deviation from open
        self.min_volume_increase = 1.2 # 20% increase over recent average

    def _sma(self, values, period):
        if len(values) < period:
            return None
        return sum(values[-period:]) / period

    def _stddev(self, values, period):
        if len(values) < period:
            return None
        slice_v = values[-period:]
        mean = sum(slice_v) / period
        variance = sum((x - mean) ** 2 for x in slice_v) / period
        return math.sqrt(variance)

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

        # Calculate initial average gain/loss
        avg_gain = sum(gains[-period:]) / period
        avg_loss = sum(losses[-period:]) / period

        if avg_loss == 0:
            return 100.0 if avg_gain > 0 else 50.0 # Handle division by zero
        rs = avg_gain / avg_loss
        return 100.0 - (100.0 / (1.0 + rs))

    def on_bar(self, ctx: BarContext) -> Optional[Signal]:
        if ctx.bar_index < self.METADATA["warmup_bars"]:
            return None

        closes = ctx.closes(self.rsi_period + 1)
        if len(closes) < self.rsi_period + 1:
            return None

        current_hour = ctx.bar.timestamp.hour
        current_close = ctx.bar.close

        # --- Session Management ---
        if current_hour == self.overnight_start_hour and not self.session_active:
            # New overnight session starts
            self.session_active = True
            self.overnight_open = ctx.bar.open
            self.overnight_high = ctx.bar.high
            self.overnight_low = ctx.bar.low
            self.last_trade_bar = -self.cooldown_bars # Reset cooldown for new session
            return None # Don't trade on the very first bar of the session

        if self.session_active and current_hour > self.overnight_end_hour:
            # End of overnight session, reset for next day
            self.session_active = False
            self.overnight_open = None
            self.overnight_high = -math.inf
            self.overnight_low = math.inf
            return None

        if self.session_active:
            self.overnight_high = max(self.overnight_high, ctx.bar.high)
            self.overnight_low = min(self.overnight_low, ctx.bar.low)

        # --- Exit Logic (if holding a position) ---
        if ctx.has_position():
            # Exit if past session midpoint or near end of session
            if current_hour >= self.overnight_end_hour - 1: # Exit 1 hour before session end
                reason = "session_end_exit"
                if ctx.position_direction() == "long":
                    entry_price = ctx.position["entry_price"]
                    if current_close > entry_price:
                        reason = "session_end_exit_profit"
                    else:
                        reason = "session_end_exit_loss"
                elif ctx.position_direction() == "short":
                    entry_price = ctx.position["entry_price"]
                    if current_close < entry_price:
                        reason = "session_end_exit_profit"
                    else:
                        reason = "session_end_exit_loss"

                self.last_trade_bar = ctx.bar_index
                return ctx.signal("flat", confidence=0.8, metadata={
                    "reason": reason,
                    "current_hour": current_hour,
                    "overnight_end_hour": self.overnight_end_hour,
                    "entry_price": ctx.position["entry_price"],
                    "current_close": current_close
                })
            # No other exit conditions for this strategy, rely on SL/TP

        # --- Entry Logic ---
        if not self.session_active or self.overnight_open is None:
            return None # Only trade during active overnight session

        # Cooldown check
        if ctx.bar_index - self.last_trade_bar < self.cooldown_bars:
            return None

        # Regime Filters
        fear_greed = ctx.features.get("fear_greed_index", 50)
        funding_rate = ctx.features.get("funding_rate_ethusdt", 0.0)
        market_regime = ctx.market.get("regime", "NORMAL")
        trend_regime = ctx.market.get("trend_regime", "neutral")

        # Avoid trading in crisis regimes or strong trends against the fade
        if market_regime in ["CRISIS", "MELTDOWN"]:
            return None
        if (trend_regime == "bull" and funding_rate > 0.0001) or \
           (trend_regime == "bear" and funding_rate < -0.0001):
            # Strong trend with high funding, avoid fading
            return None

        # Calculate indicators
        rsi = self._rsi(closes, self.rsi_period)
        if rsi is None:
            return None

        # Volume analysis
        volumes = ctx.volumes(20)
        if len(volumes) < 20:
            return None
        avg_volume_recent = self._sma(volumes[:-1], 19) # Average of past 19 bars
        if avg_volume_recent is None:
            return None

        current_volume = ctx.bar.volume
        volume_ratio = current_volume / avg_volume_recent if avg_volume_recent > 0 else 1.0

        # Fading logic: look for extreme moves and reversals
        # We are fading the extreme moves away from the overnight open
        # and only entering around the midpoint of the session
        if current_hour >= self.overnight_start_hour + 1 and current_hour <= self.fade_midpoint_hour:
            # Long entry condition: price significantly below overnight open, oversold, and potentially reversing
            if current_close < self.overnight_open * (1 - self.price_dev_multiplier):
                if rsi < 30: # Heavily oversold
                    if volume_ratio > self.min_volume_increase: # Confirm with increased volume
                        # Ensure we are not already in a position
                        if not ctx.has_position():
                            self.last_trade_bar = ctx.bar_index
                            return ctx.signal("long", confidence=0.75, metadata={
                                "reason": "overnight_fade_long_oversold",
                                "current_hour": current_hour,
                                "overnight_open": self.overnight_open,
                                "current_close": current_close,
                                "price_deviation_pct": (self.overnight_open - current_close) / self.overnight_open * 100,
                                "rsi": rsi,
                                "volume_ratio": volume_ratio,
                                "fear_greed": fear_greed,
                                "funding_rate": funding_rate,
                                "market_regime": market_regime
                            })

            # Short entry condition: price significantly above overnight open, overbought, and potentially reversing
            elif current_close > self.overnight_open * (1 + self.price_dev_multiplier):
                if rsi > 70: # Heavily overbought
                    if volume_ratio > self.min_volume_increase: # Confirm with increased volume
                        # Ensure we are not already in a position
                        if not ctx.has_position():
                            self.last_trade_bar = ctx.bar_index
                            return ctx.signal("short", confidence=0.75, metadata={
                                "reason": "overnight_fade_short_overbought",
                                "current_hour": current_hour,
                                "overnight_open": self.overnight_open,
                                "current_close": current_close,
                                "price_deviation_pct": (current_close - self.overnight_open) / self.overnight_open * 100,
                                "rsi": rsi,
                                "volume_ratio": volume_ratio,
                                "fear_greed": fear_greed,
                                "funding_rate": funding_rate,
                                "market_regime": market_regime
                            })
        return None

    def on_position_close(self, ctx: BarContext, position: Dict[str, Any]) -> None:
        # Reset cooldown on position close, regardless of profit/loss
        self.last_trade_bar = ctx.bar_index
        # Could add adaptive logic here, e.g., adjust confidence based on recent performance
        pass