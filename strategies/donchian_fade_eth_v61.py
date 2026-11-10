from domains.strategy_contract import Strategy, BarContext, Signal
from typing import Optional, Dict, Any
import math

class DonchianTrapFade(Strategy):
    METADATA = {
        "name": "DonchianTrapFade",
        "domain": "eth_usdc",
        "declared_sl_bps": 220.0,
        "declared_tp_bps": 340.0,
        "declared_hold_seconds": 18000,  # 5 1-hour bars target
        "warmup_bars": 30,
        "required_features": ["fear_greed_index"],
    }

    def initialize(self, ctx: BarContext) -> None:
        self.lookback = 20
        self.min_wick_ratio = 0.30  # wick must be at least 30% of total candle range
        self.cooldown_bars = 4
        self.max_hold_bars = 6
        self.last_exit_bar = -100
        self.entry_bar = -100

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
        self.entry_bar = -100

    def on_bar(self, ctx: BarContext) -> Optional[Signal]:
        closes = ctx.closes(self.lookback + 2)
        highs = ctx.highs(self.lookback + 2)
        lows = ctx.lows(self.lookback + 2)

        if len(closes) < self.lookback + 2:
            return None

        # Filter crisis or meltdown regimes to avoid getting run over in runaway trends
        regime = ctx.market.get("regime", "NORMAL")
        if regime in ("CRISIS", "MELTDOWN"):
            if ctx.has_position():
                return ctx.signal(
                    "flat",
                    confidence=0.8,
                    metadata={"reason": "exit_crisis_regime", "regime": regime}
                )
            return None

        # Active position management: time-based exit to keep trades independent
        if ctx.has_position():
            bars_held = ctx.bar_index - self.entry_bar
            if bars_held >= self.max_hold_bars:
                return ctx.signal(
                    "flat",
                    confidence=0.6,
                    metadata={"reason": "time_based_horizon_exit", "bars_held": bars_held}
                )
            return None

        # Cooldown guard after position close
        if ctx.bar_index - self.last_exit_bar < self.cooldown_bars:
            return None

        atr = self._atr(highs, lows, closes, period=14)
        if atr is None or atr <= 0:
            return None

        # Reference Donchian channel: prior 20 bars excluding current bar
        prior_highs = highs[-self.lookback - 1:-1]
        prior_lows = lows[-self.lookback - 1:-1]
        highest_prev = max(prior_highs)
        lowest_prev = min(prior_lows)

        current_high = highs[-1]
        current_low = lows[-1]
        current_close = closes[-1]
        current_open = ctx.bar.open
        candle_range = current_high - current_low

        if candle_range < 0.25 * atr:
            return None

        fear_greed = ctx.features.get("fear_greed_index", 50)

        # Bull Trap / Bearish Sweep:
        # High pokes above the prior channel high by a meaningful margin, but closes back inside.
        upper_penetration = current_high - highest_prev
        upper_wick = current_high - max(current_open, current_close)

        if (current_high > highest_prev and
            current_close < highest_prev and
            upper_penetration >= 0.15 * atr and
            upper_wick >= self.min_wick_ratio * candle_range and
            fear_greed > 25):  # avoid fading sweeps during extreme structural panic

            wick_bps = (current_high - current_close) / current_close * 10000.0
            sl_bps = max(100.0, min(wick_bps * 1.25, 260.0))
            tp_bps = max(180.0, sl_bps * 1.6)

            self.entry_bar = ctx.bar_index
            return ctx.signal(
                "short",
                confidence=0.72,
                stop_loss_bps=sl_bps,
                take_profit_bps=tp_bps,
                horizon_seconds=18000,
                metadata={
                    "reason": "bull_trap_sweep_fade",
                    "highest_prev": highest_prev,
                    "bar_high": current_high,
                    "close": current_close,
                    "sl_bps": sl_bps,
                    "tp_bps": tp_bps,
                    "fear_greed": fear_greed
                }
            )

        # Bear Trap / Bullish Sweep:
        # Low pokes below prior channel low by a meaningful margin, but closes back inside.
        lower_penetration = lowest_prev - current_low
        lower_wick = min(current_open, current_close) - current_low

        if (current_low < lowest_prev and
            current_close > lowest_prev and
            lower_penetration >= 0.15 * atr and
            lower_wick >= self.min_wick_ratio * candle_range and
            fear_greed < 75):  # avoid fading sweeps during extreme structural euphoria

            wick_bps = (current_close - current_low) / current_close * 10000.0
            sl_bps = max(100.0, min(wick_bps * 1.25, 260.0))
            tp_bps = max(180.0, sl_bps * 1.6)

            self.entry_bar = ctx.bar_index
            return ctx.signal(
                "long",
                confidence=0.72,
                stop_loss_bps=sl_bps,
                take_profit_bps=tp_bps,
                horizon_seconds=18000,
                metadata={
                    "reason": "bear_trap_sweep_fade",
                    "lowest_prev": lowest_prev,
                    "bar_low": current_low,
                    "close": current_close,
                    "sl_bps": sl_bps,
                    "tp_bps": tp_bps,
                    "fear_greed": fear_greed
                }
            )

        return None