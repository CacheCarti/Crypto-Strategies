from domains.strategy_contract import Strategy, BarContext, Signal
from typing import Optional, Dict, Any
import math

class EthBtcSpreadMeanReversion(Strategy):
    METADATA = {
        "name": "EthBtcSpreadMeanReversion",
        "domain": "eth_usdc",
        "declared_sl_bps": 280.0,
        "declared_tp_bps": 420.0,
        "declared_hold_seconds": 14400,
        "warmup_bars": 40,
        "required_features": ["btc_return_pct", "eth_return_pct"]
    }

    def initialize(self, ctx: BarContext) -> None:
        self.spread_history = []
        self.max_history = 120
        self.spread_z_threshold = 2.0
        self.last_trade_bar = -999
        self.cooldown_bars = 10
        self.atr_period = 14

    def _atr(self, highs, lows, closes, period=14):
        if len(closes) < period + 1:
            return None
        trs = []
        for i in range(1, len(closes)):
            tr = max(highs[i] - lows[i], abs(highs[i] - closes[i - 1]), abs(lows[i] - closes[i - 1]))
            trs.append(tr)
        return sum(trs[-period:]) / period

    def on_position_close(self, ctx: BarContext, position: Dict[str, Any]) -> None:
        self.last_trade_bar = ctx.bar_index

    def on_bar(self, ctx: BarContext) -> Optional[Signal]:
        closes = ctx.closes(40)
        highs = ctx.highs(40)
        lows = ctx.lows(40)
        if len(closes) < 40:
            return None

        # Cross-asset spread calculation: ETH 24h return minus BTC 24h return
        eth_ret = ctx.features.get("eth_return_pct", 0.0)
        btc_ret = ctx.features.get("btc_return_pct", 0.0)
        spread = eth_ret - btc_ret

        self.spread_history.append(spread)
        if len(self.spread_history) > self.max_history:
            self.spread_history.pop(0)

        # Require enough spread history for robust z-score calculation
        if len(self.spread_history) < 30:
            return None

        # Cooldown guard: ensure selective event-boundary trading
        if (ctx.bar_index - self.last_trade_bar) < self.cooldown_bars:
            return None

        # Calculate mean and standard deviation of historical spread
        mean_spread = sum(self.spread_history) / len(self.spread_history)
        variance = sum((s - mean_spread) ** 2 for s in self.spread_history) / len(self.spread_history)
        std_spread = math.sqrt(variance)

        if std_spread < 1e-4:
            return None

        z_score = (spread - mean_spread) / std_spread

        # Market regime filter: avoid extreme meltdown/crisis spikes
        regime = ctx.market.get("regime", "NORMAL")
        if regime in ["CRISIS", "MELTDOWN"]:
            if ctx.has_position():
                return ctx.signal("flat", confidence=0.8, metadata={"reason": "exit_crisis_regime", "regime": regime})
            return None

        # Volatility normalization for dynamic SL/TP
        atr = self._atr(highs, lows, closes, self.atr_period)
        atr_bps = (atr / closes[-1]) * 10000.0 if atr else 200.0
        sl_bps = max(180.0, min(360.0, atr_bps * 1.5))
        tp_bps = max(250.0, min(500.0, atr_bps * 2.2))

        # Check existing position management
        if ctx.has_position():
            pos_dir = ctx.position_direction()
            # Mean reversion completion: spread returns to near-neutral
            if pos_dir == "long" and z_score > -0.2:
                return ctx.signal(
                    "flat",
                    confidence=0.6,
                    metadata={"reason": "eth_lag_reverted", "z_score": round(z_score, 2), "spread": round(spread, 3)}
                )
            elif pos_dir == "short" and z_score < 0.2:
                return ctx.signal(
                    "flat",
                    confidence=0.6,
                    metadata={"reason": "eth_outrun_reverted", "z_score": round(z_score, 2), "spread": round(spread, 3)}
                )
            return None

        # Entry logic: strict threshold crossing event boundaries
        # When ETH heavily lags BTC (z_score <= -2.0): expect catch-up rally
        if z_score <= -self.spread_z_threshold:
            self.last_trade_bar = ctx.bar_index
            confidence = min(0.9, 0.6 + abs(z_score) * 0.1)
            return ctx.signal(
                "long",
                confidence=confidence,
                stop_loss_bps=sl_bps,
                take_profit_bps=tp_bps,
                horizon_seconds=14400,
                metadata={
                    "reason": "eth_lag_catchup_long",
                    "z_score": round(z_score, 2),
                    "spread": round(spread, 3),
                    "eth_ret": round(eth_ret, 3),
                    "btc_ret": round(btc_ret, 3),
                    "atr_bps": round(atr_bps, 1)
                }
            )

        # When ETH heavily outperforms BTC (z_score >= 2.0): expect spread pull-back
        elif z_score >= self.spread_z_threshold:
            self.last_trade_bar = ctx.bar_index
            confidence = min(0.9, 0.6 + abs(z_score) * 0.1)
            return ctx.signal(
                "short",
                confidence=confidence,
                stop_loss_bps=sl_bps,
                take_profit_bps=tp_bps,
                horizon_seconds=14400,
                metadata={
                    "reason": "eth_outrun_mean_revert_short",
                    "z_score": round(z_score, 2),
                    "spread": round(spread, 3),
                    "eth_ret": round(eth_ret, 3),
                    "btc_ret": round(btc_ret, 3),
                    "atr_bps": round(atr_bps, 1)
                }
            )

        return None