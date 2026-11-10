from domains.strategy_contract import Strategy, BarContext, Signal
from typing import Optional, Dict, Any
import math

class EthBtcRelativeMomentum(Strategy):
    METADATA = {
        "name": "ETH-BTC Relative Strength Momentum",
        "domain": "eth_usdc",
        "declared_sl_bps": 250.0,
        "declared_tp_bps": 400.0,
        "declared_hold_seconds": 14400,
        "warmup_bars": 35,
        "required_features": ["eth_return_pct", "btc_return_pct"],
    }

    def initialize(self, ctx: BarContext) -> None:
        self.spread_threshold = 2.0
        self.reversion_threshold = 0.5
        self.cooldown_period = 6
        self.last_exit_bar = -999
        self.max_hold_bars = 18

    def _ema(self, values, period):
        if len(values) < period:
            return None
        k = 2.0 / (period + 1)
        ema = sum(values[:period]) / period
        for v in values[period:]:
            ema = v * k + ema * (1 - k)
        return ema

    def _atr(self, highs, lows, closes, period=14):
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
        closes = ctx.closes(35)
        highs = ctx.highs(35)
        lows = ctx.lows(35)
        if len(closes) < 35:
            return None

        regime = ctx.market.get("regime", "NORMAL")
        if regime in ("CRISIS", "MELTDOWN"):
            if ctx.has_position():
                return ctx.signal("flat", confidence=0.8, metadata={"reason": "crisis_regime_exit"})
            return None

        eth_ret = ctx.features.get("eth_return_pct", 0.0)
        btc_ret = ctx.features.get("btc_return_pct", 0.0)
        spread = eth_ret - btc_ret

        price = ctx.bar.close
        ema20 = self._ema(closes, 20)
        atr = self._atr(highs, lows, closes, 14)
        if ema20 is None or atr is None:
            return None

        pos = ctx.position
        if pos is not None:
            direction = pos.get("direction")
            bars_held = ctx.bar_index - self.last_exit_bar if self.last_exit_bar > 0 else 1

            if direction == "long" and spread <= self.reversion_threshold:
                return ctx.signal(
                    "flat",
                    confidence=0.6,
                    metadata={"reason": "eth_outperformance_reverted", "spread": spread, "price": price}
                )
            elif direction == "short" and spread >= -self.reversion_threshold:
                return ctx.signal(
                    "flat",
                    confidence=0.6,
                    metadata={"reason": "eth_underperformance_reverted", "spread": spread, "price": price}
                )

            if bars_held >= self.max_hold_bars:
                return ctx.signal(
                    "flat",
                    confidence=0.5,
                    metadata={"reason": "time_based_rotation_exit", "bars_held": bars_held, "spread": spread}
                )
            return None

        if ctx.bar_index - self.last_exit_bar < self.cooldown_period:
            return None

        vol_bps = (atr / price) * 10000.0
        dyn_sl = max(180.0, min(350.0, vol_bps * 1.5))
        dyn_tp = max(250.0, min(500.0, vol_bps * 2.2))

        if spread > self.spread_threshold and price > ema20:
            conf = min(0.9, 0.55 + (spread - self.spread_threshold) * 0.08)
            return ctx.signal(
                "long",
                confidence=conf,
                stop_loss_bps=dyn_sl,
                take_profit_bps=dyn_tp,
                metadata={
                    "reason": "eth_lead_breakout_long",
                    "spread": spread,
                    "eth_ret": eth_ret,
                    "btc_ret": btc_ret,
                    "ema20": ema20,
                    "price": price,
                }
            )

        if spread < -self.spread_threshold and price < ema20:
            conf = min(0.9, 0.55 + (-spread - self.spread_threshold) * 0.08)
            return ctx.signal(
                "short",
                confidence=conf,
                stop_loss_bps=dyn_sl,
                take_profit_bps=dyn_tp,
                metadata={
                    "reason": "eth_lag_breakdown_short",
                    "spread": spread,
                    "eth_ret": eth_ret,
                    "btc_ret": btc_ret,
                    "ema20": ema20,
                    "price": price,
                }
            )

        return None