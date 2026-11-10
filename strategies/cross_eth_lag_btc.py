from domains.strategy_contract import Strategy, BarContext, Signal
from typing import Optional, Dict, Any
import math

class EthLagBtcBetaConvergence(Strategy):
    METADATA = {
        "name": "Eth Lag Btc Beta Convergence",
        "domain": "btc_usdc",
        "declared_sl_bps": 250.0,
        "declared_tp_bps": 500.0,
        "declared_hold_seconds": 3600 * 6, # Aim for a 6-hour hold
        "warmup_bars": 50,
        "required_features": ["eth_return_pct"],
    }

    def initialize(self, ctx: BarContext) -> None:
        self.eth_return_period = 3 # Look at recent ETH returns (3 hours)
        self.btc_return_period = 3 # Look at recent BTC returns (3 hours)
        self.cooldown_bars = 12 # Cooldown for 12 hours after a trade
        self.last_trade_bar = -self.cooldown_bars

        # Thresholds for "fresh decisive move"
        self.eth_move_threshold_long = 0.005 # 0.5% move in 3 hours
        self.eth_move_threshold_short = -0.005 # -0.5% move in 3 hours
        self.btc_lag_threshold = 0.002 # BTC should lag by at least 0.2%

        # EMA for trend filtering (optional, but good for advanced)
        self.ema_period = 20

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
        if ctx.bar_index - self.last_trade_bar < self.cooldown_bars:
            return None

        closes = ctx.closes(self.ema_period + 1) # +1 for EMA calculation
        if len(closes) < self.ema_period + 1:
            return None

        current_price = ctx.bar.close
        ema = self._ema(closes, self.ema_period)
        if ema is None:
            return None

        # Get recent ETH and BTC returns
        eth_returns = ctx.features.get("eth_return_pct", 0.0) / 100.0 # Convert % to decimal
        btc_returns = ctx.features.get("btc_return_pct", 0.0) / 100.0 # Convert % to decimal

        # Check for open position
        if ctx.has_position():
            # Check for normalization or reversal for exit
            if ctx.position_direction() == "long":
                # If BTC caught up or ETH reversed, consider exiting
                if btc_returns >= eth_returns * 0.8 or eth_returns < 0: # BTC caught up 80% or ETH turned negative
                     self.last_trade_bar = ctx.bar_index
                     return ctx.signal("flat", confidence=0.7, metadata={"reason": "long_beta_convergence_or_eth_reversal", "eth_return_pct": eth_returns*100, "btc_return_pct": btc_returns*100})
            elif ctx.position_direction() == "short":
                # If BTC caught up or ETH reversed, consider exiting
                if btc_returns <= eth_returns * 0.8 or eth_returns > 0: # BTC caught up 80% or ETH turned positive
                     self.last_trade_bar = ctx.bar_index
                     return ctx.signal("flat", confidence=0.7, metadata={"reason": "short_beta_convergence_or_eth_reversal", "eth_return_pct": eth_returns*100, "btc_return_pct": btc_returns*100})
            return None # Hold if no exit condition met

        # Entry logic
        signal_confidence = 0.0

        # Long entry: ETH moved up decisively, BTC lagged, and price is above EMA
        if eth_returns > self.eth_move_threshold_long and \
           (eth_returns - btc_returns) > self.btc_lag_threshold and \
           current_price > ema:
            signal_confidence = 0.7 # High confidence for this setup
            self.last_trade_bar = ctx.bar_index
            return ctx.signal(
                "long",
                confidence=signal_confidence,
                metadata={
                    "reason": "eth_leader_btc_lag_long",
                    "eth_return_pct": eth_returns * 100,
                    "btc_return_pct": btc_returns * 100,
                    "price": current_price,
                    "ema": ema,
                }
            )

        # Short entry: ETH moved down decisively, BTC lagged, and price is below EMA
        if eth_returns < self.eth_move_threshold_short and \
           (eth_returns - btc_returns) < -self.btc_lag_threshold and \
           current_price < ema:
            signal_confidence = 0.7 # High confidence for this setup
            self.last_trade_bar = ctx.bar_index
            return ctx.signal(
                "short",
                confidence=signal_confidence,
                metadata={
                    "reason": "eth_leader_btc_lag_short",
                    "eth_return_pct": eth_returns * 100,
                    "btc_return_pct": btc_returns * 100,
                    "price": current_price,
                    "ema": ema,
                }
            )

        return None

    def on_position_close(self, ctx: BarContext, position: Dict[str, Any]) -> None:
        # We already handle cooldown in on_bar by updating self.last_trade_bar
        pass