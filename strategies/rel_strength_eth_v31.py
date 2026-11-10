from domains.strategy_contract import Strategy, BarContext, Signal
from typing import Optional, Dict, Any
import math

class EthBtcRelativeStrengthMomentum(Strategy):
    METADATA = {
        "name": "EthBtcRelativeStrengthMomentum",
        "domain": "eth_usdc",
        "declared_sl_bps": 250.0,
        "declared_tp_bps": 450.0,
        "declared_hold_seconds": 14400,
        "warmup_bars": 40,
        "required_features": ["eth_return_pct", "btc_return_pct"],
    }

    def initialize(self, ctx: BarContext) -> None:
        self.spread_threshold = 2.0
        self.ema_period = 20
        self.atr_period = 14
        self.cooldown_bars = 6
        self.max_hold_bars = 16
        self.last_exit_bar = -999
        self.entry_bar = 0

    def _ema(self, values, period: int):
        if len(values) < period:
            return None
        k = 2.0 / (period + 1)
        ema = sum(values[:period]) / period
        for v in values[period:]:
            ema = v * k + ema * (1.0 - k)
        return ema

    def _atr(self, highs, lows, closes, period: int = 14):
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
        highs = ctx.highs(45)
        lows = ctx.lows(45)

        if len(closes) < self.METADATA["warmup_bars"]:
            return None

        # Feature reads
        eth_ret = ctx.features.get("eth_return_pct", 0.0)
        btc_ret = ctx.features.get("btc_return_pct", 0.0)
        spread = eth_ret - btc_ret

        # Context indicators
        ema20 = self._ema(closes, self.ema_period)
        atr = self._atr(highs, lows, closes, self.atr_period)
        if ema20 is None or atr is None:
            return None

        current_price = ctx.bar.close

        # Position management
        if ctx.has_position():
            pos_dir = ctx.position_direction()
            bars_held = ctx.bar_index - self.entry_bar

            # Mean-reversion exit condition of the relative spread
            if pos_dir == "long" and spread <= 0.2:
                return ctx.signal(
                    "flat",
                    confidence=0.6,
                    metadata={"reason": "spread_reverted_long_exit", "spread": spread, "bars_held": bars_held}
                )
            elif pos_dir == "short" and spread >= -0.2:
                return ctx.signal(
                    "flat",
                    confidence=0.6,
                    metadata={"reason": "spread_reverted_short_exit", "spread": spread, "bars_held": bars_held}
                )

            # Horizon time stop to ensure trade independence and cycle capital
            if bars_held >= self.max_hold_bars:
                return ctx.signal(
                    "flat",
                    confidence=0.5,
                    metadata={"reason": "max_bars_timeout", "spread": spread, "bars_held": bars_held}
                )

            return None

        # Entry logic: mandatory cooldown enforcement
        if (ctx.bar_index - self.last_exit_bar) < self.cooldown_bars:
            return None

        # Filter out extreme crisis regimes
        crisis_score = ctx.market.get("crisis_score", 0.0)
        if crisis_score > 0.65:
            return None

        # Long: ETH outpacing BTC by threshold + confirming price above trend
        if spread >= self.spread_threshold and current_price > ema20:
            self.entry_bar = ctx.bar_index
            confidence = min(0.9, 0.5 + (spread - self.spread_threshold) * 0.1)
            return ctx.signal(
                "long",
                confidence=confidence,
                stop_loss_bps=self.METADATA["declared_sl_bps"],
                take_profit_bps=self.METADATA["declared_tp_bps"],
                metadata={
                    "reason": "eth_outperforming_btc_above_trend",
                    "spread": spread,
                    "eth_ret": eth_ret,
                    "btc_ret": btc_ret,
                    "price": current_price,
                    "ema20": ema20
                }
            )

        # Short: ETH lagging BTC by threshold + confirming price below trend
        if spread <= -self.spread_threshold and current_price < ema20:
            self.entry_bar = ctx.bar_index
            confidence = min(0.9, 0.5 + (abs(spread) - self.spread_threshold) * 0.1)
            return ctx.signal(
                "short",
                confidence=confidence,
                stop_loss_bps=self.METADATA["declared_sl_bps"],
                take_profit_bps=self.METADATA["declared_tp_bps"],
                metadata={
                    "reason": "eth_underperforming_btc_below_trend",
                    "spread": spread,
                    "eth_ret": eth_ret,
                    "btc_ret": btc_ret,
                    "price": current_price,
                    "ema20": ema20
                }
            )

        return None