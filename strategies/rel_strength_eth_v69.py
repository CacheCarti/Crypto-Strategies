from domains.strategy_contract import Strategy, BarContext, Signal
from typing import Optional, Dict, Any
import math

class EthBtcRelativeStrength(Strategy):
    METADATA = {
        "name": "ETH-BTC Relative Strength Momentum",
        "domain": "eth_usdc",
        "declared_sl_bps": 280.0,
        "declared_tp_bps": 420.0,
        "declared_hold_seconds": 18000,
        "warmup_bars": 72,
        "required_features": ["eth_return_pct", "btc_return_pct"],
    }

    def initialize(self, ctx: BarContext) -> None:
        self.rolling_window = 48
        self.cooldown_period = 10
        self.spread_history = []
        self.last_exit_bar = -999

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
        closes = ctx.closes(self.rolling_window)
        if len(closes) < self.rolling_window:
            return None

        eth_ret = ctx.features.get("eth_return_pct", 0.0)
        btc_ret = ctx.features.get("btc_return_pct", 0.0)
        spread = eth_ret - btc_ret

        self.spread_history.append(spread)
        if len(self.spread_history) > self.rolling_window:
            self.spread_history.pop(0)

        if len(self.spread_history) < self.rolling_window:
            return None

        mean_spread = sum(self.spread_history) / len(self.spread_history)
        var_spread = sum((x - mean_spread) ** 2 for x in self.spread_history) / len(self.spread_history)
        std_spread = math.sqrt(var_spread) if var_spread > 1e-8 else 0.001
        z_score = (spread - mean_spread) / std_spread

        ema24 = self._ema(closes, 24)
        if ema24 is None:
            return None

        current_price = ctx.bar.close
        price_trend_up = current_price > ema24
        price_trend_down = current_price < ema24

        # Position exit handling (mean reversion of spread)
        if ctx.has_position():
            pos_dir = ctx.position_direction()
            if pos_dir == "long" and z_score < 0.2:
                return ctx.signal("flat", confidence=0.6, metadata={"reason": "spread_reverted_long_exit", "z_score": z_score, "spread": spread})
            elif pos_dir == "short" and z_score > -0.2:
                return ctx.signal("flat", confidence=0.6, metadata={"reason": "spread_reverted_short_exit", "z_score": z_score, "spread": spread})
            return None

        # Mandatory cooldown guard
        if ctx.bar_index - self.last_exit_bar < self.cooldown_period:
            return None

        # Regime safety filter from engine context
        regime = ctx.market.get("regime", "NORMAL")
        if regime in ("CRISIS", "MELTDOWN"):
            return None

        # Entry logic: Statistical outlier in relative strength confirmed by technical trend
        if z_score > 1.6 and price_trend_up:
            conf = min(0.9, 0.6 + max(0.0, (z_score - 1.6) * 0.15))
            return ctx.signal(
                "long",
                confidence=conf,
                stop_loss_bps=self.METADATA["declared_sl_bps"],
                take_profit_bps=self.METADATA["declared_tp_bps"],
                horizon_seconds=self.METADATA["declared_hold_seconds"],
                metadata={
                    "reason": "eth_outperformance_momentum_long",
                    "z_score": round(z_score, 3),
                    "spread": round(spread, 4),
                    "eth_ret": round(eth_ret, 4),
                    "btc_ret": round(btc_ret, 4),
                    "ema24": round(ema24, 2),
                    "price": current_price
                }
            )

        if z_score < -1.6 and price_trend_down:
            conf = min(0.9, 0.6 + max(0.0, (-z_score - 1.6) * 0.15))
            return ctx.signal(
                "short",
                confidence=conf,
                stop_loss_bps=self.METADATA["declared_sl_bps"],
                take_profit_bps=self.METADATA["declared_tp_bps"],
                horizon_seconds=self.METADATA["declared_hold_seconds"],
                metadata={
                    "reason": "eth_underperformance_momentum_short",
                    "z_score": round(z_score, 3),
                    "spread": round(spread, 4),
                    "eth_ret": round(eth_ret, 4),
                    "btc_ret": round(btc_ret, 4),
                    "ema24": round(ema24, 2),
                    "price": current_price
                }
            )

        return None