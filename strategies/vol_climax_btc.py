from domains.strategy_contract import Strategy, BarContext, Signal
from typing import Optional, Dict, Any
import math

class VolatilityClimaxFade(Strategy):
    METADATA = {
        "name": "Volatility Climax Fade BTC",
        "domain": "btc_usdc",
        "declared_sl_bps": 200.0,
        "declared_tp_bps": 400.0,
        "declared_hold_seconds": 7200, # 2 hours average hold
        "warmup_bars": 50,
        "required_features": [], # No external features needed for this strategy
    }

    def initialize(self, ctx: BarContext) -> None:
        self.climax_volume_period = 20
        self.climax_range_period = 20
        self.cooldown_bars = 12 # Cooldown for 12 hours after a trade
        self.last_trade_bar = -self.cooldown_bars

    def _atr(self, highs, lows, closes, period=14):
        if len(closes) < period + 1:
            return None
        trs = []
        for i in range(1, len(closes)):
            tr = max(highs[i]-lows[i], abs(highs[i]-closes[i-1]), abs(lows[i]-closes[i-1]))
            trs.append(tr)
        if len(trs) < period:
            return None
        return sum(trs[-period:]) / period

    def _sma(self, values, period):
        if len(values) < period:
            return None
        return sum(values[-period:]) / period

    def on_bar(self, ctx: BarContext) -> Optional[Signal]:
        closes = ctx.closes(self.warmup_bars + 1)
        highs = ctx.highs(self.warmup_bars + 1)
        lows = ctx.lows(self.warmup_bars + 1)
        volumes = ctx.volumes(self.warmup_bars + 1)

        if len(closes) < self.warmup_bars:
            return None

        # Cooldown logic
        if ctx.bar_index < self.last_trade_bar + self.cooldown_bars:
            return None

        # Calculate average true range
        atr = self._atr(highs, lows, closes, period=14)
        if atr is None:
            return None

        # Calculate average volume
        avg_volume = self._sma(volumes, self.climax_volume_period)
        if avg_volume is None:
            return None

        current_volume = ctx.bar.volume
        current_range = ctx.bar.high - ctx.bar.low

        # Volatility Climax Detection
        # Climax volume: current volume significantly higher than average
        volume_climax_threshold = 2.0 # 2x average volume
        is_volume_climax = current_volume > avg_volume * volume_climax_threshold

        # Climax range: current range significantly higher than ATR
        range_climax_threshold = 1.5 # 1.5x ATR
        is_range_climax = current_range > atr * range_climax_threshold

        # Combined climax condition (must have both volume and range climax)
        is_climax_bar = is_volume_climax and is_range_climax

        # Check for fade opportunity on the bar AFTER the climax bar
        if not ctx.has_position():
            # Look back one bar for climax, and two bars for the initial setup
            if ctx.bar_index >= 2:
                prev_closes = ctx.closes(2)
                prev_highs = ctx.highs(2)
                prev_lows = ctx.lows(2)
                prev_volumes = ctx.volumes(2)

                # Check if the previous bar (bar_index - 1) was a climax bar
                prev_avg_volume = self._sma(volumes[:-1], self.climax_volume_period)
                prev_atr = self._atr(highs[:-1], lows[:-1], closes[:-1], period=14)

                if prev_avg_volume is not None and prev_atr is not None:
                    was_prev_volume_climax = prev_volumes[-1] > prev_avg_volume * volume_climax_threshold
                    was_prev_range_climax = (prev_highs[-1] - prev_lows[-1]) > prev_atr * range_climax_threshold
                    was_prev_bar_climax = was_prev_volume_climax and was_prev_range_climax

                    if was_prev_bar_climax:
                        # Fade logic: current bar fails to continue the climax direction
                        # For a long climax, we look for a short fade
                        # For a short climax, we look for a long fade

                        # If previous bar closed significantly higher (long climax)
                        if prev_closes[-1] > prev_closes[-2] and ctx.bar.close < prev_closes[-1]:
                            self.last_trade_bar = ctx.bar_index
                            return ctx.signal(
                                "short",
                                confidence=0.7,
                                stop_loss_bps=self.METADATA["declared_sl_bps"],
                                take_profit_bps=self.METADATA["declared_tp_bps"],
                                horizon_seconds=self.METADATA["declared_hold_seconds"],
                                metadata={
                                    "reason": "vol_climax_short_fade",
                                    "climax_volume": prev_volumes[-1],
                                    "avg_volume": prev_avg_volume,
                                    "climax_range": prev_highs[-1] - prev_lows[-1],
                                    "atr": prev_atr,
                                    "prev_close": prev_closes[-1],
                                    "current_close": ctx.bar.close,
                                }
                            )
                        # If previous bar closed significantly lower (short climax)
                        elif prev_closes[-1] < prev_closes[-2] and ctx.bar.close > prev_closes[-1]:
                            self.last_trade_bar = ctx.bar_index
                            return ctx.signal(
                                "long",
                                confidence=0.7,
                                stop_loss_bps=self.METADATA["declared_sl_bps"],
                                take_profit_bps=self.METADATA["declared_tp_bps"],
                                horizon_seconds=self.METADATA["declared_hold_seconds"],
                                metadata={
                                    "reason": "vol_climax_long_fade",
                                    "climax_volume": prev_volumes[-1],
                                    "avg_volume": prev_avg_volume,
                                    "climax_range": prev_highs[-1] - prev_lows[-1],
                                    "atr": prev_atr,
                                    "prev_close": prev_closes[-1],
                                    "current_close": ctx.bar.close,
                                }
                            )
        return None

    def on_position_close(self, ctx: BarContext, position: Dict[str, Any]) -> None:
        # Reset last_trade_bar to ensure cooldown is applied after every closure
        self.last_trade_bar = ctx.bar_index
        pass