from domains.strategy_contract import Strategy, BarContext, Signal
from typing import Optional, Dict, Any
import math

class EthRsiDivergence(Strategy):
    METADATA = {
        "name": "EthRsiDivergence",
        "domain": "eth_usdc",
        "declared_sl_bps": 280.0,
        "declared_tp_bps": 450.0,
        "declared_hold_seconds": 21600,  # ~6 hours
        "warmup_bars": 45,
    }

    def initialize(self, ctx: BarContext) -> None:
        self.rsi_period = 14
        self.lookback = 30
        self.cooldown_bars = 8
        self.last_exit_bar = -999
        self.entry_bar = -999

    def _rsi_series(self, closes: list, period: int = 14) -> list:
        n = len(closes)
        if n < period + 1:
            return []
        
        gains = []
        losses = []
        for i in range(1, n):
            diff = closes[i] - closes[i - 1]
            gains.append(max(diff, 0.0))
            losses.append(max(-diff, 0.0))
        
        rsi_vals = []
        for i in range(period, n):
            sub_g = gains[i - period:i]
            sub_l = losses[i - period:i]
            avg_g = sum(sub_g) / period
            avg_l = sum(sub_l) / period
            if avg_l == 0:
                rsi_vals.append(100.0)
            else:
                rs = avg_g / avg_l
                rsi_vals.append(100.0 - (100.0 / (1.0 + rs)))
        return rsi_vals

    def on_position_close(self, ctx: BarContext, position: Dict[str, Any]) -> None:
        self.last_exit_bar = ctx.bar_index

    def on_bar(self, ctx: BarContext) -> Optional[Signal]:
        needed = self.lookback + self.rsi_period + 5
        closes = ctx.closes(needed)
        lows = ctx.lows(needed)
        highs = ctx.highs(needed)

        if len(closes) < needed:
            return None

        # Manage existing position exits
        if ctx.has_position():
            pos_dir = ctx.position_direction()
            bars_held = ctx.bar_index - self.entry_bar
            
            # Fast RSI normalization exit
            rsi_vals = self._rsi_series(closes[-20:], self.rsi_period)
            curr_rsi = rsi_vals[-1] if rsi_vals else 50.0
            
            if pos_dir == "long" and (curr_rsi >= 55.0 or bars_held >= 12):
                return ctx.signal("flat", confidence=0.7, metadata={"reason": "rsi_reversion_or_timeout", "rsi": curr_rsi, "bars_held": bars_held})
            elif pos_dir == "short" and (curr_rsi <= 45.0 or bars_held >= 12):
                return ctx.signal("flat", confidence=0.7, metadata={"reason": "rsi_reversion_or_timeout", "rsi": curr_rsi, "bars_held": bars_held})
            return None

        # Check post-exit cooldown
        if (ctx.bar_index - self.last_exit_bar) < self.cooldown_bars:
            return None

        # Compute full RSI series aligned with recent prices
        rsi_series = self._rsi_series(closes, self.rsi_period)
        if len(rsi_series) < self.lookback + 2:
            return None

        # Target the trailing evaluation window
        win_lows = lows[-self.lookback:]
        win_highs = highs[-self.lookback:]
        win_rsi = rsi_series[-self.lookback:]

        # Find swing pivot points (min/max excluding the immediate current bar)
        prior_lows = win_lows[:-2]
        prior_highs = win_highs[:-2]
        prior_rsi = win_rsi[:-2]

        min_prior_low = min(prior_lows)
        idx_min_low = prior_lows.index(min_prior_low)
        rsi_at_min_low = prior_rsi[idx_min_low]

        max_prior_high = max(prior_highs)
        idx_max_high = prior_highs.index(max_prior_high)
        rsi_at_max_high = prior_rsi[idx_max_high]

        curr_low = win_lows[-1]
        curr_high = win_highs[-1]
        curr_rsi = win_rsi[-1]

        # Regular Bullish Divergence: Lower price low, but higher RSI low
        # Only valid if prior low occurred in an oversold/depressed region
        if curr_low < min_prior_low and curr_rsi > (rsi_at_min_low + 3.0):
            if rsi_at_min_low < 38.0 and curr_rsi < 48.0:
                self.entry_bar = ctx.bar_index
                return ctx.signal(
                    "long",
                    confidence=0.75,
                    stop_loss_bps=self.METADATA["declared_sl_bps"],
                    take_profit_bps=self.METADATA["declared_tp_bps"],
                    horizon_seconds=self.METADATA["declared_hold_seconds"],
                    metadata={
                        "reason": "bullish_rsi_divergence",
                        "curr_low": curr_low,
                        "min_prior_low": min_prior_low,
                        "curr_rsi": curr_rsi,
                        "prior_rsi": rsi_at_min_low,
                    }
                )

        # Regular Bearish Divergence: Higher price high, but lower RSI high
        # Only valid if prior high occurred in an overbought/elevated region
        if curr_high > max_prior_high and curr_rsi < (rsi_at_max_high - 3.0):
            if rsi_at_max_high > 62.0 and curr_rsi > 52.0:
                self.entry_bar = ctx.bar_index
                return ctx.signal(
                    "short",
                    confidence=0.75,
                    stop_loss_bps=self.METADATA["declared_sl_bps"],
                    take_profit_bps=self.METADATA["declared_tp_bps"],
                    horizon_seconds=self.METADATA["declared_hold_seconds"],
                    metadata={
                        "reason": "bearish_rsi_divergence",
                        "curr_high": curr_high,
                        "max_prior_high": max_prior_high,
                        "curr_rsi": curr_rsi,
                        "prior_rsi": rsi_at_max_high,
                    }
                )

        return None