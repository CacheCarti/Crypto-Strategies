from domains.strategy_contract import Strategy, BarContext, Signal
from typing import Optional, Dict, Any
import math

class VolatilityRecoveryDrift(Strategy):
    METADATA = {
        "name": "Volatility Recovery Drift",
        "domain": "eth_usdc",
        "declared_sl_bps": 250.0,
        "declared_tp_bps": 500.0,
        "declared_hold_seconds": 7200, # 2 hours
        "warmup_bars": 60, # Need enough bars for ATR and EMAs
        "required_features": [], # No specific features required for this one
    }

    def initialize(self, ctx: BarContext) -> None:
        self.atr_period = 20
        self.ema_short_period = 8
        self.ema_long_period = 21
        self.volatility_spike_threshold_multiplier = 2.0 # ATR increase factor
        self.stabilization_bars = 4 # Number of bars for price stabilization
        self.cooldown_bars = 12 # Cooldown period after a trade
        self.last_trade_bar = -self.cooldown_bars - 1 # Initialize to allow immediate trade

    def _atr(self, highs, lows, closes, period=14):
        if len(closes) < period + 1:
            return None
        trs = []
        for i in range(1, len(closes)):
            tr = max(highs[i]-lows[i], abs(highs[i]-closes[i-1]), abs(lows[i]-closes[i-1]))
            trs.append(tr)
        return sum(trs[-period:]) / period

    def _ema(self, values, period):
        if len(values) < period:
            return None
        k = 2.0 / (period + 1)
        ema = sum(values[:period]) / period
        for v in values[period:]:
            ema = v * k + ema * (1 - k)
        return ema

    def on_bar(self, ctx: BarContext) -> Optional[Signal]:
        closes = ctx.closes(self.warmup_bars)
        highs = ctx.highs(self.warmup_bars)
        lows = ctx.lows(self.warmup_bars)

        if len(closes) < self.warmup_bars:
            return None

        # Cooldown check
        if ctx.bar_index - self.last_trade_bar < self.cooldown_bars:
            return None

        current_atr = self._atr(highs, lows, closes, self.atr_period)
        if current_atr is None:
            return None

        # Condition 1: Volatility Spike (sharp flush)
        # Check if current ATR is significantly higher than average ATR over a longer period
        # This implies a recent sharp move or flush
        previous_atrs = [self._atr(highs[:i+1], lows[:i+1], closes[:i+1], self.atr_period) for i in range(self.atr_period, len(closes))]
        previous_atrs = [a for a in previous_atrs if a is not None]

        if len(previous_atrs) < self.atr_period: # Need enough past ATRs to compare
            return None

        avg_past_atr = sum(previous_atrs[:-1]) / len(previous_atrs[:-1]) if previous_atrs[:-1] else 0.0

        volatility_spike = False
        if avg_past_atr > 0 and current_atr > avg_past_atr * self.volatility_spike_threshold_multiplier:
            # Also check if the current bar is a significant down move
            if ctx.bar.close < ctx.bar.open and (ctx.bar.open - ctx.bar.close) > current_atr * 0.5: # Significant down move
                volatility_spike = True

        if not volatility_spike:
            return None # Wait for a volatility event

        # Condition 2: Stabilization after the spike
        # We need to ensure that after the spike, price has stabilized for `stabilization_bars`
        # This means no new extreme low, and price not continuing to fall sharply.
        # Check if the lowest low in the last `stabilization_bars` (excluding the spike bar) is not too far from current close
        # And if the price has not continued to make lower lows.
        if len(lows) < self.stabilization_bars + 1:
            return None

        # The spike bar is ctx.bar_index - 1 (the last bar before the current one where we evaluate stabilization)
        # We look at the bars *after* the spike bar for stabilization.
        # For simplicity, let's say the spike event was the previous bar.
        # We need to ensure that for the last `stabilization_bars` bars (including current),
        # the price is not still actively dropping.
        recent_lows = lows[-self.stabilization_bars:]
        recent_closes = closes[-self.stabilization_bars:]
        
        # Check if price has not made new significant lows and is attempting to recover
        stabilized = True
        for i in range(1, len(recent_closes)):
            if recent_closes[i] < recent_closes[i-1] - (current_atr * 0.2): # Price shouldn't be actively collapsing
                stabilized = False
                break
        
        if not stabilized or ctx.bar.close < min(recent_lows) + (current_atr * 0.1): # Ensure price is off the absolute lows
            return None

        # Condition 3: Price attempts to recover (EMA crossover or close above short EMA)
        ema_short = self._ema(closes, self.ema_short_period)
        ema_long = self._ema(closes, self.ema_long_period)

        if ema_short is None or ema_long is None:
            return None

        # Entry signal: Price is recovering, potentially crossing above short EMA after stabilization
        # Or short EMA attempting to cross above long EMA (early stage)
        if ctx.bar.close > ema_short and ema_short > ema_long:
            if not ctx.has_position():
                self.last_trade_bar = ctx.bar_index
                return ctx.signal(
                    "long",
                    confidence=0.75,
                    metadata={
                        "reason": "volatility_recovery_drift_long",
                        "current_atr": current_atr,
                        "avg_past_atr": avg_past_atr,
                        "ema_short": ema_short,
                        "ema_long": ema_long,
                        "close": ctx.bar.close,
                        "stabilization_bars": self.stabilization_bars,
                        "cooldown_bars": self.cooldown_bars,
                    }
                )

        # Exit logic: if we have a position and conditions deteriorate, or target hit (handled by engine)
        # We'll rely on the engine's SL/TP and trailing stop to manage exits.
        # However, if we enter a strong bearish regime, we might want to flat.
        if ctx.has_position():
            if ctx.market.get("trend_regime", "neutral") == "bear" and ctx.market.get("trend_regime_confidence", 0.0) > 0.6:
                self.last_trade_bar = ctx.bar_index
                return ctx.signal(
                    "flat",
                    confidence=0.8,
                    metadata={
                        "reason": "bearish_regime_exit",
                        "trend_regime": ctx.market.get("trend_regime"),
                        "trend_confidence": ctx.market.get("trend_regime_confidence"),
                        "close": ctx.bar.close,
                    }
                )
        return None

    def on_position_close(self, ctx: BarContext, position: Dict[str, Any]) -> None:
        # Reset cooldown on position close, ensuring we don't trade immediately after an exit
        self.last_trade_bar = ctx.bar_index
        # Optional: adaptive logic based on position outcome
        # For instance, if return_bps is very negative, increase cooldown or reduce confidence
        # if position["return_bps"] < -self.METADATA["declared_sl_bps"] * 0.8: # If stopped out badly
        #     self.cooldown_bars = min(self.cooldown_bars + 5, 24) # Increase cooldown
        # elif position["return_bps"] > self.METADATA["declared_tp_bps"] * 0.8: # If took profit well
        #     self.cooldown_bars = max(self.cooldown_bars - 2, 12) # Decrease cooldown
        pass