from domains.strategy_contract import Strategy, BarContext, Signal
from typing import Optional, Dict, Any
import math

class FearGreedStabilization(Strategy):
    METADATA = {
        "name": "FearGreedStabilization",
        "domain": "eth_usdc",
        "declared_sl_bps": 250.0,
        "declared_tp_bps": 500.0,
        "declared_hold_seconds": 7200, # 2 hours
        "warmup_bars": 50,
        "required_features": ["fear_greed_index"],
    }

    def initialize(self, ctx: BarContext) -> None:
        self.fear_period = 5 # Number of consecutive bars fear_greed_index must be low
        self.fear_threshold = 30 # Fear & Greed index below this is considered "fear"
        self.stabilization_period = 3 # Bars to check for stabilization
        self.cooldown_bars = 12 # Cooldown after a trade (12 hours for 1h bars)
        self.last_trade_bar = -self.cooldown_bars * 2 # Initialize to allow first trade

    def _sma(self, values, period):
        if len(values) < period:
            return None
        return sum(values[-period:]) / period

    def on_bar(self, ctx: BarContext) -> Optional[Signal]:
        closes = ctx.closes(self.warmup_bars)
        if len(closes) < self.warmup_bars:
            return None

        # Cooldown check
        if ctx.bar_index - self.last_trade_bar < self.cooldown_bars:
            return None

        # Regime filters
        current_regime = ctx.market.get("regime", "NORMAL")
        trend_regime = ctx.market.get("trend_regime", "neutral")

        # Only consider longs in normal or calm regimes, and not in strong bear trends
        if current_regime in ["VOLATILE", "CRISIS", "MELTDOWN"]:
            return None
        if trend_regime == "bear": # Avoid fighting strong downtrends
            return None

        # Fear & Greed Index check (slow state variable)
        # Check if F&G has been low for a sustained period
        fear_greed_index = ctx.features.get("fear_greed_index", 50)
        
        # We need historical F&G values to check for a streak. 
        # Since ctx.features.get only gives current, we assume if it's below threshold now,
        # and has been below for a few days, it's a "depressed" state.
        # This is a simplification due to API limits on historical feature access.
        # In a real system, we'd check a series of past F&G values.
        # For this contract, we'll assume a recent low value implies a depressed state,
        # and rely on the stabilization bar to confirm the turn.
        
        is_depressed_sentiment = fear_greed_index < self.fear_threshold
        
        if not is_depressed_sentiment:
            return None

        # Price stabilization trigger
        # Look for the first bar where price closes above its open after a potential dip
        # and is above a short-term moving average (e.g., 5-period SMA)
        
        # Ensure enough bars for stabilization check and SMA
        if len(closes) < self.stabilization_period + 5:
            return None

        # Check for a "green" bar (close > open)
        current_close = ctx.bar.close
        current_open = ctx.bar.open
        if current_close <= current_open:
            return None # Not a stabilization bar if it's red or flat

        # Check if current close is above a short-term SMA to confirm stabilization
        sma_short = self._sma(closes, 5)
        if sma_short is None or current_close < sma_short:
            return None

        # Check for previous downtrend or consolidation before stabilization
        # We want to buy the first bounce after fear, not just any green bar.
        # Look for the close of the previous bar to be lower than the bar before that.
        # This implies a recent dip or at least not a strong uptrend already.
        if closes[-2] > closes[-3]: # If previous two bars were rising, not a dip
             return None

        # If all conditions met and no position open, signal a long
        if not ctx.has_position():
            self.last_trade_bar = ctx.bar_index
            signal_confidence = 0.6 + (1 - (fear_greed_index / 100.0)) * 0.3 # Higher confidence when more fearful
            return ctx.signal(
                "long",
                confidence=min(1.0, max(0.0, signal_confidence)),
                stop_loss_bps=self.METADATA["declared_sl_bps"],
                take_profit_bps=self.METADATA["declared_tp_bps"],
                horizon_seconds=self.METADATA["declared_hold_seconds"],
                metadata={
                    "reason": "fear_greed_stabilization_long",
                    "fear_greed_index": fear_greed_index,
                    "current_close": current_close,
                    "current_open": current_open,
                    "sma_5": sma_short,
                    "market_regime": current_regime,
                    "trend_regime": trend_regime,
                }
            )
        return None

    def on_position_close(self, ctx: BarContext, position: Dict[str, Any]) -> None:
        # We could use this for adaptive sizing or logging, but for now, just track last trade.
        # self.last_trade_bar is already updated on signal. This is primarily for future adaptive logic.
        pass