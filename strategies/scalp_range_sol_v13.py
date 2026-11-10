from domains.strategy_contract import Strategy, BarContext, Signal
from typing import Optional, Dict, Any
import math

class SolRangeFadeScalp(Strategy):
    METADATA = {
        "name": "SolRangeFadeScalp",
        "domain": "sol_usdc_scalp",
        "declared_sl_bps": 120.0,
        "declared_tp_bps": 160.0,
        "declared_hold_seconds": 600,
        "warmup_bars": 35,
    }

    def initialize(self, ctx: BarContext) -> None:
        self.channel_period = 30
        self.cooldown_bars = 45
        self.last_exit_bar = -999
        self.max_bandwidth_bps = 220.0
        self.min_bandwidth_bps = 75.0

    def _rsi(self, closes: list, period: int = 14) -> Optional[float]:
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
        highs = ctx.highs(self.channel_period + 1)
        lows = ctx.lows(self.channel_period + 1)

        if len(closes) < self.channel_period + 1:
            return None

        # Base channel on the preceding completed bars
        ref_highs = highs[-self.channel_period - 1:-1]
        ref_lows = lows[-self.channel_period - 1:-1]
        hh = max(ref_highs)
        ll = min(ref_lows)
        mid = (hh + ll) / 2.0
        channel_range = hh - ll

        if mid <= 0 or channel_range <= 0:
            return None

        bandwidth_bps = (channel_range / mid) * 10000.0
        curr_price = ctx.bar.close

        # Position management
        if ctx.has_position():
            pos_dir = ctx.position_direction()
            if pos_dir == "long" and curr_price >= mid:
                return ctx.signal(
                    "flat",
                    confidence=0.8,
                    metadata={"reason": "reached_midline_long", "price": curr_price, "mid": mid, "bandwidth_bps": bandwidth_bps}
                )
            elif pos_dir == "short" and curr_price <= mid:
                return ctx.signal(
                    "flat",
                    confidence=0.8,
                    metadata={"reason": "reached_midline_short", "price": curr_price, "mid": mid, "bandwidth_bps": bandwidth_bps}
                )
            return None

        # Mandatory cooldown
        if ctx.bar_index - self.last_exit_bar < self.cooldown_bars:
            return None

        # Regime gate: Stand down in volatile/crisis regimes or wide trending channels
        if ctx.regime in ("volatile", "crisis"):
            return None

        if not (self.min_bandwidth_bps <= bandwidth_bps <= self.max_bandwidth_bps):
            return None

        rsi = self._rsi(closes, period=10)
        if rsi is None:
            return None

        lower_bound = ll + 0.12 * channel_range
        upper_bound = hh - 0.12 * channel_range

        # Fade lower edge: swept low or deep in lower band with oversold RSI
        if (ctx.bar.low <= ll or curr_price <= lower_bound) and curr_price > ll * 0.995 and rsi <= 35.0:
            return ctx.signal(
                "long",
                confidence=0.75,
                stop_loss_bps=120.0,
                take_profit_bps=160.0,
                horizon_seconds=600,
                metadata={
                    "reason": "fade_channel_low",
                    "price": curr_price,
                    "channel_low": ll,
                    "mid": mid,
                    "rsi": rsi,
                    "bandwidth_bps": bandwidth_bps
                }
            )

        # Fade upper edge: swept high or deep in upper band with overbought RSI
        if (ctx.bar.high >= hh or curr_price >= upper_bound) and curr_price < hh * 1.005 and rsi >= 65.0:
            return ctx.signal(
                "short",
                confidence=0.75,
                stop_loss_bps=120.0,
                take_profit_bps=160.0,
                horizon_seconds=600,
                metadata={
                    "reason": "fade_channel_high",
                    "price": curr_price,
                    "channel_high": hh,
                    "mid": mid,
                    "rsi": rsi,
                    "bandwidth_bps": bandwidth_bps
                }
            )

        return None