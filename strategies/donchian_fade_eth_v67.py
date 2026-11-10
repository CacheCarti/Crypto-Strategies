from domains.strategy_contract import Strategy, BarContext, Signal
from typing import Optional, Dict, Any
import math

class DonchianFadeTrap(Strategy):
    METADATA = {
        "name": "Donchian Trap Reversal",
        "domain": "eth_usdc",
        "declared_sl_bps": 220.0,
        "declared_tp_bps": 340.0,
        "declared_hold_seconds": 14400,
        "warmup_bars": 35,
    }

    def initialize(self, ctx: BarContext) -> None:
        self.channel_period = 20
        self.atr_period = 14
        self.max_hold_bars = 4
        self.cooldown_bars = 4
        
        self.entry_bar_idx = -100
        self.last_exit_idx = -100
        self.trap_stop_price = 0.0

    def _atr(self, highs, lows, closes, period=14):
        if len(closes) < period + 1:
            return None
        trs = []
        for i in range(1, len(closes)):
            tr = max(highs[i] - lows[i], abs(highs[i] - closes[i - 1]), abs(lows[i] - closes[i - 1]))
            trs.append(tr)
        return sum(trs[-period:]) / period

    def on_position_close(self, ctx: BarContext, position: Dict[str, Any]) -> None:
        self.last_exit_idx = ctx.bar_index
        self.trap_stop_price = 0.0

    def on_bar(self, ctx: BarContext) -> Optional[Signal]:
        closes = ctx.closes(self.channel_period + 5)
        highs = ctx.highs(self.channel_period + 5)
        lows = ctx.lows(self.channel_period + 5)

        if len(closes) < self.channel_period + 2:
            return None

        current_close = ctx.bar.close
        current_high = ctx.bar.high
        current_low = ctx.bar.low
        current_idx = ctx.bar_index

        atr = self._atr(highs, lows, closes, self.atr_period)
        if atr is None or atr <= 0:
            return None

        # Position Management: Timed exit or structural breach of the trapping wick
        if ctx.has_position():
            pos_dir = ctx.position_direction()
            bars_held = current_idx - self.entry_bar_idx

            if pos_dir == "long":
                if current_close < self.trap_stop_price:
                    return ctx.signal(
                        "flat",
                        confidence=0.7,
                        metadata={"reason": "long_structural_stop", "stop_price": self.trap_stop_price, "price": current_close}
                    )
            elif pos_dir == "short":
                if current_close > self.trap_stop_price:
                    return ctx.signal(
                        "flat",
                        confidence=0.7,
                        metadata={"reason": "short_structural_stop", "stop_price": self.trap_stop_price, "price": current_close}
                    )

            if bars_held >= self.max_hold_bars:
                return ctx.signal(
                    "flat",
                    confidence=0.5,
                    metadata={"reason": "time_decay_exit", "bars_held": bars_held, "price": current_close}
                )

            return None

        # Cooldown guard after trade exit
        if (current_idx - self.last_exit_idx) < self.cooldown_bars:
            return None

        # Prior channel levels (excluding the current developing bar)
        prior_highs = highs[-(self.channel_period + 1):-1]
        prior_lows = lows[-(self.channel_period + 1):-1]
        
        channel_high = max(prior_highs)
        channel_low = min(prior_lows)

        min_wick = 0.25 * atr
        crisis_score = ctx.market.get("crisis_score", 0.0)
        if crisis_score > 0.65:
            return None

        # Bear trap (False Downside Breakout): Low pierced channel_low by min_wick, closed back above
        if (current_low < channel_low - min_wick) and (current_close > channel_low):
            self.entry_bar_idx = current_idx
            self.trap_stop_price = current_low

            stop_dist = max(current_close - current_low, 0.5 * atr)
            sl_bps = min(max((stop_dist / current_close) * 10000.0, 100.0), 280.0)
            tp_bps = sl_bps * 1.5

            return ctx.signal(
                "long",
                confidence=0.75,
                stop_loss_bps=sl_bps,
                take_profit_bps=tp_bps,
                horizon_seconds=14400,
                metadata={
                    "reason": "donchian_bear_trap_reversal",
                    "channel_low": channel_low,
                    "wick_low": current_low,
                    "atr": atr,
                    "sl_bps": sl_bps,
                    "tp_bps": tp_bps
                }
            )

        # Bull trap (False Upside Breakout): High pierced channel_high by min_wick, closed back below
        if (current_high > channel_high + min_wick) and (current_close < channel_high):
            self.entry_bar_idx = current_idx
            self.trap_stop_price = current_high

            stop_dist = max(current_high - current_close, 0.5 * atr)
            sl_bps = min(max((stop_dist / current_close) * 10000.0, 100.0), 280.0)
            tp_bps = sl_bps * 1.5

            return ctx.signal(
                "short",
                confidence=0.75,
                stop_loss_bps=sl_bps,
                take_profit_bps=tp_bps,
                horizon_seconds=14400,
                metadata={
                    "reason": "donchian_bull_trap_reversal",
                    "channel_high": channel_high,
                    "wick_high": current_high,
                    "atr": atr,
                    "sl_bps": sl_bps,
                    "tp_bps": tp_bps
                }
            )

        return None