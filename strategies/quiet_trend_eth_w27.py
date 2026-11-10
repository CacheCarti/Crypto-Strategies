from domains.strategy_contract import Strategy, BarContext, Signal
from typing import Optional, Dict, Any
import math

class CalmTrendFollower(Strategy):
    METADATA = {
        "name": "Calm Trend Follower",
        "domain": "eth_usdc",
        "declared_sl_bps": 250.0,
        "declared_tp_bps": 500.0,
        "declared_hold_seconds": 3600 * 4, # Roughly 4 hours
        "warmup_bars": 100,
        "required_features": [], # No explicit features used, only market context and OHLCV
    }

    def initialize(self, ctx: BarContext) -> None:
        self.volatility_period = 20
        self.trend_period = 50
        self.cooldown_bars = 5 # Cooldown after any trade
        self.last_trade_bar = -self.cooldown_bars

        # Store recent realized volatility for median calculation
        self.recent_volatilities = []
        self.volatility_history_size = 50 # Number of past volatility values to consider for median

    def _atr(self, highs, lows, closes, period=14):
        if len(closes) < period + 1:
            return None
        trs = []
        for i in range(1, len(closes)):
            tr = max(highs[i]-lows[i], abs(highs[i]-closes[i-1]), abs(lows[i]-closes[i-1]))
            trs.append(tr)
        return sum(trs[-period:]) / period

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

    def _median(self, data):
        if not data:
            return None
        sorted_data = sorted(data)
        n = len(sorted_data)
        if n % 2 == 1:
            return sorted_data[n // 2]
        else:
            mid1 = sorted_data[n // 2 - 1]
            mid2 = sorted_data[n // 2]
            return (mid1 + mid2) / 2


    def on_bar(self, ctx: BarContext) -> Optional[Signal]:
        closes = ctx.closes(self.warmup_bars + 1)
        highs = ctx.highs(self.warmup_bars + 1)
        lows = ctx.lows(self.warmup_bars + 1)
        volumes = ctx.volumes(self.warmup_bars + 1)

        if len(closes) < self.warmup_bars:
            return None

        # Cooldown logic
        if ctx.bar_index - self.last_trade_bar < self.cooldown_bars:
            return None

        current_close = closes[-1]
        current_high = highs[-1]
        current_low = lows[-1]

        # Calculate realized volatility (e.g., using ATR or standard deviation of returns)
        # Using standard deviation of log returns as a proxy for realized volatility
        if len(closes) > 1:
            returns = [math.log(closes[i] / closes[i-1]) for i in range(1, len(closes))]
            # Use a shorter period for volatility calculation to be reactive
            current_volatility = self._stddev(returns, self.volatility_period) * math.sqrt(252 * 24) # Annualize for rough comparison
        else:
            current_volatility = None

        if current_volatility is None:
            return None

        self.recent_volatilities.append(current_volatility)
        if len(self.recent_volatilities) > self.volatility_history_size:
            self.recent_volatilities.pop(0)

        # Get median volatility
        median_volatility = self._median(self.recent_volatilities)

        # Check for calm volatility regime
        is_calm_volatility = False
        if median_volatility is not None and current_volatility < median_volatility * 0.8: # 20% below median
            is_calm_volatility = True

        # Trend identification using SMA crossover (simple example)
        # A simple trend filter: price above/below a long-term SMA
        long_sma = self._sma(closes, self.trend_period)

        if long_sma is None:
            return None

        is_bullish_trend = current_close > long_sma
        is_bearish_trend = current_close < long_sma

        # Get market trend regime for additional confirmation
        market_trend = ctx.market.get("trend_regime", "neutral")
        market_trend_confidence = ctx.market.get("trend_regime_confidence", 0.0)

        # Exit logic: if volatility expands or trend changes, exit
        if ctx.has_position():
            if not is_calm_volatility: # Volatility expanded
                self.last_trade_bar = ctx.bar_index
                return ctx.signal("flat", confidence=0.8, metadata={
                    "reason": "volatility_expansion_exit",
                    "current_volatility": current_volatility,
                    "median_volatility": median_volatility,
                    "market_trend": market_trend,
                    "market_trend_confidence": market_trend_confidence
                })

            position_direction = ctx.position_direction()
            if position_direction == "long" and not is_bullish_trend:
                self.last_trade_bar = ctx.bar_index
                return ctx.signal("flat", confidence=0.7, metadata={
                    "reason": "trend_change_exit_long",
                    "current_close": current_close,
                    "long_sma": long_sma,
                    "market_trend": market_trend,
                    "market_trend_confidence": market_trend_confidence
                })
            elif position_direction == "short" and not is_bearish_trend:
                self.last_trade_bar = ctx.bar_index
                return ctx.signal("flat", confidence=0.7, metadata={
                    "reason": "trend_change_exit_short",
                    "current_close": current_close,
                    "long_sma": long_sma,
                    "market_trend": market_trend,
                    "market_trend_confidence": market_trend_confidence
                })
            return None # Hold position if no exit condition met

        # Entry logic
        if is_calm_volatility and market_trend_confidence > 0.6: # Only trade with confident market trend
            if is_bullish_trend and market_trend == "bull":
                self.last_trade_bar = ctx.bar_index
                return ctx.signal("long", confidence=0.75 * market_trend_confidence, metadata={
                    "reason": "calm_bull_trend_entry",
                    "current_close": current_close,
                    "long_sma": long_sma,
                    "current_volatility": current_volatility,
                    "median_volatility": median_volatility,
                    "market_trend": market_trend,
                    "market_trend_confidence": market_trend_confidence
                })
            elif is_bearish_trend and market_trend == "bear":
                self.last_trade_bar = ctx.bar_index
                return ctx.signal("short", confidence=0.75 * market_trend_confidence, metadata={
                    "reason": "calm_bear_trend_entry",
                    "current_close": current_close,
                    "long_sma": long_sma,
                    "current_volatility": current_volatility,
                    "median_volatility": median_volatility,
                    "market_trend": market_trend,
                    "market_trend_confidence": market_trend_confidence
                })

        return None

    def on_position_close(self, ctx: BarContext, position: Dict[str, Any]) -> None:
        # Optionally log or adapt strategy behavior based on position close
        pass