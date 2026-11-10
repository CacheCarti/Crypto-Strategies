from domains.strategy_contract import Strategy, BarContext, Signal
from typing import Optional, Dict, Any
import math

class PostSpikeCoilBreakout(Strategy):
    METADATA = {
        "name": "Post-Spike Coil Breakout",
        "domain": "eth_usdc",
        "declared_sl_bps": 220.0,
        "declared_tp_bps": 450.0,
        "declared_hold_seconds": 21600,  # ~6 hours hold
        "warmup_bars": 200,
        "required_features": ["funding_rate_ethusdt"],
    }

    def initialize(self, ctx: BarContext) -> None:
        self.vol_window = 168
        self.channel_len = 12
        self.cooldown_bars = 8
        self.last_exit_bar = -999
        self.spike_active = False
        self.coiled = False
        self.coil_armed_bar = -999
        self.max_coil_age = 24  # Coil expiration

    def _calc_atr(self, highs, lows, closes, period=14):
        if len(closes) < period + 1:
            return 0.0
        trs = []
        for i in range(1, len(closes)):
            tr = max(highs[i] - lows[i], abs(highs[i] - closes[i - 1]), abs(lows[i] - closes[i - 1]))
            trs.append(tr)
        return sum(trs[-period:]) / period

    def on_position_close(self, ctx: BarContext, position: Dict[str, Any]) -> None:
        self.last_exit_bar = ctx.bar_index
        self.coiled = False
        self.spike_active = False

    def on_bar(self, ctx: BarContext) -> Optional[Signal]:
        total_needed = self.vol_window + 14
        closes = ctx.closes(total_needed)
        if len(closes) < total_needed:
            return None

        # Manage open position exits
        if ctx.has_position():
            return None

        # Post-exit cooldown gate
        if ctx.bar_index - self.last_exit_bar < self.cooldown_bars:
            return None

        highs = ctx.highs(total_needed)
        lows = ctx.lows(total_needed)

        # Efficient rolling ATR series calculation
        trs = [max(highs[i] - lows[i], abs(highs[i] - closes[i - 1]), abs(lows[i] - closes[i - 1]))
               for i in range(1, len(closes))]
        
        atrs = []
        atr_window = 14
        for i in range(atr_window, len(trs) + 1):
            atrs.append(sum(trs[i - atr_window:i]) / atr_window)

        if len(atrs) < self.vol_window:
            return None

        current_atr = atrs[-1]
        recent_atrs = sorted(atrs[-self.vol_window:])
        median_atr = recent_atrs[len(recent_atrs) // 2]
        if median_atr <= 0:
            return None

        vol_ratio = current_atr / median_atr

        # Discrete State Machine for Vol Spike -> Vol Collapse (Coil)
        if vol_ratio >= 2.0:
            self.spike_active = True

        if self.spike_active and vol_ratio < 1.0:
            self.spike_active = False
            self.coiled = True
            self.coil_armed_bar = ctx.bar_index

        # Expire stale coils
        if self.coiled and (ctx.bar_index - self.coil_armed_bar > self.max_coil_age):
            self.coiled = False

        if not self.coiled:
            return None

        # Clean event-based trigger: Breakout of N-bar channel established during coil
        channel_highs = ctx.highs(self.channel_len + 1)[:-1]
        channel_lows = ctx.lows(self.channel_len + 1)[:-1]
        highest_prev = max(channel_highs)
        lowest_prev = min(channel_lows)

        current_close = ctx.bar.close
        funding = ctx.features.get("funding_rate_ethusdt", 0.0)

        # Long breakout: close breaks above channel high, avoid extreme long crowding
        if current_close > highest_prev and funding < 0.0003:
            self.coiled = False
            return ctx.signal(
                "long",
                confidence=0.75,
                stop_loss_bps=self.METADATA["declared_sl_bps"],
                take_profit_bps=self.METADATA["declared_tp_bps"],
                horizon_seconds=self.METADATA["declared_hold_seconds"],
                metadata={
                    "reason": "post_spike_coil_long_breakout",
                    "vol_ratio": round(vol_ratio, 2),
                    "channel_high": round(highest_prev, 2),
                    "close": round(current_close, 2),
                    "funding": round(funding, 6),
                }
            )

        # Short breakout: close breaks below channel low, avoid extreme short crowding
        if current_close < lowest_prev and funding > -0.0003:
            self.coiled = False
            return ctx.signal(
                "short",
                confidence=0.75,
                stop_loss_bps=self.METADATA["declared_sl_bps"],
                take_profit_bps=self.METADATA["declared_tp_bps"],
                horizon_seconds=self.METADATA["declared_hold_seconds"],
                metadata={
                    "reason": "post_spike_coil_short_breakout",
                    "vol_ratio": round(vol_ratio, 2),
                    "channel_low": round(lowest_prev, 2),
                    "close": round(current_close, 2),
                    "funding": round(funding, 6),
                }
            )

        return None