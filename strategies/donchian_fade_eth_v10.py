from domains.strategy_contract import Strategy, BarContext, Signal
from typing import Optional, Dict, Any
import math

class EthFailedBreakoutFade(Strategy):
    METADATA = {
        "name": "EthFailedBreakoutFade",
        "domain": "eth_usdc",
        "declared_sl_bps": 220.0,
        "declared_tp_bps": 340.0,
        "declared_hold_seconds": 18000,
        "warmup_bars": 35,
    }

    def initialize(self, ctx: BarContext) -> None:
        self.period = 20
        self.max_hold_bars = 6
        self.cooldown_bars = 5
        self.entry_bar = -1
        self.last_exit_bar = -self.cooldown_bars
        self.wick_atr_mult = 0.35

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
        self.entry_bar = -1

    def on_bar(self, ctx: BarContext) -> Optional[Signal]:
        closes = ctx.closes(self.period + 15)
        highs = ctx.highs(self.period + 15)
        lows = ctx.lows(self.period + 15)

        if len(closes) < self.period + 1:
            return None

        regime = ctx.market.get("regime", "NORMAL")
        if regime in ("CRISIS", "MELTDOWN"):
            if ctx.has_position():
                return ctx.signal("flat", confidence=0.8, metadata={"reason": "crisis_exit", "regime": regime})
            return None

        # Manage open position via bar horizon
        if ctx.has_position():
            bars_held = ctx.bar_index - self.entry_bar if self.entry_bar > 0 else 0
            if bars_held >= self.max_hold_bars:
                return ctx.signal("flat", confidence=0.5, metadata={
                    "reason": "max_bars_reached",
                    "bars_held": bars_held,
                    "close": ctx.bar.close
                })
            return None

        # Mandatory cooldown guard
        if (ctx.bar_index - self.last_exit_bar) < self.cooldown_bars:
            return None

        atr = self._atr(highs, lows, closes, 14)
        if atr is None or atr <= 0:
            return None

        # Exclude the current forming/closing bar from the historical range
        hist_highs = highs[-(self.period + 1):-1]
        hist_lows = lows[-(self.period + 1):-1]
        prior_max_high = max(hist_highs)
        prior_min_low = min(hist_lows)

        current_high = ctx.bar.high
        current_low = ctx.bar.low
        current_close = ctx.bar.close
        min_penetration = atr * self.wick_atr_mult

        # Bear Trap / Bullish Liquidity Sweep Fade:
        # Poked strictly below the 20-bar low by at least min_penetration, but closed back above the level
        bullish_sweep = (current_low <= (prior_min_low - min_penetration)) and (current_close > prior_min_low)

        # Bull Trap / Bearish Liquidity Sweep Fade:
        # Poked strictly above the 20-bar high by at least min_penetration, but closed back below the level
        bearish_sweep = (current_high >= (prior_max_high + min_penetration)) and (current_close < prior_max_high)

        if bullish_sweep and not bearish_sweep:
            sl_dist = current_close - current_low
            sl_bps = max(120.0, min(350.0, (sl_dist / current_close) * 10000.0 + 30.0))
            tp_bps = sl_bps * 1.5
            self.entry_bar = ctx.bar_index
            return ctx.signal(
                "long",
                confidence=0.75,
                stop_loss_bps=sl_bps,
                take_profit_bps=tp_bps,
                horizon_seconds=self.METADATA["declared_hold_seconds"],
                metadata={
                    "reason": "bear_trap_sweep_fade",
                    "prior_low": prior_min_low,
                    "bar_low": current_low,
                    "close": current_close,
                    "atr": atr,
                    "sl_bps": sl_bps
                }
            )

        if bearish_sweep and not bullish_sweep:
            sl_dist = current_high - current_close
            sl_bps = max(120.0, min(350.0, (sl_dist / current_close) * 10000.0 + 30.0))
            tp_bps = sl_bps * 1.5
            self.entry_bar = ctx.bar_index
            return ctx.signal(
                "short",
                confidence=0.75,
                stop_loss_bps=sl_bps,
                take_profit_bps=tp_bps,
                horizon_seconds=self.METADATA["declared_hold_seconds"],
                metadata={
                    "reason": "bull_trap_sweep_fade",
                    "prior_high": prior_max_high,
                    "bar_high": current_high,
                    "close": current_close,
                    "atr": atr,
                    "sl_bps": sl_bps
                }
            )

        return None