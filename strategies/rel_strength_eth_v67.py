from domains.strategy_contract import Strategy, BarContext, Signal
from typing import Optional, Dict, Any
import math

class EthBtcRelativeStrength(Strategy):
    METADATA = {
        "name": "EthBtcRelativeStrength",
        "domain": "eth_usdc",
        "declared_sl_bps": 320.0,
        "declared_tp_bps": 520.0,
        "declared_hold_seconds": 18000,
        "warmup_bars": 35,
        "required_features": ["eth_return_pct", "btc_return_pct"],
    }

    def initialize(self, ctx: BarContext) -> None:
        self.cooldown_bars = 6
        self.last_exit_bar = -999
        self.spread_threshold = 2.0
        self.max_hold_bars = 16

    def _atr(self, highs, lows, closes, period=14):
        if len(closes) < period + 1:
            return None
        trs = []
        for i in range(1, len(closes)):
            tr = max(highs[i] - lows[i], abs(highs[i] - closes[i - 1]), abs(lows[i] - closes[i - 1]))
            trs.append(tr)
        return sum(trs[-period:]) / period

    def _ema(self, values, period):
        if len(values) < period:
            return None
        k = 2.0 / (period + 1)
        ema = sum(values[:period]) / period
        for v in values[period:]:
            ema = v * k + ema * (1 - k)
        return ema

    def on_position_close(self, ctx: BarContext, position: Dict[str, Any]) -> None:
        self.last_exit_bar = ctx.bar_index

    def on_bar(self, ctx: BarContext) -> Optional[Signal]:
        closes = ctx.closes(35)
        highs = ctx.highs(35)
        lows = ctx.lows(35)

        if len(closes) < 35:
            return None

        # Feature reading
        eth_ret = ctx.features.get("eth_return_pct", 0.0)
        btc_ret = ctx.features.get("btc_return_pct", 0.0)
        spread = eth_ret - btc_ret

        # Volatility baseline
        atr = self._atr(highs, lows, closes, period=14)
        if atr is None or closes[-1] == 0:
            return None
        atr_bps = (atr / closes[-1]) * 10000.0

        ema_trend = self._ema(closes, 24)
        if ema_trend is None:
            return None

        # Manage active position via timed / reversion structure exit
        if ctx.has_position():
            pos = ctx.position or {}
            direction = pos.get("direction", ctx.position_direction())
            entry_price = pos.get("entry_price", closes[-1])

            # Exit logic: Mean reversion of spread across zero or holding timeout
            if direction == "long":
                held_bars = ctx.bar_index - self.last_exit_bar if self.last_exit_bar > 0 else 0
                if spread <= 0.2:
                    return ctx.signal(
                        "flat",
                        confidence=0.7,
                        metadata={"reason": "long_spread_reverted", "spread": spread, "atr_bps": atr_bps}
                    )
            elif direction == "short":
                if spread >= -0.2:
                    return ctx.signal(
                        "flat",
                        confidence=0.7,
                        metadata={"reason": "short_spread_reverted", "spread": spread, "atr_bps": atr_bps}
                    )
            return None

        # Cooldown guard after exit
        if ctx.bar_index - self.last_exit_bar < self.cooldown_bars:
            return None

        # Avoid crisis dislocations where relative strength signals break down
        regime = ctx.market.get("regime", "NORMAL")
        if regime in ("CRISIS", "MELTDOWN"):
            return None

        # Dynamic stop scaled by local volatility to survive swing chop
        sl_bps = max(240.0, min(480.0, atr_bps * 1.8))
        tp_bps = max(380.0, min(750.0, sl_bps * 1.6))

        # Long: ETH outpacing BTC by threshold + confirming price above short-term trend
        if spread >= self.spread_threshold and closes[-1] > ema_trend:
            confidence = min(0.9, 0.5 + (spread - self.spread_threshold) * 0.1)
            return ctx.signal(
                "long",
                confidence=confidence,
                stop_loss_bps=sl_bps,
                take_profit_bps=tp_bps,
                horizon_seconds=18000,
                metadata={
                    "reason": "eth_outperforming_btc_momentum",
                    "spread": spread,
                    "eth_ret": eth_ret,
                    "btc_ret": btc_ret,
                    "atr_bps": atr_bps,
                    "ema_trend": ema_trend
                }
            )

        # Short: ETH lagging BTC by threshold + confirming price below short-term trend
        if spread <= -self.spread_threshold and closes[-1] < ema_trend:
            confidence = min(0.9, 0.5 + (abs(spread) - self.spread_threshold) * 0.1)
            return ctx.signal(
                "short",
                confidence=confidence,
                stop_loss_bps=sl_bps,
                take_profit_bps=tp_bps,
                horizon_seconds=18000,
                metadata={
                    "reason": "eth_underperforming_btc_momentum",
                    "spread": spread,
                    "eth_ret": eth_ret,
                    "btc_ret": btc_ret,
                    "atr_bps": atr_bps,
                    "ema_trend": ema_trend
                }
            )

        return None