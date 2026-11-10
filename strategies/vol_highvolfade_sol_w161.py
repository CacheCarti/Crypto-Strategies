from domains.strategy_contract import Strategy, BarContext, Signal
from typing import Optional, Dict, Any
import math
import collections

class VolatilityFadeZScore(Strategy):
    METADATA = {
        "name": "Volatility Fade Z-Score on SOL",
        "domain": "sol_usdc",
        "declared_sl_bps": 200.0,
        "declared_tp_bps": 400.0,
        "declared_hold_seconds": 3600, # 1 hour bar, targeting 1 bar hold
        "warmup_bars": 60, # Need enough bars for 20-period stddev and Z-score
        "required_features": [], # No external features needed for this one
    }

    def initialize(self, ctx: BarContext) -> None:
        self.zscore_period = 20
        self.volatility_period = 20
        self.zscore_threshold = 2.5 # Fade moves > 2.5 std devs from mean
        self.min_volatility_quantile = 0.75 # Only fade in top 25% volatility
        self.cooldown_bars = 5 # Cooldown after a trade
        self.last_signal_bar = -self.cooldown_bars

        # Store recent volatilities to determine quantile
        self.recent_volatilities = collections.deque(maxlen=self.volatility_period * 4) # Keep enough for a few volatility periods

    def _stddev(self, values, period):
        if len(values) < period:
            return None
        slice_v = values[-period:]
        mean = sum(slice_v) / period
        variance = sum((x - mean) ** 2 for x in slice_v) / period
        return math.sqrt(variance)

    def _zscore(self, values, period):
        if len(values) < period:
            return None
        mean = sum(values[-period:]) / period
        std = self._stddev(values, period)
        if std is None or std == 0:
            return 0.0
        return (values[-1] - mean) / std

    def _atr(self, highs, lows, closes, period=14):
        if len(closes) < period + 1:
            return None
        trs = []
        for i in range(1, len(closes)):
            tr = max(highs[i]-lows[i], abs(highs[i]-closes[i-1]), abs(lows[i]-closes[i-1]))
            trs.append(tr)
        if not trs:
            return None
        return sum(trs[-period:]) / period

    def on_bar(self, ctx: BarContext) -> Optional[Signal]:
        closes = ctx.closes(self.zscore_period)
        highs = ctx.highs(self.volatility_period + 1) # Need one extra for ATR calculation
        lows = ctx.lows(self.volatility_period + 1)

        if len(closes) < self.warmup_bars or len(highs) < self.warmup_bars or len(lows) < self.warmup_bars:
            return None

        # Cooldown logic
        if ctx.bar_index - self.last_signal_bar < self.cooldown_bars:
            return None

        current_price = ctx.bar.close

        # Calculate Z-score of price relative to its recent mean
        z_score = self._zscore(closes, self.zscore_period)
        if z_score is None:
            return None

        # Calculate current volatility using ATR
        current_atr = self._atr(highs, lows, closes, self.volatility_period)
        if current_atr is None:
            return None

        self.recent_volatilities.append(current_atr)

        # Ensure we have enough historical volatility data to determine quantile
        if len(self.recent_volatilities) < self.volatility_period:
            return None

        # Determine volatility threshold for top quantile
        sorted_volatilities = sorted(list(self.recent_volatilities))
        quantile_idx = int(len(sorted_volatilities) * self.min_volatility_quantile)
        min_volatility_for_trade = sorted_volatilities[quantile_idx]

        # Check for market regime (avoid fading in strong trends)
        trend_regime = ctx.market.get("trend_regime", "neutral")
        trend_confidence = ctx.market.get("trend_regime_confidence", 0.0)

        # Entry conditions for fading extended moves in high volatility
        # We only fade if volatility is high enough AND not in a strong trend
        if current_atr >= min_volatility_for_trade and trend_confidence < 0.6: # Filter out strong trends
            if z_score > self.zscore_threshold:  # Price is significantly above mean, short it (fade the rally)
                if not ctx.has_position() or ctx.position_direction() == "long":
                    self.last_signal_bar = ctx.bar_index
                    return ctx.signal(
                        "short",
                        confidence=min(0.7, (z_score - self.zscore_threshold) * 0.2), # Scale confidence by how extreme the move is
                        stop_loss_bps=self.METADATA["declared_sl_bps"],
                        take_profit_bps=self.METADATA["declared_tp_bps"],
                        horizon_seconds=self.METADATA["declared_hold_seconds"],
                        metadata={
                            "reason": "zscore_fade_high_volatility_short",
                            "z_score": z_score,
                            "current_atr": current_atr,
                            "min_vol_for_trade": min_volatility_for_trade,
                            "price": current_price,
                            "trend_regime": trend_regime,
                            "trend_confidence": trend_confidence,
                        }
                    )
            elif z_score < -self.zscore_threshold:  # Price is significantly below mean, long it (fade the dip)
                if not ctx.has_position() or ctx.position_direction() == "short":
                    self.last_signal_bar = ctx.bar_index
                    return ctx.signal(
                        "long",
                        confidence=min(0.7, (-z_score - self.zscore_threshold) * 0.2), # Scale confidence
                        stop_loss_bps=self.METADATA["declared_sl_bps"],
                        take_profit_bps=self.METADATA["declared_tp_bps"],
                        horizon_seconds=self.METADATA["declared_hold_seconds"],
                        metadata={
                            "reason": "zscore_fade_high_volatility_long",
                            "z_score": z_score,
                            "current_atr": current_atr,
                            "min_vol_for_trade": min_volatility_for_trade,
                            "price": current_price,
                            "trend_regime": trend_regime,
                            "trend_confidence": trend_confidence,
                        }
                    )

        # If we have an open position, just hold it. No exit logic here, let SL/TP or horizon handle it.
        if ctx.has_position():
            return ctx.signal("hold", confidence=0.0)

        return None

    def on_position_close(self, ctx: BarContext, position: Dict[str, Any]) -> None:
        # Reset last_signal_bar to enforce cooldown after any position close
        self.last_signal_bar = ctx.bar_index
        # Log for analysis, could adapt parameters here
        # print(f"Position closed: {position['direction']} at {position['exit_price']} (entry: {position['entry_price']}) "
        #       f"Return: {position['return_bps']} bps, Held: {position['bars_held']} bars. Reason: {position.get('exit_reason', 'unknown')}")
        pass