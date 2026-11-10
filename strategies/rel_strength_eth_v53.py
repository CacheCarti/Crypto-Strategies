from domains.strategy_contract import Strategy, BarContext, Signal
from typing import Optional, Dict, Any
import math

class EthBtcRelativeStrengthMomentum(Strategy):
    METADATA = {
        "name": "EthBtcRelativeStrengthMomentum",
        "domain": "eth_usdc",
        "declared_sl_bps": 280.0,
        "declared_tp_bps": 520.0,
        "declared_hold_seconds": 21600,  # ~6 hours
        "warmup_bars": 40,
        "required_features": ["eth_return_pct", "btc_return_pct"],
    }

    def initialize(self, ctx: BarContext) -> None:
        self.cooldown_bars = 6
        self.last_exit_bar = -999
        self.min_spread_threshold = 2.0  # 2.0% divergence
        self.max_spread_cap = 6.5        # Avoid blowout single-trade outliers
        self.ema_period = 20

    def _ema(self, values, period: int) -> Optional[float]:
        if len(values) < period:
            return None
        k = 2.0 / (period + 1)
        ema = sum(values[:period]) / period
        for v in values[period:]:
            ema = v * k + ema * (1 - k)
        return ema

    def _atr(self, highs, lows, closes, period: int = 14) -> Optional[float]:
        if len(closes) < period + 1:
            return None
        trs = []
        for i in range(1, len(closes)):
            tr = max(highs[i] - lows[i], abs(highs[i] - closes[i - 1]), abs(lows[i] - closes[i - 1]))
            trs.append(tr)
        return sum(trs[-period:]) / period

    def on_position_close(self, ctx: BarContext, position: Dict[str, Any]) -> None:
        self.last_exit_bar = ctx.bar_index

    def on_bar(self, ctx: BarContext) -> Optional[Signal]:
        closes = ctx.closes(45)
        if len(closes) < self.METADATA["warmup_bars"]:
            return None

        # Feature extraction
        eth_ret = ctx.features.get("eth_return_pct", 0.0)
        btc_ret = ctx.features.get("btc_return_pct", 0.0)
        spread = eth_ret - btc_ret

        # If in position, manage exit / mean-reversion take-off
        if ctx.has_position():
            pos_dir = ctx.position_direction()
            # Mean-reversion exit condition when spread neutralizes
            if pos_dir == "long" and spread <= 0.2:
                return ctx.signal(
                    "flat",
                    confidence=0.7,
                    metadata={"reason": "spread_reverted_to_neutral", "spread": spread, "eth_ret": eth_ret, "btc_ret": btc_ret}
                )
            elif pos_dir == "short" and spread >= -0.2:
                return ctx.signal(
                    "flat",
                    confidence=0.7,
                    metadata={"reason": "spread_reverted_to_neutral", "spread": spread, "eth_ret": eth_ret, "btc_ret": btc_ret}
                )
            return None

        # Mandatory cooldown guard
        if (ctx.bar_index - self.last_exit_bar) < self.cooldown_bars:
            return None

        # Market regime filter: do not trade under crisis/meltdown
        market_regime = ctx.market.get("regime", "NORMAL")
        if market_regime in ("CRISIS", "MELTDOWN"):
            return None

        # Price confirmation filter: price relative to EMA(20) to ensure momentum alignment
        ema20 = self._ema(closes, self.ema_period)
        if ema20 is None:
            return None
        current_close = ctx.bar.close

        # Long Setup: ETH outperforming BTC cleanly, confirmed by price holding above EMA20
        if self.min_spread_threshold <= spread <= self.max_spread_cap and current_close > ema20:
            confidence = min(0.9, 0.55 + (spread - self.min_spread_threshold) * 0.07)
            return ctx.signal(
                "long",
                confidence=confidence,
                stop_loss_bps=self.METADATA["declared_sl_bps"],
                take_profit_bps=self.METADATA["declared_tp_bps"],
                horizon_seconds=self.METADATA["declared_hold_seconds"],
                metadata={
                    "reason": "eth_relative_strength_lead",
                    "spread": round(spread, 3),
                    "eth_ret": round(eth_ret, 3),
                    "btc_ret": round(btc_ret, 3),
                    "ema20": round(ema20, 2),
                    "close": round(current_close, 2)
                }
            )

        # Short Setup: ETH lagging BTC cleanly, confirmed by price breaking below EMA20
        if -self.max_spread_cap <= spread <= -self.min_spread_threshold and current_close < ema20:
            confidence = min(0.9, 0.55 + (abs(spread) - self.min_spread_threshold) * 0.07)
            return ctx.signal(
                "short",
                confidence=confidence,
                stop_loss_bps=self.METADATA["declared_sl_bps"],
                take_profit_bps=self.METADATA["declared_tp_bps"],
                horizon_seconds=self.METADATA["declared_hold_seconds"],
                metadata={
                    "reason": "eth_relative_weakness_lag",
                    "spread": round(spread, 3),
                    "eth_ret": round(eth_ret, 3),
                    "btc_ret": round(btc_ret, 3),
                    "ema20": round(ema20, 2),
                    "close": round(current_close, 2)
                }
            )

        return None