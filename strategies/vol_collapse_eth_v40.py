from domains.strategy_contract import Strategy, BarContext, Signal
from typing import Optional, Dict, Any
import math

class PostSpikeCoilBreakout(Strategy):
    METADATA = {
        "name": "PostSpikeCoilBreakout",
        "domain": "eth_usdc",
        "declared_sl_bps": 250.0,
        "declared_tp_bps": 500.0,
        "declared_hold_seconds": 18000,
        "warmup_bars": 180,
    }

    def initialize(self, ctx: BarContext) -> None:
        self.median_period = 168
        self.channel_period = 12
        self.cooldown_bars = 8
        self.last_exit_bar = -100
        self.spike_active = False
        self.coiled = False
        self.coil_expiry_bar = -1
        self.last_channel_high = 0.0
        self.last_channel_low = 0.0

    def on_position_close(self, ctx: BarContext, position: Dict[str, Any]) -> None:
        self.last_exit_bar = ctx.bar_index
        self.coiled = False
        self.spike_active = False

    def on_bar(self, ctx: BarContext) -> Optional[Signal]:
        total_needed = self.median_period + 15
        closes = ctx.closes(total_needed)
        if len(closes) < total_needed:
            return None

        # Return quickly if in position (engine manages stops) or cooling down
        if ctx.has_position():
            return None

        if ctx.bar_index - self.last_exit_bar < self.cooldown_bars:
            return None

        # Compute log returns volatility over 6-bar rolling window across the 168-bar span
        vols = []
        for i in range(len(closes) - self.median_period, len(closes)):
            sub = closes[i - 6 : i + 1]
            rets = [abs(sub[j] - sub[j - 1]) / sub[j - 1] for j in range(1, len(sub))]
            vols.append(sum(rets) / len(rets))

        current_vol = vols[-1]
        sorted_vols = sorted(vols)
        median_vol = sorted_vols[len(sorted_vols) // 2]

        if median_vol <= 0:
            return None

        # State machine: Vol Spike -> Collapse into Coil
        if current_vol > 2.0 * median_vol:
            self.spike_active = True
            self.coiled = False
        elif self.spike_active and current_vol < median_vol:
            self.spike_active = False
            self.coiled = True
            # Allow 18 bars (~18h) for the coil release break
            self.coil_expiry_bar = ctx.bar_index + 18
            
            # Lock the 12-bar Donchian channel bounds at coil moment
            highs_slice = ctx.highs(self.channel_period + 1)[:-1]
            lows_slice = ctx.lows(self.channel_period + 1)[:-1]
            self.last_channel_high = max(highs_slice)
            self.last_channel_low = min(lows_slice)

        if not self.coiled:
            return None

        if ctx.bar_index > self.coil_expiry_bar:
            self.coiled = False
            return None

        current_price = ctx.bar.close
        prev_close = closes[-2]

        # Stricter event boundary: trigger strictly on the first discrete cross outside the coil bounds
        long_break = current_price > self.last_channel_high and prev_close <= self.last_channel_high
        short_break = current_price < self.last_channel_low and prev_close >= self.last_channel_low

        if not (long_break or short_break):
            return None

        # Reset coil state immediately upon firing so only one trigger per event occurs
        self.coiled = False

        if long_break:
            return ctx.signal(
                "long",
                confidence=0.8,
                stop_loss_bps=self.METADATA["declared_sl_bps"],
                take_profit_bps=self.METADATA["declared_tp_bps"],
                metadata={
                    "reason": "post_spike_coil_upside_breakout",
                    "breakout_price": current_price,
                    "channel_high": self.last_channel_high,
                    "current_vol": current_vol,
                    "median_vol": median_vol,
                },
            )
        else:
            return ctx.signal(
                "short",
                confidence=0.8,
                stop_loss_bps=self.METADATA["declared_sl_bps"],
                take_profit_bps=self.METADATA["declared_tp_bps"],
                metadata={
                    "reason": "post_spike_coil_downside_breakout",
                    "breakout_price": current_price,
                    "channel_low": self.last_channel_low,
                    "current_vol": current_vol,
                    "median_vol": median_vol,
                },
            )