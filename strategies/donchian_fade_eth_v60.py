from domains.strategy_contract import Strategy, BarContext, Signal
from typing import Optional, Dict, Any
import math

class FailedBreakoutFade(Strategy):
    METADATA = {
        "name": "FailedBreakoutFade",
        "domain": "eth_usdc",
        "declared_sl_bps": 220.0,
        "declared_tp_bps": 340.0,
        "declared_hold_seconds": 14400,
        "warmup_bars": 35,
    }

    def initialize(self, ctx: BarContext) -> None:
        self.channel_period = 20
        self.atr_period = 14
        self.cooldown_bars = 6
        self.last_trade_bar = -100
        self.entry_bar = -100

    def _atr(self, highs, lows, closes, period=14):
        if len(closes) < period + 1:
            return None
        trs = []
        for i in range(1, len(closes)):
            tr = max(highs[i] - lows[i], abs(highs[i] - closes[i - 1]), abs(lows[i] - closes[i - 1]))
            trs.append(tr)
        return sum(trs[-period:]) / period

    def on_bar(self, ctx: BarContext) -> Optional[Signal]:
        bar_idx = ctx.bar_index
        needed = max(self.channel_period + 2, self.atr_period + 2)
        closes = ctx.closes(needed)
        highs = ctx.highs(needed)
        lows = ctx.lows(needed)

        if len(closes) < needed:
            return None

        # Manage existing positions: enforce a max hold of 6 bars or take profit/reversion to mean
        if ctx.has_position():
            bars_held = bar_idx - self.entry_bar
            direction = ctx.position_direction()
            recent_mid = (max(highs[-self.channel_period:]) + min(lows[-self.channel_period:])) / 2.0
            
            # Time-stop exit (4-6 bars max hold for a fade) or reversion to channel midline
            if bars_held >= 5:
                self.last_trade_bar = bar_idx
                return ctx.signal("flat", confidence=0.6, metadata={"reason": "time_stop_hold_limit", "bars_held": bars_held})
            
            if direction == "long" and ctx.bar.close >= recent_mid:
                self.last_trade_bar = bar_idx
                return ctx.signal("flat", confidence=0.7, metadata={"reason": "tp_midline_reached", "close": ctx.bar.close, "mid": recent_mid})
            elif direction == "short" and ctx.bar.close <= recent_mid:
                self.last_trade_bar = bar_idx
                return ctx.signal("flat", confidence=0.7, metadata={"reason": "tp_midline_reached", "close": ctx.bar.close, "mid": recent_mid})

            return None

        # Cooldown guard after trade exit
        if bar_idx - self.last_trade_bar < self.cooldown_bars:
            return None

        # Regime safety filter: skip entries during extreme crisis
        regime = ctx.market.get("regime", "NORMAL")
        if regime in ("CRISIS", "MELTDOWN"):
            return None

        atr = self._atr(highs, lows, closes, self.atr_period)
        if atr is None or atr <= 0:
            return None

        # Prior 20-bar extremes excluding the current bar
        prior_highs = highs[-(self.channel_period + 1):-1]
        prior_lows = lows[-(self.channel_period + 1):-1]
        range_high = max(prior_highs)
        range_low = min(prior_lows)

        curr_high = ctx.bar.high
        curr_low = ctx.bar.low
        curr_close = ctx.bar.close

        # Stricter event boundary: require wick poke >= 0.25 * ATR outside the range
        min_penetration = 0.25 * atr

        # Bull Trap / Bearish Sweep: poke above range_high, close back strictly inside, close below open
        bear_poke = curr_high - range_high
        if bear_poke >= min_penetration and curr_close < range_high and curr_close < ctx.bar.open:
            wick_ratio = (curr_high - max(ctx.bar.open, curr_close)) / (curr_high - curr_low + 1e-6)
            if wick_ratio > 0.40:
                self.entry_bar = bar_idx
                self.last_trade_bar = bar_idx
                sl_bps = max(100.0, min(300.0, ((curr_high - curr_close) / curr_close) * 10000.0 + 30.0))
                tp_bps = sl_bps * 1.5
                return ctx.signal(
                    "short",
                    confidence=0.75,
                    stop_loss_bps=sl_bps,
                    take_profit_bps=tp_bps,
                    metadata={
                        "reason": "bear_trap_failed_breakout",
                        "poke_dist": bear_poke,
                        "wick_ratio": round(wick_ratio, 3),
                        "range_high": range_high,
                        "close": curr_close,
                        "atr": round(atr, 2),
                    }
                )

        # Bear Trap / Bullish Sweep: poke below range_low, close back strictly inside, close above open
        bull_poke = range_low - curr_low
        if bull_poke >= min_penetration and curr_close > range_low and curr_close > ctx.bar.open:
            wick_ratio = (min(ctx.bar.open, curr_close) - curr_low) / (curr_high - curr_low + 1e-6)
            if wick_ratio > 0.40:
                self.entry_bar = bar_idx
                self.last_trade_bar = bar_idx
                sl_bps = max(100.0, min(300.0, ((curr_close - curr_low) / curr_close) * 10000.0 + 30.0))
                tp_bps = sl_bps * 1.5
                return ctx.signal(
                    "long",
                    confidence=0.75,
                    stop_loss_bps=sl_bps,
                    take_profit_bps=tp_bps,
                    metadata={
                        "reason": "bull_trap_failed_breakout",
                        "poke_dist": bull_poke,
                        "wick_ratio": round(wick_ratio, 3),
                        "range_low": range_low,
                        "close": curr_close,
                        "atr": round(atr, 2),
                    }
                )

        return None

    def on_position_close(self, ctx: BarContext, position: Dict[str, Any]) -> None:
        self.last_trade_bar = ctx.bar_index