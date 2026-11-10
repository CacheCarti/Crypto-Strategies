from domains.strategy_contract import Strategy, BarContext, Signal
from typing import Optional, Dict, Any
import math

class SolNarrowRangeFadeScalp(Strategy):
    METADATA = {
        "name": "SolNarrowRangeFadeScalp",
        "domain": "sol_usdc_scalp",
        "declared_sl_bps": 90.0,
        "declared_tp_bps": 140.0,
        "declared_hold_seconds": 900,
        "warmup_bars": 35,
    }

    def initialize(self, ctx: BarContext) -> None:
        self.channel_period = 30
        self.cooldown_bars = 6
        self.max_hold_bars = 7
        self.last_exit_bar = -999
        self.entry_bar = -999

    def _atr(self, highs, lows, closes, period=14):
        if len(closes) < period + 1:
            return None
        trs = []
        for i in range(1, len(closes)):
            tr = max(highs[i] - lows[i], abs(highs[i] - closes[i - 1]), abs(lows[i] - closes[i - 1]))
            trs.append(tr)
        return sum(trs[-period:]) / period

    def on_position_close(self, ctx: BarContext, position: Dict[str, Any]) -> None:
        self.last_exit_bar = ctx.bar_index
        self.entry_bar = -999

    def on_bar(self, ctx: BarContext) -> Optional[Signal]:
        closes = ctx.closes(self.channel_period + 5)
        highs = ctx.highs(self.channel_period + 5)
        lows = ctx.lows(self.channel_period + 5)

        if len(closes) < self.channel_period + 2:
            return None

        # Filter out extreme crisis regimes where range fade fails
        regime = ctx.market.get("regime", "NORMAL")
        if regime in ("CRISIS", "MELTDOWN"):
            if ctx.has_position():
                return ctx.signal("flat", confidence=0.8, metadata={"reason": "crisis_exit", "regime": regime})
            return None

        current_close = ctx.bar.close
        recent_highs = highs[-self.channel_period:]
        recent_lows = lows[-self.channel_period:]
        highest = max(recent_highs)
        lowest = min(recent_lows)
        midline = (highest + lowest) / 2.0
        
        if midline <= 0:
            return None
            
        range_bps = ((highest - lowest) / midline) * 10000.0

        # Dynamic timed/midline exit logic if in position
        if ctx.has_position():
            bars_in_trade = ctx.bar_index - self.entry_bar if self.entry_bar > 0 else 1
            direction = ctx.position_direction()

            # Midline target exit (fade target reached)
            if direction == "long" and current_close >= midline:
                return ctx.signal("flat", confidence=0.7, metadata={
                    "reason": "long_midline_reached",
                    "close": current_close,
                    "midline": midline,
                    "bars_in_trade": bars_in_trade
                })
            elif direction == "short" and current_close <= midline:
                return ctx.signal("flat", confidence=0.7, metadata={
                    "reason": "short_midline_reached",
                    "close": current_close,
                    "midline": midline,
                    "bars_in_trade": bars_in_trade
                })

            # Time stop: Range fade fails if not resolved quickly
            if bars_in_trade >= self.max_hold_bars:
                return ctx.signal("flat", confidence=0.6, metadata={
                    "reason": "time_stop_exhaustion",
                    "bars_in_trade": bars_in_trade,
                    "close": current_close
                })

            return None

        # Cooldown guard after trade exit
        if (ctx.bar_index - self.last_exit_bar) < self.cooldown_bars:
            return None

        # Channel width condition: only trade during calm sideways chop (65 bps to 180 bps width)
        # Narrow enough to be sideways chop, but wide enough to overcome 12 bps friction
        if not (65.0 <= range_bps <= 175.0):
            return None

        # Volatility check: channel expansion check via ATR
        atr = self._atr(highs, lows, closes, 14)
        if atr is None:
            return None
        atr_bps = (atr / current_close) * 10000.0
        if atr_bps > 38.0:
            return None

        # Position within channel: 0.0 (at lowest) to 1.0 (at highest)
        channel_span = highest - lowest
        if channel_span <= 0:
            return None
        pos_in_channel = (current_close - lowest) / channel_span

        # Edge fade setups
        # Long: touching lower 12% of channel with rejection wick (close > low)
        if pos_in_channel <= 0.12 and ctx.bar.close > ctx.bar.low:
            self.entry_bar = ctx.bar_index
            return ctx.signal(
                "long",
                confidence=0.75,
                stop_loss_bps=85.0,
                take_profit_bps=130.0,
                metadata={
                    "reason": "range_fade_lower_edge",
                    "pos_in_channel": round(pos_in_channel, 3),
                    "range_bps": round(range_bps, 1),
                    "atr_bps": round(atr_bps, 1),
                    "midline": round(midline, 3)
                }
            )

        # Short: touching upper 12% of channel with rejection wick (close < high)
        if pos_in_channel >= 0.88 and ctx.bar.close < ctx.bar.high:
            self.entry_bar = ctx.bar_index
            return ctx.signal(
                "short",
                confidence=0.75,
                stop_loss_bps=85.0,
                take_profit_bps=130.0,
                metadata={
                    "reason": "range_fade_upper_edge",
                    "pos_in_channel": round(pos_in_channel, 3),
                    "range_bps": round(range_bps, 1),
                    "atr_bps": round(atr_bps, 1),
                    "midline": round(midline, 3)
                }
            )

        return None