from domains.strategy_contract import Strategy, BarContext, Signal
from typing import Optional, Dict, Any
import math

class BtcEthBetaConvergence(Strategy):
    METADATA = {
        "name": "BTC-ETH Beta Convergence",
        "domain": "eth_usdc",
        "declared_sl_bps": 250.0,
        "declared_tp_bps": 500.0,
        "declared_hold_seconds": 3600 * 4,  # Intended avg hold time around 4 hours
        "warmup_bars": 60,  # Need enough bars for 24-period EMA and 24-hour BTC return
        "required_features": ["btc_return_pct", "eth_return_pct"],
    }

    def initialize(self, ctx: BarContext) -> None:
        self.leader_lookback = 4  # Bars for leader's "fresh decisive move"
        self.beta_period = 24     # Period for calculating simple beta-like relationship
        self.entry_threshold_bps = 80  # BTC move must be at least this much to be "decisive"
        self.divergence_threshold_bps = 50 # ETH must lag BTC by at least this much
        self.cooldown_bars = 6    # Cooldown period after a trade
        self.last_trade_bar = -self.cooldown_bars

        # For confidence scaling based on recent performance
        self.recent_wins = 0
        self.recent_losses = 0
        self.performance_lookback = 10 # Number of recent trades to consider

    def _ema(self, values, period):
        if len(values) < period:
            return None
        k = 2.0 / (period + 1)
        # Calculate initial SMA for the first EMA value
        ema = sum(values[:period]) / period
        for v in values[period:]:
            ema = v * k + ema * (1 - k)
        return ema

    def on_bar(self, ctx: BarContext) -> Optional[Signal]:
        closes = ctx.closes(self.METADATA["warmup_bars"])
        if len(closes) < self.METADATA["warmup_bars"]:
            return None

        # Cooldown logic
        if ctx.bar_index - self.last_trade_bar < self.cooldown_bars:
            return None

        btc_returns = [ctx.features.get("btc_return_pct", 0.0) for _ in range(self.leader_lookback)]
        eth_returns = [ctx.features.get("eth_return_pct", 0.0) for _ in range(self.leader_lookback)]

        # Get recent BTC and ETH returns (converted to bps)
        current_btc_return_bps = ctx.features.get("btc_return_pct", 0.0) * 100
        current_eth_return_bps = ctx.features.get("eth_return_pct", 0.0) * 100

        # Regime filter: Only trade in normal to high volatility
        # Avoid crisis regimes and strong bear trends for long, strong bull trends for short
        regime = ctx.market.get("regime", "NORMAL")
        trend_regime = ctx.market.get("trend_regime", "neutral")

        if regime == "CRISIS" or regime == "MELTDOWN":
            return None # Stand down in extreme crisis

        # Calculate a simple "beta" proxy over the last beta_period bars
        # This is a very simplified approach to get a feel for correlation
        btc_history = [ctx.features.get("btc_return_pct", 0.0) for _ in range(self.beta_period)]
        eth_history = [ctx.features.get("eth_return_pct", 0.0) for _ in range(self.beta_period)]

        if len(btc_history) < self.beta_period or len(eth_history) < self.beta_period:
            return None

        # Simple covariance/variance for a directional beta proxy
        # Avoided complex math to stay within performance budget
        btc_mean = sum(btc_history) / self.beta_period
        eth_mean = sum(eth_history) / self.beta_period
        
        covariance = sum((btc_history[i] - btc_mean) * (eth_history[i] - eth_mean) for i in range(self.beta_period))
        btc_variance = sum((x - btc_mean) ** 2 for x in btc_history)

        beta = covariance / btc_variance if btc_variance != 0 else 0.0

        # Calculate momentum for ETH using EMA crossover
        short_ema_period = 5
        long_ema_period = 20
        
        eth_closes = ctx.closes(max(short_ema_period, long_ema_period))
        if len(eth_closes) < max(short_ema_period, long_ema_period):
            return None

        short_ema = self._ema(eth_closes, short_ema_period)
        long_ema = self._ema(eth_closes, long_ema_period)

        if short_ema is None or long_ema is None:
            return None

        # Determine confidence based on recent performance
        base_confidence = 0.6
        if self.recent_wins + self.recent_losses > 0:
            win_rate = self.recent_wins / (self.recent_wins + self.recent_losses)
            # Scale confidence between 0.4 and 0.8 based on win rate
            confidence = 0.4 + (win_rate * 0.4)
        else:
            confidence = base_confidence

        # Entry logic: BTC makes a decisive move, ETH lags, and beta is positive
        if not ctx.has_position():
            # Long condition: BTC moved up significantly, ETH lagged, and ETH showing bullish momentum
            if (current_btc_return_bps > self.entry_threshold_bps and
                current_btc_return_bps - current_eth_return_bps > self.divergence_threshold_bps and
                beta > 0.5 and  # Require positive correlation
                short_ema > long_ema and # ETH bullish momentum
                trend_regime != "bear"): # Avoid longing in a strong bear trend

                self.last_trade_bar = ctx.bar_index
                return ctx.signal(
                    "long",
                    confidence=confidence,
                    metadata={
                        "reason": "btc_eth_beta_convergence_long",
                        "btc_return_bps": current_btc_return_bps,
                        "eth_return_bps": current_eth_return_bps,
                        "divergence_bps": current_btc_return_bps - current_eth_return_bps,
                        "beta_proxy": beta,
                        "short_ema": short_ema,
                        "long_ema": long_ema,
                        "regime": regime,
                        "trend_regime": trend_regime
                    }
                )

            # Short condition: BTC moved down significantly, ETH lagged, and ETH showing bearish momentum
            elif (current_btc_return_bps < -self.entry_threshold_bps and
                  current_eth_return_bps - current_btc_return_bps > self.divergence_threshold_bps and # ETH less negative than BTC
                  beta > 0.5 and # Require positive correlation
                  short_ema < long_ema and # ETH bearish momentum
                  trend_regime != "bull"): # Avoid shorting in a strong bull trend
                
                self.last_trade_bar = ctx.bar_index
                return ctx.signal(
                    "short",
                    confidence=confidence,
                    metadata={
                        "reason": "btc_eth_beta_convergence_short",
                        "btc_return_bps": current_btc_return_bps,
                        "eth_return_bps": current_eth_return_bps,
                        "divergence_bps": current_eth_return_bps - current_btc_return_bps,
                        "beta_proxy": beta,
                        "short_ema": short_ema,
                        "long_ema": long_ema,
                        "regime": regime,
                        "trend_regime": trend_regime
                    }
                )

        # Exit logic: If in a position, check if conditions normalize or reverse momentum
        if ctx.has_position():
            if ctx.position_direction() == "long":
                # Exit if ETH momentum turns bearish or divergence closes significantly
                if short_ema < long_ema or (current_btc_return_bps - current_eth_return_bps < self.divergence_threshold_bps / 2):
                    self.last_trade_bar = ctx.bar_index
                    return ctx.signal(
                        "flat",
                        confidence=0.5,
                        metadata={
                            "reason": "long_exit_momentum_loss_or_divergence_closed",
                            "current_eth_price": ctx.bar.close,
                            "short_ema": short_ema,
                            "long_ema": long_ema,
                            "current_divergence_bps": current_btc_return_bps - current_eth_return_bps
                        }
                    )
            elif ctx.position_direction() == "short":
                # Exit if ETH momentum turns bullish or divergence closes significantly
                if short_ema > long_ema or (current_eth_return_bps - current_btc_return_bps < self.divergence_threshold_bps / 2):
                    self.last_trade_bar = ctx.bar_index
                    return ctx.signal(
                        "flat",
                        confidence=0.5,
                        metadata={
                            "reason": "short_exit_momentum_gain_or_divergence_closed",
                            "current_eth_price": ctx.bar.close,
                            "short_ema": short_ema,
                            "long_ema": long_ema,
                            "current_divergence_bps": current_eth_return_bps - current_btc_return_bps
                        }
                    )
        
        return None

    def on_position_close(self, ctx: BarContext, position: Dict[str, Any]) -> None:
        if position["return_bps"] > 0:
            self.recent_wins = min(self.performance_lookback, self.recent_wins + 1)
            self.recent_losses = max(0, self.recent_losses - 1)
        else:
            self.recent_losses = min(self.performance_lookback, self.recent_losses + 1)
            self.recent_wins = max(0, self.recent_wins - 1)
        
        # Log for debugging/analysis
        ctx.log(f"Position closed: {position['direction']} return={position['return_bps']:.2f} bps, bars held={position['bars_held']}, wins={self.recent_wins}, losses={self.recent_losses}")