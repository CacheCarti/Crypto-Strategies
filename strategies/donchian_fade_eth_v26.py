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
        self.period = 20
        self.min_wick_ratio = 0.25
        self.max_hold_bars = 6
        self.entry_bar = 0
        self.last_exit_bar = -10
        self.cooldown_bars = 4

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
        total_bars = self.period + 15
        closes = ctx.closes(total_bars)
        highs = ctx.highs(total_bars)
        lows = ctx.lows(total_bars)

        if len(closes) < total_bars:
            return None

        # Position time-based management
        if ctx.has_position():
            bars_held = ctx.bar_index - self.entry_bar
            if bars_held >= self.max_hold_bars:
                return ctx.signal("flat", confidence=0.5, metadata={
                    "reason": "max_bars_time_exit",
                    "bars_held": bars_held,
                    "close": ctx.bar.close
                })
            return None

        # Cooldown check
        if (ctx.bar_index - self.last_exit_bar) < self.cooldown_bars:
            return None

        # Market regime filter: avoid fading traps during crisis / meltdown
        regime = ctx.market.get("regime", "NORMAL")
        if regime in ("CRISIS", "MELTDOWN"):
            return None

        atr = self._atr(highs, lows, closes, 14)
        if atr is None or atr <= 0:
            return None

        current_high = ctx.bar.high
        current_low = ctx.bar.low
        current_close = ctx.bar.close
        current_open = ctx.bar.open

        # Previous 20 bars range (excluding current bar)
        prev_highs = highs[-(self.period + 1):-1]
        prev_lows = lows[-(self.period + 1):-1]
        prior_max_high = max(prev_highs)
        prior_min_low = min(prev_lows)

        bar_range = current_high - current_low
        if bar_range <= 0:
            return None

        min_wick_dist = atr * 0.15

        # Bull Trap / Failed Upside Breakout -> Go Short
        # Poked above prior 20-bar high, but closed back below it, with a substantial upper wick
        upper_wick = current_high - max(current_open, current_close)
        poke_high = current_high - prior_max_high

        if poke_high >= min_wick_dist and current_close < prior_max_high:
            if (upper_wick / bar_range) >= self.min_wick_ratio:
                # Dynamic stop-loss based on distance beyond the wick high
                wick_risk_bps = ((current_high - current_close) / current_close) * 10000.0
                sl_bps = max(150.0, min(300.0, wick_risk_bps + 30.0))
                tp_bps = max(220.0, sl_bps * 1.5)

                self.entry_bar = ctx.bar_index
                return ctx.signal(
                    "short",
                    confidence=0.75,
                    stop_loss_bps=sl_bps,
                    take_profit_bps=tp_bps,
                    horizon_seconds=self.METADATA["declared_hold_seconds"],
                    metadata={
                        "reason": "donchian_bull_trap_fade",
                        "prior_high": prior_max_high,
                        "bar_high": current_high,
                        "close": current_close,
                        "poke_high": poke_high,
                        "atr": atr
                    }
                )

        # Bear Trap / Failed Downside Breakout -> Go Long
        # Poked below prior 20-bar low, but closed back above it, with a substantial lower wick
        lower_wick = min(current_open, current_close) - current_low
        poke_low = prior_min_low - current_low

        if poke_low >= min_wick_dist and current_close > prior_min_low:
            if (lower_wick / bar_range) >= self.min_wick_ratio:
                wick_risk_bps = ((current_close - current_low) / current_close) * 10000.0
                sl_bps = max(150.0, min(300.0, wick_risk_bps + 30.0))
                tp_bps = max(220.0, sl_bps * 1.5)

                self.entry_bar = ctx.bar_index
                return ctx.signal(
                    "long",
                    confidence=0.75,
                    stop_loss_bps=sl_bps,
                    take_profit_bps=tp_bps,
                    horizon_seconds=self.METADATA["declared_hold_seconds"],
                    metadata={
                        "reason": "donchian_bear_trap_fade",
                        "prior_low": prior_min_low,
                        "bar_low": current_low,
                        "close": current_close,
                        "poke_low": poke_low,
                        "atr": atr
                    }
                )

        return None