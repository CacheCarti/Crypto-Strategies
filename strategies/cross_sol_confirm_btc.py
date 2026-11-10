from domains.strategy_contract import Strategy, BarContext, Signal
from typing import Optional, Dict, Any
import math

class CrossAssetTrendConfirm(Strategy):
    METADATA = {
        "name": "Cross Asset Trend Confirmation (BTC)",
        "domain": "btc_usdc",
        "declared_sl_bps": 250.0,
        "declared_tp_bps": 500.0,
        "declared_hold_seconds": 3600 * 6, # Aim for 6 hours
        "warmup_bars": 50,
        "required_features": ["sol_return_pct", "btc_return_pct"],
    }

    def initialize(self, ctx: BarContext) -> None:
        self.fast_ema_period = 10
        self.slow_ema_period = 30
        self.cooldown_bars = 5 # Cooldown after an exit
        self.bars_since_last_trade = 0
        self.min_btc_return_pct = 0.5 # Minimum BTC return to confirm trend
        self.min_sol_return_pct = 0.7 # Minimum SOL return for leadership signal
        self.max_sol_btc_diff_pct = 0.5 # Max difference between SOL and BTC returns for agreement
        self.exit_threshold_pct = 0.1 # When SOL/BTC return difference normalizes for exit

    def _ema(self, values, period):
        if len(values) < period:
            return None
        k = 2.0 / (period + 1)
        ema = sum(values[:period]) / period
        for v in values[period:]:
            ema = v * k + ema * (1 - k)
        return ema

    def on_bar(self, ctx: BarContext) -> Optional[Signal]:
        self.bars_since_last_trade += 1

        closes = ctx.closes(self.warmup_bars + 1)
        if len(closes) < self.warmup_bars:
            return None

        # Calculate EMAs for BTC (target asset)
        fast_ema = self._ema(closes, self.fast_ema_period)
        slow_ema = self._ema(closes, self.slow_ema_period)

        if fast_ema is None or slow_ema is None:
            return None

        current_price = ctx.bar.close

        # Get cross-asset returns
        sol_return_pct = ctx.features.get("sol_return_pct", 0.0)
        btc_return_pct = ctx.features.get("btc_return_pct", 0.0)

        # Entry conditions
        if not ctx.has_position() and self.bars_since_last_trade >= self.cooldown_bars:
            # Bullish: SOL leads, BTC confirms, BTC EMAs cross up
            if sol_return_pct > self.min_sol_return_pct and \
               btc_return_pct > self.min_btc_return_pct and \
               abs(sol_return_pct - btc_return_pct) < self.max_sol_btc_diff_pct and \
               fast_ema > slow_ema and \
               ctx.closes(2)[-1] <= ctx.opens(2)[-1] and current_price > ctx.closes(2)[-1]: # Close-confirmed entry

                self.bars_since_last_trade = 0
                return ctx.signal(
                    "long",
                    confidence=0.7,
                    metadata={
                        "reason": "cross_asset_bullish_confirmation",
                        "sol_return_pct": sol_return_pct,
                        "btc_return_pct": btc_return_pct,
                        "fast_ema": fast_ema,
                        "slow_ema": slow_ema,
                        "current_price": current_price
                    }
                )

            # Bearish: SOL leads down, BTC confirms down, BTC EMAs cross down
            elif sol_return_pct < -self.min_sol_return_pct and \
                 btc_return_pct < -self.min_btc_return_pct and \
                 abs(sol_return_pct - btc_return_pct) < self.max_sol_btc_diff_pct and \
                 fast_ema < slow_ema and \
                 ctx.closes(2)[-1] >= ctx.opens(2)[-1] and current_price < ctx.closes(2)[-1]: # Close-confirmed entry

                self.bars_since_last_trade = 0
                return ctx.signal(
                    "short",
                    confidence=0.7,
                    metadata={
                        "reason": "cross_asset_bearish_confirmation",
                        "sol_return_pct": sol_return_pct,
                        "btc_return_pct": btc_return_pct,
                        "fast_ema": fast_ema,
                        "slow_ema": slow_ema,
                        "current_price": current_price
                    }
                )

        # Exit conditions
        if ctx.has_position():
            # Exit if the cross-asset relationship normalizes or reverses
            if ctx.position_direction() == "long":
                # If SOL return drops or BTC return drops, or the difference becomes too large
                if sol_return_pct < self.exit_threshold_pct or \
                   btc_return_pct < self.exit_threshold_pct or \
                   abs(sol_return_pct - btc_return_pct) > self.max_sol_btc_diff_pct * 2: # Exit if difference widens significantly
                    return ctx.signal(
                        "flat",
                        confidence=0.5,
                        metadata={
                            "reason": "cross_asset_long_relationship_normalized",
                            "sol_return_pct": sol_return_pct,
                            "btc_return_pct": btc_return_pct,
                            "current_price": current_price
                        }
                    )
            elif ctx.position_direction() == "short":
                # If SOL return rises or BTC return rises, or the difference becomes too large
                if sol_return_pct > -self.exit_threshold_pct or \
                   btc_return_pct > -self.exit_threshold_pct or \
                   abs(sol_return_pct - btc_return_pct) > self.max_sol_btc_diff_pct * 2:
                    return ctx.signal(
                        "flat",
                        confidence=0.5,
                        metadata={
                            "reason": "cross_asset_short_relationship_normalized",
                            "sol_return_pct": sol_return_pct,
                            "btc_return_pct": btc_return_pct,
                            "current_price": current_price
                        }
                    )
        return None

    def on_position_close(self, ctx: BarContext, position: Dict[str, Any]) -> None:
        self.bars_since_last_trade = 0 # Reset cooldown on any position close