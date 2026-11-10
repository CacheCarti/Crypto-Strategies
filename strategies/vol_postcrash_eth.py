from domains.strategy_contract import Strategy, BarContext, Signal
from typing import Optional, Dict, Any
import math

class VolatilityRecoveryDrift(Strategy):
    METADATA = {
        "name": "Volatility Recovery Drift ETH",
        "domain": "eth_usdc",
        "declared_sl_bps": 200.0,
        "declared_tp_bps": 400.0,
        "declared_hold_seconds": 7200,  # 2 hours average hold
        "warmup_bars": 50,
        "required_features": [], # No specific features needed, using OHLCV and regime
    }

    def initialize(self, ctx: BarContext) -> None:
        self.volatility_period = 20
        self.stabilization_period = 3
        self.recovery_sma_period = 5
        self.cooldown_bars = 12 # Cooldown for 12 bars (12 hours) after a trade
        self.last_trade_bar = -self.cooldown_bars

    def _stddev(self, values, period):
        if len(values) < period:
            return None
        slice_v = values[-period:]
        mean = sum(slice_v) / period
        variance = sum((x - mean) ** 2 for x in slice_v) / period
        return math.sqrt(variance)

    def _sma(self, values, period):
        if len(values) < period:
            return None
        return sum(values[-period:]) / period

    def on_bar(self, ctx: BarContext) -> Optional[Signal]:
        closes = ctx.closes(self.volatility_period + self.stabilization_period + self.recovery_sma_period)
        highs = ctx.highs(self.volatility_period + self.stabilization_period + self.recovery_sma_period)
        lows = ctx.lows(self.volatility_period + self.stabilization_period + self.recovery_sma_period)

        if len(closes) < self.METADATA["warmup_bars"]:
            return None

        # Cooldown logic
        if ctx.bar_index < self.last_trade_bar + self.cooldown_bars:
            return None

        current_close = ctx.bar.close

        # Volatility check (using standard deviation of returns)
        returns = [math.log(closes[i] / closes[i-1]) for i in range(1, len(closes))]
        volatility = self._stddev(returns, self.volatility_period)
        
        # We need enough returns for volatility calculation
        if volatility is None or len(returns) < self.volatility_period:
            return None

        # Determine if there was a sharp flush (significant drop followed by potential bounce)
        # Look for a sharp drop in the recent past that is higher than average volatility
        # Use a relative drop, e.g., 2-3x average volatility
        recent_drop_threshold_multiplier = 2.5
        recent_drop_period = 5 # Look back 5 bars for a significant drop
        
        # Check for a sharp drop in the last `recent_drop_period` bars
        # A flush event: current close is significantly lower than a high in the last `recent_drop_period`
        # and the market has moved down quickly.
        
        # This is a bit tricky to define precisely without over-complicating.
        # Let's simplify: A sharp drop is when the price dropped by more than X standard deviations of daily returns
        # within a short period.
        
        # For simplicity, let's just check if the current close is much lower than the max high in the last few bars
        # and the range of the current bar is large.
        
        max_high_recent = max(highs[-recent_drop_period:])
        min_low_recent = min(lows[-recent_drop_period:])
        
        # Condition 1: Sharp flush. Price dropped significantly from a recent high.
        # This is a proxy for a "panic event" or "flush"
        # We define a flush as a drop of at least 2x the average true range within the last few bars
        
        # Calculate ATR for dynamic threshold
        atr_period = 14
        if len(highs) < atr_period + 1:
            return None
        
        trs = []
        for i in range(1, len(closes)):
            tr = max(highs[i]-lows[i], abs(highs[i]-closes[i-1]), abs(lows[i]-closes[i-1]))
            trs.append(tr)
        
        if len(trs) < atr_period:
            return None
            
        current_atr = sum(trs[-atr_period:]) / atr_period
        
        # A "flush" event: current price is significantly below a recent high (e.g., 2 * ATR)
        # AND the range of the last bar is large (to signify a sharp move)
        flush_occurred = False
        if current_close < max_high_recent - (2.0 * current_atr) and (ctx.bar.high - ctx.bar.low) > (1.5 * current_atr):
            flush_occurred = True

        if not flush_occurred:
            return None

        # Condition 2: Stabilization after the flush
        # Check if the price has stopped making lower lows for a few bars
        # and if volatility has started to decrease (or at least not increase further)
        
        # Look at the last `stabilization_period` bars (excluding the current bar if it's still flushing)
        # For stabilization, we want the lows to not be significantly lower than previous lows
        # and closes to be relatively flat or slightly higher.
        
        stabilization_lows = lows[-(self.stabilization_period + 1):-1] # Exclude current bar's low
        stabilization_closes = closes[-(self.stabilization_period + 1):-1] # Exclude current bar's close
        
        if len(stabilization_lows) < self.stabilization_period:
            return None

        # Check if the recent lows are not making new significant lows
        # This is a simplified check: the current low should not be the absolute lowest in the stabilization period
        if ctx.bar.low == min(lows[-self.stabilization_period:]): # Current bar is the lowest, not stabilized
            return None

        # Check if price is starting to recover (e.g., current close > average of last few stabilization closes)
        # This combines stabilization with the start of recovery drift
        
        # Recovery drift: current price above a short-term SMA of stabilization closes
        recovery_sma = self._sma(closes[-self.stabilization_period-1:-1], self.recovery_sma_period)
        
        if recovery_sma is None:
            return None

        # Entry condition:
        # 1. A flush event has occurred
        # 2. Stabilization is evident (not making new lows, or even better, finding support)
        # 3. Price is now closing above a short-term moving average of the stabilization period, indicating recovery drift
        # 4. Not currently in a position
        # 5. Cooldown period respected
        
        if not ctx.has_position():
            if flush_occurred and current_close > recovery_sma:
                # Calculate confidence based on how strong the recovery is relative to the flush
                # For simplicity, let's use a fixed confidence for now
                confidence = 0.75

                self.last_trade_bar = ctx.bar_index
                return ctx.signal(
                    "long",
                    confidence=confidence,
                    metadata={
                        "reason": "volatility_recovery_drift",
                        "current_close": current_close,
                        "recovery_sma": recovery_sma,
                        "max_high_recent": max_high_recent,
                        "current_atr": current_atr,
                        "regime": ctx.regime,
                    }
                )

        return None

    def on_position_close(self, ctx: BarContext, position: Dict[str, Any]) -> None:
        # Use this for adaptive sizing or logging, if needed.
        # For this strategy, we just ensure cooldown is reset.
        pass