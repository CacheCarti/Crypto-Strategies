from domains.strategy_contract import Strategy, BarContext, Signal
from typing import Optional, Dict, Any
import math

class EthBtcRelativeStrengthTrend(Strategy):
    METADATA = {
        "name": "EthBtcRelativeStrengthTrend",
        "domain": "eth_usdc",
        "declared_sl_bps": 320.0,
        "declared_tp_bps": 540.0,
        "declared_hold_seconds": 14400,
        "warmup_bars": 40,
        "required_features": ["eth_return_pct", "btc_return_pct"],
    }

    def initialize(self, ctx: BarContext) -> None:
        self.spread_threshold = 2.0
        self.cooldown_period = 6
        self.last_exit_bar = -999
        self.entry_bar = -999
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
            ema = v * k + ema * (1.0 - k)
        return ema

    def on_position_close(self, ctx: BarContext, position: Dict[str, Any]) -> None:
        self.last_exit_bar = ctx.bar_index
        self.entry_bar = -999

    def on_bar(self, ctx: BarContext) -> Optional[Signal]:
        closes = ctx.closes(40)
        highs = ctx.highs(40)
        lows = ctx.lows(40)

        if len(closes) < 30:
            return None

        # Feature reading
        eth_ret = ctx.features.get("eth_return_pct", 0.0)
        btc_ret = ctx.features.get("btc_return_pct", 0.0)
        spread = eth_ret - btc_ret

        # Regime & Crisis gating
        crisis_score = ctx.market.get("crisis_score", 0.0)
        if crisis_score > 0.65 or ctx.regime == "crisis":
            if ctx.has_position():
                return ctx.signal("flat", confidence=0.8, metadata={"reason": "crisis_exit", "crisis_score": crisis_score})
            return None

        # In-position management: structural / timed exit + mean-reversion of spread
        if ctx.has_position():
            pos_dir = ctx.position_direction()
            bars_held = ctx.bar_index - self.entry_bar

            # 1. Structural spread reversion exit
            if pos_dir == "long" and spread <= 0.2:
                return ctx.signal(
                    "flat",
                    confidence=0.6,
                    metadata={"reason": "long_spread_reverted", "spread": spread, "bars_held": bars_held}
                )
            elif pos_dir == "short" and spread >= -0.2:
                return ctx.signal(
                    "flat",
                    confidence=0.6,
                    metadata={"reason": "short_spread_reverted", "spread": spread, "bars_held": bars_held}
                )

            # 2. Time-decay exit to avoid hanging in stagnant trades
            if bars_held >= self.max_hold_bars:
                return ctx.signal(
                    "flat",
                    confidence=0.5,
                    metadata={"reason": "timed_hold_expiry", "bars_held": bars_held, "spread": spread}
                )
            return None

        # Mandatory cooldown guard
        if (ctx.bar_index - self.last_exit_bar) < self.cooldown_period:
            return None

        # Trend filter to prevent counter-trend fakeouts
        ema20 = self._ema(closes, 20)
        if ema20 is None:
            return None

        atr = self._atr(highs, lows, closes, 14)
        if atr is None or closes[-1] == 0:
            return None

        # Volatility-based adaptive stops (in bps)
        atr_bps = (atr / closes[-1]) * 10000.0
        sl_bps = max(180.0, min(450.0, atr_bps * 1.8))
        tp_bps = max(280.0, min(750.0, atr_bps * 3.2))

        # Long Entry: ETH clearly outperforming BTC and price confirmed above EMA20
        if spread >= self.spread_threshold and closes[-1] > ema20:
            confidence = min(0.9, 0.55 + (spread - self.spread_threshold) * 0.05)
            self.entry_bar = ctx.bar_index
            return ctx.signal(
                "long",
                confidence=confidence,
                stop_loss_bps=sl_bps,
                take_profit_bps=tp_bps,
                horizon_seconds=self.METADATA["declared_hold_seconds"],
                metadata={
                    "reason": "eth_outperformance_trend_long",
                    "spread": spread,
                    "eth_ret": eth_ret,
                    "btc_ret": btc_ret,
                    "ema20": ema20,
                    "atr_bps": atr_bps
                }
            )

        # Short Entry: ETH clearly underperforming BTC and price confirmed below EMA20
        if spread <= -self.spread_threshold and closes[-1] < ema20:
            confidence = min(0.9, 0.55 + (abs(spread) - self.spread_threshold) * 0.05)
            self.entry_bar = ctx.bar_index
            return ctx.signal(
                "short",
                confidence=confidence,
                stop_loss_bps=sl_bps,
                take_profit_bps=tp_bps,
                horizon_seconds=self.METADATA["declared_hold_seconds"],
                metadata={
                    "reason": "eth_underperformance_trend_short",
                    "spread": spread,
                    "eth_ret": eth_ret,
                    "btc_ret": btc_ret,
                    "ema20": ema20,
                    "atr_bps": atr_bps
                }
            )

        return None