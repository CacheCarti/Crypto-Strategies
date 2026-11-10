from domains.strategy_contract import Strategy, BarContext, Signal
from typing import Optional, Dict, Any
import math

class DonchianTrapFade(Strategy):
    METADATA = {
        "name": "Donchian Trap Fade",
        "domain": "eth_usdc",
        "declared_sl_bps": 220.0,
        "declared_tp_bps": 340.0,
        "declared_hold_seconds": 18000,  # ~5 hours
        "warmup_bars": 35,
    }

    def initialize(self, ctx: BarContext) -> None:
        self.lookback = 20
        self.atr_period = 14
        self.cooldown_bars = 4
        self.max_hold_bars = 6
        self.min_wick_atr_mult = 0.35

        self.last_exit_bar = -999
        self.entry_bar = -999
        self.entry_wick_price = 0.0

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
        self.entry_bar = -999
        self.entry_wick_price = 0.0

    def on_bar(self, ctx: BarContext) -> Optional[Signal]:
        total_needed = max(self.lookback + 2, self.atr_period + 2)
        closes = ctx.closes(total_needed)
        if len(closes) < total_needed:
            return None

        highs = ctx.highs(total_needed)
        lows = ctx.lows(total_needed)

        current_bar = ctx.bar
        current_close = current_bar.close
        current_high = current_bar.high
        current_low = current_bar.low

        atr = self._atr(highs, lows, closes, self.atr_period)
        if atr is None or atr <= 0:
            return None

        # Position Management
        if ctx.has_position():
            direction = ctx.position_direction()
            bars_in_trade = ctx.bar_index - self.entry_bar if self.entry_bar > 0 else 0

            # Time-based decay exit to free capital and prevent prolonged drawdowns
            if bars_in_trade >= self.max_hold_bars:
                return ctx.signal(
                    "flat",
                    confidence=0.6,
                    metadata={"reason": "time_stop_max_bars", "bars_held": bars_in_trade}
                )

            # Invalidation exit: candle close beyond the initial trap wick
            if direction == "short" and self.entry_wick_price > 0 and current_close > self.entry_wick_price:
                return ctx.signal(
                    "flat",
                    confidence=0.8,
                    metadata={"reason": "wick_invalidation_short", "wick_price": self.entry_wick_price, "close": current_close}
                )

            if direction == "long" and self.entry_wick_price > 0 and current_close < self.entry_wick_price:
                return ctx.signal(
                    "flat",
                    confidence=0.8,
                    metadata={"reason": "wick_invalidation_long", "wick_price": self.entry_wick_price, "close": current_close}
                )

            return None

        # Mandatory Cooldown Gate
        if (ctx.bar_index - self.last_exit_bar) < self.cooldown_bars:
            return None

        # Donchian channel evaluated strictly over the prior 20 bars (excluding current bar)
        prior_highs = highs[-self.lookback - 1 : -1]
        prior_lows = lows[-self.lookback - 1 : -1]
        prior_max_high = max(prior_highs)
        prior_min_low = min(prior_lows)

        min_wick_size = atr * self.min_wick_atr_mult

        # Bull Trap / Liquidity Sweep High -> Fade Short
        # Poked strictly above prior 20-bar high, but closed firmly back below it with a meaningful wick
        if current_high > prior_max_high and current_close < prior_max_high:
            upper_wick = current_high - max(current_bar.open, current_close)
            if upper_wick >= min_wick_size:
                wick_bps = (upper_wick / current_close) * 10000.0
                sl_bps = max(120.0, min(300.0, wick_bps + 30.0))
                tp_bps = sl_bps * 1.5

                self.entry_bar = ctx.bar_index
                self.entry_wick_price = current_high

                return ctx.signal(
                    "short",
                    confidence=0.72,
                    stop_loss_bps=sl_bps,
                    take_profit_bps=tp_bps,
                    horizon_seconds=self.METADATA["declared_hold_seconds"],
                    metadata={
                        "reason": "donchian_bull_trap_fade",
                        "prior_max_high": prior_max_high,
                        "wick_high": current_high,
                        "upper_wick": upper_wick,
                        "atr": atr,
                        "close": current_close
                    }
                )

        # Bear Trap / Liquidity Sweep Low -> Fade Long
        # Poked strictly below prior 20-bar low, but closed firmly back above it with a meaningful wick
        if current_low < prior_min_low and current_close > prior_min_low:
            lower_wick = min(current_bar.open, current_close) - current_low
            if lower_wick >= min_wick_size:
                wick_bps = (lower_wick / current_close) * 10000.0
                sl_bps = max(120.0, min(300.0, wick_bps + 30.0))
                tp_bps = sl_bps * 1.5

                self.entry_bar = ctx.bar_index
                self.entry_wick_price = current_low

                return ctx.signal(
                    "long",
                    confidence=0.72,
                    stop_loss_bps=sl_bps,
                    take_profit_bps=tp_bps,
                    horizon_seconds=self.METADATA["declared_hold_seconds"],
                    metadata={
                        "reason": "donchian_bear_trap_fade",
                        "prior_min_low": prior_min_low,
                        "wick_low": current_low,
                        "lower_wick": lower_wick,
                        "atr": atr,
                        "close": current_close
                    }
                )

        return None