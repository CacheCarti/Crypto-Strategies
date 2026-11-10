from domains.strategy_contract import Strategy, BarContext, Signal
from typing import Optional, Dict, Any
import math

class EthBtcSpreadMeanReversion(Strategy):
    METADATA = {
        "name": "EthBtcSpreadMeanReversion",
        "domain": "eth_usdc",
        "declared_sl_bps": 220.0,
        "declared_tp_bps": 340.0,
        "declared_hold_seconds": 14400,
        "warmup_bars": 40,
        "required_features": ["btc_return_pct", "eth_return_pct"],
    }

    def initialize(self, ctx: BarContext) -> None:
        self.cooldown_bars = 6
        self.last_exit_bar = -100
        self.spread_threshold = 1.6
        self.rsi_period = 14

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

    def _bollinger_bandwidth(self, closes, period=20):
        if len(closes) < period:
            return None
        sub = closes[-period:]
        mean = sum(sub) / period
        var = sum((x - mean) ** 2 for x in sub) / period
        std = math.sqrt(var)
        if mean == 0:
            return 0.0
        return (4.0 * std) / mean

    def on_position_close(self, ctx: BarContext, position: Dict[str, Any]) -> None:
        self.last_exit_bar = ctx.bar_index

    def on_bar(self, ctx: BarContext) -> Optional[Signal]:
        closes = ctx.closes(40)
        if len(closes) < self.METADATA["warmup_bars"]:
            return None

        # Check regime to avoid entering in extreme tail risk
        crisis_score = ctx.market.get("crisis_score", 0.0)
        if crisis_score > 0.65:
            if ctx.has_position():
                return ctx.signal("flat", confidence=0.8, metadata={"reason": "crisis_exit", "crisis_score": crisis_score})
            return None

        rsi = self._rsi(closes, self.rsi_period)
        if rsi is None:
            return None

        # Cross-asset spread calculation
        btc_ret = ctx.features.get("btc_return_pct", 0.0)
        eth_ret = ctx.features.get("eth_return_pct", 0.0)
        spread = eth_ret - btc_ret

        # Volatility state filter via Bollinger Bandwidth: avoid dead consolidated markets
        bbw = self._bollinger_bandwidth(closes, 20)
        if bbw is None or bbw < 0.008:
            return None

        # Position management
        if ctx.has_position():
            pos_dir = ctx.position_direction()
            # Mean reversion achieved or reversal conditions
            if pos_dir == "long" and (spread >= 0.2 or rsi >= 62.0):
                return ctx.signal("flat", confidence=0.7, metadata={"reason": "tp_spread_normalized", "spread": spread, "rsi": rsi})
            elif pos_dir == "short" and (spread <= -0.2 or rsi <= 38.0):
                return ctx.signal("flat", confidence=0.7, metadata={"reason": "tp_spread_normalized", "spread": spread, "rsi": rsi})
            return None

        # Post-trade cooldown check
        if ctx.bar_index - self.last_exit_bar < self.cooldown_bars:
            return None

        # Entry logic: ETH materially lagging BTC -> Catch-up Long
        # ETH return is notably lower than BTC return, and RSI is not overbought
        if spread <= -self.spread_threshold and rsi < 50.0:
            conf = min(0.9, max(0.55, 0.55 + abs(spread) * 0.08))
            return ctx.signal(
                "long",
                confidence=conf,
                stop_loss_bps=self.METADATA["declared_sl_bps"],
                take_profit_bps=self.METADATA["declared_tp_bps"],
                horizon_seconds=self.METADATA["declared_hold_seconds"],
                metadata={
                    "reason": "eth_lagging_btc_mean_reversion",
                    "spread": spread,
                    "eth_ret": eth_ret,
                    "btc_ret": btc_ret,
                    "rsi": rsi,
                    "bbw": bbw,
                },
            )

        # Entry logic: ETH materially overextended vs BTC -> Pullback Short
        # ETH return is notably higher than BTC return, and RSI is not oversold
        if spread >= self.spread_threshold and rsi > 50.0:
            conf = min(0.9, max(0.55, 0.55 + spread * 0.08))
            return ctx.signal(
                "short",
                confidence=conf,
                stop_loss_bps=self.METADATA["declared_sl_bps"],
                take_profit_bps=self.METADATA["declared_tp_bps"],
                horizon_seconds=self.METADATA["declared_hold_seconds"],
                metadata={
                    "reason": "eth_outrunning_btc_mean_reversion",
                    "spread": spread,
                    "eth_ret": eth_ret,
                    "btc_ret": btc_ret,
                    "rsi": rsi,
                    "bbw": bbw,
                },
            )

        return None