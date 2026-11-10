from domains.strategy_contract import Strategy, BarContext, Signal
from typing import Optional, Dict, Any
import math

class PostSpikeCoil(Strategy):
    METADATA = {
        "name": "PostSpikeCoilBreakout",
        "domain": "eth_usdc",
        "declared_sl_bps": 220.0,
        "declared_tp_bps": 480.0,
        "declared_hold_seconds": 14400,
        "warmup_bars": 180,
    }

    def initialize(self, ctx: BarContext) -> None:
        self.lookback_vol = 168
        self.channel_len = 12
        self.cooldown_period = 10
        self.last_exit_bar = -999
        self.spike_detected = False
        self.spike_age = 0
        self.max_spike_age = 36

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
        self.spike_detected = False
        self.spike_age = 0

    def on_bar(self, ctx: BarContext) -> Optional[Signal]:
        closes = ctx.closes(self.lookback_vol + 20)
        highs = ctx.highs(self.lookback_vol + 20)
        lows = ctx.lows(self.lookback_vol + 20)

        if len(closes) < self.lookback_vol + 15:
            return None

        # Manage open position exits or tracking
        if ctx.has_position():
            return None

        # Mandatory cooldown after exiting
        if ctx.bar_index - self.last_exit_bar < self.cooldown_period:
            return None

        # Compute rolling ATRs to establish historical baseline vs recent volatility
        atrs = []
        for i in range(len(closes) - self.lookback_vol, len(closes) + 1):
            sub_c = closes[:i]
            sub_h = highs[:i]
            sub_l = lows[:i]
            a = self._atr(sub_h, sub_l, sub_c, period=14)
            if a is not None:
                atrs.append(a)

        if len(atrs) < self.lookback_vol:
            return None

        current_atr = atrs[-1]
        sorted_atrs = sorted(atrs[-self.lookback_vol:])
        median_atr = sorted_atrs[len(sorted_atrs) // 2]

        if median_atr <= 0:
            return None

        # State machine tracking chaos -> compression transition
        if self.spike_detected:
            self.spike_age += 1
            if self.spike_age > self.max_spike_age:
                self.spike_detected = False
                self.spike_age = 0
        else:
            # Check for recent volatility spike (ATR spiked > 1.9x median in the last 24 bars)
            recent_atrs = atrs[-24:]
            if any(a > 1.9 * median_atr for a in recent_atrs):
                # Volatility has now collapsed back down near or below baseline (compression)
                if current_atr <= 1.15 * median_atr:
                    self.spike_detected = True
                    self.spike_age = 1

        if not self.spike_detected:
            return None

        # Coil release triggers: Donchian 12-bar breakout
        recent_highs = highs[- (self.channel_len + 1): -1]
        recent_lows = lows[- (self.channel_len + 1): -1]
        breakout_high = max(recent_highs)
        breakout_low = min(recent_lows)

        current_close = closes[-1]
        current_open = ctx.bar.open

        # Regime / Crisis check to avoid toxic execution environments
        market_regime = ctx.market.get("regime", "NORMAL")
        if market_regime in ("CRISIS", "MELTDOWN"):
            return None

        # Long breakout from coil
        if current_close > breakout_high and current_close > current_open:
            self.spike_detected = False
            return ctx.signal(
                "long",
                confidence=0.75,
                stop_loss_bps=self.METADATA["declared_sl_bps"],
                take_profit_bps=self.METADATA["declared_tp_bps"],
                horizon_seconds=self.METADATA["declared_hold_seconds"],
                metadata={
                    "reason": "coil_release_long_breakout",
                    "current_atr": round(current_atr, 4),
                    "median_atr": round(median_atr, 4),
                    "breakout_high": round(breakout_high, 2),
                    "close": round(current_close, 2),
                    "spike_age": self.spike_age,
                }
            )

        # Short breakout from coil
        if current_close < breakout_low and current_close < current_open:
            self.spike_detected = False
            return ctx.signal(
                "short",
                confidence=0.75,
                stop_loss_bps=self.METADATA["declared_sl_bps"],
                take_profit_bps=self.METADATA["declared_tp_bps"],
                horizon_seconds=self.METADATA["declared_hold_seconds"],
                metadata={
                    "reason": "coil_release_short_breakout",
                    "current_atr": round(current_atr, 4),
                    "median_atr": round(median_atr, 4),
                    "breakout_low": round(breakout_low, 2),
                    "close": round(current_close, 2),
                    "spike_age": self.spike_age,
                }
            )

        return None