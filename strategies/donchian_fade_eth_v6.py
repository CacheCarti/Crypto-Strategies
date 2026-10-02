from domains.strategy_contract import Strategy, BarContext, Signal
from typing import Optional, Dict, Any
import math


class FailedBreakoutFade(Strategy):
    METADATA = {
        "name": "FailedBreakoutFade",
        "domain": "eth_usdc",
        "declared_sl_bps": 220.0,
        "declared_tp_bps": 360.0,
        "declared_hold_seconds": 21600,
        "warmup_bars": 35,
    }

    def initialize(self, ctx: BarContext) -> None:
        self.lookback = 24
        self.min_wick_bps = 15.0
        self.cooldown_bars = 6
        self.max_hold_bars = 7
        self.last_exit_bar = -999
        self.entry_bar = -999
        self.entry_wick_extreme = 0.0

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
        self.entry_bar = -999
        self.entry_wick_extreme = 0.0

    def on_bar(self, ctx: BarContext) -> Optional[Signal]:
        closes = ctx.closes(self.lookback + 5)
        highs = ctx.highs(self.lookback + 5)
        lows = ctx.lows(self.lookback + 5)

        if len(closes) < self.lookback + 2:
            return None

        current_close = ctx.bar.close
        current_open = ctx.bar.open
        current_high = ctx.bar.high
        current_low = ctx.bar.low

        atr = self._atr(highs, lows, closes, period=14)
        if atr is None or atr <= 0:
            return None

        # Position Management & Time-Based Exits
        if ctx.has_position():
            bars_held = ctx.bar_index - self.entry_bar if self.entry_bar > 0 else 0
            direction = ctx.position_direction()

            # Time stop exit
            if bars_held >= self.max_hold_bars:
                return ctx.signal(
                    "flat",
                    confidence=0.6,
                    metadata={
                        "reason": "time_horizon_reached",
                        "bars_held": bars_held,
                        "current_close": current_close,
                        "atr": atr,
                    },
                )

            # Reversion exit towards the median price of the range
            prior_closes = closes[-self.lookback - 1 : -1]
            median_price = sum(prior_closes) / len(prior_closes)

            if direction == "long" and current_close >= median_price:
                return ctx.signal(
                    "flat",
                    confidence=0.7,
                    metadata={
                        "reason": "mean_reversion_target_hit",
                        "current_close": current_close,
                        "median_target": median_price,
                    },
                )
            elif direction == "short" and current_close <= median_price:
                return ctx.signal(
                    "flat",
                    confidence=0.7,
                    metadata={
                        "reason": "mean_reversion_target_hit",
                        "current_close": current_close,
                        "median_target": median_price,
                    },
                )

            return None

        # Cooldown check
        if ctx.bar_index - self.last_exit_bar < self.cooldown_bars:
            return None

        # Regime safety filter
        market_regime = ctx.market.get("regime", "NORMAL")
        if market_regime in ("CRISIS", "MELTDOWN") or ctx.regime == "crisis":
            return None

        # Reference window excluding the current bar
        ref_highs = highs[-self.lookback - 1 : -1]
        ref_lows = lows[-self.lookback - 1 : -1]

        prior_high = max(ref_highs)
        prior_low = min(ref_lows)

        min_wick_dist_high = prior_high * (self.min_wick_bps / 10000.0)
        min_wick_dist_low = prior_low * (self.min_wick_bps / 10000.0)

        # Bull Trap / Failed Upside Breakout -> Go Short
        is_bull_trap = (
            current_high >= (prior_high + min_wick_dist_high)
            and current_close < prior_high
            and current_close < current_open
        )

        # Bear Trap / Failed Downside Breakout -> Go Long
        is_bear_trap = (
            current_low <= (prior_low - min_wick_dist_low)
            and current_close > prior_low
            and current_close > current_open
        )

        if is_bear_trap:
            wick_depth_bps = round(((prior_low - current_low) / prior_low) * 10000.0, 1)
            stop_dist_bps = min(max(wick_depth_bps + 40.0, 120.0), 280.0)
            take_profit_bps = round(stop_dist_bps * 1.6, 1)

            self.entry_bar = ctx.bar_index
            self.entry_wick_extreme = current_low

            return ctx.signal(
                "long",
                confidence=0.75,
                stop_loss_bps=stop_dist_bps,
                take_profit_bps=take_profit_bps,
                metadata={
                    "reason": "bear_trap_liquidity_sweep",
                    "prior_low": prior_low,
                    "wick_low": current_low,
                    "close": current_close,
                    "wick_depth_bps": wick_depth_bps,
                    "atr": atr,
                },
            )

        if is_bull_trap:
            wick_depth_bps = round(((current_high - prior_high) / prior_high) * 10000.0, 1)
            stop_dist_bps = min(max(wick_depth_bps + 40.0, 120.0), 280.0)
            take_profit_bps = round(stop_dist_bps * 1.6, 1)

            self.entry_bar = ctx.bar_index
            self.entry_wick_extreme = current_high

            return ctx.signal(
                "short",
                confidence=0.75,
                stop_loss_bps=stop_dist_bps,
                take_profit_bps=take_profit_bps,
                metadata={
                    "reason": "bull_trap_liquidity_sweep",
                    "prior_high": prior_high,
                    "wick_high": current_high,
                    "close": current_close,
                    "wick_depth_bps": wick_depth_bps,
                    "atr": atr,
                },
            )

        return None