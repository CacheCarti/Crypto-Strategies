from domains.strategy_contract import Strategy, BarContext, Signal
from typing import Optional, Dict, Any
import math

class EthBtcSpreadMeanReversion(Strategy):
    METADATA = {
        "name": "EthBtcSpreadMeanReversion",
        "domain": "eth_usdc",
        "declared_sl_bps": 250.0,
        "declared_tp_bps": 400.0,
        "declared_hold_seconds": 14400,  # 4 hours average hold
        "warmup_bars": 35,
        "required_features": ["btc_return_pct", "eth_return_pct"],
    }

    def initialize(self, ctx: BarContext) -> None:
        self.cooldown_bars = 4
        self.last_exit_bar = -100
        self.lookback = 24
        self.spread_threshold = 1.8  # Required divergence in 24h returns (%)

    def _atr(self, highs, lows, closes, period=14):
        if len(closes) < period + 1:
            return None
        trs = []
        for i in range(1, len(closes)):
            tr = max(highs[i] - lows[i], abs(highs[i] - closes[i - 1]), abs(lows[i] - closes[i - 1]))
            trs.append(tr)
        return sum(trs[-period:]) / period

    def _rsi(self, closes, period=14):
        if len(closes) < period + 1:
            return None
        gains, losses = [], []
        for i in range(1, len(closes)):
            diff = closes[i] - closes[i - 1]
            gains.append(max(diff, 0.0))
            losses.append(max(-diff, 0.0))
        avg_gain = sum(gains[-period:]) / period
        avg_loss = sum(losses[-period:]) / period
        if avg_loss == 0.0:
            return 100.0
        rs = avg_gain / avg_loss
        return 100.0 - (100.0 / (1.0 + rs))

    def on_position_close(self, ctx: BarContext, position: Dict[str, Any]) -> None:
        self.last_exit_bar = ctx.bar_index

    def on_bar(self, ctx: BarContext) -> Optional[Signal]:
        closes = ctx.closes(self.lookback + 5)
        highs = ctx.highs(self.lookback + 5)
        lows = ctx.lows(self.lookback + 5)

        if len(closes) < self.lookback + 5:
            return None

        # Check market regime to avoid crisis / extreme volatility breakdown
        market_regime = ctx.market.get("regime", "NORMAL")
        if market_regime in ("CRISIS", "MELTDOWN"):
            if ctx.has_position():
                return ctx.signal("flat", confidence=0.8, metadata={"reason": "crisis_exit", "regime": market_regime})
            return None

        # Cooldown guard after trade exit
        if ctx.bar_index - self.last_exit_bar < self.cooldown_bars:
            return None

        # Extract cross-asset return features (24h rolling return percentages)
        btc_ret = ctx.features.get("btc_return_pct", 0.0)
        eth_ret = ctx.features.get("eth_return_pct", 0.0)
        spread = eth_ret - btc_ret  # Positive if ETH outperforms BTC, negative if ETH lags

        # Volatility & technical indicators for local confirmation
        atr = self._atr(highs, lows, closes, 14)
        rsi = self._rsi(closes, 14)

        if atr is None or rsi is None or atr <= 0.0:
            return None

        current_price = ctx.bar.close
        atr_pct = (atr / current_price) * 100.0

        # Handle active position management (mean reversion complete)
        if ctx.has_position():
            pos_dir = ctx.position_direction()
            # If spread converged, take early profit / flatten
            if pos_dir == "long" and spread >= -0.2:
                return ctx.signal("flat", confidence=0.6, metadata={"reason": "spread_mean_reverted", "spread": spread, "rsi": rsi})
            elif pos_dir == "short" and spread <= 0.2:
                return ctx.signal("flat", confidence=0.6, metadata={"reason": "spread_mean_reverted", "spread": spread, "rsi": rsi})
            return None

        # Entry logic:
        # Volatility condition: require reasonable movement, avoiding dead consolidation
        if atr_pct < 0.35:
            return None

        # Setup 1: ETH severely lags BTC (spread < -threshold) AND local ETH RSI not overbought -> Long ETH catch-up
        if spread < -self.spread_threshold and rsi < 55.0:
            conf = min(0.9, 0.5 + abs(spread) / 10.0)
            return ctx.signal(
                "long",
                confidence=conf,
                stop_loss_bps=250.0,
                take_profit_bps=400.0,
                horizon_seconds=14400,
                metadata={
                    "reason": "eth_lagging_btc_mean_reversion",
                    "spread": round(spread, 3),
                    "eth_ret": round(eth_ret, 3),
                    "btc_ret": round(btc_ret, 3),
                    "rsi": round(rsi, 2),
                    "atr_pct": round(atr_pct, 3),
                }
            )

        # Setup 2: ETH significantly overextends BTC (spread > threshold) AND local ETH RSI not oversold -> Short ETH reversion
        if spread > self.spread_threshold and rsi > 45.0:
            conf = min(0.9, 0.5 + abs(spread) / 10.0)
            return ctx.signal(
                "short",
                confidence=conf,
                stop_loss_bps=250.0,
                take_profit_bps=400.0,
                horizon_seconds=14400,
                metadata={
                    "reason": "eth_outrunning_btc_mean_reversion",
                    "spread": round(spread, 3),
                    "eth_ret": round(eth_ret, 3),
                    "btc_ret": round(btc_ret, 3),
                    "rsi": round(rsi, 2),
                    "atr_pct": round(atr_pct, 3),
                }
            )

        return None