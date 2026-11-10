from domains.strategy_contract import Strategy, BarContext, Signal
from typing import Optional, Dict, Any
import math

class CrossAssetEthSolTrend(Strategy):
    METADATA = {
        "name": "CrossAssetEthSolTrend",
        "domain": "sol_usdc",
        "declared_sl_bps": 250.0,
        "declared_tp_bps": 500.0,
        "declared_hold_seconds": 7200,  # 2 hours average hold
        "warmup_bars": 50,  # For 20-period EMA, and 24-period return lookback
        "required_features": ["eth_return_pct", "sol_return_pct"],
    }

    def initialize(self, ctx: BarContext) -> None:
        self.ema_period = 20
        self.return_lookback = 24  # Look back 24 bars for return
        self.cooldown_bars = 5
        self.last_trade_bar = -self.cooldown_bars

    def _ema(self, values, period):
        if len(values) < period:
            return None
        k = 2.0 / (period + 1)
        ema = sum(values[:period]) / period
        for v in values[period:]:
            ema = v * k + ema * (1 - k)
        return ema

    def on_bar(self, ctx: BarContext) -> Optional[Signal]:
        closes = ctx.closes(self.ema_period + 1)
        if len(closes) < self.METADATA["warmup_bars"]:
            return None

        # Cooldown logic
        if ctx.bar_index - self.last_trade_bar < self.cooldown_bars:
            return None

        current_price = ctx.bar.close
        ema = self._ema(closes, self.ema_period)

        if ema is None:
            return None

        # Get ETH and SOL returns
        eth_returns = ctx.features.get("eth_return_pct", 0.0)
        sol_returns = ctx.features.get("sol_return_pct", 0.0)

        # Market regime filter
        regime = ctx.market.get("regime", "NORMAL")
        trend_regime = ctx.market.get("trend_regime", "neutral")

        # Only trade in NORMAL or HIGH_VOL regimes, and when the trend is not MELTDOWN (crisis)
        if regime in ["CRISIS", "MELTDOWN"]:
            return None
        
        # Causal driver: ETH leads SOL
        # Long condition: ETH up, SOL confirms up
        # Short condition: ETH down, SOL confirms down
        
        # Thresholds for returns
        return_threshold_long = 0.5  # 0.5% positive return for both
        return_threshold_short = -0.5 # 0.5% negative return for both

        # Confidence based on trend regime
        confidence = 0.6
        if trend_regime == "bull":
            confidence = 0.75
        elif trend_regime == "bear":
            confidence = 0.75
        elif trend_regime == "neutral":
            confidence = 0.5

        # Long entry logic
        if (not ctx.has_position() and
            eth_returns > return_threshold_long and
            sol_returns > return_threshold_long and
            current_price > ema and # Price above EMA
            trend_regime != "bear" # Avoid long in bear market
        ):
            self.last_trade_bar = ctx.bar_index
            return ctx.signal(
                "long",
                confidence=confidence,
                metadata={
                    "reason": "eth_sol_trend_confirm_long",
                    "eth_return_pct": eth_returns,
                    "sol_return_pct": sol_returns,
                    "price": current_price,
                    "ema": ema,
                    "market_regime": regime,
                    "trend_regime": trend_regime,
                }
            )

        # Short entry logic
        if (not ctx.has_position() and
            eth_returns < return_threshold_short and
            sol_returns < return_threshold_short and
            current_price < ema and # Price below EMA
            trend_regime != "bull" # Avoid short in bull market
        ):
            self.last_trade_bar = ctx.bar_index
            return ctx.signal(
                "short",
                confidence=confidence,
                metadata={
                    "reason": "eth_sol_trend_confirm_short",
                    "eth_return_pct": eth_returns,
                    "sol_return_pct": sol_returns,
                    "price": current_price,
                    "ema": ema,
                    "market_regime": regime,
                    "trend_regime": trend_regime,
                }
            )

        # Exit logic if a position is open
        if ctx.has_position():
            # Exit if the leader (ETH) disagrees with the current position, or SOL trend reverses
            if ctx.position_direction() == "long":
                if eth_returns < -0.1 or sol_returns < -0.1 or current_price < ema * 0.99: # 1% drop below EMA
                    self.last_trade_bar = ctx.bar_index
                    return ctx.signal(
                        "flat",
                        confidence=0.5,
                        metadata={
                            "reason": "eth_sol_disagreement_long_exit",
                            "eth_return_pct": eth_returns,
                            "sol_return_pct": sol_returns,
                            "price": current_price,
                            "ema": ema,
                            "market_regime": regime,
                            "trend_regime": trend_regime,
                        }
                    )
            elif ctx.position_direction() == "short":
                if eth_returns > 0.1 or sol_returns > 0.1 or current_price > ema * 1.01: # 1% rise above EMA
                    self.last_trade_bar = ctx.bar_index
                    return ctx.signal(
                        "flat",
                        confidence=0.5,
                        metadata={
                            "reason": "eth_sol_disagreement_short_exit",
                            "eth_return_pct": eth_returns,
                            "sol_return_pct": sol_returns,
                            "price": current_price,
                            "ema": ema,
                            "market_regime": regime,
                            "trend_regime": trend_regime,
                        }
                    )
        return None

    def on_position_close(self, ctx: BarContext, position: Dict[str, Any]) -> None:
        # Optional: Log position close details or adjust internal state based on performance
        pass