from domains.strategy_contract import Strategy, BarContext, Signal
from typing import Optional, Dict, Any
import math

class VolatilityStructureBTC(Strategy):
    METADATA = {
        "name": "VolatilityStructureBTC",
        "domain": "btc_usdc",
        "declared_sl_bps": 250.0,
        "declared_tp_bps": 500.0,
        "declared_hold_seconds": 7200, # 2 hours avg hold
        "warmup_bars": 50,
        "required_features": ["fear_greed_index", "btc_return_pct"],
    }

    def initialize(self, ctx: BarContext) -> None:
        self.lookback_period = 20
        self.cooldown_bars = 12 # Wait 12 bars (12 hours) after a trade
        self.last_trade_bar = -self.cooldown_bars
        self.min_vol_diff_pct = 0.05 # Minimum 5% difference between high and low volatility
        self.min_pullback_pct = 0.005 # 0.5% pullback
        self.max_pullback_pct = 0.02 # 2% pullback
        self.prev_swing_high = None
        self.prev_swing_low = None
        self.current_trend = "neutral" # "bullish", "bearish", "neutral"
        self.trend_strength_threshold = 0.01 # 1% move to confirm trend

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

    def _atr(self, highs, lows, closes, period=14):
        if len(closes) < period + 1:
            return None
        trs = []
        for i in range(1, len(closes)):
            tr = max(highs[i]-lows[i], abs(highs[i]-closes[i-1]), abs(lows[i]-closes[i-1]))
            trs.append(tr)
        return sum(trs[-period:]) / period

    def on_bar(self, ctx: BarContext) -> Optional[Signal]:
        closes = ctx.closes(self.lookback_period + 1)
        highs = ctx.highs(self.lookback_period + 1)
        lows = ctx.lows(self.lookback_period + 1)
        volumes = ctx.volumes(self.lookback_period + 1)

        if len(closes) < self.METADATA["warmup_bars"]:
            return None

        current_price = ctx.bar.close
        prev_close = closes[-2]

        # Cooldown logic
        if ctx.bar_index - self.last_trade_bar < self.cooldown_bars:
            return None

        # Market regime filters
        fear_greed = ctx.features.get("fear_greed_index", 50)
        btc_return = ctx.features.get("btc_return_pct", 0.0)
        market_regime = ctx.market.get("regime", "NORMAL")

        # Volatility assessment
        atr = self._atr(highs, lows, closes, period=self.lookback_period)
        if atr is None or atr == 0:
            return None

        # Identify swing points and trend
        high_idx = highs.index(max(highs[-self.lookback_period:]))
        low_idx = lows.index(min(lows[-self.lookback_period:]))

        current_high = highs[-1]
        current_low = lows[-1]

        # Update swing points
        if self.prev_swing_high is None or current_high > self.prev_swing_high:
            self.prev_swing_high = current_high
        if self.prev_swing_low is None or current_low < self.prev_swing_low:
            self.prev_swing_low = current_low

        # Basic trend identification
        if closes[-1] > self._sma(closes, self.lookback_period):
            if closes[-1] > closes[-self.lookback_period] * (1 + self.trend_strength_threshold):
                self.current_trend = "bullish"
            elif closes[-1] < closes[-self.lookback_period] * (1 - self.trend_strength_threshold):
                self.current_trend = "bearish"
            else:
                self.current_trend = "neutral"
        else:
            if closes[-1] < closes[-self.lookback_period] * (1 - self.trend_strength_threshold):
                self.current_trend = "bearish"
            elif closes[-1] > closes[-self.lookback_period] * (1 + self.trend_strength_threshold):
                self.current_trend = "bullish"
            else:
                self.current_trend = "neutral"

        # Long entry conditions: Pullback in an uptrend
        if self.current_trend == "bullish" and not ctx.has_position():
            # Check for higher highs and higher lows
            if current_high > self.prev_swing_high and current_low > self.prev_swing_low:
                # Price has pulled back from a recent high
                pullback_from_high = (self.prev_swing_high - current_price) / self.prev_swing_high
                if self.min_pullback_pct < pullback_from_high < self.max_pullback_pct:
                    # Confirm with a rebound off a recent low or strong support
                    if current_price > prev_close and closes[-1] > closes[-2]: # green candle after pullback
                        self.last_trade_bar = ctx.bar_index
                        self.prev_swing_high = current_high # Reset for next swing
                        self.prev_swing_low = current_low
                        confidence = 0.7 + (100 - fear_greed) / 200.0 # Higher confidence if fear is high
                        return ctx.signal(
                            "long",
                            confidence=min(1.0, confidence),
                            metadata={
                                "reason": "bullish_pullback_reversal",
                                "current_price": current_price,
                                "pullback_pct": f"{pullback_from_high:.4f}",
                                "prev_swing_high": self.prev_swing_high,
                                "prev_swing_low": self.prev_swing_low,
                                "fear_greed": fear_greed,
                                "btc_return_pct": btc_return,
                                "market_regime": market_regime,
                            }
                        )

        # Short entry conditions: Rally in a downtrend
        if self.current_trend == "bearish" and not ctx.has_position():
            # Check for lower highs and lower lows
            if current_high < self.prev_swing_high and current_low < self.prev_swing_low:
                # Price has rallied from a recent low
                rally_from_low = (current_price - self.prev_swing_low) / self.prev_swing_low
                if self.min_pullback_pct < rally_from_low < self.max_pullback_pct:
                    # Confirm with a rejection off a recent high or strong resistance
                    if current_price < prev_close and closes[-1] < closes[-2]: # red candle after rally
                        self.last_trade_bar = ctx.bar_index
                        self.prev_swing_high = current_high # Reset for next swing
                        self.prev_swing_low = current_low
                        confidence = 0.7 + fear_greed / 200.0 # Higher confidence if greed is high
                        return ctx.signal(
                            "short",
                            confidence=min(1.0, confidence),
                            metadata={
                                "reason": "bearish_rally_rejection",
                                "current_price": current_price,
                                "rally_pct": f"{rally_from_low:.4f}",
                                "prev_swing_high": self.prev_swing_high,
                                "prev_swing_low": self.prev_swing_low,
                                "fear_greed": fear_greed,
                                "btc_return_pct": btc_return,
                                "market_regime": market_regime,
                            }
                        )

        return None

    def on_position_close(self, ctx: BarContext, position: Dict[str, Any]) -> None:
        # Reset last_trade_bar to enforce cooldown after any position close
        self.last_trade_bar = ctx.bar_index
        # You could also use the position data for adaptive sizing or strategy adjustments
        # For this strategy, we just ensure the cooldown.