from domains.strategy_contract import Strategy, BarContext, Signal
from typing import Optional, Dict, Any
import math

class EthBtcConfirmEth(Strategy):
    METADATA = {
        "name": "ETH BTC Confirm ETH Momentum",
        "domain": "eth_usdc",
        "declared_sl_bps": 200.0,
        "declared_tp_bps": 400.0,
        "declared_hold_seconds": 3600,
        "warmup_bars": 50,
        "required_features": ["btc_return_pct", "eth_return_pct"]
    }

    def initialize(self, ctx: BarContext) -> None:
        self.momentum_period = 10
        self.volatility_period = 20
        self.cooldown_bars = 5
        self.last_trade_bar = -self.cooldown_bars

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
        closes = ctx.closes(self.warmup_bars)
        eth_returns = ctx.features.get("eth_return_pct", 0.0)
        btc_returns = ctx.features.get("btc_return_pct", 0.0)

        if len(closes) < self.warmup_bars:
            return None

        # Cooldown logic
        if ctx.bar_index - self.last_trade_bar < self.cooldown_bars:
            return None

        # ETH momentum (price-based entry)
        eth_momentum = closes[-1] - closes[-self.momentum_period]
        
        # BTC volatility check
        btc_closes = ctx.market.get("btc_closes_1h", []) # Assuming BTC closes are available in market context
        if len(btc_closes) < self.volatility_period:
            return None
        
        btc_volatility = self._stddev(btc_closes, self.volatility_period)
        
        # Crisis score from market context
        crisis_score = ctx.market.get("crisis_score", 0.0)

        # BTC volatility threshold (proxy for crisis spike)
        # Using a simple threshold; adjust based on backtest results
        btc_volatility_threshold = 0.02 # Example: 2% std dev in 20 bars
        
        # Check for existing position
        has_position = ctx.has_position()
        position_direction = ctx.position_direction()

        # Exit logic
        if has_position:
            if position_direction == "long":
                # Exit long if ETH momentum reverses or BTC confirmation is lost
                if eth_momentum < 0 or btc_returns < 0:
                    self.last_trade_bar = ctx.bar_index
                    return ctx.signal("flat", confidence=0.7, metadata={
                        "reason": "eth_momentum_reversal_or_btc_loss",
                        "eth_momentum": eth_momentum,
                        "btc_return_pct": btc_returns,
                        "current_price": ctx.bar.close
                    })
            elif position_direction == "short":
                # Exit short if ETH momentum reverses or BTC confirmation is lost
                if eth_momentum > 0 or btc_returns > 0:
                    self.last_trade_bar = ctx.bar_index
                    return ctx.signal("flat", confidence=0.7, metadata={
                        "reason": "eth_momentum_reversal_or_btc_loss",
                        "eth_momentum": eth_momentum,
                        "btc_return_pct": btc_returns,
                        "current_price": ctx.bar.close
                    })
            return None # Hold existing position

        # Entry logic
        # Long entry conditions
        if eth_momentum > 0.005 * closes[-1] and \
           btc_returns > 0.001 and \
           btc_volatility is not None and btc_volatility < btc_volatility_threshold and \
           crisis_score < 0.3: # Avoid trading during high crisis
            
            self.last_trade_bar = ctx.bar_index
            return ctx.signal("long", confidence=0.8, metadata={
                "reason": "eth_btc_leader_confirmation_long",
                "eth_momentum_pct": (eth_momentum / closes[-1]) * 100,
                "btc_return_pct": btc_returns,
                "btc_volatility": btc_volatility,
                "crisis_score": crisis_score,
                "current_price": ctx.bar.close
            })

        # Short entry conditions
        elif eth_momentum < -0.005 * closes[-1] and \
             btc_returns < -0.001 and \
             btc_volatility is not None and btc_volatility < btc_volatility_threshold and \
             crisis_score < 0.3: # Avoid trading during high crisis

            self.last_trade_bar = ctx.bar_index
            return ctx.signal("short", confidence=0.8, metadata={
                "reason": "eth_btc_leader_confirmation_short",
                "eth_momentum_pct": (eth_momentum / closes[-1]) * 100,
                "btc_return_pct": btc_returns,
                "btc_volatility": btc_volatility,
                "crisis_score": crisis_score,
                "current_price": ctx.bar.close
            })

        return None

    def on_position_close(self, ctx: BarContext, position: Dict[str, Any]) -> None:
        # Optional: You can use this for logging or adaptive sizing
        pass