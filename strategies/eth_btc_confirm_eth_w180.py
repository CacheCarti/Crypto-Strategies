from domains.strategy_contract import Strategy, BarContext, Signal
from typing import Optional, Dict, Any
import math

class LeaderConfirmationMomentum(Strategy):
    METADATA = {
        "name": "Leader Confirmation Momentum",
        "domain": "eth_usdc",
        "declared_sl_bps": 250.0,
        "declared_tp_bps": 500.0,
        "declared_hold_seconds": 3600 * 6, # Aim for 6-hour average hold
        "warmup_bars": 50,
        "required_features": ["btc_return_pct", "eth_return_pct"],
    }

    def initialize(self, ctx: BarContext) -> None:
        self.eth_momentum_period = 10
        self.btc_momentum_period = 10
        self.volatility_period = 20
        self.cooldown_bars = 5 # Cooldown after a trade
        self.last_trade_bar_index = -self.cooldown_bars

    def _sma(self, values, period):
        if len(values) < period:
            return None
        return sum(values[-period:]) / period

    def _stddev(self, values, period):
        if len(values) < period:
            return None
        slice_v = values[-period:]
        mean = sum(slice_v) / period
        variance = sum((x - mean) ** 2 for x in slice_v) / period
        return math.sqrt(variance)

    def on_bar(self, ctx: BarContext) -> Optional[Signal]:
        closes = ctx.closes(self.eth_momentum_period + self.btc_momentum_period + self.volatility_period)
        eth_returns = ctx.features.get("eth_return_pct", [])
        btc_returns = ctx.features.get("btc_return_pct", [])

        # Ensure enough data for all indicators and features
        if len(closes) < self.warmup_bars or len(eth_returns) < self.eth_momentum_period or len(btc_returns) < self.btc_momentum_period + self.volatility_period:
            return None

        # Cooldown logic
        if ctx.bar_index < self.last_trade_bar_index + self.cooldown_bars:
            return None

        current_eth_close = closes[-1]

        # ETH Momentum (price-based trigger)
        eth_momentum_pct = (current_eth_close - closes[-self.eth_momentum_period]) / closes[-self.eth_momentum_period] * 100
        
        # BTC Momentum (directional confirmation)
        current_btc_return = btc_returns[-1]
        
        # BTC Volatility (regime filter for crisis spikes)
        # We need historical BTC returns to calculate volatility
        btc_returns_for_vol = btc_returns[-self.volatility_period:]
        if len(btc_returns_for_vol) < self.volatility_period:
            return None
        btc_volatility = self._stddev(btc_returns_for_vol, self.volatility_period)
        
        # Crisis score from market context for additional filtering
        crisis_score = ctx.market.get("crisis_score", 0.0)

        # Exit logic
        if ctx.has_position():
            position = ctx.position
            # Momentum exhaustion: ETH momentum reverses significantly
            if position["direction"] == "long" and eth_momentum_pct < 0:
                self.last_trade_bar_index = ctx.bar_index
                return ctx.signal("flat", confidence=0.7, metadata={"reason": "eth_momentum_exhaustion", "eth_momentum_pct": eth_momentum_pct, "btc_return_pct": current_btc_return})
            elif position["direction"] == "short" and eth_momentum_pct > 0:
                self.last_trade_bar_index = ctx.bar_index
                return ctx.signal("flat", confidence=0.7, metadata={"reason": "eth_momentum_exhaustion", "eth_momentum_pct": eth_momentum_pct, "btc_return_pct": current_btc_return})
            
            # BTC confirmation loss: BTC return contradicts ETH momentum or BTC volatility spikes
            if position["direction"] == "long" and (current_btc_return < 0 or btc_volatility > 0.02 or crisis_score > 0.3): # 0.02 is an arbitrary threshold for "high" daily volatility, adjust as needed
                self.last_trade_bar_index = ctx.bar_index
                return ctx.signal("flat", confidence=0.6, metadata={"reason": "btc_confirmation_loss_long", "eth_momentum_pct": eth_momentum_pct, "btc_return_pct": current_btc_return, "btc_volatility": btc_volatility, "crisis_score": crisis_score})
            elif position["direction"] == "short" and (current_btc_return > 0 or btc_volatility > 0.02 or crisis_score > 0.3):
                self.last_trade_bar_index = ctx.bar_index
                return ctx.signal("flat", confidence=0.6, metadata={"reason": "btc_confirmation_loss_short", "eth_momentum_pct": eth_momentum_pct, "btc_return_pct": current_btc_return, "btc_volatility": btc_volatility, "crisis_score": crisis_score})

            return None # Hold position if no exit condition met

        # Entry logic (only if no position is open)
        
        # BTC Volatility Filter: Avoid trading during BTC crisis spikes
        if btc_volatility > 0.02 or crisis_score > 0.3:
            return None

        # Long Entry: ETH shows positive momentum, BTC agrees, and not in high crisis
        if eth_momentum_pct > 0.5 and current_btc_return > 0.1: # ETH > 0.5% gain, BTC > 0.1% gain
            self.last_trade_bar_index = ctx.bar_index
            return ctx.signal(
                "long",
                confidence=0.7,
                metadata={
                    "reason": "eth_btc_leader_confirmation_long",
                    "eth_momentum_pct": eth_momentum_pct,
                    "btc_return_pct": current_btc_return,
                    "btc_volatility": btc_volatility,
                    "crisis_score": crisis_score
                }
            )

        # Short Entry: ETH shows negative momentum, BTC agrees, and not in high crisis
        if eth_momentum_pct < -0.5 and current_btc_return < -0.1: # ETH < -0.5% loss, BTC < -0.1% loss
            self.last_trade_bar_index = ctx.bar_index
            return ctx.signal(
                "short",
                confidence=0.7,
                metadata={
                    "reason": "eth_btc_leader_confirmation_short",
                    "eth_momentum_pct": eth_momentum_pct,
                    "btc_return_pct": current_btc_return,
                    "btc_volatility": btc_volatility,
                    "crisis_score": crisis_score
                }
            )

        return None

    def on_position_close(self, ctx: BarContext, position: Dict[str, Any]) -> None:
        # Optional: Log position close for analysis or reset adaptive state
        pass