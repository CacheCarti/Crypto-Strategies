from domains.strategy_contract import Strategy, BarContext, Signal
from typing import Optional, Dict, Any
import math

class DonchianTrapFade(Strategy):
    METADATA = {
        "name": "DonchianTrapFade",
        "domain": "eth_usdc",
        "declared_sl_bps": 220.0,
        "declared_tp_bps": 380.0,
        "declared_hold_seconds": 18000,  # ~5 hours
        "warmup_bars": 35,
    }

    def initialize(self, ctx: BarContext) -> None:
        self.lookback = 20
        self.cooldown_bars = 4
        self.last_exit_bar = -100
        self.entry_bar = -100
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

    def on_bar(self, ctx: BarContext) -> Optional[Signal]:
        closes = ctx.closes(self.lookback + 5)
        highs = ctx.highs(self.lookback + 5)
        lows = ctx.lows(self.lookback + 5)

        if len(closes) < self.lookback + 2:
            return None

        # Manage open position time-based exit
        if ctx.has_position():
            bars_held = ctx.bar_index - self.entry_bar
            if bars_held >= self.max_hold_bars:
                self.last_exit_bar = ctx.bar_index
                return ctx.signal(
                    "flat",
                    confidence=0.6,
                    metadata={"reason": "max_bars_held_reached", "bars_held": bars_held}
                )
            return None

        # Mandatory cooldown after trade exit
        if ctx.bar_index - self.last_exit_bar < self.cooldown_bars:
            return None

        # Regime safety filter: skip entering during crisis / meltdown
        regime = ctx.market.get("regime", "NORMAL")
        if regime in ("CRISIS", "MELTDOWN"):
            return None

        # Prior 20-bar channel (excluding current bar)
        prior_highs = highs[-(self.lookback + 1):-1]
        prior_lows = lows[-(self.lookback + 1):-1]
        chan_high = max(prior_highs)
        chan_low = min(prior_lows)

        curr_high = ctx.bar.high
        curr_low = ctx.bar.low
        curr_close = ctx.bar.close

        atr = self._atr(highs, lows, closes, period=14)
        if atr is None or atr <= 0:
            return None

        # Significant penetration requirement to filter chop and capture real stop hunts (~0.35 ATR)
        min_penetration = 0.35 * atr

        # Bull Trap (Failed Breakout above channel):
        # High pierced significantly above 20-bar high, but close finished back inside the range.
        if curr_high >= chan_high + min_penetration and curr_close < chan_high:
            wick_overshoot_bps = ((curr_high - chan_high) / chan_high) * 10000.0
            # Dynamic stop based on the wick high + safety buffer
            stop_bps = max(140.0, min(320.0, ((curr_high - curr_close) / curr_close) * 10000.0 + 35.0))
            take_bps = stop_bps * 1.6

            self.entry_bar = ctx.bar_index
            return ctx.signal(
                "short",
                confidence=0.75,
                stop_loss_bps=stop_bps,
                take_profit_bps=take_bps,
                metadata={
                    "reason": "bull_trap_donchian_fade",
                    "chan_high": round(chan_high, 2),
                    "curr_high": round(curr_high, 2),
                    "curr_close": round(curr_close, 2),
                    "wick_overshoot_bps": round(wick_overshoot_bps, 1),
                    "atr": round(atr, 2),
                }
            )

        # Bear Trap (Failed Breakdown below channel):
        # Low pierced significantly below 20-bar low, but close finished back inside the range.
        if curr_low <= chan_low - min_penetration and curr_close > chan_low:
            wick_overshoot_bps = ((chan_low - curr_low) / chan_low) * 10000.0
            stop_bps = max(140.0, min(320.0, ((curr_close - curr_low) / curr_close) * 10000.0 + 35.0))
            take_bps = stop_bps * 1.6

            self.entry_bar = ctx.bar_index
            return ctx.signal(
                "long",
                confidence=0.75,
                stop_loss_bps=stop_bps,
                take_profit_bps=take_bps,
                metadata={
                    "reason": "bear_trap_donchian_fade",
                    "chan_low": round(chan_low, 2),
                    "curr_low": round(curr_low, 2),
                    "curr_close": round(curr_close, 2),
                    "wick_overshoot_bps": round(wick_overshoot_bps, 1),
                    "atr": round(atr, 2),
                }
            )

        return None