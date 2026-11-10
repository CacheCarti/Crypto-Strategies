from domains.strategy_contract import Strategy, BarContext, Signal
from typing import Optional, Dict, Any
import math

class DonchianTrapFade(Strategy):
    METADATA = {
        "name": "DonchianTrapFade",
        "domain": "eth_usdc",
        "declared_sl_bps": 220.0,
        "declared_tp_bps": 340.0,
        "declared_hold_seconds": 18000,
        "warmup_bars": 35,
    }

    def initialize(self, ctx: BarContext) -> None:
        self.lookback = 24
        self.atr_period = 14
        self.cooldown_bars = 6
        self.max_hold_bars = 8
        self.entry_bar_idx = -100
        self.last_exit_bar_idx = -100

    def _atr(self, highs, lows, closes, period=14):
        if len(closes) < period + 1:
            return None
        trs = []
        for i in range(1, len(closes)):
            tr = max(highs[i] - lows[i], abs(highs[i] - closes[i - 1]), abs(lows[i] - closes[i - 1]))
            trs.append(tr)
        return sum(trs[-period:]) / period

    def on_position_close(self, ctx: BarContext, position: Dict[str, Any]) -> None:
        self.last_exit_bar_idx = ctx.bar_index

    def on_bar(self, ctx: BarContext) -> Optional[Signal]:
        closes = ctx.closes(self.lookback + self.atr_period + 2)
        if len(closes) < self.lookback + self.atr_period:
            return None

        highs = ctx.highs(self.lookback + self.atr_period + 2)
        lows = ctx.lows(self.lookback + self.atr_period + 2)

        curr_bar = ctx.bar
        atr = self._atr(highs, lows, closes, self.atr_period)
        if atr is None or atr <= 0.0:
            return None

        # Manage open position via time-based horizon
        if ctx.has_position():
            bars_held = ctx.bar_index - self.entry_bar_idx
            if bars_held >= self.max_hold_bars:
                return ctx.signal(
                    "flat",
                    confidence=0.6,
                    metadata={"reason": "time_based_exit", "bars_held": bars_held, "price": curr_bar.close}
                )
            return None

        # Mandatory post-exit cooldown
        if (ctx.bar_index - self.last_exit_bar_idx) < self.cooldown_bars:
            return None

        # Establish reference Donchian range excluding current bar
        prev_highs = highs[-self.lookback - 1 : -1]
        prev_lows = lows[-self.lookback - 1 : -1]
        prior_channel_high = max(prev_highs)
        prior_channel_low = min(prev_lows)

        min_wick_poke = 0.35 * atr
        crisis_score = ctx.market.get("crisis_score", 0.0)
        regime = ctx.market.get("regime", "NORMAL")
        if crisis_score > 0.65 or regime == "MELTDOWN":
            return None

        # Bull trap: Poked cleanly above prior channel high, but failed and closed back inside lower half of bar
        if curr_bar.high > prior_channel_high + min_wick_poke:
            if curr_bar.close < prior_channel_high and curr_bar.close <= (curr_bar.open + curr_bar.high) / 2.0:
                wick_size = curr_bar.high - max(curr_bar.close, curr_bar.open)
                bar_range = curr_bar.high - curr_bar.low
                if bar_range > 0 and (wick_size / bar_range) >= 0.40:
                    sl_dist_bps = ((curr_bar.high - curr_bar.close) / curr_bar.close) * 10000.0 + 30.0
                    sl_bps = max(130.0, min(260.0, sl_dist_bps))
                    tp_bps = sl_bps * 1.5

                    self.entry_bar_idx = ctx.bar_index
                    return ctx.signal(
                        "short",
                        confidence=0.75,
                        stop_loss_bps=sl_bps,
                        take_profit_bps=tp_bps,
                        metadata={
                            "reason": "bull_trap_rejection",
                            "prior_high": prior_channel_high,
                            "poke_amt": curr_bar.high - prior_channel_high,
                            "atr": atr,
                            "wick_ratio": wick_size / bar_range,
                        }
                    )

        # Bear trap: Poked cleanly below prior channel low, but rejected and closed back inside upper half of bar
        if curr_bar.low < prior_channel_low - min_wick_poke:
            if curr_bar.close > prior_channel_low and curr_bar.close >= (curr_bar.open + curr_bar.low) / 2.0:
                wick_size = min(curr_bar.close, curr_bar.open) - curr_bar.low
                bar_range = curr_bar.high - curr_bar.low
                if bar_range > 0 and (wick_size / bar_range) >= 0.40:
                    sl_dist_bps = ((curr_bar.close - curr_bar.low) / curr_bar.close) * 10000.0 + 30.0
                    sl_bps = max(130.0, min(260.0, sl_dist_bps))
                    tp_bps = sl_bps * 1.5

                    self.entry_bar_idx = ctx.bar_index
                    return ctx.signal(
                        "long",
                        confidence=0.75,
                        stop_loss_bps=sl_bps,
                        take_profit_bps=tp_bps,
                        metadata={
                            "reason": "bear_trap_rejection",
                            "prior_low": prior_channel_low,
                            "poke_amt": prior_channel_low - curr_bar.low,
                            "atr": atr,
                            "wick_ratio": wick_size / bar_range,
                        }
                    )

        return None