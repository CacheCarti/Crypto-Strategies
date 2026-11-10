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
        "required_features": ["btc_return_pct"]
    }

    def initialize(self, ctx: BarContext) -> None:
        self.channel_period = 20
        self.atr_period = 14
        self.cooldown_bars = 6
        self.last_exit_bar = -100
        self.entry_bar = -1

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
        closes = ctx.closes(self.channel_period + 10)
        highs = ctx.highs(self.channel_period + 10)
        lows = ctx.lows(self.channel_period + 10)

        if len(closes) < self.channel_period + 2:
            return None

        # Time-based exit: hold up to 5 bars (~5 hours)
        if ctx.has_position():
            bars_held = ctx.bar_index - self.entry_bar
            if bars_held >= 5:
                return ctx.signal(
                    "flat",
                    confidence=0.5,
                    metadata={"reason": "time_exit_reached", "bars_held": bars_held}
                )
            return None

        # Cooldown guard after any position exit
        if (ctx.bar_index - self.last_exit_bar) < self.cooldown_bars:
            return None

        # Market regime filter: avoid fading strong trending crises or meltdowns
        crisis_score = ctx.market.get("crisis_score", 0.0)
        if crisis_score > 0.45:
            return None

        atr = self._atr(highs, lows, closes, self.atr_period)
        if atr is None or atr <= 0:
            return None

        curr_bar = ctx.bar
        # Look back 20 bars prior to the current bar to define range
        prev_highs = highs[-(self.channel_period + 1):-1]
        prev_lows = lows[-(self.channel_period + 1):-1]
        upper_level = max(prev_highs)
        lower_level = min(prev_lows)

        # Cross-asset momentum confirmation
        btc_ret = ctx.features.get("btc_return_pct", 0.0)

        # Minimum required wick extension beyond level to capture real stop-hunts
        min_wick = 0.25 * atr

        # Bull Trap / Failed Upside Breakout -> Short
        # Pierced upper boundary by min_wick, closed back below level, and BTC confirms lack of strong bull impulse
        if curr_bar.high >= upper_level + min_wick and curr_bar.close < upper_level:
            if btc_ret < 1.5:  # BTC not running away in a vertical impulse
                self.entry_bar = ctx.bar_index
                return ctx.signal(
                    "short",
                    confidence=0.70,
                    stop_loss_bps=self.METADATA["declared_sl_bps"],
                    take_profit_bps=self.METADATA["declared_tp_bps"],
                    horizon_seconds=self.METADATA["declared_hold_seconds"],
                    metadata={
                        "reason": "bull_trap_rejection",
                        "wick_extension": curr_bar.high - upper_level,
                        "atr": atr,
                        "upper_level": upper_level,
                        "close": curr_bar.close,
                        "btc_ret": btc_ret
                    }
                )

        # Bear Trap / Failed Downside Breakout -> Long
        # Pierced lower boundary by min_wick, closed back above level, and BTC confirms lack of severe dump
        if curr_bar.low <= lower_level - min_wick and curr_bar.close > lower_level:
            if btc_ret > -1.5:  # BTC not crashing down in a heavy sell-off
                self.entry_bar = ctx.bar_index
                return ctx.signal(
                    "long",
                    confidence=0.70,
                    stop_loss_bps=self.METADATA["declared_sl_bps"],
                    take_profit_bps=self.METADATA["declared_tp_bps"],
                    horizon_seconds=self.METADATA["declared_hold_seconds"],
                    metadata={
                        "reason": "bear_trap_rejection",
                        "wick_extension": lower_level - curr_bar.low,
                        "atr": atr,
                        "lower_level": lower_level,
                        "close": curr_bar.close,
                        "btc_ret": btc_ret
                    }
                )

        return None