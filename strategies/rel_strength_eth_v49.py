from domains.strategy_contract import Strategy, BarContext, Signal
from typing import Optional, Dict, Any
import math

class EthBtcRelativeStrengthRotation(Strategy):
    METADATA = {
        "name": "ETH-BTC Adaptive Relative Strength Rotation",
        "domain": "eth_usdc",
        "declared_sl_bps": 280.0,
        "declared_tp_bps": 450.0,
        "declared_hold_seconds": 18000,  # ~5 hours target swing hold
        "warmup_bars": 72,
        "required_features": ["eth_return_pct", "btc_return_pct"]
    }

    def initialize(self, ctx: BarContext) -> None:
        self.cooldown_bars = 8
        self.last_exit_bar = -100
        self.window = 48
        self.spread_history = []

    def _ema(self, values, period):
        if len(values) < period:
            return None
        k = 2.0 / (period + 1)
        ema = sum(values[:period]) / period
        for v in values[period:]:
            ema = v * k + ema * (1 - k)
        return ema

    def _stddev(self, values):
        if len(values) < 2:
            return 0.0
        mean = sum(values) / len(values)
        variance = sum((x - mean) ** 2 for x in values) / len(values)
        return math.sqrt(variance)

    def on_position_close(self, ctx: BarContext, position: Dict[str, Any]) -> None:
        self.last_exit_bar = ctx.bar_index

    def on_bar(self, ctx: BarContext) -> Optional[Signal]:
        closes = ctx.closes(self.METADATA["warmup_bars"])
        if len(closes) < self.METADATA["warmup_bars"]:
            return None

        # Fetch returns & compute spread
        eth_ret = ctx.features.get("eth_return_pct", 0.0)
        btc_ret = ctx.features.get("btc_return_pct", 0.0)
        spread = eth_ret - btc_ret

        self.spread_history.append(spread)
        if len(self.spread_history) > self.window:
            self.spread_history.pop(0)

        if len(self.spread_history) < self.window:
            return None

        # Robust rolling distribution (Z-score of spread to avoid static magic numbers)
        spread_mean = sum(self.spread_history) / len(self.spread_history)
        spread_std = self._stddev(self.spread_history)
        spread_z = (spread - spread_mean) / spread_std if spread_std > 1e-6 else 0.0

        # Technical alignment on ETH price
        ema20 = self._ema(closes, 20)
        current_close = ctx.bar.close

        # Position management
        if ctx.has_position():
            direction = ctx.position_direction()
            # Mean reversion of spread back towards neutral
            if direction == "long" and spread_z <= 0.2:
                return ctx.signal(
                    "flat",
                    confidence=0.6,
                    metadata={"reason": "long_spread_reverted", "spread_z": round(spread_z, 2), "spread": round(spread, 3)}
                )
            elif direction == "short" and spread_z >= -0.2:
                return ctx.signal(
                    "flat",
                    confidence=0.6,
                    metadata={"reason": "short_spread_reverted", "spread_z": round(spread_z, 2), "spread": round(spread, 3)}
                )
            return None

        # Cooldown guard after trade exit
        if (ctx.bar_index - self.last_exit_bar) < self.cooldown_bars:
            return None

        # Regime safety filter from market context
        market_regime = ctx.market.get("regime", "NORMAL")
        if market_regime in ("CRISIS", "MELTDOWN"):
            return None

        # Entry logic: Statistical outlier in relative strength confirmed by short-term price trend
        # Threshold: spread_z > 1.6 (~top 5% relative strength), spread > 1.5% minimum real divergence
        if spread_z >= 1.6 and spread >= 1.5 and (ema20 is not None and current_close > ema20):
            conf = min(0.9, 0.6 + 0.1 * min(spread_z - 1.6, 3.0))
            return ctx.signal(
                "long",
                confidence=conf,
                stop_loss_bps=self.METADATA["declared_sl_bps"],
                take_profit_bps=self.METADATA["declared_tp_bps"],
                horizon_seconds=self.METADATA["declared_hold_seconds"],
                metadata={
                    "reason": "eth_outperformance_momentum",
                    "spread": round(spread, 3),
                    "spread_z": round(spread_z, 2),
                    "ema20": round(ema20, 2),
                    "close": current_close
                }
            )

        elif spread_z <= -1.6 and spread <= -1.5 and (ema20 is not None and current_close < ema20):
            conf = min(0.9, 0.6 + 0.1 * min(abs(spread_z) - 1.6, 3.0))
            return ctx.signal(
                "short",
                confidence=conf,
                stop_loss_bps=self.METADATA["declared_sl_bps"],
                take_profit_bps=self.METADATA["declared_tp_bps"],
                horizon_seconds=self.METADATA["declared_hold_seconds"],
                metadata={
                    "reason": "eth_underperformance_breakdown",
                    "spread": round(spread, 3),
                    "spread_z": round(spread_z, 2),
                    "ema20": round(ema20, 2),
                    "close": current_close
                }
            )

        return None