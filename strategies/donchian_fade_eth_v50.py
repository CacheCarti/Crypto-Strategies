from domains.strategy_contract import Strategy, BarContext, Signal
from typing import Optional, Dict, Any
import math

class FailedBreakoutFade(Strategy):
    METADATA = {
        "name": "FailedBreakoutFade",
        "domain": "eth_usdc",
        "declared_sl_bps": 220.0,
        "declared_tp_bps": 340.0,
        "declared_hold_seconds": 14400,
        "warmup_bars": 35,
    }

    def initialize(self, ctx: BarContext) -> None:
        self.channel_period = 20
        self.atr_period = 14
        self.cooldown_bars = 6
        self.max_hold_bars = 5
        self.last_trade_bar = -999
        self.entry_bar = -999

    def _atr(self, highs, lows, closes, period=14):
        if len(closes) < period + 1:
            return None
        trs = []
        for i in range(1, len(closes)):
            tr = max(highs[i] - lows[i], abs(highs[i] - closes[i - 1]), abs(lows[i] - closes[i - 1]))
            trs.append(tr)
        return sum(trs[-period:]) / period

    def on_position_close(self, ctx: BarContext, position: Dict[str, Any]) -> None:
        self.last_trade_bar = ctx.bar_index

    def on_bar(self, ctx: BarContext) -> Optional[Signal]:
        total_needed = max(self.channel_period, self.atr_period) + 2
        closes = ctx.closes(total_needed)
        if len(closes) < total_needed:
            return None

        highs = ctx.highs(total_needed)
        lows = ctx.lows(total_needed)

        # Time-based exit check
        if ctx.has_position():
            bars_in_trade = ctx.bar_index - self.entry_bar
            if bars_in_trade >= self.max_hold_bars:
                self.last_trade_bar = ctx.bar_index
                return ctx.signal(
                    "flat",
                    confidence=0.5,
                    metadata={"reason": "time_stop_exit", "bars_held": bars_in_trade, "close": ctx.bar.close}
                )
            return None

        # Cooldown guard
        if ctx.bar_index - self.last_trade_bar < self.cooldown_bars:
            return None

        # Regime safety
        regime = ctx.market.get("regime", "NORMAL")
        crisis_score = ctx.market.get("crisis_score", 0.0)
        if regime in ("CRISIS", "MELTDOWN") or crisis_score > 0.65:
            return None

        atr = self._atr(highs, lows, closes, self.atr_period)
        if atr is None or atr <= 0:
            return None

        # Reference Donchian range prior to current bar
        prev_highs = highs[-(self.channel_period + 1):-1]
        prev_lows = lows[-(self.channel_period + 1):-1]
        range_high = max(prev_highs)
        range_low = min(prev_lows)

        curr_high = ctx.bar.high
        curr_low = ctx.bar.low
        curr_close = ctx.bar.close

        # Minimum required wick extension beyond range to confirm stop-run
        min_penetration = 0.25 * atr

        # Bull Trap / Long Liquidation Poke -> Fade Short
        bull_trap = (curr_high >= range_high + min_penetration) and (curr_close < range_high)
        if bull_trap:
            wick_size = curr_high - max(curr_close, ctx.bar.open)
            body_size = abs(curr_close - ctx.bar.open)
            if wick_size > body_size * 0.8:
                stop_bps = min(max((curr_high - curr_close) / curr_close * 10000.0 + 20.0, 100.0), 300.0)
                tp_bps = stop_bps * 1.5
                self.entry_bar = ctx.bar_index
                self.last_trade_bar = ctx.bar_index
                return ctx.signal(
                    "short",
                    confidence=0.75,
                    stop_loss_bps=stop_bps,
                    take_profit_bps=tp_bps,
                    horizon_seconds=14400,
                    metadata={
                        "reason": "donchian_bull_trap_fade",
                        "range_high": range_high,
                        "curr_high": curr_high,
                        "close": curr_close,
                        "atr": atr,
                        "penetration_atr": (curr_high - range_high) / atr
                    }
                )

        # Bear Trap / Short Squeeze Poke -> Fade Long
        bear_trap = (curr_low <= range_low - min_penetration) and (curr_close > range_low)
        if bear_trap:
            wick_size = min(curr_close, ctx.bar.open) - curr_low
            body_size = abs(curr_close - ctx.bar.open)
            if wick_size > body_size * 0.8:
                stop_bps = min(max((curr_close - curr_low) / curr_close * 10000.0 + 20.0, 100.0), 300.0)
                tp_bps = stop_bps * 1.5
                self.entry_bar = ctx.bar_index
                self.last_trade_bar = ctx.bar_index
                return ctx.signal(
                    "long",
                    confidence=0.75,
                    stop_loss_bps=stop_bps,
                    take_profit_bps=tp_bps,
                    horizon_seconds=14400,
                    metadata={
                        "reason": "donchian_bear_trap_fade",
                        "range_low": range_low,
                        "curr_low": curr_low,
                        "close": curr_close,
                        "atr": atr,
                        "penetration_atr": (range_low - curr_low) / atr
                    }
                )

        return None