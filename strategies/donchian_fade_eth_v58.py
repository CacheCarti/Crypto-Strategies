from domains.strategy_contract import Strategy, BarContext, Signal
from typing import Optional, Dict, Any
import math

class EthDonchianTrapFade(Strategy):
    METADATA = {
        "name": "EthDonchianTrapFade",
        "domain": "eth_usdc",
        "declared_sl_bps": 220.0,
        "declared_tp_bps": 340.0,
        "declared_hold_seconds": 18000,  # ~5 hours
        "warmup_bars": 35,
    }

    def initialize(self, ctx: BarContext) -> None:
        self.lookback = 20
        self.min_wick_atr_mult = 0.35
        self.max_hold_bars = 6
        self.cooldown_period = 8
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
        closes = ctx.closes(self.lookback + 15)
        highs = ctx.highs(self.lookback + 15)
        lows = ctx.lows(self.lookback + 15)

        if len(closes) < self.lookback + 14:
            return None

        # Manage open position timeout
        if ctx.has_position():
            bars_held = ctx.bar_index - self.entry_bar
            if bars_held >= self.max_hold_bars:
                self.last_exit_bar = ctx.bar_index
                return ctx.signal(
                    "flat",
                    confidence=0.5,
                    metadata={"reason": "time_based_decay_exit", "bars_held": bars_held, "price": ctx.bar.close}
                )
            return None

        # Mandatory post-trade cooldown
        if ctx.bar_index - self.last_exit_bar < self.cooldown_period:
            return None

        atr = self._atr(highs, lows, closes, period=14)
        if atr is None or atr <= 0:
            return None

        # Avoid extreme crisis / meltdown states
        market_regime = ctx.market.get("regime", "NORMAL")
        crisis_score = ctx.market.get("crisis_score", 0.0)
        if market_regime in ("CRISIS", "MELTDOWN") or crisis_score > 0.65:
            return None

        # Donchian channel over the prior 20 bars (excluding current forming bar)
        prior_highs = highs[-(self.lookback + 1):-1]
        prior_lows = lows[-(self.lookback + 1):-1]
        upper_level = max(prior_highs)
        lower_level = min(prior_lows)

        curr_high = ctx.bar.high
        curr_low = ctx.bar.low
        curr_close = ctx.bar.close

        min_wick_distance = atr * self.min_wick_atr_mult

        # Bull Trap / Failed Upside Breakout:
        # Intrabar poke significantly above upper boundary, but closes back firmly below it
        bull_trap = (curr_high > upper_level + min_wick_distance) and (curr_close < upper_level)

        # Bear Trap / Failed Downside Breakout:
        # Intrabar poke significantly below lower boundary, but closes back firmly above it
        bear_trap = (curr_low < lower_level - min_wick_distance) and (curr_close > lower_level)

        if bull_trap and not bear_trap:
            # Dynamic stop placement: past the hunt wick, bounded within declared parameters
            wick_distance_bps = ((curr_high - curr_close) / curr_close) * 10000.0
            stop_loss = max(140.0, min(self.METADATA["declared_sl_bps"], wick_distance_bps + 25.0))
            take_profit = stop_loss * 1.5

            self.entry_bar = ctx.bar_index
            return ctx.signal(
                "short",
                confidence=0.75,
                stop_loss_bps=stop_loss,
                take_profit_bps=take_profit,
                horizon_seconds=self.METADATA["declared_hold_seconds"],
                metadata={
                    "reason": "bull_trap_donchian_fade",
                    "upper_level": upper_level,
                    "wick_high": curr_high,
                    "close": curr_close,
                    "atr": atr,
                    "wick_bps": wick_distance_bps
                }
            )

        if bear_trap and not bull_trap:
            # Dynamic stop placement: below the hunt wick
            wick_distance_bps = ((curr_close - curr_low) / curr_close) * 10000.0
            stop_loss = max(140.0, min(self.METADATA["declared_sl_bps"], wick_distance_bps + 25.0))
            take_profit = stop_loss * 1.5

            self.entry_bar = ctx.bar_index
            return ctx.signal(
                "long",
                confidence=0.75,
                stop_loss_bps=stop_loss,
                take_profit_bps=take_profit,
                horizon_seconds=self.METADATA["declared_hold_seconds"],
                metadata={
                    "reason": "bear_trap_donchian_fade",
                    "lower_level": lower_level,
                    "wick_low": curr_low,
                    "close": curr_close,
                    "atr": atr,
                    "wick_bps": wick_distance_bps
                }
            )

        return None