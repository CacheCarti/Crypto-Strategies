from domains.strategy_contract import Strategy, BarContext, Signal
from typing import Optional, Dict, Any
import math

class WickRejectionStrategy(Strategy):
    METADATA = {
        "name": "Wick Rejection SOLUSDC",
        "domain": "sol_usdc",
        "declared_sl_bps": 200.0,
        "declared_tp_bps": 300.0,
        "declared_hold_seconds": 3600,
        "warmup_bars": 50,
        "required_features": ["funding_rate_solusdt", "fear_greed_index"],
    }

    def initialize(self, ctx: BarContext) -> None:
        self.wick_threshold_pct = 0.6  # Wick must be at least 60% of total bar range
        self.volume_multiplier = 1.5   # Volume must be 1.5x average
        self.atr_period = 14
        self.sma_period = 20
        self.cooldown_bars = 5
        self.last_signal_bar = -self.cooldown_bars

    def _atr(self, highs, lows, closes, period):
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

    def on_bar(self, ctx: BarContext) -> Optional[Signal]:
        if ctx.bar_index < self.METADATA["warmup_bars"]:
            return None

        closes = ctx.closes(self.sma_period + 1)
        opens = ctx.opens(self.sma_period + 1)
        highs = ctx.highs(self.atr_period + 1)
        lows = ctx.lows(self.atr_period + 1)
        volumes = ctx.volumes(self.sma_period + 1)

        if len(closes) < self.sma_period + 1 or len(highs) < self.atr_period + 1:
            return None

        # Cooldown logic
        if ctx.bar_index - self.last_signal_bar < self.cooldown_bars:
            return None

        # Market regime filters
        current_regime = ctx.market.get("regime", "NORMAL")
        current_trend = ctx.market.get("trend_regime", "neutral")
        fear_greed = ctx.features.get("fear_greed_index", 50)
        
        # Avoid trading during extreme crisis or very uncertain trends
        if current_regime == "CRISIS" or current_regime == "MELTDOWN":
            return None
        
        # Get current bar data
        current_open = ctx.bar.open
        current_close = ctx.bar.close
        current_high = ctx.bar.high
        current_low = ctx.bar.low
        current_volume = ctx.bar.volume

        # Calculate wick size relative to bar range
        bar_range = current_high - current_low
        if bar_range == 0:
            return None

        upper_wick = current_high - max(current_open, current_close)
        lower_wick = min(current_open, current_close) - current_low

        upper_wick_ratio = upper_wick / bar_range
        lower_wick_ratio = lower_wick / bar_range

        # Volume condition
        avg_volume = self._sma(volumes[:-1], self.sma_period)
        if avg_volume is None or current_volume < avg_volume * self.volume_multiplier:
            return None

        # Check for rejection wick
        signal_direction: Optional[str] = None
        confidence = 0.0
        reason = ""
        metadata = {}

        # Long signal: Large lower wick (rejection of lower prices)
        if lower_wick_ratio >= self.wick_threshold_pct and current_close > current_open:
            # Check for subsequent confirmation
            if ctx.bar_index > 0:
                prev_close = closes[-2]
                if current_close > prev_close: # Current bar closes higher than previous close
                    signal_direction = "long"
                    confidence = 0.7 + (lower_wick_ratio - self.wick_threshold_pct) * 0.5 # Scale confidence by wick size
                    reason = "strong_lower_wick_rejection_confirmed"
                    metadata = {
                        "lower_wick_ratio": lower_wick_ratio,
                        "volume_multiplier": current_volume / avg_volume,
                        "current_regime": current_regime,
                        "trend_regime": current_trend,
                        "fear_greed": fear_greed
                    }

        # Short signal: Large upper wick (rejection of higher prices)
        elif upper_wick_ratio >= self.wick_threshold_pct and current_close < current_open:
            # Check for subsequent confirmation
            if ctx.bar_index > 0:
                prev_close = closes[-2]
                if current_close < prev_close: # Current bar closes lower than previous close
                    signal_direction = "short"
                    confidence = 0.7 + (upper_wick_ratio - self.wick_threshold_pct) * 0.5 # Scale confidence by wick size
                    reason = "strong_upper_wick_rejection_confirmed"
                    metadata = {
                        "upper_wick_ratio": upper_wick_ratio,
                        "volume_multiplier": current_volume / avg_volume,
                        "current_regime": current_regime,
                        "trend_regime": current_trend,
                        "fear_greed": fear_greed
                    }

        # If a position is open, check for exit conditions
        if ctx.has_position():
            position = ctx.position
            entry_price = position["entry_price"]
            direction = position["direction"]
            
            # Simple partial retracement exit (e.g., 50% of the wick)
            # Or exit if price moves against us significantly
            current_atr = self._atr(highs, lows, closes, self.atr_period)
            if current_atr is None:
                return None # Not enough data for ATR

            if direction == "long":
                # Exit if price drops below a certain level relative to the entry bar's low
                # Or if price retraces too much from the wick's high
                # Here, we set a tighter stop than the declared_sl_bps
                stop_loss_level = entry_price - current_atr * 0.8 # Tight stop based on ATR
                
                # Dynamic take profit: 50% retracement of the rejection wick
                # This needs to be calculated based on the wick that triggered the entry.
                # For simplicity, we'll use a fixed TP for now, or rely on declared TP.
                
                if current_close < stop_loss_level:
                    self.last_signal_bar = ctx.bar_index
                    return ctx.signal("flat", confidence=0.8, metadata={
                        "reason": "long_stop_loss_atr_breach",
                        "current_price": current_close,
                        "stop_level": stop_loss_level,
                        "atr": current_atr
                    })
            elif direction == "short":
                # Exit if price rises above a certain level relative to the entry bar's high
                stop_loss_level = entry_price + current_atr * 0.8
                if current_close > stop_loss_level:
                    self.last_signal_bar = ctx.bar_index
                    return ctx.signal("flat", confidence=0.8, metadata={
                        "reason": "short_stop_loss_atr_breach",
                        "current_price": current_close,
                        "stop_level": stop_loss_level,
                        "atr": current_atr
                    })
            # If no exit signal, hold the position
            return None

        # If no position is open and a signal is generated
        if signal_direction:
            self.last_signal_bar = ctx.bar_index
            return ctx.signal(
                signal_direction,
                confidence=min(1.0, confidence), # Cap confidence at 1.0
                stop_loss_bps=self.METADATA["declared_sl_bps"],
                take_profit_bps=self.METADATA["declared_tp_bps"],
                horizon_seconds=self.METADATA["declared_hold_seconds"],
                metadata=metadata
            )

        return None

    def on_position_close(self, ctx: BarContext, position: Dict[str, Any]) -> None:
        # Reset last signal bar to enforce cooldown after any position close
        self.last_signal_bar = ctx.bar_index
        # You could also use this to adapt parameters, e.g., if many losses, reduce volume_multiplier
        # For now, just using it to enforce cooldown.
        pass