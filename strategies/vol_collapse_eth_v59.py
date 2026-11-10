from domains.strategy_contract import Strategy, BarContext, Signal
from typing import Optional, Dict, Any
import math

class PostSpikeCoilBreakout(Strategy):
    METADATA = {
        "name": "PostSpikeCoilBreakout",
        "domain": "eth_usdc",
        "declared_sl_bps": 280.0,
        "declared_tp_bps": 560.0,
        "declared_hold_seconds": 21600,  # ~6 hours
        "warmup_bars": 180,
    }

    def initialize(self, ctx: BarContext) -> None:
        self.vol_window = 168
        self.channel_len = 12
        self.spike_state = False
        self.coiled_bars = 0
        self.cooldown_until = 0
        self.max_coil_duration = 36
        self.min_cooldown = 8

    def _realized_vol(self, closes: list, window: int = 12) -> float:
        if len(closes) < window + 1:
            return 0.0
        rets = [abs((closes[i] - closes[i - 1]) / closes[i - 1]) for i in range(len(closes) - window, len(closes))]
        return sum(rets) / len(rets)

    def on_position_close(self, ctx: BarContext, position: Dict[str, Any]) -> None:
        self.cooldown_until = ctx.bar_index + self.min_cooldown
        self.spike_state = False
        self.coiled_bars = 0

    def on_bar(self, ctx: BarContext) -> Optional[Signal]:
        closes = ctx.closes(self.vol_window + 15)
        if len(closes) < self.vol_window + 12:
            return None

        # Check ongoing positions: exit on tight counter-reversal or trailing horizon
        if ctx.has_position():
            pos_dir = ctx.position_direction()
            recent_low = min(ctx.lows(6)[:-1]) if len(ctx.lows(6)) >= 6 else None
            recent_high = max(ctx.highs(6)[:-1]) if len(ctx.highs(6)) >= 6 else None
            
            if pos_dir == "long" and recent_low and ctx.bar.close < recent_low:
                return ctx.signal("flat", confidence=0.6, metadata={"reason": "exit_channel_fail_long", "price": ctx.bar.close})
            elif pos_dir == "short" and recent_high and ctx.bar.close > recent_high:
                return ctx.signal("flat", confidence=0.6, metadata={"reason": "exit_channel_fail_short", "price": ctx.bar.close})
            return None

        if ctx.bar_index < self.cooldown_until:
            return None

        # Calculate rolling short-term realized volatility (12-bar window)
        current_rv = self._realized_vol(closes, 12)
        
        # Sample historical short-term volatilities across the 168-bar baseline
        hist_rvs = []
        step = 6
        for offset in range(12, self.vol_window + 1, step):
            sub_closes = closes[-(offset + 12):-offset]
            if len(sub_closes) >= 13:
                hist_rvs.append(self._realized_vol(sub_closes, 12))

        if len(hist_rvs) < 10:
            return None

        hist_rvs.sort()
        med_rv = hist_rvs[len(hist_rvs) // 2]
        p85_rv = hist_rvs[int(len(hist_rvs) * 0.85)]

        if med_rv <= 1e-6:
            return None

        # State machine: detect spike into p85/2x median, then transition to coil below median
        if current_rv >= max(p85_rv, 1.8 * med_rv):
            self.spike_state = True
            self.coiled_bars = 0
        elif self.spike_state:
            if current_rv <= med_rv:
                self.coiled_bars += 1
            else:
                self.coiled_bars = 0

            # Coil timeout
            if self.coiled_bars > self.max_coil_duration:
                self.spike_state = False
                self.coiled_bars = 0

        # Trigger release on Donchian 12-bar breakout during confirmed coil state (at least 2 bars of compression)
        if self.spike_state and self.coiled_bars >= 2:
            highs_12 = ctx.highs(self.channel_len + 1)[:-1]
            lows_12 = ctx.lows(self.channel_len + 1)[:-1]
            if len(highs_12) < self.channel_len or len(lows_12) < self.channel_len:
                return None

            donchian_high = max(highs_12)
            donchian_low = min(lows_12)
            close = ctx.bar.close

            # Trend/regime sanity filter
            regime = ctx.market.get("regime", "NORMAL")
            if regime == "MELTDOWN":
                return None

            if close > donchian_high:
                self.spike_state = False
                self.coiled_bars = 0
                self.cooldown_until = ctx.bar_index + self.min_cooldown
                return ctx.signal(
                    "long",
                    confidence=0.75,
                    stop_loss_bps=self.METADATA["declared_sl_bps"],
                    take_profit_bps=self.METADATA["declared_tp_bps"],
                    horizon_seconds=self.METADATA["declared_hold_seconds"],
                    metadata={
                        "reason": "coil_breakout_long",
                        "current_rv": round(current_rv, 6),
                        "median_rv": round(med_rv, 6),
                        "donchian_high": round(donchian_high, 2),
                        "price": round(close, 2),
                    },
                )
            elif close < donchian_low:
                self.spike_state = False
                self.coiled_bars = 0
                self.cooldown_until = ctx.bar_index + self.min_cooldown
                return ctx.signal(
                    "short",
                    confidence=0.70,
                    stop_loss_bps=self.METADATA["declared_sl_bps"],
                    take_profit_bps=self.METADATA["declared_tp_bps"],
                    horizon_seconds=self.METADATA["declared_hold_seconds"],
                    metadata={
                        "reason": "coil_breakout_short",
                        "current_rv": round(current_rv, 6),
                        "median_rv": round(med_rv, 6),
                        "donchian_low": round(donchian_low, 2),
                        "price": round(close, 2),
                    },
                )

        return None