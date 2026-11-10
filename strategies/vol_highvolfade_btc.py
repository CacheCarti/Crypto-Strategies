from domains.strategy_contract import Strategy, BarContext, Signal
from typing import Optional, Dict, Any
import math

class VolHighVolFadeBtc(Strategy):
    METADATA = {
        "name": "Vol High Vol Fade BTC",
        "domain": "btc_usdc",
        "declared_sl_bps": 200.0,
        "declared_tp_bps": 400.0,
        "declared_hold_seconds": 7200, # 2 hours hold
        "warmup_bars": 60, # Need enough bars for 20-period ATR and Z-score
    }

    def initialize(self, ctx: BarContext) -> None:
        self.volatility_period = 20
        self.zscore_period = 20
        self.cooldown_bars = 12 # Wait 12 hours after a trade (12 bars * 1 hour/bar)
        self.last_trade_bar = -self.cooldown_bars

    def _atr(self, highs, lows, closes, period):
        if len(closes) < period + 1:
            return None
        trs = []
        for i in range(1, len(closes)):
            tr = max(highs[i]-lows[i], abs(highs[i]-closes[i-1]), abs(lows[i]-closes[i-1]))
            trs.append(tr)
        return sum(trs[-period:]) / period

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

    def on_bar(self, ctx: BarContext) -> Optional[Signal]:
        closes = ctx.closes(self.warmup_bars)
        highs = ctx.highs(self.warmup_bars)
        lows = ctx.lows(self.warmup_bars)

        if len(closes) < self.warmup_bars:
            return None

        # Cooldown logic
        if ctx.bar_index - self.last_trade_bar < self.cooldown_bars:
            return None

        current_price = ctx.bar.close

        # Calculate ATR for volatility
        atr_val = self._atr(highs, lows, closes, self.volatility_period)
        if atr_val is None:
            return None

        # Calculate Z-score for price deviation
        z_score = self._zscore(closes, self.zscore_period)
        if z_score is None:
            return None

        # Determine if current volatility is in the top quartile
        # We need a longer history of ATR values to determine top quartile
        # For simplicity, we'll use a fixed threshold for "high volatility"
        # In a real system, this would be dynamic based on historical ATR distribution.
        # For this strategy, let's define "high volatility" as ATR > 1.5 * average ATR (or a fixed value)
        # To avoid recomputing average ATR on every bar, we'll use a hardcoded threshold based on typical BTC 1h ATR.
        # A more robust approach would compute rolling average ATR in initialize or a helper.
        # For now, let's assume a "high volatility" threshold.
        # Example threshold: 0.005 (0.5% of price)
        # This needs to be dynamic or a percentage of price, not absolute.
        # Let's use a simple heuristic: if current ATR is significantly higher than a recent average.
        # Or, just use the market regime provided by ctx.market.get("regime").

        # Using market regime as a proxy for volatility quartile
        is_high_volatility = ctx.market.get("regime") in ["HIGH_VOL", "CRISIS"]
        # Alternatively, if we need to implement top quartile strictly from OHLCV:
        # We'd need to store a history of ATRs and calculate the 75th percentile.
        # This is more complex than allowed for bar-by-bar.
        # For this constraint, ctx.market.get("regime") is a valid and simpler proxy.

        signal_direction = None
        confidence = 0.0
        reason = ""

        # Entry logic: Fade extended Z-score moves only in high volatility
        if is_high_volatility:
            # Short signal: Price is significantly overbought (high positive Z-score)
            if z_score > 2.0: # Z-score 2.0 means 2 std dev above mean
                if not ctx.has_position() or ctx.position_direction() == "long":
                    signal_direction = "short"
                    confidence = min(0.8, (z_score - 2.0) / 1.0) # Scale confidence
                    reason = "zscore_overbought_fade_high_vol"
            # Long signal: Price is significantly oversold (high negative Z-score)
            elif z_score < -2.0: # Z-score -2.0 means 2 std dev below mean
                if not ctx.has_position() or ctx.position_direction() == "short":
                    signal_direction = "long"
                    confidence = min(0.8, (-2.0 - z_score) / 1.0) # Scale confidence
                    reason = "zscore_oversold_fade_high_vol"

        # Exit logic (if a position is open)
        if ctx.has_position():
            # If our fading thesis is invalidated (price continues to move against us
            # or Z-score returns to neutral range)
            if ctx.position_direction() == "short" and z_score < 0.5:
                signal_direction = "flat"
                reason = "short_fade_zscore_neutralized"
                confidence = 0.5
            elif ctx.position_direction() == "long" and z_score > -0.5:
                signal_direction = "flat"
                reason = "long_fade_zscore_neutralized"
                confidence = 0.5
            # If market regime changes to calm, exit fades
            elif ctx.market.get("regime") == "CALM":
                signal_direction = "flat"
                reason = "regime_calm_exit"
                confidence = 0.6

        if signal_direction:
            self.last_trade_bar = ctx.bar_index # Reset cooldown
            return ctx.signal(
                signal_direction,
                confidence=confidence,
                metadata={
                    "reason": reason,
                    "current_price": current_price,
                    "z_score": z_score,
                    "atr_val": atr_val,
                    "market_regime": ctx.market.get("regime"),
                }
            )

        return None

    def on_position_close(self, ctx: BarContext, position: Dict[str, Any]) -> None:
        # Reset cooldown on close as well, just in case a position closes not by our signal
        self.last_trade_bar = ctx.bar_index
        # You could add adaptive logic here, e.g., if many losses, reduce confidence
        pass