from domains.strategy_contract import Strategy, BarContext, Signal
from typing import Optional, Dict, Any
import math

class DonchianTrapFade(Strategy):
    METADATA = {
        "name": "DonchianTrapFade",
        "domain": "eth_usdc",
        "declared_sl_bps": 220.0,
        "declared_tp_bps": 380.0,
        "declared_hold_seconds": 18000,  # ~5 hours target hold
        "warmup_bars": 35,
    }

    def initialize(self, ctx: BarContext) -> None:
        self.lookback = 20
        self.atr_period = 14
        self.wick_atr_mult = 0.25
        self.max_hold_bars = 6
        self.cooldown_bars = 10
        self.last_exit_bar = -999
        self.entry_bar = -999

    def _atr(self, highs, lows, closes, period):
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
        closes = ctx.closes(self.lookback + self.atr_period + 5)
        highs = ctx.highs(self.lookback + self.atr_period + 5)
        lows = ctx.lows(self.lookback + self.atr_period + 5)

        if len(closes) < self.lookback + self.atr_period + 2:
            return None

        # Position management: time-based exit to prevent bagholding failed retests
        if ctx.has_position():
            bars_held = ctx.bar_index - self.entry_bar
            if bars_held >= self.max_hold_bars:
                self.last_exit_bar = ctx.bar_index
                return ctx.signal(
                    "flat",
                    confidence=0.6,
                    metadata={"reason": "max_bars_held_reached", "bars_held": bars_held, "close": ctx.bar.close}
                )
            return None

        # Cooldown guard: enforce strict spacing after an exit
        if (ctx.bar_index - self.last_exit_bar) < self.cooldown_bars:
            return None

        # Regime safety: avoid fading during severe market stress
        regime = ctx.market.get("regime", "NORMAL")
        if regime in ("CRISIS", "MELTDOWN"):
            return None

        atr = self._atr(highs, lows, closes, self.atr_period)
        if atr is None or atr <= 0:
            return None

        # Prior 20-bar boundaries (excluding the current bar)
        prior_highs = highs[-self.lookback - 1:-1]
        prior_lows = lows[-self.lookback - 1:-1]
        range_high = max(prior_highs)
        range_low = min(prior_lows)

        current_high = ctx.bar.high
        current_low = ctx.bar.low
        current_close = ctx.bar.close
        min_penetration = atr * self.wick_atr_mult

        # Bear Trap / Liquidity Sweep Long:
        # Intrabar poke below prior low with a clear wick, but closed back firmly inside
        if current_low < (range_low - min_penetration) and current_close > range_low:
            # Dynamic SL based on the wick invalidation point
            dist = max(current_close - current_low, atr * 0.5)
            sl_bps = min(max((dist / current_close) * 10000.0 * 1.1, 140.0), 300.0)
            tp_bps = sl_bps * 1.8

            self.entry_bar = ctx.bar_index
            return ctx.signal(
                "long",
                confidence=0.75,
                stop_loss_bps=sl_bps,
                take_profit_bps=tp_bps,
                horizon_seconds=21600,
                metadata={
                    "reason": "donchian_bear_trap_sweep_reclaim",
                    "range_low": range_low,
                    "wick_low": current_low,
                    "close": current_close,
                    "atr": atr,
                    "sl_bps": sl_bps
                }
            )

        # Bull Trap / Liquidity Sweep Short:
        # Intrabar poke above prior high with a clear wick, but closed back firmly inside
        if current_high > (range_high + min_penetration) and current_close < range_high:
            # Dynamic SL based on the wick invalidation point
            dist = max(current_high - current_close, atr * 0.5)
            sl_bps = min(max((dist / current_close) * 10000.0 * 1.1, 140.0), 300.0)
            tp_bps = sl_bps * 1.8

            self.entry_bar = ctx.bar_index
            return ctx.signal(
                "short",
                confidence=0.75,
                stop_loss_bps=sl_bps,
                take_profit_bps=tp_bps,
                horizon_seconds=21600,
                metadata={
                    "reason": "donchian_bull_trap_sweep_rejection",
                    "range_high": range_high,
                    "wick_high": current_high,
                    "close": current_close,
                    "atr": atr,
                    "sl_bps": sl_bps
                }
            )

        return None