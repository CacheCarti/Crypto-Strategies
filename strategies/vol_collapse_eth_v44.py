from domains.strategy_contract import Strategy, BarContext, Signal
from typing import Optional, Dict, Any
import math

class PostSpikeCoilBreakout(Strategy):
    METADATA = {
        "name": "PostSpikeCoilBreakout",
        "domain": "eth_usdc",
        "declared_sl_bps": 220.0,
        "declared_tp_bps": 440.0,
        "declared_hold_seconds": 18000,
        "warmup_bars": 175,
        "required_features": ["btc_return_pct"],
    }

    def initialize(self, ctx: BarContext) -> None:
        self.spike_period = 168
        self.channel_period = 12
        self.spike_active = False
        self.coiled = False
        self.coil_bars = 0
        self.cooldown_remaining = 0
        self.max_coil_duration = 36
        self.cooldown_duration = 10

    def _realized_vol(self, closes, period=14):
        if len(closes) < period + 1:
            return 0.0
        log_rets = []
        for i in range(len(closes) - period, len(closes)):
            c1 = closes[i]
            c0 = closes[i - 1]
            if c0 > 0 and c1 > 0:
                log_rets.append(math.log(c1 / c0))
        if len(log_rets) < period:
            return 0.0
        mean = sum(log_rets) / period
        var = sum((r - mean) ** 2 for r in log_rets) / period
        return math.sqrt(var)

    def on_position_close(self, ctx: BarContext, position: Dict[str, Any]) -> None:
        self.cooldown_remaining = self.cooldown_duration
        self.coiled = False
        self.spike_active = False
        self.coil_bars = 0

    def on_bar(self, ctx: BarContext) -> Optional[Signal]:
        closes = ctx.closes(self.spike_period + 16)
        highs = ctx.highs(self.channel_period + 2)
        lows = ctx.lows(self.channel_period + 2)

        if len(closes) < self.spike_period + 15:
            return None

        if self.cooldown_remaining > 0:
            self.cooldown_remaining -= 1

        curr_rv = self._realized_vol(closes, period=12)

        rv_history = []
        for offset in range(self.spike_period):
            end_idx = len(closes) - offset
            sub = closes[max(0, end_idx - 13):end_idx]
            if len(sub) >= 13:
                rv_history.append(self._realized_vol(sub, period=12))

        if not rv_history:
            return None

        sorted_rv = sorted(rv_history)
        med_rv = sorted_rv[len(sorted_rv) // 2]

        if curr_rv > 2.0 * med_rv and med_rv > 1e-6:
            self.spike_active = True
            self.coiled = False
            self.coil_bars = 0
        elif self.spike_active and curr_rv < med_rv:
            self.spike_active = False
            self.coiled = True
            self.coil_bars = 0

        if self.coiled:
            self.coil_bars += 1
            if self.coil_bars > self.max_coil_duration:
                self.coiled = False
                self.coil_bars = 0

        if ctx.has_position():
            return None

        if not self.coiled or self.cooldown_remaining > 0:
            return None

        recent_highs = highs[- (self.channel_period + 1): -1]
        recent_lows = lows[- (self.channel_period + 1): -1]
        if len(recent_highs) < self.channel_period or len(recent_lows) < self.channel_period:
            return None

        hh = max(recent_highs)
        ll = min(recent_lows)
        curr_price = ctx.bar.close

        btc_ret = ctx.features.get("btc_return_pct", 0.0)

        if curr_price > hh and btc_ret > 0.0:
            self.coiled = False
            self.coil_bars = 0
            conf = min(0.85, 0.55 + abs(btc_ret) * 0.05)
            return ctx.signal(
                "long",
                confidence=conf,
                stop_loss_bps=self.METADATA["declared_sl_bps"],
                take_profit_bps=self.METADATA["declared_tp_bps"],
                horizon_seconds=self.METADATA["declared_hold_seconds"],
                metadata={
                    "reason": "coil_breakout_long_btc_confirmed",
                    "curr_rv": curr_rv,
                    "med_rv": med_rv,
                    "hh": hh,
                    "price": curr_price,
                    "btc_ret": btc_ret,
                }
            )

        if curr_price < ll and btc_ret < 0.0:
            self.coiled = False
            self.coil_bars = 0
            conf = min(0.85, 0.55 + abs(btc_ret) * 0.05)
            return ctx.signal(
                "short",
                confidence=conf,
                stop_loss_bps=self.METADATA["declared_sl_bps"],
                take_profit_bps=self.METADATA["declared_tp_bps"],
                horizon_seconds=self.METADATA["declared_hold_seconds"],
                metadata={
                    "reason": "coil_breakout_short_btc_confirmed",
                    "curr_rv": curr_rv,
                    "med_rv": med_rv,
                    "ll": ll,
                    "price": curr_price,
                    "btc_ret": btc_ret,
                }
            )

        return None