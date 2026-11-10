from domains.strategy_contract import Strategy, BarContext, Signal
from typing import Optional, Dict, Any
import math

class VolatilityClimaxFade(Strategy):
    METADATA = {
        "name": "VolatilityClimaxFadeBTC",
        "domain": "btc_usdc",
        "declared_sl_bps": 200.0,
        "declared_tp_bps": 400.0,
        "declared_hold_seconds": 3600 * 4,  # Intended to hold for a few hours
        "warmup_bars": 50,
        "required_features": [], # No external features used
    }

    def initialize(self, ctx: BarContext) -> None:
        self.vol_period = 20
        self.climax_volume_mult = 2.0  # Volume must be X times avg
        self.climax_range_mult = 1.5   # Range must be X times avg
        self.cooldown_bars = 12        # Cooldown after a trade
        self.last_signal_bar = -self.cooldown_bars

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

    def on_bar(self, ctx: BarContext) -> Optional[Signal]:
        closes = ctx.closes(self.vol_period * 2) # Need more history for avg vol/range
        opens = ctx.opens(self.vol_period * 2)
        highs = ctx.highs(self.vol_period * 2)
        lows = ctx.lows(self.vol_period * 2)
        volumes = ctx.volumes(self.vol_period * 2)

        if len(closes) < self.vol_period * 2:
            return None

        current_bar = ctx.bar
        current_volume = current_bar.volume
        current_range = current_bar.high - current_bar.low

        # Cooldown check
        if ctx.bar_index < self.last_signal_bar + self.cooldown_bars:
            return None

        # Calculate average volume and range
        avg_volume_recent = self._sma(volumes[-self.vol_period:], self.vol_period)
        avg_range_recent = self._sma([h - l for h, l in zip(highs[-self.vol_period:], lows[-self.vol_period:])], self.vol_period)

        if avg_volume_recent is None or avg_range_recent is None:
            return None

        # Volatility Climax Detection (Bar N-1)
        prev_bar_volume = volumes[-2]
        prev_bar_range = highs[-2] - lows[-2]
        prev_bar_close = closes[-2]
        prev_bar_open = opens[-2]

        is_volume_climax = prev_bar_volume > avg_volume_recent * self.climax_volume_mult
        is_range_climax = prev_bar_range > avg_range_recent * self.climax_range_mult

        if is_volume_climax and is_range_climax:
            # Check for failure to continue on current bar (Bar N)
            # Fade long if previous bar was a strong up move and current bar fails to continue
            if prev_bar_close > prev_bar_open: # Previous bar was bullish
                # Current bar is lower close than previous close, indicating rejection
                if current_bar.close < prev_bar_close:
                    self.last_signal_bar = ctx.bar_index
                    return ctx.signal(
                        "short",
                        confidence=0.7,
                        stop_loss_bps=self.METADATA["declared_sl_bps"],
                        take_profit_bps=self.METADATA["declared_tp_bps"],
                        horizon_seconds=self.METADATA["declared_hold_seconds"],
                        metadata={
                            "reason": "vol_range_climax_fade_bull_rejection",
                            "prev_vol_mult": prev_bar_volume / avg_volume_recent,
                            "prev_range_mult": prev_bar_range / avg_range_recent,
                            "prev_close": prev_bar_close,
                            "current_close": current_bar.close,
                            "regime": ctx.market.get("regime", "NORMAL")
                        }
                    )
            # Fade short if previous bar was a strong down move and current bar fails to continue
            elif prev_bar_close < prev_bar_open: # Previous bar was bearish
                # Current bar is higher close than previous close, indicating rejection
                if current_bar.close > prev_bar_close:
                    self.last_signal_bar = ctx.bar_index
                    return ctx.signal(
                        "long",
                        confidence=0.7,
                        stop_loss_bps=self.METADATA["declared_sl_bps"],
                        take_profit_bps=self.METADATA["declared_tp_bps"],
                        horizon_seconds=self.METADATA["declared_hold_seconds"],
                        metadata={
                            "reason": "vol_range_climax_fade_bear_rejection",
                            "prev_vol_mult": prev_bar_volume / avg_volume_recent,
                            "prev_range_mult": prev_bar_range / avg_range_recent,
                            "prev_close": prev_bar_close,
                            "current_close": current_bar.close,
                            "regime": ctx.market.get("regime", "NORMAL")
                        }
                    )
        return None

    def on_position_close(self, ctx: BarContext, position: Dict[str, Any]) -> None:
        # No adaptive logic for this strategy currently
        pass