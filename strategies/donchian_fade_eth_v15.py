from domains.strategy_contract import Strategy, BarContext, Signal
from typing import Optional, Dict, Any
import math

class DonchianTrapReversal(Strategy):
    METADATA = {
        "name": "DonchianTrapReversal",
        "domain": "eth_usdc",
        "declared_sl_bps": 220.0,
        "declared_tp_bps": 440.0,
        "declared_hold_seconds": 14400,  # ~4 hours
        "warmup_bars": 35,
    }

    def initialize(self, ctx: BarContext) -> None:
        self.channel_period = 20
        self.cooldown_bars = 6
        self.last_exit_bar = -100
        self.min_wick_atr_mult = 0.5
        self.max_hold_bars = 5

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
        req_bars = self.channel_period + 16
        highs = ctx.highs(req_bars)
        lows = ctx.lows(req_bars)
        closes = ctx.closes(req_bars)

        if len(closes) < req_bars:
            return None

        # Check existing position time-based exit
        if ctx.has_position():
            pos = ctx.position
            # Horizon exit after max_hold_bars
            if ctx.bar_index - self.last_exit_bar > self.max_hold_bars and self.last_exit_bar > 0:
                return ctx.signal("flat", confidence=0.5, metadata={"reason": "time_stop_reached", "bars": self.max_hold_bars})
            return None

        # Post-exit cooldown gate to prevent overtrading
        if ctx.bar_index - self.last_exit_bar < self.cooldown_bars:
            return None

        # Avoid extreme crises where stop-runs continue indefinitely
        crisis = ctx.market.get("crisis_score", 0.0)
        if crisis > 0.65:
            return None

        atr = self._atr(highs, lows, closes, 14)
        if atr is None or atr <= 0:
            return None

        # Prior 20-bar Donchian levels (excluding current active bar)
        prior_highs = highs[-(self.channel_period + 1):-1]
        prior_lows = lows[-(self.channel_period + 1):-1]
        d_high = max(prior_highs)
        d_low = min(prior_lows)

        curr_high = ctx.bar.high
        curr_low = ctx.bar.low
        curr_close = ctx.bar.close

        min_wick = self.min_wick_atr_mult * atr

        # Bull trap (poke above 20-bar high, close strictly back below it with a meaningful wick)
        bull_trap = (curr_high > d_high + min_wick) and (curr_close < d_high)

        # Bear trap (poke below 20-bar low, close strictly back above it with a meaningful wick)
        bear_trap = (curr_low < d_low - min_wick) and (curr_close > d_low)

        if bull_trap and not bear_trap:
            wick_size = curr_high - max(curr_close, d_high)
            sl_bps = max(100.0, min(320.0, (wick_size / curr_close) * 10000.0 * 1.25))
            tp_bps = sl_bps * 2.0
            conf = min(0.9, 0.5 + (wick_size / atr) * 0.2)

            return ctx.signal(
                "short",
                confidence=conf,
                stop_loss_bps=sl_bps,
                take_profit_bps=tp_bps,
                horizon_seconds=self.METADATA["declared_hold_seconds"],
                metadata={
                    "reason": "donchian_bull_trap_rejection",
                    "donchian_high": d_high,
                    "wick_atr_ratio": round(wick_size / atr, 2),
                    "atr": round(atr, 2),
                    "price": curr_close
                }
            )

        if bear_trap and not bull_trap:
            wick_size = min(curr_close, d_low) - curr_low
            sl_bps = max(100.0, min(320.0, (wick_size / curr_close) * 10000.0 * 1.25))
            tp_bps = sl_bps * 2.0
            conf = min(0.9, 0.5 + (wick_size / atr) * 0.2)

            return ctx.signal(
                "long",
                confidence=conf,
                stop_loss_bps=sl_bps,
                take_profit_bps=tp_bps,
                horizon_seconds=self.METADATA["declared_hold_seconds"],
                metadata={
                    "reason": "donchian_bear_trap_rejection",
                    "donchian_low": d_low,
                    "wick_atr_ratio": round(wick_size / atr, 2),
                    "atr": round(atr, 2),
                    "price": curr_close
                }
            )

        return None