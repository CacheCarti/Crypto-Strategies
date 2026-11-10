from domains.strategy_contract import Strategy, BarContext, Signal
from typing import Optional, Dict, Any
import math

class EthBtcRelativeStrengthRotation(Strategy):
    METADATA = {
        "name": "ETH-BTC Adaptive Relative Strength",
        "domain": "eth_usdc",
        "declared_sl_bps": 250.0,
        "declared_tp_bps": 450.0,
        "declared_hold_seconds": 18000,
        "warmup_bars": 50,
        "required_features": ["eth_return_pct", "btc_return_pct"],
    }

    def initialize(self, ctx: BarContext) -> None:
        self.spread_history = []
        self.window = 48
        self.cooldown_bars = 6
        self.last_exit_bar = -999
        self.entry_bar = -999

    def on_position_close(self, ctx: BarContext, position: Dict[str, Any]) -> None:
        self.last_exit_bar = ctx.bar_index

    def _ema(self, values, period):
        if len(values) < period:
            return None
        k = 2.0 / (period + 1)
        ema = sum(values[:period]) / period
        for v in values[period:]:
            ema = v * k + ema * (1 - k)
        return ema

    def on_bar(self, ctx: BarContext) -> Optional[Signal]:
        closes = ctx.closes(self.METADATA["warmup_bars"])
        if len(closes) < self.METADATA["warmup_bars"]:
            return None

        eth_ret = ctx.features.get("eth_return_pct", 0.0)
        btc_ret = ctx.features.get("btc_return_pct", 0.0)
        raw_spread = eth_ret - btc_ret

        self.spread_history.append(raw_spread)
        if len(self.spread_history) > self.window:
            self.spread_history.pop(0)

        if len(self.spread_history) < self.window:
            return None

        # Compute rolling z-score of the spread to avoid fixed static thresholds
        mean_spread = sum(self.spread_history) / len(self.spread_history)
        variance = sum((x - mean_spread) ** 2 for x in self.spread_history) / len(self.spread_history)
        std_spread = math.sqrt(variance)

        if std_spread < 1e-6:
            z_spread = 0.0
        else:
            z_spread = (raw_spread - mean_spread) / std_spread

        current_pos = ctx.position_direction()

        # Exit logic: mean reversion of the relative spread or holding timeout
        if current_pos is not None:
            bars_held = ctx.bar_index - self.entry_bar
            if current_pos == "long" and z_spread <= 0.0:
                return ctx.signal(
                    "flat",
                    confidence=0.6,
                    metadata={"reason": "eth_outperformance_reverted", "z_spread": round(z_spread, 3), "raw_spread": round(raw_spread, 3)}
                )
            elif current_pos == "short" and z_spread >= 0.0:
                return ctx.signal(
                    "flat",
                    confidence=0.6,
                    metadata={"reason": "eth_underperformance_reverted", "z_spread": round(z_spread, 3), "raw_spread": round(raw_spread, 3)}
                )
            if bars_held >= 12:
                return ctx.signal(
                    "flat",
                    confidence=0.5,
                    metadata={"reason": "max_holding_reached", "bars_held": bars_held}
                )
            return None

        # Cooldown guard
        if ctx.bar_index - self.last_exit_bar < self.cooldown_bars:
            return None

        # Regime & Price Confirmation: Use EMA20 of price to ensure momentum alignment
        ema20 = self._ema(closes, 20)
        if ema20 is None:
            return None

        current_price = ctx.bar.close

        # Entry logic: Statistical breakout in relative strength confirmed by price action
        # Z-Score > 1.6 indicates significant ETH leadership (top ~5% tail of rolling window)
        if z_spread > 1.6 and current_price > ema20:
            self.entry_bar = ctx.bar_index
            conf = min(0.9, max(0.5, 0.5 + (z_spread - 1.6) * 0.2))
            return ctx.signal(
                "long",
                confidence=conf,
                stop_loss_bps=self.METADATA["declared_sl_bps"],
                take_profit_bps=self.METADATA["declared_tp_bps"],
                metadata={
                    "reason": "eth_relative_strength_expansion",
                    "z_spread": round(z_spread, 3),
                    "raw_spread": round(raw_spread, 3),
                    "price": current_price,
                    "ema20": round(ema20, 2),
                }
            )

        # Z-Score < -1.6 indicates significant ETH weakness confirmed by price below EMA20
        if z_spread < -1.6 and current_price < ema20:
            self.entry_bar = ctx.bar_index
            conf = min(0.9, max(0.5, 0.5 + abs(z_spread + 1.6) * 0.2))
            return ctx.signal(
                "short",
                confidence=conf,
                stop_loss_bps=self.METADATA["declared_sl_bps"],
                take_profit_bps=self.METADATA["declared_tp_bps"],
                metadata={
                    "reason": "eth_relative_weakness_expansion",
                    "z_spread": round(z_spread, 3),
                    "raw_spread": round(raw_spread, 3),
                    "price": current_price,
                    "ema20": round(ema20, 2),
                }
            )

        return None