from domains.strategy_contract import Strategy, BarContext, Signal
from typing import Optional, Dict, Any
import math

class VolumeClimaxReversal(Strategy):
    METADATA = {
        "name": "VolumeClimaxReversal",
        "domain": "btc_usdc",
        "declared_sl_bps": 200.0,
        "declared_tp_bps": 400.0,
        "declared_hold_seconds": 7200,  # 2 hours
        "warmup_bars": 50,
    }

    def initialize(self, ctx: BarContext) -> None:
        self.volume_period = 20
        self.range_period = 20
        self.climax_volume_multiplier = 2.5
        self.climax_range_multiplier = 1.5
        self.cooldown_bars = 12 # 12 hours cooldown
        self.last_trade_bar = -self.cooldown_bars

    def _sma(self, values, period):
        if len(values) < period:
            return None
        return sum(values[-period:]) / period

    def _atr(self, highs, lows, closes, period=14):
        if len(closes) < period + 1:
            return None
        trs = []
        for i in range(1, len(closes)):
            tr = max(highs[i]-lows[i], abs(highs[i]-closes[i-1]), abs(lows[i]-closes[i-1]))
            trs.append(tr)
        return sum(trs[-period:]) / period

    def on_bar(self, ctx: BarContext) -> Optional[Signal]:
        closes = ctx.closes(self.warmup_bars)
        volumes = ctx.volumes(self.warmup_bars)
        highs = ctx.highs(self.warmup_bars)
        lows = ctx.lows(self.warmup_bars)

        if len(closes) < self.warmup_bars:
            return None

        if ctx.bar_index < self.last_trade_bar + self.cooldown_bars:
            return None # Cooldown active

        current_volume = ctx.bar.volume
        current_range = ctx.bar.high - ctx.bar.low

        avg_volume = self._sma(volumes[:-1], self.volume_period) # Exclude current bar for average
        avg_range = self._sma([h - l for h, l in zip(highs[:-1], lows[:-1])], self.range_period)

        if avg_volume is None or avg_range is None:
            return None

        is_volume_climax = current_volume > avg_volume * self.climax_volume_multiplier
        is_range_climax = current_range > avg_range * self.climax_range_multiplier

        # Detect climax bar
        if is_volume_climax and is_range_climax:
            # Check for extended move (e.g., previous X bars all up or all down)
            # Simple check: last 3 bars before climax were in one direction
            if len(closes) < 4:
                return None

            # Check for up-climax (potential short)
            if closes[-2] > closes[-3] and closes[-3] > closes[-4] and ctx.bar.close > closes[-2]: # Strong move up
                if ctx.bar.close < ctx.bar.open: # Climax bar closed lower than open (weakness)
                    # Wait for next bar to confirm reversal
                    if ctx.bar_index > 0 and closes[-1] < closes[-2]: # Previous bar closed lower than the one before it
                        self.last_trade_bar = ctx.bar_index
                        return ctx.signal(
                            "short",
                            confidence=0.7,
                            stop_loss_bps=self.METADATA["declared_sl_bps"],
                            take_profit_bps=self.METADATA["declared_tp_bps"],
                            metadata={
                                "reason": "volume_range_climax_reversal_short",
                                "climax_volume": current_volume,
                                "avg_volume": avg_volume,
                                "climax_range": current_range,
                                "avg_range": avg_range,
                                "climax_bar_close": ctx.bar.close,
                                "climax_bar_open": ctx.bar.open,
                                "prev_bar_close": closes[-1],
                                "prev_prev_bar_close": closes[-2],
                            }
                        )
            # Check for down-climax (potential long)
            elif closes[-2] < closes[-3] and closes[-3] < closes[-4] and ctx.bar.close < closes[-2]: # Strong move down
                if ctx.bar.close > ctx.bar.open: # Climax bar closed higher than open (strength)
                    # Wait for next bar to confirm reversal
                    if ctx.bar_index > 0 and closes[-1] > closes[-2]: # Previous bar closed higher than the one before it
                        self.last_trade_bar = ctx.bar_index
                        return ctx.signal(
                            "long",
                            confidence=0.7,
                            stop_loss_bps=self.METADATA["declared_sl_bps"],
                            take_profit_bps=self.METADATA["declared_tp_bps"],
                            metadata={
                                "reason": "volume_range_climax_reversal_long",
                                "climax_volume": current_volume,
                                "avg_volume": avg_volume,
                                "climax_range": current_range,
                                "avg_range": avg_range,
                                "climax_bar_close": ctx.bar.close,
                                "climax_bar_open": ctx.bar.open,
                                "prev_bar_close": closes[-1],
                                "prev_prev_bar_close": closes[-2],
                            }
                        )
        return None

    def on_position_close(self, ctx: BarContext, position: Dict[str, Any]) -> None:
        # Reset last trade bar to enforce cooldown after any position close
        self.last_trade_bar = ctx.bar_index