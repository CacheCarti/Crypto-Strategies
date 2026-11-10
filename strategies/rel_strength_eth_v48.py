from domains.strategy_contract import Strategy, BarContext, Signal
from typing import Optional, Dict, Any
import math

class RelStrengthEthRotation(Strategy):
    METADATA = {
        "name": "RelStrengthEthRotation",
        "domain": "eth_usdc",
        "declared_sl_bps": 280.0,
        "declared_tp_bps": 450.0,
        "declared_hold_seconds": 21600,  # ~6 hours target horizon
        "warmup_bars": 35,
        "required_features": ["eth_return_pct", "btc_return_pct"],
    }

    def initialize(self, ctx: BarContext) -> None:
        self.cooldown_bars = 6
        self.last_exit_bar = -999
        self.min_spread_bps = 2.0  # 2% spread threshold
        self.rsi_period = 14
        self.ema_period = 20

    def _ema(self, values, period: int):
        if len(values) < period:
            return None
        k = 2.0 / (period + 1)
        ema = sum(values[:period]) / period
        for v in values[period:]:
            ema = v * k + ema * (1 - k)
        return ema

    def _rsi(self, closes, period: int = 14):
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

        # Feature inputs
        eth_ret = ctx.features.get("eth_return_pct", 0.0)
        btc_ret = ctx.features.get("btc_return_pct", 0.0)
        spread = eth_ret - btc_ret

        # Current indicators
        rsi = self._rsi(closes, self.rsi_period)
        ema = self._ema(closes, self.ema_period)
        if rsi is None or ema is None:
            return None

        current_close = ctx.bar.close

        # Manage active position: exit when spread mean-reverts or momentum reverses
        if ctx.has_position():
            pos_dir = ctx.position_direction()
            if pos_dir == "long":
                if spread <= 0.2 or rsi > 70.0:
                    return ctx.signal(
                        "flat",
                        confidence=0.6,
                        metadata={
                            "reason": "long_rel_strength_mean_reverted",
                            "spread": spread,
                            "rsi": rsi,
                            "close": current_close,
                        },
                    )
            elif pos_dir == "short":
                if spread >= -0.2 or rsi < 30.0:
                    return ctx.signal(
                        "flat",
                        confidence=0.6,
                        metadata={
                            "reason": "short_rel_strength_mean_reverted",
                            "spread": spread,
                            "rsi": rsi,
                            "close": current_close,
                        },
                    )
            return None

        # Mandatory cooldown guard
        if (ctx.bar_index - self.last_exit_bar) < self.cooldown_bars:
            return None

        # Market stress protection
        regime = ctx.market.get("regime", "NORMAL")
        crisis_score = ctx.market.get("crisis_score", 0.0)
        if regime in ("CRISIS", "MELTDOWN") or crisis_score > 0.6:
            return None

        # Entry Event 1: Outperformance lead breakout
        if spread >= self.min_spread_bps and current_close > ema and 45.0 <= rsi <= 68.0:
            confidence = min(0.85, 0.55 + (spread - self.min_spread_bps) * 0.08)
            return ctx.signal(
                "long",
                confidence=confidence,
                stop_loss_bps=self.METADATA["declared_sl_bps"],
                take_profit_bps=self.METADATA["declared_tp_bps"],
                horizon_seconds=self.METADATA["declared_hold_seconds"],
                metadata={
                    "reason": "eth_relative_strength_expansion_long",
                    "spread": spread,
                    "rsi": rsi,
                    "ema": ema,
                    "eth_ret": eth_ret,
                    "btc_ret": btc_ret,
                },
            )

        # Entry Event 2: Underperformance breakdown
        if spread <= -self.min_spread_bps and current_close < ema and 32.0 <= rsi <= 55.0:
            confidence = min(0.85, 0.55 + (abs(spread) - self.min_spread_bps) * 0.08)
            return ctx.signal(
                "short",
                confidence=confidence,
                stop_loss_bps=self.METADATA["declared_sl_bps"],
                take_profit_bps=self.METADATA["declared_tp_bps"],
                horizon_seconds=self.METADATA["declared_hold_seconds"],
                metadata={
                    "reason": "eth_relative_weakness_expansion_short",
                    "spread": spread,
                    "rsi": rsi,
                    "ema": ema,
                    "eth_ret": eth_ret,
                    "btc_ret": btc_ret,
                },
            )

        return None