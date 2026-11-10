from domains.strategy_contract import Strategy, BarContext, Signal
from typing import Optional, Dict, Any
import math

class EthFailedBreakoutFade(Strategy):
    METADATA = {
        "name": "EthFailedBreakoutFade",
        "domain": "eth_usdc",
        "declared_sl_bps": 220.0,
        "declared_tp_bps": 340.0,
        "declared_hold_seconds": 18000,  # ~5 hours target hold
        "warmup_bars": 35,
    }

    def initialize(self, ctx: BarContext) -> None:
        self.lookback = 20
        self.cooldown_bars = 6
        self.max_hold_bars = 8
        self.last_trade_bar = -999
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
        self.last_trade_bar = ctx.bar_index

    def on_bar(self, ctx: BarContext) -> Optional[Signal]:
        if ctx.bar_index < self.METADATA["warmup_bars"]:
            return None

        # Time-based exit for open positions
        if ctx.has_position():
            bars_held = ctx.bar_index - self.entry_bar
            if bars_held >= self.max_hold_bars:
                self.last_trade_bar = ctx.bar_index
                return ctx.signal(
                    "flat",
                    confidence=0.6,
                    metadata={"reason": "max_bars_reached", "bars_held": bars_held}
                )
            return None

        # Cooldown guard
        if ctx.bar_index - self.last_trade_bar < self.cooldown_bars:
            return None

        # Need enough historical bars
        highs = ctx.highs(self.lookback + 2)
        lows = ctx.lows(self.lookback + 2)
        closes = ctx.closes(self.lookback + 2)

        if len(highs) < self.lookback + 2:
            return None

        atr = self._atr(highs, lows, closes, period=14)
        if atr is None or atr <= 0:
            return None

        # Prior lookback window excluding the current bar
        prior_highs = highs[-(self.lookback + 1):-1]
        prior_lows = lows[-(self.lookback + 1):-1]

        range_high = max(prior_highs)
        range_low = min(prior_lows)

        current_high = ctx.bar.high
        current_low = ctx.bar.low
        current_close = ctx.bar.close

        # Volatility-scaled minimum penetration requirement (at least 0.25 ATR wick outside the range)
        min_penetration = 0.25 * atr

        # Upper trap: Intrabar sweep above range_high, but close drops back below it
        upper_penetration = current_high - range_high
        bull_trap = (upper_penetration >= min_penetration) and (current_close < range_high)

        # Lower trap: Intrabar sweep below range_low, but close rises back above it
        lower_penetration = range_low - current_low
        bear_trap = (lower_penetration >= min_penetration) and (current_close > range_low)

        # Avoid extreme crisis conditions
        crisis_score = ctx.market.get("crisis_score", 0.0)
        if crisis_score > 0.7:
            return None

        if bull_trap:
            # Dynamic stop just beyond the sweep high, clamped to declared SL
            stop_distance = max(current_high - current_close, atr * 0.8)
            stop_loss_bps = min(max((stop_distance / current_close) * 10000.0, 120.0), 280.0)
            take_profit_bps = stop_loss_bps * 1.5

            self.entry_bar = ctx.bar_index
            self.last_trade_bar = ctx.bar_index

            return ctx.signal(
                "short",
                confidence=0.75,
                stop_loss_bps=stop_loss_bps,
                take_profit_bps=take_profit_bps,
                horizon_seconds=self.METADATA["declared_hold_seconds"],
                metadata={
                    "reason": "failed_breakout_short",
                    "range_high": range_high,
                    "sweep_high": current_high,
                    "penetration_atr": round(upper_penetration / atr, 2),
                    "close": current_close,
                    "atr": atr,
                }
            )

        if bear_trap:
            # Dynamic stop just beyond the sweep low, clamped to declared SL
            stop_distance = max(current_close - current_low, atr * 0.8)
            stop_loss_bps = min(max((stop_distance / current_close) * 10000.0, 120.0), 280.0)
            take_profit_bps = stop_loss_bps * 1.5

            self.entry_bar = ctx.bar_index
            self.last_trade_bar = ctx.bar_index

            return ctx.signal(
                "long",
                confidence=0.75,
                stop_loss_bps=stop_loss_bps,
                take_profit_bps=take_profit_bps,
                horizon_seconds=self.METADATA["declared_hold_seconds"],
                metadata={
                    "reason": "failed_breakdown_long",
                    "range_low": range_low,
                    "sweep_low": current_low,
                    "penetration_atr": round(lower_penetration / atr, 2),
                    "close": current_close,
                    "atr": atr,
                }
            )

        return None