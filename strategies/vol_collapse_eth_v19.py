from domains.strategy_contract import Strategy, BarContext, Signal
from typing import Optional, Dict, Any
import math

class PostSpikeCoilBreakout(Strategy):
    METADATA = {
        "name": "PostSpikeCoilBreakout",
        "domain": "eth_usdc",
        "declared_sl_bps": 220.0,
        "declared_tp_bps": 440.0,
        "declared_hold_seconds": 21600,  # 6 hours
        "warmup_bars": 180,
    }

    def initialize(self, ctx: BarContext) -> None:
        self.vol_lookback = 168
        self.channel_len = 12
        self.cooldown_bars = 8
        self.last_exit_bar = -100
        self.spike_detected = False
        self.spike_age = 0
        self.max_spike_age = 24

    def _realized_vol(self, closes, period=14):
        if len(closes) < period + 1:
            return None
        returns = []
        for i in range(len(closes) - period, len(closes)):
            r = math.log(closes[i] / closes[i - 1])
            returns.append(r)
        mean_r = sum(returns) / period
        var = sum((r - mean_r) ** 2 for r in returns) / period
        return math.sqrt(var)

    def on_position_close(self, ctx: BarContext, position: Dict[str, Any]) -> None:
        self.last_exit_bar = ctx.bar_index
        self.spike_detected = False
        self.spike_age = 0

    def on_bar(self, ctx: BarContext) -> Optional[Signal]:
        closes = ctx.closes(self.vol_lookback + 20)
        highs = ctx.highs(self.channel_len + 5)
        lows = ctx.lows(self.channel_len + 5)

        if len(closes) < self.vol_lookback + 15:
            return None

        # Cooldown check
        if ctx.bar_index - self.last_exit_bar < self.cooldown_bars:
            return None

        # Track 14-period realized vol history across lookback to evaluate percentile state
        # Approximate realized vols over past 168 bars by sampling every 6 bars to remain fast
        vols = []
        step = 6
        for offset in range(0, self.vol_lookback - 14, step):
            sub_closes = closes[-(14 + offset + 1):len(closes) - offset]
            v = self._realized_vol(sub_closes, period=14)
            if v is not None:
                vols.append(v)

        if len(vols) < 15:
            return None

        current_vol = self._realized_vol(closes, period=14)
        if current_vol is None:
            return None

        sorted_vols = sorted(vols)
        n = len(sorted_vols)
        p50 = sorted_vols[int(n * 0.50)]
        p80 = sorted_vols[int(n * 0.80)]

        # State machine: chaos spike -> compression coil -> breakout
        if current_vol >= p80:
            self.spike_detected = True
            self.spike_age = 0
        elif self.spike_detected:
            self.spike_age += 1
            if self.spike_age > self.max_spike_age:
                self.spike_detected = False
                self.spike_age = 0

        # Coil occurs when spike was recent, but vol collapsed back below the median
        is_coiling = self.spike_detected and (current_vol <= p50) and (self.spike_age >= 2)

        # In-position management
        if ctx.has_position():
            pos_dir = ctx.position_direction()
            pos = ctx.position
            entry_p = pos.get("entry_price", ctx.bar.close) if pos else ctx.bar.close
            
            # Trailing target or structure break exit
            if pos_dir == "long":
                prior_low = min(lows[-self.channel_len - 1:-1])
                if ctx.bar.close < prior_low:
                    return ctx.signal("flat", confidence=0.7, metadata={"reason": "channel_low_breach", "close": ctx.bar.close, "prior_low": prior_low})
            elif pos_dir == "short":
                prior_high = max(highs[-self.channel_len - 1:-1])
                if ctx.bar.close > prior_high:
                    return ctx.signal("flat", confidence=0.7, metadata={"reason": "channel_high_breach", "close": ctx.bar.close, "prior_high": prior_high})
            return None

        # Entry logic: break of 12-bar high/low strictly following coil formation
        if not is_coiling:
            return None

        prev_channel_high = max(highs[-self.channel_len - 1:-1])
        prev_channel_low = min(lows[-self.channel_len - 1:-1])

        # Filter against market crisis regimes
        market_regime = ctx.market.get("regime", "NORMAL")
        if market_regime in ("CRISIS", "MELTDOWN"):
            return None

        # Breakout release
        if ctx.bar.close > prev_channel_high:
            self.spike_detected = False  # Reset coil on release
            return ctx.signal(
                "long",
                confidence=0.75,
                stop_loss_bps=self.METADATA["declared_sl_bps"],
                take_profit_bps=self.METADATA["declared_tp_bps"],
                horizon_seconds=self.METADATA["declared_hold_seconds"],
                metadata={
                    "reason": "coil_breakout_long",
                    "close": ctx.bar.close,
                    "channel_high": prev_channel_high,
                    "current_vol": current_vol,
                    "median_vol": p50,
                    "spike_age": self.spike_age
                }
            )
        elif ctx.bar.close < prev_channel_low:
            self.spike_detected = False  # Reset coil on release
            return ctx.signal(
                "short",
                confidence=0.75,
                stop_loss_bps=self.METADATA["declared_sl_bps"],
                take_profit_bps=self.METADATA["declared_tp_bps"],
                horizon_seconds=self.METADATA["declared_hold_seconds"],
                metadata={
                    "reason": "coil_breakout_short",
                    "close": ctx.bar.close,
                    "channel_low": prev_channel_low,
                    "current_vol": current_vol,
                    "median_vol": p50,
                    "spike_age": self.spike_age
                }
            )

        return None