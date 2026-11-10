from domains.strategy_contract import Strategy, BarContext, Signal
from typing import Optional, Dict, Any
import math

class DonchianFailedBreakoutFade(Strategy):
    METADATA = {
        "name": "DonchianFailedBreakoutFade",
        "domain": "eth_usdc",
        "declared_sl_bps": 220.0,
        "declared_tp_bps": 340.0,
        "declared_hold_seconds": 18000,  # ~5 hours target hold
        "warmup_bars": 35,
    }

    def initialize(self, ctx: BarContext) -> None:
        self.channel_period = 20
        self.atr_period = 14
        self.cooldown_bars = 6
        self.max_hold_bars = 8
        self.last_exit_bar = -999
        self.entry_bar = 0

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
        total_needed = max(self.channel_period, self.atr_period) + 2
        closes = ctx.closes(total_needed)
        if len(closes) < total_needed:
            return None

        highs = ctx.highs(total_needed)
        lows = ctx.lows(total_needed)

        # Time-based exit management
        if ctx.has_position():
            bars_held = ctx.bar_index - self.entry_bar
            if bars_held >= self.max_hold_bars:
                direction = ctx.position_direction()
                return ctx.signal(
                    "flat",
                    confidence=0.6,
                    metadata={
                        "reason": "max_bars_time_exit",
                        "bars_held": bars_held,
                        "position_direction": direction,
                        "close": ctx.bar.close
                    }
                )
            return None

        # Mandatory cooldown guard between trades
        if ctx.bar_index - self.last_exit_bar < self.cooldown_bars:
            return None

        # Avoid extreme meltdown/crisis regimes where range fades get steamrolled
        market_regime = ctx.market.get("regime", "NORMAL")
        if market_regime in ("CRISIS", "MELTDOWN"):
            return None

        atr = self._atr(highs, lows, closes, self.atr_period)
        if atr is None or atr <= 0:
            return None

        # Reference channel: 20 bars prior to current bar
        prior_highs = highs[-(self.channel_period + 1):-1]
        prior_lows = lows[-(self.channel_period + 1):-1]
        range_high = max(prior_highs)
        range_low = min(prior_lows)

        current_high = ctx.bar.high
        current_low = ctx.bar.low
        current_close = ctx.bar.close

        # Dynamic stop-hunt requirement: wick must poke at least 0.25 ATR past the level
        min_penetration = 0.25 * atr

        # Bull trap (failed upside breakout):
        # High pierced prior 20-bar high by min_penetration, but bar closed strictly below range_high
        bull_trap = (current_high >= range_high + min_penetration) and (current_close < range_high)

        # Bear trap (failed downside breakout):
        # Low pierced prior 20-bar low by min_penetration, but bar closed strictly above range_low
        bear_trap = (current_low <= range_low - min_penetration) and (current_close > range_low)

        if bull_trap and not bear_trap:
            # Stop loss anchored slightly beyond the rejection wick
            wick_distance = current_high - current_close
            sl_bps = max(120.0, min(300.0, (wick_distance / current_close) * 10000.0 * 1.15))
            tp_bps = max(180.0, sl_bps * 1.5)

            self.entry_bar = ctx.bar_index
            return ctx.signal(
                "short",
                confidence=0.75,
                stop_loss_bps=sl_bps,
                take_profit_bps=tp_bps,
                metadata={
                    "reason": "donchian_bull_trap_fade",
                    "range_high": range_high,
                    "wick_high": current_high,
                    "penetration": current_high - range_high,
                    "atr": atr,
                    "sl_bps": sl_bps,
                    "tp_bps": tp_bps
                }
            )

        if bear_trap and not bull_trap:
            # Stop loss anchored slightly beyond the rejection wick
            wick_distance = current_close - current_low
            sl_bps = max(120.0, min(300.0, (wick_distance / current_close) * 10000.0 * 1.15))
            tp_bps = max(180.0, sl_bps * 1.5)

            self.entry_bar = ctx.bar_index
            return ctx.signal(
                "long",
                confidence=0.75,
                stop_loss_bps=sl_bps,
                take_profit_bps=tp_bps,
                metadata={
                    "reason": "donchian_bear_trap_fade",
                    "range_low": range_low,
                    "wick_low": current_low,
                    "penetration": range_low - current_low,
                    "atr": atr,
                    "sl_bps": sl_bps,
                    "tp_bps": tp_bps
                }
            )

        return None