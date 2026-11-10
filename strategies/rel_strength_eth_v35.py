from domains.strategy_contract import Strategy, BarContext, Signal
from typing import Optional, Dict, Any
import math

class EthBtcRelativeStrength(Strategy):
    METADATA = {
        "name": "EthBtcRelativeStrength",
        "domain": "eth_usdc",
        "declared_sl_bps": 220.0,
        "declared_tp_bps": 380.0,
        "declared_hold_seconds": 18000,
        "warmup_bars": 55,
        "required_features": ["eth_return_pct", "btc_return_pct"],
    }

    def initialize(self, ctx: BarContext) -> None:
        self.cooldown_bars = 8
        self.last_exit_bar = -999
        self.last_spread = 0.0
        self.min_spread_bps = 2.4
        self.max_hold_bars = 24

    def _ema(self, values: list, period: int) -> Optional[float]:
        if len(values) < period:
            return None
        k = 2.0 / (period + 1)
        ema = sum(values[:period]) / period
        for v in values[period:]:
            ema = v * k + ema * (1 - k)
        return ema

    def _rsi(self, closes: list, period: int = 14) -> Optional[float]:
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
        closes = ctx.closes(60)
        if len(closes) < 55:
            return None

        current_price = ctx.bar.close
        ema20 = self._ema(closes, 20)
        ema50 = self._ema(closes, 50)
        rsi = self._rsi(closes, 14)

        if ema20 is None or ema50 is None or rsi is None:
            return None

        eth_ret = ctx.features.get("eth_return_pct", 0.0)
        btc_ret = ctx.features.get("btc_return_pct", 0.0)
        spread = eth_ret - btc_ret
        prev_spread = self.last_spread
        self.last_spread = spread

        regime = ctx.market.get("regime", "NORMAL")
        crisis_score = ctx.market.get("crisis_score", 0.0)
        if regime in ("CRISIS", "MELTDOWN") or crisis_score > 0.45:
            if ctx.has_position():
                return ctx.signal(
                    "flat",
                    confidence=0.8,
                    metadata={"reason": "market_crisis_regime_exit", "regime": regime, "crisis_score": crisis_score}
                )
            return None

        # Manage existing open position
        if ctx.has_position():
            pos_dir = ctx.position_direction()
            entry_price = ctx.position.get("entry_price", current_price) if ctx.position else current_price

            # Mean-reversion exit condition
            if pos_dir == "long" and spread < 0.2:
                return ctx.signal(
                    "flat",
                    confidence=0.65,
                    metadata={"reason": "eth_outperformance_reverted", "spread": spread, "price": current_price}
                )
            elif pos_dir == "short" and spread > -0.2:
                return ctx.signal(
                    "flat",
                    confidence=0.65,
                    metadata={"reason": "eth_underperformance_reverted", "spread": spread, "price": current_price}
                )
            return None

        # Mandatory cooldown check after exit to avoid friction death
        if (ctx.bar_index - self.last_exit_bar) < self.cooldown_bars:
            return None

        # High-conviction entry criteria:
        # Require transition threshold cross or breakout confirmation to prevent continuous triggers
        long_trigger = (
            spread >= self.min_spread_bps
            and prev_spread < self.min_spread_bps
            and current_price > ema20
            and ema20 > ema50
            and 45.0 < rsi < 68.0
        )

        short_trigger = (
            spread <= -self.min_spread_bps
            and prev_spread > -self.min_spread_bps
            and current_price < ema20
            and ema20 < ema50
            and 32.0 < rsi < 55.0
        )

        if long_trigger:
            conf = min(0.9, 0.55 + (abs(spread) - self.min_spread_bps) * 0.08)
            return ctx.signal(
                "long",
                confidence=conf,
                stop_loss_bps=self.METADATA["declared_sl_bps"],
                take_profit_bps=self.METADATA["declared_tp_bps"],
                horizon_seconds=self.METADATA["declared_hold_seconds"],
                metadata={
                    "reason": "eth_rel_strength_breakout",
                    "spread": spread,
                    "rsi": rsi,
                    "ema20": ema20,
                    "ema50": ema50,
                    "eth_ret": eth_ret,
                    "btc_ret": btc_ret,
                }
            )

        if short_trigger:
            conf = min(0.9, 0.55 + (abs(spread) - self.min_spread_bps) * 0.08)
            return ctx.signal(
                "short",
                confidence=conf,
                stop_loss_bps=self.METADATA["declared_sl_bps"],
                take_profit_bps=self.METADATA["declared_tp_bps"],
                horizon_seconds=self.METADATA["declared_hold_seconds"],
                metadata={
                    "reason": "eth_rel_weakness_breakdown",
                    "spread": spread,
                    "rsi": rsi,
                    "ema20": ema20,
                    "ema50": ema50,
                    "eth_ret": eth_ret,
                    "btc_ret": btc_ret,
                }
            )

        return None