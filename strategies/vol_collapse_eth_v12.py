from domains.strategy_contract import Strategy, BarContext, Signal
from typing import Optional, Dict, Any
import math

class EthVolCoilBreakout(Strategy):
    METADATA = {
        "name": "EthVolCoilBreakout",
        "domain": "eth_usdc",
        "declared_sl_bps": 220.0,
        "declared_tp_bps": 480.0,
        "declared_hold_seconds": 21600,  # ~6 hours target hold
        "warmup_bars": 185,
    }

    def initialize(self, ctx: BarContext) -> None:
        self.vol_period = 168
        self.channel_period = 12
        self.spike_threshold = 1.8
        self.cooldown_period = 8
        self.max_hold_bars = 24
        
        self.spike_armed = False
        self.coil_ready = False
        self.bars_since_coil = 0
        self.max_coil_window = 20
        self.cooldown_until = 0

    def _realized_vol(self, closes, window=12):
        if len(closes) < window + 1:
            return 0.0
        returns = [math.log(closes[i] / closes[i - 1]) for i in range(-window, 0)]
        mean_ret = sum(returns) / window
        var = sum((r - mean_ret) ** 2 for r in returns) / window
        return math.sqrt(var)

    def _median(self, values):
        s = sorted(values)
        n = len(s)
        mid = n // 2
        return (s[mid - 1] + s[mid]) / 2.0 if n % 2 == 0 else s[mid]

    def on_position_close(self, ctx: BarContext, position: Dict[str, Any]) -> None:
        self.cooldown_until = ctx.bar_index + self.cooldown_period
        self.spike_armed = False
        self.coil_ready = False
        self.bars_since_coil = 0

    def on_bar(self, ctx: BarContext) -> Optional[Signal]:
        closes = ctx.closes(self.vol_period + 20)
        highs = ctx.highs(self.channel_period + 5)
        lows = ctx.lows(self.channel_period + 5)

        if len(closes) < self.vol_period + 15:
            return None

        # Manage existing position with time horizon stop
        if ctx.has_position():
            entry_price = ctx.position.get("entry_price", ctx.bar.close) if ctx.position else ctx.bar.close
            direction = ctx.position_direction()
            
            # Simple trailing / invalidation check
            if direction == "long" and ctx.bar.close < min(lows[-4:-1]):
                return ctx.signal("flat", confidence=0.6, metadata={"reason": "long_coil_failed", "price": ctx.bar.close})
            elif direction == "short" and ctx.bar.close > max(highs[-4:-1]):
                return ctx.signal("flat", confidence=0.6, metadata={"reason": "short_coil_failed", "price": ctx.bar.close})
            return None

        if ctx.bar_index < self.cooldown_until:
            return None

        # Filter out extreme crisis regimes (dominant failure fix from prior attempts)
        market_regime = ctx.market.get("regime", "NORMAL")
        crisis_score = ctx.market.get("crisis_score", 0.0)
        if market_regime in ("CRISIS", "MELTDOWN") or crisis_score > 0.45:
            self.spike_armed = False
            self.coil_ready = False
            return None

        # Estimate rolling 12-bar realized vol history over 168 bars to compute median
        step = 6
        vols = []
        for offset in range(0, self.vol_period, step):
            sub_closes = closes[-(offset + 13): len(closes) - offset]
            v = self._realized_vol(sub_closes, window=12)
            if v > 0:
                vols.append(v)

        if len(vols) < 15:
            return None

        med_vol = self._median(vols)
        current_vol = self._realized_vol(closes, window=12)

        if med_vol <= 0:
            return None

        # State machine: Vol Spike -> Vol Collapse to Compression -> Breakout Release
        if current_vol > med_vol * self.spike_threshold:
            self.spike_armed = True
            self.coil_ready = False
            self.bars_since_coil = 0

        elif self.spike_armed and current_vol < med_vol:
            self.spike_armed = False
            self.coil_ready = True
            self.bars_since_coil = 0

        if self.coil_ready:
            self.bars_since_coil += 1
            if self.bars_since_coil > self.max_coil_window:
                self.coil_ready = False
                return None

            # Lookback channel excluding current bar
            breakout_high = max(highs[-self.channel_period - 1:-1])
            breakout_low = min(lows[-self.channel_period - 1:-1])
            cur_price = ctx.bar.close

            # Alignment with medium trend to improve directional alpha
            fast_ma = sum(closes[-12:]) / 12.0
            slow_ma = sum(closes[-48:]) / 48.0

            if cur_price > breakout_high and fast_ma >= slow_ma:
                self.coil_ready = False
                return ctx.signal(
                    "long",
                    confidence=0.75,
                    stop_loss_bps=self.METADATA["declared_sl_bps"],
                    take_profit_bps=self.METADATA["declared_tp_bps"],
                    horizon_seconds=self.METADATA["declared_hold_seconds"],
                    metadata={
                        "reason": "coil_breakout_long",
                        "price": cur_price,
                        "breakout_high": breakout_high,
                        "vol_ratio": round(current_vol / med_vol, 3),
                        "bars_coiled": self.bars_since_coil,
                    }
                )

            elif cur_price < breakout_low and fast_ma <= slow_ma:
                self.coil_ready = False
                return ctx.signal(
                    "short",
                    confidence=0.75,
                    stop_loss_bps=self.METADATA["declared_sl_bps"],
                    take_profit_bps=self.METADATA["declared_tp_bps"],
                    horizon_seconds=self.METADATA["declared_hold_seconds"],
                    metadata={
                        "reason": "coil_breakout_short",
                        "price": cur_price,
                        "breakout_low": breakout_low,
                        "vol_ratio": round(current_vol / med_vol, 3),
                        "bars_coiled": self.bars_since_coil,
                    }
                )

        return None