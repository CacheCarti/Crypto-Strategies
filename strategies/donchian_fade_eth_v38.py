from domains.strategy_contract import Strategy, BarContext, Signal
from typing import Optional, Dict, Any
import math

class DonchianTrapFade(Strategy):
    METADATA = {
        "name": "DonchianTrapFade",
        "domain": "eth_usdc",
        "declared_sl_bps": 220.0,
        "declared_tp_bps": 330.0,
        "declared_hold_seconds": 18000,  # ~5 hours average hold
        "warmup_bars": 35,
    }

    def initialize(self, ctx: BarContext) -> None:
        self.channel_period = 20
        self.atr_period = 14
        self.cooldown_bars = 8
        self.max_hold_bars = 6
        self.min_wick_atr_mult = 0.4
        
        self.last_exit_bar = -100
        self.entry_bar = -100

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

    def on_bar(self, ctx: BarContext) -> Optional[Signal]:
        warmup = max(self.channel_period, self.atr_period) + 2
        if ctx.bar_index < warmup:
            return None

        closes = ctx.closes(warmup)
        highs = ctx.highs(warmup)
        lows = ctx.lows(warmup)
        if len(closes) < warmup:
            return None

        curr_bar = ctx.bar
        curr_idx = ctx.bar_index

        # Time-based exit logic
        if ctx.has_position():
            bars_held = curr_idx - self.entry_bar
            if bars_held >= self.max_hold_bars:
                return ctx.signal(
                    "flat",
                    confidence=0.6,
                    metadata={"reason": "time_stop_exit", "bars_held": bars_held, "price": curr_bar.close}
                )
            return None

        # Mandatory cooldown guard
        if curr_idx - self.last_exit_bar < self.cooldown_bars:
            return None

        # Calculate Donchian boundaries based on PRIOR 20 bars (excluding current bar)
        prior_highs = highs[-self.channel_period - 1:-1]
        prior_lows = lows[-self.channel_period - 1:-1]
        channel_high = max(prior_highs)
        channel_low = min(prior_lows)

        atr = self._atr(highs, lows, closes, self.atr_period)
        if atr is None or atr <= 0:
            return None

        min_wick_dist = atr * self.min_wick_atr_mult
        midpoint = (channel_high + channel_low) / 2.0

        # Bull Trap / Short Setup:
        # High pierced the channel high by at least min_wick_dist, but closed back firmly inside (below channel high)
        if curr_bar.high >= channel_high + min_wick_dist and curr_bar.close < channel_high:
            # Rejection confirmation: close must be in bottom 65% of current bar's range
            bar_range = curr_bar.high - curr_bar.low
            if bar_range > 0 and (curr_bar.close - curr_bar.low) / bar_range <= 0.65:
                wick_bps = ((curr_bar.high - curr_bar.close) / curr_bar.close) * 10000.0
                sl_bps = max(100.0, min(wick_bps + 30.0, 250.0))
                # Target back toward range midpoint
                dist_to_mid = ((curr_bar.close - midpoint) / curr_bar.close) * 10000.0
                tp_bps = max(150.0, min(dist_to_mid, 350.0))

                self.entry_bar = curr_idx
                return ctx.signal(
                    "short",
                    confidence=0.75,
                    stop_loss_bps=sl_bps,
                    take_profit_bps=tp_bps,
                    horizon_seconds=18000,
                    metadata={
                        "reason": "donchian_bull_trap_fade",
                        "channel_high": channel_high,
                        "high": curr_bar.high,
                        "close": curr_bar.close,
                        "atr": atr,
                        "sl_bps": sl_bps,
                        "tp_bps": tp_bps
                    }
                )

        # Bear Trap / Long Setup:
        # Low pierced the channel low by at least min_wick_dist, but closed back firmly inside (above channel low)
        if curr_bar.low <= channel_low - min_wick_dist and curr_bar.close > channel_low:
            # Rejection confirmation: close must be in top 65% of current bar's range
            bar_range = curr_bar.high - curr_bar.low
            if bar_range > 0 and (curr_bar.close - curr_bar.low) / bar_range >= 0.35:
                wick_bps = ((curr_bar.close - curr_bar.low) / curr_bar.close) * 10000.0
                sl_bps = max(100.0, min(wick_bps + 30.0, 250.0))
                # Target back toward range midpoint
                dist_to_mid = ((midpoint - curr_bar.close) / curr_bar.close) * 10000.0
                tp_bps = max(150.0, min(dist_to_mid, 350.0))

                self.entry_bar = curr_idx
                return ctx.signal(
                    "long",
                    confidence=0.75,
                    stop_loss_bps=sl_bps,
                    take_profit_bps=tp_bps,
                    horizon_seconds=18000,
                    metadata={
                        "reason": "donchian_bear_trap_fade",
                        "channel_low": channel_low,
                        "low": curr_bar.low,
                        "close": curr_bar.close,
                        "atr": atr,
                        "sl_bps": sl_bps,
                        "tp_bps": tp_bps
                    }
                )

        return None