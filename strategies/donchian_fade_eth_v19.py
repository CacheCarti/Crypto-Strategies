from domains.strategy_contract import Strategy, BarContext, Signal
from typing import Optional, Dict, Any
import math

class DonchianTrapFade(Strategy):
    METADATA = {
        "name": "DonchianTrapFade",
        "domain": "eth_usdc",
        "declared_sl_bps": 220.0,
        "declared_tp_bps": 340.0,
        "declared_hold_seconds": 18000,  # ~5 hours average hold
        "warmup_bars": 35,
    }

    def initialize(self, ctx: BarContext) -> None:
        self.lookback = 20
        self.cooldown_bars = 4
        self.max_hold_bars = 6
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
        self.entry_bar = -999

    def on_bar(self, ctx: BarContext) -> Optional[Signal]:
        closes = ctx.closes(self.lookback + 15)
        highs = ctx.highs(self.lookback + 15)
        lows = ctx.lows(self.lookback + 15)

        if len(closes) < self.lookback + 15:
            return None

        # Manage open position via time-decay exit (holding a few bars)
        if ctx.has_position():
            bars_held = ctx.bar_index - self.entry_bar
            if self.entry_bar > 0 and bars_held >= self.max_hold_bars:
                return ctx.signal(
                    "flat",
                    confidence=0.6,
                    metadata={
                        "reason": "time_decay_exit",
                        "bars_held": bars_held,
                        "close": ctx.bar.close
                    }
                )
            return None

        # Cooldown guard after trade exit to prevent friction churn
        if (ctx.bar_index - self.last_trade_bar) < self.cooldown_bars:
            return None

        # Market stress / crisis filter: avoid fading breakouts during extreme regime dislocation
        regime = ctx.market.get("regime", "NORMAL")
        if regime in ("CRISIS", "MELTDOWN"):
            return None

        atr = self._atr(highs, lows, closes, 14)
        if atr is None or atr <= 0:
            return None

        # Range prior to current candle (bars [-(lookback+1) : -1])
        prior_highs = highs[-(self.lookback + 1):-1]
        prior_lows = lows[-(self.lookback + 1):-1]
        range_high = max(prior_highs)
        range_low = min(prior_lows)

        current_high = ctx.bar.high
        current_low = ctx.bar.low
        current_close = ctx.bar.close

        # Minimum wick penetration relative to ATR to ensure an authentic stop-hunt poke
        min_penetration = 0.25 * atr

        # Bull Trap / Failed Upside Breakout:
        # High pokes cleanly above range_high, but close snaps back inside range_high
        is_bull_trap = (
            (current_high >= range_high + min_penetration) and
            (current_close < range_high) and
            (current_close < (current_high - 0.35 * (current_high - current_low)))
        )

        # Bear Trap / Failed Downside Breakdown:
        # Low pokes cleanly below range_low, but close snaps back inside range_low
        is_bear_trap = (
            (current_low <= range_low - min_penetration) and
            (current_close > range_low) and
            (current_close > (current_low + 0.35 * (current_high - current_low)))
        )

        if is_bull_trap:
            # Stop loss placed beyond the rejection wick
            wick_distance_bps = ((current_high - current_close) / current_close) * 10000.0
            sl_bps = max(140.0, min(wick_distance_bps + 30.0, 260.0))
            tp_bps = sl_bps * 1.5

            self.entry_bar = ctx.bar_index
            self.last_trade_bar = ctx.bar_index
            return ctx.signal(
                "short",
                confidence=0.72,
                stop_loss_bps=sl_bps,
                take_profit_bps=tp_bps,
                metadata={
                    "reason": "failed_breakout_bull_trap",
                    "range_high": range_high,
                    "bar_high": current_high,
                    "bar_close": current_close,
                    "wick_bps": wick_distance_bps,
                    "atr": atr
                }
            )

        if is_bear_trap:
            # Stop loss placed beyond the rejection wick
            wick_distance_bps = ((current_close - current_low) / current_close) * 10000.0
            sl_bps = max(140.0, min(wick_distance_bps + 30.0, 260.0))
            tp_bps = sl_bps * 1.5

            self.entry_bar = ctx.bar_index
            self.last_trade_bar = ctx.bar_index
            return ctx.signal(
                "long",
                confidence=0.72,
                stop_loss_bps=sl_bps,
                take_profit_bps=tp_bps,
                metadata={
                    "reason": "failed_breakdown_bear_trap",
                    "range_low": range_low,
                    "bar_low": current_low,
                    "bar_close": current_close,
                    "wick_bps": wick_distance_bps,
                    "atr": atr
                }
            )

        return None