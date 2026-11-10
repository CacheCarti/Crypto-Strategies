from domains.strategy_contract import Strategy, BarContext, Signal
from typing import Optional, Dict, Any
import math

class SolLeaderBTCFollower(Strategy):
    METADATA = {
        "name": "SolLeaderBTCFollower",
        "domain": "btc_usdc",
        "declared_sl_bps": 250.0,
        "declared_tp_bps": 500.0,
        "declared_hold_seconds": 3600 * 6, # Aim for 6-hour holds
        "warmup_bars": 50,
        "required_features": ["sol_return_pct", "btc_return_pct"],
    }

    def initialize(self, ctx: BarContext) -> None:
        self.fast_period = 10
        self.slow_period = 20
        self.cooldown_bars = 5
        self.last_trade_bar = -self.cooldown_bars

    def _sma(self, values, period):
        if len(values) < period:
            return None
        return sum(values[-period:]) / period

    def on_bar(self, ctx: BarContext) -> Optional[Signal]:
        closes = ctx.closes(self.slow_period + 1)
        if len(closes) < self.slow_period + 1:
            return None

        # Cooldown check
        if ctx.bar_index - self.last_trade_bar < self.cooldown_bars:
            return None

        # Get features
        sol_return_pct = ctx.features.get("sol_return_pct", 0.0)
        btc_return_pct = ctx.features.get("btc_return_pct", 0.0)

        # Calculate EMAs for BTC price trend
        ema_fast = self._sma(closes, self.fast_period) # Using SMA for simplicity, could be EMA
        ema_slow = self._sma(closes, self.slow_period) # Using SMA for simplicity, could be EMA

        if ema_fast is None or ema_slow is None:
            return None

        # BTC price trend: Fast EMA above Slow EMA
        btc_trend_bullish = ema_fast > ema_slow
        btc_trend_bearish = ema_fast < ema_slow

        # Causal driver: SOL return
        # "freshness" - we want a recent, clear move in SOL
        sol_strong_positive = sol_return_pct > 0.5 # e.g., SOL up 0.5% in the last hour
        sol_strong_negative = sol_return_pct < -0.5 # e.g., SOL down 0.5% in the last hour

        # Confirmation: BTC return agrees with SOL's direction
        btc_confirms_positive = btc_return_pct > 0.0
        btc_confirms_negative = btc_return_pct < 0.0

        # Stand down: BTC leader (BTC's own return) disagrees with SOL's direction
        # This condition is tricky as we are trading BTC based on SOL.
        # Let's interpret "leader disagrees" as BTC's own price trend opposing the SOL signal.
        # Or more simply, if BTC's immediate return is counter to SOL's strong move.

        # Signal conditions
        signal_direction = None
        signal_confidence = 0.0
        reason = "no_signal"
        signal_metadata = {}

        if not ctx.has_position():
            # Long entry conditions
            if sol_strong_positive and btc_trend_bullish and btc_confirms_positive:
                signal_direction = "long"
                signal_confidence = 0.75
                reason = "sol_leader_btc_follower_long"
                signal_metadata = {
                    "sol_return_pct": sol_return_pct,
                    "btc_return_pct": btc_return_pct,
                    "ema_fast": ema_fast,
                    "ema_slow": ema_slow,
                    "current_price": ctx.bar.close
                }
            # Short entry conditions
            elif sol_strong_negative and btc_trend_bearish and btc_confirms_negative:
                signal_direction = "short"
                signal_confidence = 0.75
                reason = "sol_leader_btc_follower_short"
                signal_metadata = {
                    "sol_return_pct": sol_return_pct,
                    "btc_return_pct": btc_return_pct,
                    "ema_fast": ema_fast,
                    "ema_slow": ema_slow,
                    "current_price": ctx.bar.close
                }

            if signal_direction:
                self.last_trade_bar = ctx.bar_index
                return ctx.signal(
                    signal_direction,
                    confidence=signal_confidence,
                    metadata=signal_metadata
                )
        else:
            # Exit conditions
            # Exit when the relationship normalizes or reverses
            # If long and SOL strength fades or BTC trend turns
            if ctx.position_direction() == "long":
                if not (sol_strong_positive or btc_trend_bullish):
                    signal_direction = "flat"
                    signal_confidence = 0.6
                    reason = "long_exit_sol_fading_or_btc_trend_reversing"
                    signal_metadata = {
                        "sol_return_pct": sol_return_pct,
                        "btc_return_pct": btc_return_pct,
                        "ema_fast": ema_fast,
                        "ema_slow": ema_slow,
                        "current_price": ctx.bar.close
                    }
            # If short and SOL weakness fades or BTC trend turns
            elif ctx.position_direction() == "short":
                if not (sol_strong_negative or btc_trend_bearish):
                    signal_direction = "flat"
                    signal_confidence = 0.6
                    reason = "short_exit_sol_fading_or_btc_trend_reversing"
                    signal_metadata = {
                        "sol_return_pct": sol_return_pct,
                        "btc_return_pct": btc_return_pct,
                        "ema_fast": ema_fast,
                        "ema_slow": ema_slow,
                        "current_price": ctx.bar.close
                    }
            
            if signal_direction:
                self.last_trade_bar = ctx.bar_index
                return ctx.signal(
                    signal_direction,
                    confidence=signal_confidence,
                    metadata=signal_metadata
                )

        return None

    def on_position_close(self, ctx: BarContext, position: Dict[str, Any]) -> None:
        # Not used for adaptive sizing in this version, but can be used for logging.
        pass