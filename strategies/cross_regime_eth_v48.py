from domains.strategy_contract import Strategy, BarContext, Signal
from typing import Optional, Dict, Any
import math

class EthCrossCatchupReversion(Strategy):
    METADATA = {
        "name": "EthCrossCatchupReversion",
        "domain": "eth_usdc",
        "declared_sl_bps": 280.0,
        "declared_tp_bps": 420.0,
        "declared_hold_seconds": 14400,
        "warmup_bars": 40,
        "required_features": ["btc_return_pct", "eth_return_pct"],
    }

    def initialize(self, ctx: BarContext) -> None:
        self.last_exit_bar = -999
        self.cooldown_bars = 6
        self.spread_threshold = 2.4
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

    def on_position_close(self, ctx: BarContext, position: Dict[str, Any]) -> None:
        self.last_exit_bar = ctx.bar_index

    def on_bar(self, ctx: BarContext) -> Optional[Signal]:
        closes = ctx.closes(self.METADATA["warmup_bars"])
        if len(closes) < self.METADATA["warmup_bars"]:
            return None

        # Cooldown guard: enforce hard boundary between sequential trades
        if (ctx.bar_index - self.last_exit_bar) < self.cooldown_bars:
            return None

        btc_ret = ctx.features.get("btc_return_pct", 0.0)
        eth_ret = ctx.features.get("eth_return_pct", 0.0)
        spread = btc_ret - eth_ret  # positive when BTC outperforms ETH materially

        rsi = self._rsi(closes, self.rsi_period)
        if rsi is None:
            return None

        # Position management
        if ctx.has_position():
            direction = ctx.position_direction()
            # Mean-reversion exit when spread normalizes or RSI reaches counter-extremes
            if direction == "long" and (spread <= 0.0 or rsi >= 68.0):
                return ctx.signal(
                    "flat",
                    confidence=0.6,
                    metadata={"reason": "long_spread_reverted_or_rsi_high", "spread": spread, "rsi": rsi}
                )
            elif direction == "short" and (spread >= 0.0 or rsi <= 32.0):
                return ctx.signal(
                    "flat",
                    confidence=0.6,
                    metadata={"reason": "short_spread_reverted_or_rsi_low", "spread": spread, "rsi": rsi}
                )
            return None

        # Avoid entry during chaotic crisis environments
        regime = ctx.market.get("regime", "NORMAL")
        if regime in ("CRISIS", "MELTDOWN"):
            return None

        # Event: BTC strong, ETH lags -> Expect ETH catch-up long (confirmation: RSI not overbought)
        if spread >= self.spread_threshold and rsi < 58.0:
            conf = min(0.85, 0.55 + (spread - self.spread_threshold) * 0.08)
            return ctx.signal(
                "long",
                confidence=conf,
                stop_loss_bps=self.METADATA["declared_sl_bps"],
                take_profit_bps=self.METADATA["declared_tp_bps"],
                horizon_seconds=self.METADATA["declared_hold_seconds"],
                metadata={
                    "reason": "eth_lag_catchup_event",
                    "btc_return_pct": btc_ret,
                    "eth_return_pct": eth_ret,
                    "spread": spread,
                    "rsi": rsi
                }
            )

        # Event: ETH overextends relative to BTC -> Expect mean reversion short (confirmation: RSI not oversold)
        if spread <= -self.spread_threshold and rsi > 42.0:
            conf = min(0.85, 0.55 + (abs(spread) - self.spread_threshold) * 0.08)
            return ctx.signal(
                "short",
                confidence=conf,
                stop_loss_bps=self.METADATA["declared_sl_bps"],
                take_profit_bps=self.METADATA["declared_tp_bps"],
                horizon_seconds=self.METADATA["declared_hold_seconds"],
                metadata={
                    "reason": "eth_overextended_mean_reversion_event",
                    "btc_return_pct": btc_ret,
                    "eth_return_pct": eth_ret,
                    "spread": spread,
                    "rsi": rsi
                }
            )

        return None