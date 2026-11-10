from domains.strategy_contract import Strategy, BarContext, Signal
from typing import Optional, Dict, Any
import math

class CrossAssetBTCLagSOL(Strategy):
    METADATA = {
        "name": "Cross Asset BTC Lag SOL",
        "domain": "sol_usdc",
        "declared_sl_bps": 250.0,
        "declared_tp_bps": 500.0,
        "declared_hold_seconds": 3600, # 1 hour bar, aim for 1-2 bars hold
        "warmup_bars": 60, # Need enough history for EMA and returns
        "required_features": ["btc_return_pct", "eth_return_pct"],
    }

    def initialize(self, ctx: BarContext) -> None:
        self.btc_return_period = 2 # bars for BTC return
        self.sol_return_period = 2 # bars for SOL return
        self.ema_period = 20 # For SOL price trend
        self.cooldown_bars = 5 # Cooldown after a trade
        self.last_signal_bar = -self.cooldown_bars - 1 # Initialize to allow immediate trade

    def _ema(self, values, period):
        if len(values) < period:
            return None
        k = 2.0 / (period + 1)
        ema = sum(values[:period]) / period
        for v in values[period:]:
            ema = v * k + ema * (1 - k)
        return ema

    def on_bar(self, ctx: BarContext) -> Optional[Signal]:
        if ctx.bar_index < self.METADATA["warmup_bars"]:
            return None

        # Cooldown logic
        if ctx.bar_index <= self.last_signal_bar + self.cooldown_bars:
            return None

        # Get closes for SOL
        sol_closes = ctx.closes(self.ema_period + 1)
        if len(sol_closes) < self.ema_period + 1:
            return None

        # Calculate SOL EMA
        sol_ema = self._ema(sol_closes[:-1], self.ema_period) # EMA up to previous bar
        if sol_ema is None:
            return None

        current_sol_close = ctx.bar.close

        # Get BTC and ETH returns from features
        btc_return_pct = ctx.features.get("btc_return_pct", 0.0)
        eth_return_pct = ctx.features.get("eth_return_pct", 0.0)

        # Market regime filters
        regime = ctx.market.get("regime", "NORMAL")
        trend_regime = ctx.market.get("trend_regime", "neutral")
        crisis_score = ctx.market.get("crisis_score", 0.0)

        # Entry conditions
        signal_direction = None
        confidence = 0.0
        reason = ""
        signal_metadata = {}

        # Long condition: BTC makes a strong positive move, SOL lags, and SOL is above its short-term EMA
        # Filter out extreme volatility and crisis regimes
        if regime in ["NORMAL", "HIGH_VOL"] and crisis_score < 0.2:
            if btc_return_pct > 0.5 and eth_return_pct > 0.0: # BTC strong positive, ETH also positive for broader market confirmation
                # Check if SOL has NOT yet caught up significantly
                # Compare current SOL close to its EMA to ensure it's not already overextended
                if current_sol_close > sol_ema: # SOL is in an uptrend
                    # Check if SOL's recent move is less than BTC's
                    # This is a simplified check for "lagging"
                    # We look for BTC to move strongly positive, and SOL to be positive but not as strongly (or just starting)
                    # A more sophisticated lag would compare return deltas over a very short period
                    # For simplicity, we assume if BTC is up strongly and SOL is above its EMA, SOL is likely to follow
                    if current_sol_close / sol_closes[-2] - 1 < btc_return_pct / 100.0 * 0.8: # SOL's 1-bar return is less than 80% of BTC's 1-day return
                        signal_direction = "long"
                        confidence = 0.6 + (min(btc_return_pct, 2.0) / 2.0) * 0.4 # Scale confidence by BTC strength
                        reason = "btc_positive_lag_sol_above_ema"
                        signal_metadata = {
                            "btc_return_pct": btc_return_pct,
                            "eth_return_pct": eth_return_pct,
                            "sol_current_close": current_sol_close,
                            "sol_ema": sol_ema,
                            "regime": regime,
                            "crisis_score": crisis_score,
                        }

            # Short condition: BTC makes a strong negative move, SOL lags, and SOL is below its short-term EMA
            if btc_return_pct < -0.5 and eth_return_pct < 0.0: # BTC strong negative, ETH also negative
                if current_sol_close < sol_ema: # SOL is in a downtrend
                     if current_sol_close / sol_closes[-2] - 1 > btc_return_pct / 100.0 * 0.8: # SOL's 1-bar return is less negative than 80% of BTC's 1-day return
                        signal_direction = "short"
                        confidence = 0.6 + (min(abs(btc_return_pct), 2.0) / 2.0) * 0.4 # Scale confidence by BTC strength
                        reason = "btc_negative_lag_sol_below_ema"
                        signal_metadata = {
                            "btc_return_pct": btc_return_pct,
                            "eth_return_pct": eth_return_pct,
                            "sol_current_close": current_sol_close,
                            "sol_ema": sol_ema,
                            "regime": regime,
                            "crisis_score": crisis_score,
                        }

        # Exit condition: relationship normalizes or time stop (handled by engine via declared_hold_seconds)
        # We define "normalizes" as SOL's recent price action aligning with or exceeding BTC's move.
        # However, the engine's take_profit_bps and stop_loss_bps will handle explicit exits.
        # For a "relationship normalization" exit, we can look for the momentum to reverse against us,
        # or for SOL to have fully caught up and potentially overshot.
        if ctx.has_position():
            pos = ctx.position
            if pos["direction"] == "long":
                # If BTC return turns negative or SOL's 1-bar return significantly exceeds BTC's
                if btc_return_pct < -0.2 or (current_sol_close / sol_closes[-2] - 1) > (btc_return_pct / 100.0 * 1.5): # SOL overshot BTC's move
                    return ctx.signal("flat", confidence=0.7, metadata={
                        "reason": "long_exit_btc_reversal_or_sol_overshot",
                        "btc_return_pct": btc_return_pct,
                        "sol_1bar_return": (current_sol_close / sol_closes[-2] - 1),
                        "entry_price": pos["entry_price"],
                        "current_price": current_sol_close
                    })
            elif pos["direction"] == "short":
                # If BTC return turns positive or SOL's 1-bar return significantly exceeds BTC's (in positive direction)
                if btc_return_pct > 0.2 or (current_sol_close / sol_closes[-2] - 1) < (btc_return_pct / 100.0 * 1.5): # SOL overshot BTC's move
                    return ctx.signal("flat", confidence=0.7, metadata={
                        "reason": "short_exit_btc_reversal_or_sol_overshot",
                        "btc_return_pct": btc_return_pct,
                        "sol_1bar_return": (current_sol_close / sol_closes[-2] - 1),
                        "entry_price": pos["entry_price"],
                        "current_price": current_sol_close
                    })

        if signal_direction and not ctx.has_position():
            self.last_signal_bar = ctx.bar_index
            return ctx.signal(
                signal_direction,
                confidence=confidence,
                stop_loss_bps=self.METADATA["declared_sl_bps"],
                take_profit_bps=self.METADATA["declared_tp_bps"],
                horizon_seconds=self.METADATA["declared_hold_seconds"],
                metadata=signal_metadata
            )

        return None

    def on_position_close(self, ctx: BarContext, position: Dict[str, Any]) -> None:
        # Use this for adaptive sizing or logging. For now, just a placeholder.
        # No action needed for this strategy beyond default cooldown.
        pass