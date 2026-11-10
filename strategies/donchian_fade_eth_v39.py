from domains.strategy_contract import Strategy, BarContext, Signal
from typing import Optional, Dict, Any
import math

class DonchianTrapFade(Strategy):
    METADATA = {
        "name": "DonchianTrapFade",
        "domain": "eth_usdc",
        "declared_sl_bps": 220.0,
        "declared_tp_bps": 340.0,
        "declared_hold_seconds": 18000,
        "warmup_bars": 35,
    }

    def initialize(self, ctx: BarContext) -> None:
        self.channel_period = 20
        self.atr_period = 14
        self.last_trade_bar = -100
        self.cooldown_bars = 5
        self.entry_bar = -1
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
        self.last_trade_bar = ctx.bar_index
        self.entry_bar = -1

    def on_bar(self, ctx: BarContext) -> Optional[Signal]:
        total_needed = max(self.channel_period + 1, self.atr_period + 2)
        closes = ctx.closes(total_needed)
        highs = ctx.highs(total_needed)
        lows = ctx.lows(total_needed)
        
        if len(closes) < total_needed:
            return None

        # Time-based exit for open position
        if ctx.has_position():
            if self.entry_bar > 0 and (ctx.bar_index - self.entry_bar) >= self.max_hold_bars:
                return ctx.signal(
                    "flat",
                    confidence=0.5,
                    metadata={"reason": "max_bars_exhaustion", "bars_held": ctx.bar_index - self.entry_bar}
                )
            return None

        # Cooldown check
        if (ctx.bar_index - self.last_trade_bar) < self.cooldown_bars:
            return None

        atr = self._atr(highs, lows, closes, self.atr_period)
        if atr is None or atr <= 0:
            return None

        # Regime safety filter: avoid entering failed breakouts during outright crisis/meltdown
        regime = ctx.market.get("regime", "NORMAL")
        if regime in ("CRISIS", "MELTDOWN"):
            return None

        # Prior 20-bar Donchian channel excluding current bar
        prior_highs = highs[-(self.channel_period + 1):-1]
        prior_lows = lows[-(self.channel_period + 1):-1]
        dh = max(prior_highs)
        dl = min(prior_lows)

        curr_high = highs[-1]
        curr_low = lows[-1]
        curr_close = closes[-1]
        curr_open = ctx.opens(1)[0]

        # Minimum wick extension relative to volatility (0.25 ATR minimum probe)
        min_wick = 0.25 * atr

        # Bull trap: high probes above prior DH, but closes back inside channel with rejection candle
        bear_trap_wick = curr_high - max(dh, curr_open, curr_close)
        is_bull_trap = (curr_high > dh) and (curr_close < dh) and (bear_trap_wick >= min_wick) and (curr_close <= curr_open)

        # Bear trap: low probes below prior DL, but closes back inside channel with bounce candle
        bull_trap_wick = min(dl, curr_open, curr_close) - curr_low
        is_bear_trap = (curr_low < dl) and (curr_close > dl) and (bull_trap_wick >= min_wick) and (curr_close >= curr_open)

        if is_bear_trap:
            wick_bps = (bull_trap_wick / curr_close) * 10000.0
            sl_bps = max(120.0, min(300.0, wick_bps + 40.0))
            tp_bps = sl_bps * 1.6
            self.entry_bar = ctx.bar_index
            self.last_trade_bar = ctx.bar_index
            return ctx.signal(
                "long",
                confidence=0.72,
                stop_loss_bps=sl_bps,
                take_profit_bps=tp_bps,
                metadata={
                    "reason": "donchian_low_wick_trap_long",
                    "donchian_low": dl,
                    "wick_extension": bull_trap_wick,
                    "atr": atr,
                    "close": curr_close
                }
            )

        if is_bull_trap:
            wick_bps = (bear_trap_wick / curr_close) * 10000.0
            sl_bps = max(120.0, min(300.0, wick_bps + 40.0))
            tp_bps = sl_bps * 1.6
            self.entry_bar = ctx.bar_index
            self.last_trade_bar = ctx.bar_index
            return ctx.signal(
                "short",
                confidence=0.72,
                stop_loss_bps=sl_bps,
                take_profit_bps=tp_bps,
                metadata={
                    "reason": "donchian_high_wick_trap_short",
                    "donchian_high": dh,
                    "wick_extension": bear_trap_wick,
                    "atr": atr,
                    "close": curr_close
                }
            )

        return None