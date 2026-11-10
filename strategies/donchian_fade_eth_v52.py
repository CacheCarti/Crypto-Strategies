from domains.strategy_contract import Strategy, BarContext, Signal
from typing import Optional, Dict, Any
import math

class DonchianFadeTrap(Strategy):
    METADATA = {
        "name": "DonchianFadeTrap",
        "domain": "eth_usdc",
        "declared_sl_bps": 220.0,
        "declared_tp_bps": 340.0,
        "declared_hold_seconds": 18000,  # ~5 hours target hold
        "warmup_bars": 35,
    }

    def initialize(self, ctx: BarContext) -> None:
        self.lookback = 20
        self.atr_period = 14
        self.min_wick_atr_mult = 0.35  # Meaningful poke beyond channel
        self.cooldown_bars = 4
        self.last_exit_bar = -999
        self.bars_in_pos = 0
        self.max_hold_bars = 6

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
        self.bars_in_pos = 0

    def on_bar(self, ctx: BarContext) -> Optional[Signal]:
        n = self.lookback + 5
        closes = ctx.closes(n)
        highs = ctx.highs(n)
        lows = ctx.lows(n)

        if len(closes) < n:
            return None

        # Position tracking and time-based / mean-reversion exit
        if ctx.has_position():
            self.bars_in_pos += 1
            if self.bars_in_pos >= self.max_hold_bars:
                return ctx.signal(
                    "flat",
                    confidence=0.5,
                    metadata={"reason": "time_decay_exit", "bars_held": self.bars_in_pos}
                )
            return None
        else:
            self.bars_in_pos = 0

        # Mandatory cooldown check
        if ctx.bar_index - self.last_exit_bar < self.cooldown_bars:
            return None

        # Regime Gate: Fading traps requires two-sided liquidity and fails during runaways/crises.
        # Prior evidence failed from taking counter-trend traps in runaway/crisis conditions.
        market_regime = ctx.market.get("regime", "NORMAL")
        crisis_score = ctx.market.get("crisis_score", 0.0)
        if market_regime in ("CRISIS", "MELTDOWN") or crisis_score > 0.4:
            return None

        atr = self._atr(highs, lows, closes, self.atr_period)
        if atr is None or atr <= 0:
            return None

        # 20-bar Donchian channel prior to the current bar
        prior_highs = highs[-(self.lookback + 1):-1]
        prior_lows = lows[-(self.lookback + 1):-1]
        channel_high = max(prior_highs)
        channel_low = min(prior_lows)

        curr_bar = ctx.bar
        min_penetration = self.min_wick_atr_mult * atr

        # Bull trap setup: High poked meaningfully above range, but closed back inside
        bull_trap = (curr_bar.high > channel_high + min_penetration) and (curr_bar.close < channel_high)

        # Bear trap setup: Low poked meaningfully below range, but closed back inside
        bear_trap = (curr_bar.low < channel_low - min_penetration) and (curr_bar.close > channel_low)

        # Regime alignment: ensure we do not fade strong macro trends
        trend_regime = ctx.market.get("trend_regime", "neutral")

        if bull_trap and trend_regime != "bull":
            # Dynamic stop: placed just beyond the trap wick
            wick_distance = curr_bar.high - curr_bar.close
            stop_bps = max(100.0, min(300.0, (wick_distance / curr_bar.close) * 10000.0 + 30.0))
            tp_bps = stop_bps * 1.5

            return ctx.signal(
                "short",
                confidence=0.75,
                stop_loss_bps=stop_bps,
                take_profit_bps=tp_bps,
                horizon_seconds=self.METADATA["declared_hold_seconds"],
                metadata={
                    "reason": "bull_trap_rejection",
                    "channel_high": round(channel_high, 2),
                    "wick_high": round(curr_bar.high, 2),
                    "close": round(curr_bar.close, 2),
                    "atr": round(atr, 2),
                    "trend_regime": trend_regime
                }
            )

        if bear_trap and trend_regime != "bear":
            wick_distance = curr_bar.close - curr_bar.low
            stop_bps = max(100.0, min(300.0, (wick_distance / curr_bar.close) * 10000.0 + 30.0))
            tp_bps = stop_bps * 1.5

            return ctx.signal(
                "long",
                confidence=0.75,
                stop_loss_bps=stop_bps,
                take_profit_bps=tp_bps,
                horizon_seconds=self.METADATA["declared_hold_seconds"],
                metadata={
                    "reason": "bear_trap_rejection",
                    "channel_low": round(channel_low, 2),
                    "wick_low": round(curr_bar.low, 2),
                    "close": round(curr_bar.close, 2),
                    "atr": round(atr, 2),
                    "trend_regime": trend_regime
                }
            )

        return None