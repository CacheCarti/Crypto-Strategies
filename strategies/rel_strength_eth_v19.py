from domains.strategy_contract import Strategy, BarContext, Signal
from typing import Optional, Dict, Any
import math

class AdaptiveRelativeStrength(Strategy):
    METADATA = {
        "name": "Adaptive Relative Strength Rotation",
        "domain": "eth_usdc",
        "declared_sl_bps": 320.0,
        "declared_tp_bps": 520.0,
        "declared_hold_seconds": 18000,
        "warmup_bars": 72,
        "required_features": ["btc_return_pct", "eth_return_pct"],
    }

    def initialize(self, ctx: BarContext) -> None:
        self.window = 48
        self.cooldown_bars = 8
        self.last_exit_bar = -999
        self.spread_history = []

    def _ema(self, values, period: int):
        if len(values) < period:
            return None
        k = 2.0 / (period + 1)
        ema = sum(values[:period]) / period
        for v in values[period:]:
            ema = v * k + ema * (1.0 - k)
        return ema

    def on_position_close(self, ctx: BarContext, position: Dict[str, Any]) -> None:
        self.last_exit_bar = ctx.bar_index

    def on_bar(self, ctx: BarContext) -> Optional[Signal]:
        closes = ctx.closes(self.METADATA["warmup_bars"])
        if len(closes) < self.METADATA["warmup_bars"]:
            return None

        # Fetch cross-asset returns and track dynamic spread
        eth_ret = ctx.features.get("eth_return_pct", 0.0)
        btc_ret = ctx.features.get("btc_return_pct", 0.0)
        spread = eth_ret - btc_ret

        self.spread_history.append(spread)
        if len(self.spread_history) > self.window:
            self.spread_history.pop(0)

        if len(self.spread_history) < self.window:
            return None

        # Calculate rolling distribution of relative strength to avoid fixed 2% threshold fragility
        mean_spread = sum(self.spread_history) / len(self.spread_history)
        variance = sum((x - mean_spread) ** 2 for x in self.spread_history) / len(self.spread_history)
        std_spread = math.sqrt(variance) if variance > 1e-8 else 0.01
        z_spread = (spread - mean_spread) / std_spread

        # Trend and momentum confirmation on ETH itself
        ema20 = self._ema(closes, 20)
        ema50 = self._ema(closes, 50)
        if ema20 is None or ema50 is None:
            return None

        current_close = ctx.bar.close

        # Position management / mean-reversion exit
        if ctx.has_position():
            direction = ctx.position_direction()
            # Mean reversion of spread triggers graceful profit take / de-risk
            if direction == "long" and z_spread <= 0.2:
                return ctx.signal(
                    "flat",
                    confidence=0.6,
                    metadata={"reason": "long_spread_reverted", "z_spread": round(z_spread, 2), "spread": round(spread, 3)}
                )
            if direction == "short" and z_spread >= -0.2:
                return ctx.signal(
                    "flat",
                    confidence=0.6,
                    metadata={"reason": "short_spread_reverted", "z_spread": round(z_spread, 2), "spread": round(spread, 3)}
                )
            return None

        # Enforce cooldown after prior trade exit
        if ctx.bar_index - self.last_exit_bar < self.cooldown_bars:
            return None

        # Filter crisis/meltdown regimes
        market_regime = ctx.market.get("regime", "NORMAL")
        if market_regime in ("CRISIS", "MELTDOWN"):
            return None

        # Entry logic: Statistical relative dominance confirmed by local price trend alignment
        # Requires relative-strength divergence of > 1.4 std dev
        if z_spread > 1.4 and current_close > ema20 and ema20 > ema50:
            confidence = min(0.9, 0.55 + 0.15 * (z_spread - 1.4))
            return ctx.signal(
                "long",
                confidence=confidence,
                stop_loss_bps=self.METADATA["declared_sl_bps"],
                take_profit_bps=self.METADATA["declared_tp_bps"],
                horizon_seconds=self.METADATA["declared_hold_seconds"],
                metadata={
                    "reason": "eth_outperformance_trend_long",
                    "z_spread": round(z_spread, 3),
                    "spread_pct": round(spread, 3),
                    "ema20": round(ema20, 2),
                    "ema50": round(ema50, 2),
                    "price": current_close
                }
            )

        if z_spread < -1.4 and current_close < ema20 and ema20 < ema50:
            confidence = min(0.9, 0.55 + 0.15 * (abs(z_spread) - 1.4))
            return ctx.signal(
                "short",
                confidence=confidence,
                stop_loss_bps=self.METADATA["declared_sl_bps"],
                take_profit_bps=self.METADATA["declared_tp_bps"],
                horizon_seconds=self.METADATA["declared_hold_seconds"],
                metadata={
                    "reason": "eth_underperformance_trend_short",
                    "z_spread": round(z_spread, 3),
                    "spread_pct": round(spread, 3),
                    "ema20": round(ema20, 2),
                    "ema50": round(ema50, 2),
                    "price": current_close
                }
            )

        return None