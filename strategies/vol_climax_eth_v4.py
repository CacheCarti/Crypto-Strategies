from domains.strategy_contract import Strategy, BarContext, Signal
from typing import Optional, Dict, Any
import math

class EthVolClimaxFade(Strategy):
    METADATA = {
        "name": "ETH Volatility Climax Fade",
        "domain": "eth_usdc",
        "declared_sl_bps": 200.0,
        "declared_tp_bps": 400.0,
        "declared_hold_seconds": 3600 * 4, # Roughly 4 hours
        "warmup_bars": 50,
        "required_features": [], # No external features required
    }

    def initialize(self, ctx: BarContext) -> None:
        self.max_lookback = self.METADATA["warmup_bars"]
        self.volume_period = 20
        self.range_period = 20
        self.cooldown_bars = 12 # Cooldown for 12 hours after a trade
        self.last_trade_bar_index = -self.cooldown_bars - 1 # Initialize to allow immediate trading

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
        closes = ctx.closes(self.max_lookback)
        highs = ctx.highs(self.max_lookback)
        lows = ctx.lows(self.max_lookback)
        volumes = ctx.volumes(self.max_lookback)

        if len(closes) < self.max_lookback:
            return None

        # Cooldown check
        if ctx.bar_index <= self.last_trade_bar_index + self.cooldown_bars:
            return None

        current_close = closes[-1]
        current_high = highs[-1]
        current_low = lows[-1]
        current_volume = volumes[-1]
        current_range = current_high - current_low

        # Calculate average volume and range for comparison
        avg_volume = self._sma(volumes[:-1], self.volume_period) # Exclude current bar for fair comparison
        avg_range = self._sma([h - l for h, l in zip(highs[:-1], lows[:-1])], self.range_period)

        if avg_volume is None or avg_range is None:
            return None

        # Volatility Climax Detection: Volume > 2x avg, Range > 2x avg
        # And current bar is the climax bar (previous bar is not)
        is_volume_climax = current_volume > (avg_volume * 2.0)
        is_range_climax = current_range > (avg_range * 2.0)

        # Ensure previous bar was not also a climax, to detect the "event boundary"
        prev_volume = volumes[-2]
        prev_range = highs[-2] - lows[-2]
        is_prev_volume_climax = prev_volume > (avg_volume * 2.0)
        is_prev_range_climax = prev_range > (avg_range * 2.0)

        clean_event_boundary = not (is_prev_volume_climax and is_prev_range_climax)

        # Fade Condition: Current bar is a climax, but the next bar (current_bar) fails to continue the move
        # We need to look at the close relative to the high/low of the climax bar
        # For a long fade (short signal), we expect high volume/range AND a close near the low of the climax bar,
        # indicating rejection of further upside.
        # For a short fade (long signal), we expect high volume/range AND a close near the high of the climax bar,
        # indicating rejection of further downside.

        # Regime filter: only trade in "normal" or "volatile" regimes, avoid "crisis" or "meltdown"
        # Also, avoid trading if the market is trending strongly against the fade direction
        regime = ctx.market.get("regime", "NORMAL")
        trend_regime = ctx.market.get("trend_regime", "neutral")

        # Long Fade (Short Signal)
        if (is_volume_climax and is_range_climax and clean_event_boundary and
            current_close < (current_high - 0.75 * current_range) and # Close in bottom 25% of bar
            ctx.position_direction() != "short" and
            regime in ["NORMAL", "VOLATILE"] and
            trend_regime != "bull"): # Avoid fading against strong bull trend
            
            self.last_trade_bar_index = ctx.bar_index
            return ctx.signal(
                "short",
                confidence=0.75,
                metadata={
                    "reason": "vol_range_climax_fade_short",
                    "current_volume": current_volume,
                    "avg_volume": avg_volume,
                    "current_range": current_range,
                    "avg_range": avg_range,
                    "close_relative_to_range": (current_close - current_low) / current_range,
                    "market_regime": regime,
                    "trend_regime": trend_regime,
                }
            )

        # Short Fade (Long Signal)
        if (is_volume_climax and is_range_climax and clean_event_boundary and
            current_close > (current_low + 0.75 * current_range) and # Close in top 25% of bar
            ctx.position_direction() != "long" and
            regime in ["NORMAL", "VOLATILE"] and
            trend_regime != "bear"): # Avoid fading against strong bear trend
            
            self.last_trade_bar_index = ctx.bar_index
            return ctx.signal(
                "long",
                confidence=0.75,
                metadata={
                    "reason": "vol_range_climax_fade_long",
                    "current_volume": current_volume,
                    "avg_volume": avg_volume,
                    "current_range": current_range,
                    "avg_range": avg_range,
                    "close_relative_to_range": (current_close - current_low) / current_range,
                    "market_regime": regime,
                    "trend_regime": trend_regime,
                }
            )

        return None

    def on_position_close(self, ctx: BarContext, position: Dict[str, Any]) -> None:
        # Update last_trade_bar_index to enforce cooldown after a position closes
        self.last_trade_bar_index = ctx.bar_index
        # You could add adaptive logic here, e.g., adjust confidence based on recent performance
        pass