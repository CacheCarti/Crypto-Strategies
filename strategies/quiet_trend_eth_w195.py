from domains.strategy_contract import Strategy, BarContext, Signal
from typing import Optional, Dict, Any
import math
import statistics

class CalmTrendStrategy(Strategy):
    METADATA = {
        "name": "CalmTrendStrategy",
        "domain": "eth_usdc",
        "declared_sl_bps": 250.0,
        "declared_tp_bps": 500.0,
        "declared_hold_seconds": 3600, # 1 hour bar, aim for 1-3 bars hold
        "warmup_bars": 60, # Need enough for ATR and SMA calculations
        "required_features": [] # No specific external features used, relying on OHLCV and market context
    }

    def initialize(self, ctx: BarContext) -> None:
        # Indicator periods
        self.vol_period = 30 # For realized volatility
        self.vol_median_period = 10 # For median of volatility
        self.sma_period = 20 # For trend direction
        self.cooldown_bars = 5 # Cooldown period after a trade
        self.last_trade_bar = -self.cooldown_bars # Initialize to allow immediate trading

        # State for adaptive confidence or other adaptive logic
        self.recent_performance = [] # Store return_bps of recent trades
        self.performance_window = 10 # Number of recent trades to consider
        self.winning_streak = 0
        self.losing_streak = 0

    def _atr(self, highs, lows, closes, period=14):
        if len(closes) < period + 1:
            return None
        trs = []
        for i in range(1, len(closes)):
            tr = max(highs[i]-lows[i], abs(highs[i]-closes[i-1]), abs(lows[i]-closes[i-1]))
            trs.append(tr)
        if len(trs) < period: # Ensure enough TRs for the sum
            return None
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

    def _realized_volatility(self, closes, period):
        if len(closes) < period + 1:
            return None
        returns = [(closes[i] - closes[i-1]) / closes[i-1] for i in range(1, len(closes))]
        if len(returns) < period:
            return None
        return self._stddev(returns, period) * math.sqrt(252 * 24) # Annualized hourly vol

    def on_bar(self, ctx: BarContext) -> Optional[Signal]:
        closes = ctx.closes(self.METADATA["warmup_bars"] + self.vol_period + self.vol_median_period)
        highs = ctx.highs(self.METADATA["warmup_bars"] + self.vol_period + self.vol_median_period)
        lows = ctx.lows(self.METADATA["warmup_bars"] + self.vol_period + self.vol_median_period)

        if len(closes) < self.METADATA["warmup_bars"]:
            return None

        # Cooldown logic
        if ctx.bar_index - self.last_trade_bar < self.cooldown_bars:
            return None

        # Calculate realized volatility
        current_vol = self._realized_volatility(closes, self.vol_period)
        if current_vol is None:
            return None

        # Calculate historical volatilities for median
        historical_volatilities = []
        for i in range(self.vol_period, len(closes) - 1):
            vol = self._realized_volatility(closes[:i+1], self.vol_period)
            if vol is not None:
                historical_volatilities.append(vol)

        if len(historical_volatilities) < self.vol_median_period:
            return None

        # Median of recent volatilities
        recent_vols = historical_volatilities[-self.vol_median_period:]
        median_vol = statistics.median(recent_vols)

        # Trend calculation
        sma = self._sma(closes, self.sma_period)
        if sma is None:
            return None

        # Current price relative to SMA
        price_above_sma = ctx.bar.close > sma
        price_below_sma = ctx.bar.close < sma

        # Volatility condition: current vol must be below median vol
        is_calm_volatility = current_vol < median_vol

        # Trend persistence check (look for consistent direction relative to SMA)
        # Check if the market has been consistently above/below SMA for a few bars
        sma_cross_count = 0
        for i in range(max(0, len(closes) - self.sma_period), len(closes) - 1):
            prev_sma = self._sma(closes[:i], self.sma_period)
            if prev_sma is None: continue
            if (closes[i-1] < prev_sma and closes[i] > sma) or \
               (closes[i-1] > prev_sma and closes[i] < sma):
                sma_cross_count += 1
        
        is_persistent_trend = sma_cross_count <= 2 # Allow for minor wiggles, but not too many crosses

        # Confidence scaling based on recent performance
        base_confidence = 0.6
        if len(self.recent_performance) > 0:
            avg_return = sum(self.recent_performance) / len(self.recent_performance)
            if avg_return > 0:
                base_confidence += min(0.3, avg_return / 1000.0) # Max 0.3 boost for good performance
            else:
                base_confidence = max(0.3, base_confidence + max(-0.3, avg_return / 1000.0)) # Max 0.3 reduction for bad performance
        
        # Ensure confidence is within 0.0-1.0
        confidence = max(0.1, min(1.0, base_confidence))

        # Exit condition: volatility expands (current vol crosses above median vol)
        if ctx.has_position():
            # Check if volatility has expanded significantly
            if current_vol > median_vol * 1.05: # 5% buffer for expansion
                self.last_trade_bar = ctx.bar_index
                return ctx.signal(
                    "flat",
                    confidence=0.8,
                    metadata={
                        "reason": "volatility_expansion_exit",
                        "current_vol": current_vol,
                        "median_vol": median_vol,
                        "pos_direction": ctx.position_direction(),
                        "entry_price": ctx.position["entry_price"]
                    }
                )

        # Entry logic
        if not ctx.has_position() and is_calm_volatility and is_persistent_trend:
            if price_above_sma:
                self.last_trade_bar = ctx.bar_index
                return ctx.signal(
                    "long",
                    confidence=confidence,
                    metadata={
                        "reason": "calm_bull_trend_entry",
                        "current_vol": current_vol,
                        "median_vol": median_vol,
                        "price": ctx.bar.close,
                        "sma": sma,
                        "vol_period": self.vol_period,
                        "sma_period": self.sma_period,
                        "confidence_scaled": confidence
                    }
                )
            elif price_below_sma:
                self.last_trade_bar = ctx.bar_index
                return ctx.signal(
                    "short",
                    confidence=confidence,
                    metadata={
                        "reason": "calm_bear_trend_entry",
                        "current_vol": current_vol,
                        "median_vol": median_vol,
                        "price": ctx.bar.close,
                        "sma": sma,
                        "vol_period": self.vol_period,
                        "sma_period": self.sma_period,
                        "confidence_scaled": confidence
                    }
                )
        return None

    def on_position_close(self, ctx: BarContext, position: Dict[str, Any]) -> None:
        # Update recent performance for adaptive confidence
        self.recent_performance.append(position["return_bps"])
        if len(self.recent_performance) > self.performance_window:
            self.recent_performance.pop(0)

        if position["return_bps"] > 0:
            self.winning_streak += 1
            self.losing_streak = 0
        else:
            self.losing_streak += 1
            self.winning_streak = 0

        # Optional: Adjust cooldown based on streak
        # if self.losing_streak >= 3:
        #     self.cooldown_bars = 10
        # elif self.winning_streak >= 3:
        #     self.cooldown_bars = 3
        # else:
        #     self.cooldown_bars = 5