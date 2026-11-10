from domains.strategy_contract import Strategy, BarContext, Signal
from typing import Optional, Dict, Any
import math

class EthSolBetaConvergence(Strategy):
    METADATA = {
        "name": "EthSol Beta Convergence",
        "domain": "sol_usdc",
        "declared_sl_bps": 200.0,
        "declared_tp_bps": 400.0,
        "declared_hold_seconds": 7200, # 2 hours
        "warmup_bars": 50,
        "required_features": ["eth_return_pct"],
    }

    def initialize(self, ctx: BarContext) -> None:
        self.eth_return_lookback = 4 # bars for ETH return
        self.sol_return_lookback = 4 # bars for SOL return
        self.divergence_threshold = 0.005 # 0.5% divergence
        self.min_eth_move = 0.005 # ETH must move at least 0.5%
        self.cooldown_bars = 5
        self.last_signal_bar = -self.cooldown_bars

        # For moving average convergence
        self.fast_ema_period = 10
        self.slow_ema_period = 30

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

        if ctx.bar_index - self.last_signal_bar < self.cooldown_bars:
            return None

        closes = ctx.closes(self.slow_ema_period)
        if len(closes) < self.slow_ema_period:
            return None

        # Calculate ETH return over lookback period
        eth_returns = [ctx.features.get("eth_return_pct", 0.0) for _ in range(self.eth_return_lookback)]
        # For simplicity, let's assume eth_return_pct is a 1-bar return.
        # If it's cumulative, we might need to adjust. Assuming it's a recent 1-bar change.
        recent_eth_return = eth_returns[-1] # Most recent ETH 1-bar return

        # Calculate SOL return over the same period
        sol_closes_short = ctx.closes(self.sol_return_lookback + 1)
        if len(sol_closes_short) < self.sol_return_lookback + 1:
            return None
        recent_sol_return = (sol_closes_short[-1] - sol_closes_short[-1 - self.sol_return_lookback]) / sol_closes_short[-1 - self.sol_return_lookback]

        # Check for significant ETH move
        if abs(recent_eth_return) < self.min_eth_move:
            return None

        # Calculate EMAs for SOL price
        fast_ema = self._ema(closes, self.fast_ema_period)
        slow_ema = self._ema(closes, self.slow_ema_period)

        if fast_ema is None or slow_ema is None:
            return None

        signal_direction = None
        confidence = 0.0
        reason = ""
        metadata = {}

        # ETH moves up significantly, SOL lags behind
        if recent_eth_return > self.min_eth_move and (recent_eth_return - recent_sol_return) > self.divergence_threshold:
            # Check for SOL's current trend for confirmation
            if fast_ema > slow_ema and closes[-1] > fast_ema: # SOL showing bullish momentum
                if not ctx.has_position() or ctx.position_direction() == "short":
                    signal_direction = "long"
                    confidence = min(0.8, (recent_eth_return - recent_sol_return) * 10.0) # Scale confidence
                    reason = "eth_leads_sol_long_divergence"
                    metadata = {
                        "eth_return": recent_eth_return,
                        "sol_return": recent_sol_return,
                        "divergence": recent_eth_return - recent_sol_return,
                        "sol_fast_ema": fast_ema,
                        "sol_slow_ema": slow_ema,
                        "current_price": closes[-1],
                    }

        # ETH moves down significantly, SOL lags behind
        elif recent_eth_return < -self.min_eth_move and (recent_sol_return - recent_eth_return) > self.divergence_threshold:
            # Check for SOL's current trend for confirmation
            if fast_ema < slow_ema and closes[-1] < fast_ema: # SOL showing bearish momentum
                if not ctx.has_position() or ctx.position_direction() == "long":
                    signal_direction = "short"
                    confidence = min(0.8, (recent_sol_return - recent_eth_return) * 10.0) # Scale confidence
                    reason = "eth_leads_sol_short_divergence"
                    metadata = {
                        "eth_return": recent_eth_return,
                        "sol_return": recent_sol_return,
                        "divergence": recent_sol_return - recent_eth_return,
                        "sol_fast_ema": fast_ema,
                        "sol_slow_ema": slow_ema,
                        "current_price": closes[-1],
                    }

        # Exit logic: if a position is open and the beta convergence looks normalized
        if ctx.has_position():
            current_sol_return_from_entry = (closes[-1] - ctx.position["entry_price"]) / ctx.position["entry_price"]

            # If ETH has reversed or SOL has caught up
            if (ctx.position_direction() == "long" and (recent_eth_return < -self.min_eth_move / 2 or (recent_eth_return - recent_sol_return) < self.divergence_threshold / 2)) or \
               (ctx.position_direction() == "short" and (recent_eth_return > self.min_eth_move / 2 or (recent_sol_return - recent_eth_return) < self.divergence_threshold / 2)):
                signal_direction = "flat"
                confidence = 0.6
                reason = "beta_convergence_normalized"
                metadata = {
                    "entry_price": ctx.position["entry_price"],
                    "current_price": closes[-1],
                    "eth_return_at_exit": recent_eth_return,
                    "sol_return_at_exit": recent_sol_return,
                    "divergence_at_exit": recent_eth_return - recent_sol_return if ctx.position_direction() == "long" else recent_sol_return - recent_eth_return,
                }

        if signal_direction:
            self.last_signal_bar = ctx.bar_index
            return ctx.signal(
                signal_direction,
                confidence=confidence,
                metadata=metadata
            )

        return None

    def on_position_close(self, ctx: BarContext, position: Dict[str, Any]) -> None:
        # Not used for adaptive sizing in this version, but good for logging
        pass