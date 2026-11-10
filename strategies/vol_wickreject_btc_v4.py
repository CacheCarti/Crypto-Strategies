from domains.strategy_contract import Strategy, BarContext, Signal
from typing import Optional, Dict, Any
import math

class VolatilityWickReject(Strategy):
    METADATA = {
        "name": "Volatility Wick Rejection",
        "domain": "btc_usdc",
        "declared_sl_bps": 200.0,
        "declared_tp_bps": 400.0,
        "declared_hold_seconds": 3600 * 4,  # Aim for 4-hour hold
        "warmup_bars": 50,
        "required_features": [], # No specific features needed for this OHLCV-based strategy
    }

    def initialize(self, ctx: BarContext) -> None:
        self.wick_threshold_ratio = 0.6  # Wick must be at least 60% of total bar range
        self.volume_ratio_threshold = 1.5 # Volume must be 1.5x average
        self.cooldown_bars = 12 # Cooldown after a trade (12 hours)
        self.last_trade_bar = -self.cooldown_bars

        # Periods for average true range and volume
        self.atr_period = 20
        self.volume_period = 20

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
        closes = ctx.closes(self.warmup_bars + 1)
        opens = ctx.opens(self.warmup_bars + 1)
        highs = ctx.highs(self.warmup_bars + 1)
        lows = ctx.lows(self.warmup_bars + 1)
        volumes = ctx.volumes(self.warmup_bars + 1)

        if len(closes) < self.warmup_bars + 1:
            return None

        # Cooldown logic
        if ctx.bar_index - self.last_trade_bar < self.cooldown_bars:
            return None

        current_bar_open = opens[-1]
        current_bar_close = closes[-1]
        current_bar_high = highs[-1]
        current_bar_low = lows[-1]
        current_bar_volume = volumes[-1]

        prev_bar_open = opens[-2]
        prev_bar_close = closes[-2]
        prev_bar_high = highs[-2]
        prev_bar_low = lows[-2]
        prev_bar_volume = volumes[-2]

        # Calculate average volume
        avg_volume = self._sma(volumes[:-1], self.volume_period)
        if avg_volume is None or current_bar_volume < avg_volume * self.volume_ratio_threshold:
            return None

        # Calculate average true range for volatility context
        avg_atr = self._atr(highs[:-1], lows[:-1], closes[:-1], self.atr_period)
        if avg_atr is None:
            return None

        # Regime filter: only trade in 'normal' or 'volatile' regimes
        # Avoid 'crisis' or 'calm' for this type of event-driven strategy
        regime = ctx.market.get("regime", "NORMAL")
        if regime not in ["NORMAL", "HIGH_VOL"]:
            return None

        signal_reason = None
        signal_direction = None
        confidence = 0.0

        # Bearish Rejection (Upper Wick)
        # Previous bar has a large upper wick and high volume
        # Current bar confirms the rejection by closing lower
        prev_bar_range = prev_bar_high - prev_bar_low
        if prev_bar_range > 0: # Avoid division by zero
            upper_wick = prev_bar_high - max(prev_bar_open, prev_bar_close)
            if upper_wick / prev_bar_range >= self.wick_threshold_ratio and prev_bar_close < prev_bar_open:
                # Confirmation: current bar closes lower than previous bar's close
                if current_bar_close < prev_bar_close:
                    signal_direction = "short"
                    signal_reason = "bearish_wick_rejection_confirmed"
                    # Confidence scales with how much lower the current bar closes
                    confidence = min(0.9, (prev_bar_close - current_bar_close) / avg_atr)


        # Bullish Rejection (Lower Wick)
        # Previous bar has a large lower wick and high volume
        # Current bar confirms the rejection by closing higher
        if prev_bar_range > 0: # Avoid division by zero
            lower_wick = min(prev_bar_open, prev_bar_close) - prev_bar_low
            if lower_wick / prev_bar_range >= self.wick_threshold_ratio and prev_bar_close > prev_bar_open:
                # Confirmation: current bar closes higher than previous bar's close
                if current_bar_close > prev_bar_close:
                    signal_direction = "long"
                    signal_reason = "bullish_wick_rejection_confirmed"
                    # Confidence scales with how much higher the current bar closes
                    confidence = min(0.9, (current_bar_close - prev_bar_close) / avg_atr)


        if signal_direction and confidence > 0.4: # Only act on higher confidence signals
            self.last_trade_bar = ctx.bar_index
            return ctx.signal(
                signal_direction,
                confidence=confidence,
                metadata={
                    "reason": signal_reason,
                    "prev_bar_high": prev_bar_high,
                    "prev_bar_low": prev_bar_low,
                    "prev_bar_close": prev_bar_close,
                    "prev_bar_volume": prev_bar_volume,
                    "current_bar_close": current_bar_close,
                    "current_bar_volume": current_bar_volume,
                    "avg_volume": avg_volume,
                    "avg_atr": avg_atr,
                    "market_regime": regime,
                }
            )

        return None

    def on_position_close(self, ctx: BarContext, position: Dict[str, Any]) -> None:
        # Optional: Log position close details or adjust parameters
        pass