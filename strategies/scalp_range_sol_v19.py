from domains.strategy_contract import Strategy, BarContext, Signal
from typing import Optional, Dict, Any
import math

class SolRangeFadeScalp(Strategy):
    METADATA = {
        "name": "SolRangeFadeScalp",
        "domain": "sol_usdc_scalp",
        "declared_sl_bps": 120.0,
        "declared_tp_bps": 160.0,
        "declared_hold_seconds": 900,
        "warmup_bars": 35,
    }

    def initialize(self, ctx: BarContext) -> None:
        self.channel_period = 30
        self.cooldown_bars = 45
        self.last_exit_bar = -999
        self.max_bandwidth_bps = 220.0
        self.min_bandwidth_bps = 70.0

    def _rsi(self, closes, period=14):
        if len(closes) < period + 1:
            return None
        gains, losses = [], []
        for i in range(1, len(closes)):
            diff = closes[i] - closes[i - 1]
            gains.append(max(diff, 0.0))
            losses.append(max(-diff, 0.0))
        avg_gain = sum(gains[-period:]) / period
        avg_loss = sum(losses[-period:]) / period
        if avg_loss == 0.0:
            return 100.0
        rs = avg_gain / avg_loss
        return 100.0 - (100.0 / (1.0 + rs))

    def on_position_close(self, ctx: BarContext, position: Dict[str, Any]) -> None:
        self.last_exit_bar = ctx.bar_index

    def on_bar(self, ctx: BarContext) -> Optional[Signal]:
        closes = ctx.closes(self.channel_period + 1)
        if len(closes) < self.channel_period + 1:
            return None

        highs = ctx.highs(self.channel_period)
        lows = ctx.lows(self.channel_period)
        
        current_close = closes[-1]
        highest = max(highs)
        lowest = min(lows)
        
        if lowest <= 0:
            return None
            
        midline = (highest + lowest) / 2.0
        bandwidth_bps = ((highest - lowest) / midline) * 10000.0

        # Position management
        if ctx.has_position():
            direction = ctx.position_direction()
            pos_dict = ctx.position or {}
            entry_price = pos_dict.get("entry_price", current_close)
            
            # Midline reversion exit or reversal
            if direction == "long" and current_close >= midline:
                self.last_exit_bar = ctx.bar_index
                return ctx.signal("flat", confidence=0.7, metadata={
                    "reason": "midline_reached_long",
                    "close": current_close,
                    "midline": midline,
                    "bandwidth_bps": bandwidth_bps
                })
            elif direction == "short" and current_close <= midline:
                self.last_exit_bar = ctx.bar_index
                return ctx.signal("flat", confidence=0.7, metadata={
                    "reason": "midline_reached_short",
                    "close": current_close,
                    "midline": midline,
                    "bandwidth_bps": bandwidth_bps
                })
            return None

        # Mandatory cooldown to prevent overtrading and friction bleed
        if ctx.bar_index - self.last_exit_bar < self.cooldown_bars:
            return None

        # Only trade during quiet, sideways regimes
        regime = ctx.market.get("regime", "NORMAL")
        if regime in ("CRISIS", "MELTDOWN"):
            return None

        # Channel must be narrow (chop), avoiding trending blowouts
        if not (self.min_bandwidth_bps <= bandwidth_bps <= self.max_bandwidth_bps):
            return None

        rsi_vals = ctx.closes(20)
        rsi = self._rsi(rsi_vals, period=10)
        if rsi is None:
            return None

        channel_range = highest - lowest
        if channel_range <= 0:
            return None

        # Proximity threshold: within 12% of the channel edge
        edge_zone = channel_range * 0.12

        # Fade lower bound: close is near channel low and short-term oversold
        if current_close <= lowest + edge_zone and rsi < 38.0:
            return ctx.signal(
                "long",
                confidence=0.75,
                stop_loss_bps=120.0,
                take_profit_bps=160.0,
                horizon_seconds=900,
                metadata={
                    "reason": "fade_channel_low",
                    "close": current_close,
                    "channel_low": lowest,
                    "midline": midline,
                    "bandwidth_bps": bandwidth_bps,
                    "rsi": round(rsi, 2)
                }
            )

        # Fade upper bound: close is near channel high and short-term overbought
        if current_close >= highest - edge_zone and rsi > 62.0:
            return ctx.signal(
                "short",
                confidence=0.75,
                stop_loss_bps=120.0,
                take_profit_bps=160.0,
                horizon_seconds=900,
                metadata={
                    "reason": "fade_channel_high",
                    "close": current_close,
                    "channel_high": highest,
                    "midline": midline,
                    "bandwidth_bps": bandwidth_bps,
                    "rsi": round(rsi, 2)
                }
            )

        return None