from domains.strategy_contract import Strategy, BarContext, Signal
from typing import Optional, Dict, Any
import math

class DonchianFailedBreakoutFade(Strategy):
    METADATA = {
        "name": "DonchianFailedBreakoutFade",
        "domain": "eth_usdc",
        "declared_sl_bps": 220.0,
        "declared_tp_bps": 340.0,
        "declared_hold_seconds": 14400,
        "warmup_bars": 35,
    }

    def initialize(self, ctx: BarContext) -> None:
        self.lookback = 20
        self.cooldown_period = 8
        self.max_hold_bars = 4
        self.min_penetration_bps = 15.0
        
        self.entry_bar_idx = -1
        self.cooldown_until_bar = 0

    def _atr(self, highs, lows, closes, period=14):
        if len(closes) < period + 1:
            return None
        trs = []
        for i in range(1, len(closes)):
            tr = max(highs[i] - lows[i], abs(highs[i] - closes[i - 1]), abs(lows[i] - closes[i - 1]))
            trs.append(tr)
        return sum(trs[-period:]) / period

    def on_bar(self, ctx: BarContext) -> Optional[Signal]:
        closes = ctx.closes(self.lookback + 10)
        highs = ctx.highs(self.lookback + 10)
        lows = ctx.lows(self.lookback + 10)

        if len(closes) < self.lookback + 2:
            return None

        # Time-based exit for open positions
        if ctx.has_position():
            bars_in_pos = ctx.bar_index - self.entry_bar_idx
            if bars_in_pos >= self.max_hold_bars:
                self.cooldown_until_bar = ctx.bar_index + self.cooldown_period
                return ctx.signal("flat", confidence=0.5, metadata={
                    "reason": "time_based_exit",
                    "bars_held": bars_in_pos,
                    "close": ctx.bar.close
                })
            return None

        # Cooldown guard
        if ctx.bar_index < self.cooldown_until_bar:
            return None

        # Avoid crisis/extreme volatility
        regime = ctx.market.get("regime", "NORMAL")
        if regime in ("CRISIS", "MELTDOWN"):
            return None

        # Historical channel (excluding the current forming bar)
        prior_highs = highs[-self.lookback - 1 : -1]
        prior_lows = lows[-self.lookback - 1 : -1]
        
        channel_high = max(prior_highs)
        channel_low = min(prior_lows)

        current_high = ctx.bar.high
        current_low = ctx.bar.low
        current_close = ctx.bar.close

        atr = self._atr(highs, lows, closes, 14)
        if atr is None or atr <= 0:
            return None

        min_wick = channel_high * (self.min_penetration_bps / 10000.0)

        # Bull Trap / Failed Breakout to the Upside -> Go Short
        # Intrabar pierced prior channel high with meaningful extension, but closed back inside
        bull_trap = (current_high >= channel_high + min_wick) and (current_close < channel_high)

        # Bear Trap / Failed Breakdown to the Downside -> Go Long
        # Intrabar pierced prior channel low with meaningful extension, but closed back inside
        bear_trap = (current_low <= channel_low - min_wick) and (current_close > channel_low)

        if bull_trap and not bear_trap:
            # Dynamic stop just above the rejection wick
            wick_distance_bps = ((current_high - current_close) / current_close) * 10000.0
            sl_bps = max(120.0, min(280.0, wick_distance_bps + 25.0))
            tp_bps = sl_bps * 1.5

            self.entry_bar_idx = ctx.bar_index
            self.cooldown_until_bar = ctx.bar_index + self.cooldown_period
            return ctx.signal(
                "short",
                confidence=0.75,
                stop_loss_bps=sl_bps,
                take_profit_bps=tp_bps,
                horizon_seconds=14400,
                metadata={
                    "reason": "bull_trap_rejection_fade",
                    "channel_high": channel_high,
                    "current_high": current_high,
                    "close": current_close,
                    "sl_bps": sl_bps,
                    "tp_bps": tp_bps
                }
            )

        if bear_trap and not bull_trap:
            # Dynamic stop just below the rejection wick
            wick_distance_bps = ((current_close - current_low) / current_close) * 10000.0
            sl_bps = max(120.0, min(280.0, wick_distance_bps + 25.0))
            tp_bps = sl_bps * 1.5

            self.entry_bar_idx = ctx.bar_index
            self.cooldown_until_bar = ctx.bar_index + self.cooldown_period
            return ctx.signal(
                "long",
                confidence=0.75,
                stop_loss_bps=sl_bps,
                take_profit_bps=tp_bps,
                horizon_seconds=14400,
                metadata={
                    "reason": "bear_trap_rejection_fade",
                    "channel_low": channel_low,
                    "current_low": current_low,
                    "close": current_close,
                    "sl_bps": sl_bps,
                    "tp_bps": tp_bps
                }
            )

        return None

    def on_position_close(self, ctx: BarContext, position: Dict[str, Any]) -> None:
        self.cooldown_until_bar = ctx.bar_index + self.cooldown_period