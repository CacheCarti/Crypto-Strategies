from domains.strategy_contract import Strategy, BarContext, Signal
from typing import Optional, Dict, Any
import math

class SolLagBtcConvergence(Strategy):
    METADATA = {
        "name": "SolLagBtcConvergence",
        "domain": "btc_usdc",
        "declared_sl_bps": 200.0,
        "declared_tp_bps": 400.0,
        "declared_hold_seconds": 7200, # 2 hours avg hold
        "warmup_bars": 60,
        "required_features": ["sol_return_pct", "btc_return_pct"],
    }

    def initialize(self, ctx: BarContext) -> None:
        self.lookback_period = 24
        self.return_threshold = 0.005 # 0.5% move
        self.cooldown_bars = 10
        self.last_trade_bar = -self.cooldown_bars * 2 # Initialize to allow first trade

        # Track previous states for transition detection
        self.prev_sol_strong_up = False
        self.prev_sol_strong_down = False

    def _sma(self, values, period):
        if len(values) < period:
            return None
        return sum(values[-period:]) / period

    def on_bar(self, ctx: BarContext) -> Optional[Signal]:
        closes = ctx.closes(self.lookback_period + 1)
        if len(closes) < self.lookback_period + 1:
            return None

        # Cooldown check
        if ctx.bar_index - self.last_trade_bar < self.cooldown_bars:
            return None

        sol_returns = [ctx.features.get("sol_return_pct", 0.0) for _ in range(self.lookback_period)]
        btc_returns = [ctx.features.get("btc_return_pct", 0.0) for _ in range(self.lookback_period)]

        if not sol_returns or not btc_returns:
            return None

        current_sol_return = ctx.features.get("sol_return_pct", 0.0)
        current_btc_return = ctx.features.get("btc_return_pct", 0.0)
        
        # Calculate recent average returns for comparison
        avg_sol_return_recent = sum(sol_returns[-5:]) / 5 if len(sol_returns) >= 5 else 0.0
        avg_btc_return_recent = sum(btc_returns[-5:]) / 5 if len(btc_returns) >= 5 else 0.0

        # Condition 1: SOL makes a fresh decisive move
        sol_strong_up = current_sol_return > self.return_threshold * 1.5 and avg_sol_return_recent > self.return_threshold
        sol_strong_down = current_sol_return < -self.return_threshold * 1.5 and avg_sol_return_recent < -self.return_threshold

        # Condition 2: BTC has not caught up (lagging)
        btc_lagging_up = current_btc_return < self.return_threshold * 0.5 and avg_btc_return_recent < self.return_threshold * 0.5
        btc_lagging_down = current_btc_return > -self.return_threshold * 0.5 and avg_btc_return_recent > -self.return_threshold * 0.5
        
        signal = None
        confidence = 0.0

        # Long entry logic
        if not ctx.has_position():
            if sol_strong_up and btc_lagging_up:
                # Transition detection: only fire if SOL just became strong up
                if not self.prev_sol_strong_up:
                    confidence = min(0.7, 0.4 + (current_sol_return / (self.return_threshold * 3)))
                    signal = ctx.signal(
                        "long",
                        confidence=confidence,
                        metadata={
                            "reason": "sol_leads_btc_up_convergence",
                            "sol_return": current_sol_return,
                            "btc_return": current_btc_return,
                            "avg_sol_return_recent": avg_sol_return_recent,
                            "avg_btc_return_recent": avg_btc_return_recent,
                        }
                    )
                    self.last_trade_bar = ctx.bar_index

            # Short entry logic
            elif sol_strong_down and btc_lagging_down:
                # Transition detection: only fire if SOL just became strong down
                if not self.prev_sol_strong_down:
                    confidence = min(0.7, 0.4 + (abs(current_sol_return) / (self.return_threshold * 3)))
                    signal = ctx.signal(
                        "short",
                        confidence=confidence,
                        metadata={
                            "reason": "sol_leads_btc_down_convergence",
                            "sol_return": current_sol_return,
                            "btc_return": current_btc_return,
                            "avg_sol_return_recent": avg_sol_return_recent,
                            "avg_btc_return_recent": avg_btc_return_recent,
                        }
                    )
                    self.last_trade_bar = ctx.bar_index

        # Exit logic: when relationship normalizes or reverses
        if ctx.has_position():
            pos_direction = ctx.position_direction()
            
            # Normalization check: if BTC has caught up or SOL has reversed
            if pos_direction == "long":
                # BTC caught up OR SOL started to reverse
                if current_btc_return > self.return_threshold * 0.8 or current_sol_return < -self.return_threshold * 0.5:
                    signal = ctx.signal(
                        "flat",
                        confidence=0.6,
                        metadata={
                            "reason": "long_exit_btc_converged_or_sol_reversed",
                            "sol_return": current_sol_return,
                            "btc_return": current_btc_return,
                        }
                    )
                    self.last_trade_bar = ctx.bar_index # Reset cooldown on exit as well
            elif pos_direction == "short":
                # BTC caught up OR SOL started to reverse
                if current_btc_return < -self.return_threshold * 0.8 or current_sol_return > self.return_threshold * 0.5:
                    signal = ctx.signal(
                        "flat",
                        confidence=0.6,
                        metadata={
                            "reason": "short_exit_btc_converged_or_sol_reversed",
                            "sol_return": current_sol_return,
                            "btc_return": current_btc_return,
                        }
                    )
                    self.last_trade_bar = ctx.bar_index # Reset cooldown on exit as well

        # Update previous states for transition detection
        self.prev_sol_strong_up = sol_strong_up
        self.prev_sol_strong_down = sol_strong_down

        return signal

    def on_position_close(self, ctx: BarContext, position: Dict[str, Any]) -> None:
        # Optional: Log or adapt based on position outcome
        pass