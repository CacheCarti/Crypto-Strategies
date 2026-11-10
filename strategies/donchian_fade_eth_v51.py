from domains.strategy_contract import Strategy, BarContext, Signal
from typing import Optional, Dict, Any
import math

class DonchianFadeTrap(Strategy):
    METADATA = {
        "name": "Donchian Trap Reversal",
        "domain": "eth_usdc",
        "declared_sl_bps": 220.0,
        "declared_tp_bps": 340.0,
        "declared_hold_seconds": 18000,  # 5 hours
        "warmup_bars": 35,
    }

    def initialize(self, ctx: BarContext) -> None:
        self.lookback = 20
        self.atr_period = 14
        self.max_hold_bars = 5
        self.cooldown_bars = 4
        self.last_exit_bar = -999
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
        self.last_exit_bar = ctx.bar_index

    def on_bar(self, ctx: BarContext) -> Optional[Signal]:
        n_needed = max(self.lookback, self.atr_period) + 2
        closes = ctx.closes(n_needed)
        if len(closes) < n_needed:
            return None

        highs = ctx.highs(n_needed)
        lows = ctx.lows(n_needed)
        bar = ctx.bar

        # Time-based exit for open positions
        if ctx.has_position():
            bars_in_trade = ctx.bar_index - self.entry_bar
            if bars_in_trade >= self.max_hold_bars:
                return ctx.signal(
                    "flat",
                    confidence=0.5,
                    metadata={"reason": "time_horizon_reached", "bars_held": bars_in_trade}
                )
            return None

        # Cooldown guard after trade exit
        if ctx.bar_index - self.last_exit_bar < self.cooldown_bars:
            return None

        atr = self._atr(highs, lows, closes, self.atr_period)
        if atr is None or atr <= 0:
            return None

        # Prior 20-bar Donchian Channel (strictly excluding current bar)
        prior_highs = highs[-(self.lookback + 1):-1]
        prior_lows = lows[-(self.lookback + 1):-1]
        prev_range_high = max(prior_highs)
        prev_range_low = min(prior_lows)

        # Trap criteria:
        # Intrabar wick breached the boundary by at least 0.25 ATR (clean stop-run)
        # Close falls back strictly inside the range
        min_wick = 0.25 * atr

        # Bull trap: Wick > prev_high + min_wick, but Close < prev_high -> Go Short
        is_bull_trap = (bar.high >= prev_range_high + min_wick) and (bar.close < prev_range_high)

        # Bear trap: Wick < prev_low - min_wick, but Close > prev_low -> Go Long
        is_bear_trap = (bar.low <= prev_range_low - min_wick) and (bar.close > prev_range_low)

        if is_bull_trap:
            # Dynamic stop just above the rejection wick
            wick_distance = bar.high - bar.close
            stop_bps = max(120.0, min(300.0, (wick_distance / bar.close) * 10000.0 + 30.0))
            take_bps = stop_bps * 1.5

            self.entry_bar = ctx.bar_index
            return ctx.signal(
                "short",
                confidence=0.75,
                stop_loss_bps=round(stop_bps, 1),
                take_profit_bps=round(take_bps, 1),
                horizon_seconds=self.METADATA["declared_hold_seconds"],
                metadata={
                    "reason": "donchian_bull_trap_reversal",
                    "prev_high": round(prev_range_high, 2),
                    "wick_high": round(bar.high, 2),
                    "close": round(bar.close, 2),
                    "atr": round(atr, 2),
                    "wick_atr_ratio": round((bar.high - prev_range_high) / atr, 2),
                }
            )

        if is_bear_trap:
            # Dynamic stop just below the rejection wick
            wick_distance = bar.close - bar.low
            stop_bps = max(120.0, min(300.0, (wick_distance / bar.close) * 10000.0 + 30.0))
            take_bps = stop_bps * 1.5

            self.entry_bar = ctx.bar_index
            return ctx.signal(
                "long",
                confidence=0.75,
                stop_loss_bps=round(stop_bps, 1),
                take_profit_bps=round(take_bps, 1),
                horizon_seconds=self.METADATA["declared_hold_seconds"],
                metadata={
                    "reason": "donchian_bear_trap_reversal",
                    "prev_low": round(prev_range_low, 2),
                    "wick_low": round(bar.low, 2),
                    "close": round(bar.close, 2),
                    "atr": round(atr, 2),
                    "wick_atr_ratio": round((prev_range_low - bar.low) / atr, 2),
                }
            )

        return None