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
        highs = ctx.highs(self.lookback + 15)
        lows = ctx.lows(self.lookback + 15)
        closes = ctx.closes(self.lookback + 15)

        if len(closes) < self.lookback + 15:
            return None

        # Manage active position time-stop
        if ctx.has_position():
            bars_held = ctx.bar_index - self.entry_bar
            if bars_held >= self.max_hold_bars:
                self.last_trade_bar = ctx.bar_index
                return ctx.signal(
                    "flat",
                    confidence=0.6,
                    metadata={"reason": "max_bars_exhaustion", "bars_held": bars_held, "price": ctx.bar.close}
                )
            return None

        # Check entry cooldown
        if ctx.bar_index - self.last_trade_bar < self.cooldown_bars:
            return None

        atr = self._atr(highs, lows, closes, period=14)
        if atr is None or atr <= 0:
            return None

        curr_bar = ctx.bar
        # Range of the prior 20 bars (excluding the current one)
        prior_highs = highs[-self.lookback - 1:-1]
        prior_lows = lows[-self.lookback - 1:-1]
        range_high = max(prior_highs)
        range_low = min(prior_lows)

        # Volatility-scaled minimum poke to confirm a genuine liquidity sweep (at least 0.2 ATR)
        min_penetration = 0.20 * atr

        # Bull Trap / Upward Failed Breakout:
        # High pokes meaningfully above the 20-bar high, but bar closes back inside the range.
        short_poke = curr_bar.high - range_high
        is_bull_trap = (short_poke >= min_penetration) and (curr_bar.close < range_high)

        # Bear Trap / Downward Failed Breakout:
        # Low pokes meaningfully below the 20-bar low, but bar closes back inside the range.
        long_poke = range_low - curr_bar.low
        is_bear_trap = (long_poke >= min_penetration) and (curr_bar.close > range_low)

        # Optional regime filter: do not short in meltdown/crisis volatility spikes
        crisis_score = ctx.market.get("crisis_score", 0.0)
        if crisis_score > 0.70:
            return None

        if is_bear_trap:
            # Dynamic SL based on the swept wick distance plus a buffer
            risk_bps = max(100.0, min(260.0, ((curr_bar.close - curr_bar.low) / curr_bar.close) * 10000.0 + 30.0))
            tp_bps = risk_bps * 1.6
            self.last_trade_bar = ctx.bar_index
            self.entry_bar = ctx.bar_index
            return ctx.signal(
                "long",
                confidence=0.72,
                stop_loss_bps=risk_bps,
                take_profit_bps=tp_bps,
                metadata={
                    "reason": "failed_breakout_bear_trap_long",
                    "poke_atr_ratio": round(long_poke / atr, 2),
                    "range_low": round(range_low, 2),
                    "wick_low": round(curr_bar.low, 2),
                    "close": round(curr_bar.close, 2),
                    "atr": round(atr, 2),
                }
            )

        if is_bull_trap:
            risk_bps = max(100.0, min(260.0, ((curr_bar.high - curr_bar.close) / curr_bar.close) * 10000.0 + 30.0))
            tp_bps = risk_bps * 1.6
            self.last_trade_bar = ctx.bar_index
            self.entry_bar = ctx.bar_index
            return ctx.signal(
                "short",
                confidence=0.72,
                stop_loss_bps=risk_bps,
                take_profit_bps=tp_bps,
                metadata={
                    "reason": "failed_breakout_bull_trap_short",
                    "poke_atr_ratio": round(short_poke / atr, 2),
                    "range_high": round(range_high, 2),
                    "wick_high": round(curr_bar.high, 2),
                    "close": round(curr_bar.close, 2),
                    "atr": round(atr, 2),
                }
            )

        return None