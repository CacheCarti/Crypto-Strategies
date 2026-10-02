from domains.strategy_contract import Strategy, BarContext, Signal
from typing import Optional, Dict, Any
import math

class EthBtcRelativeMomentum(Strategy):
    METADATA = {
        "name": "ETH-BTC Relative Momentum Rotation",
        "domain": "eth_usdc",
        "declared_sl_bps": 250.0,
        "declared_tp_bps": 500.0,
        "declared_hold_seconds": 14400,
        "warmup_bars": 35,
        "required_features": ["eth_return_pct", "btc_return_pct"],
    }

    def initialize(self, ctx: BarContext) -> None:
        self.spread_threshold = 1.0
        self.reversion_threshold = 0.15
        self.ema_period = 20
        self.rsi_period = 14
        self.cooldown_period = 4
        self.max_hold_bars = 24
        self.last_exit_bar = -999
        self.entry_bar = -999

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
        if avg_loss == 0:
            return 100.0
        rs = avg_gain / avg_loss
        return 100.0 - (100.0 / (1.0 + rs))

    def on_position_close(self, ctx: BarContext, position: Dict[str, Any]) -> None:
        self.last_exit_bar = ctx.bar_index

    def on_bar(self, ctx: BarContext) -> Optional[Signal]:
        closes = ctx.closes(self.METADATA["warmup_bars"])
        if len(closes) < self.METADATA["warmup_bars"]:
            return None

        eth_ret = ctx.features.get("eth_return_pct", 0.0)
        btc_ret = ctx.features.get("btc_return_pct", 0.0)
        spread = eth_ret - btc_ret

        ema = self._ema(closes, self.ema_period)
        rsi = self._rsi(closes, self.rsi_period)

        if ema is None or rsi is None:
            return None

        curr_price = ctx.bar.close
        has_pos = ctx.has_position()
        pos_dir = ctx.position_direction()

        # Exit management for active positions
        if has_pos:
            bars_held = ctx.bar_index - self.entry_bar
            if pos_dir == "long":
                if spread <= self.reversion_threshold or rsi >= 75.0 or bars_held >= self.max_hold_bars:
                    return ctx.signal(
                        "flat",
                        confidence=0.6,
                        metadata={
                            "reason": "long_spread_reverted_or_time_exit",
                            "spread": round(spread, 3),
                            "rsi": round(rsi, 2),
                            "bars_held": bars_held,
                        },
                    )
            elif pos_dir == "short":
                if spread >= -self.reversion_threshold or rsi <= 25.0 or bars_held >= self.max_hold_bars:
                    return ctx.signal(
                        "flat",
                        confidence=0.6,
                        metadata={
                            "reason": "short_spread_reverted_or_time_exit",
                            "spread": round(spread, 3),
                            "rsi": round(rsi, 2),
                            "bars_held": bars_held,
                        },
                    )
            return None

        # Cooldown guard after trade exit
        if (ctx.bar_index - self.last_exit_bar) < self.cooldown_period:
            return None

        # Crisis protection filter
        crisis_score = ctx.market.get("crisis_score", 0.0)
        if crisis_score > 0.85 or ctx.regime == "crisis":
            return None

        # Long Setup: ETH outperforming BTC by >= threshold with non-exhausted RSI
        if spread >= self.spread_threshold and rsi < 70.0 and curr_price >= ema * 0.992:
            conf = min(0.90, 0.55 + max(0.0, (spread - self.spread_threshold) * 0.10))
            self.entry_bar = ctx.bar_index
            return ctx.signal(
                "long",
                confidence=round(conf, 2),
                stop_loss_bps=self.METADATA["declared_sl_bps"],
                take_profit_bps=self.METADATA["declared_tp_bps"],
                horizon_seconds=self.METADATA["declared_hold_seconds"],
                metadata={
                    "reason": "eth_btc_relative_strength_lead",
                    "spread": round(spread, 3),
                    "eth_ret": round(eth_ret, 3),
                    "btc_ret": round(btc_ret, 3),
                    "rsi": round(rsi, 2),
                    "ema": round(ema, 2),
                },
            )

        # Short Setup: ETH underperforming BTC by <= -threshold with non-exhausted RSI
        if spread <= -self.spread_threshold and rsi > 30.0 and curr_price <= ema * 1.008:
            conf = min(0.90, 0.55 + max(0.0, (-spread - self.spread_threshold) * 0.10))
            self.entry_bar = ctx.bar_index
            return ctx.signal(
                "short",
                confidence=round(conf, 2),
                stop_loss_bps=self.METADATA["declared_sl_bps"],
                take_profit_bps=self.METADATA["declared_tp_bps"],
                horizon_seconds=self.METADATA["declared_hold_seconds"],
                metadata={
                    "reason": "eth_btc_relative_weakness_lag",
                    "spread": round(spread, 3),
                    "eth_ret": round(eth_ret, 3),
                    "btc_ret": round(btc_ret, 3),
                    "rsi": round(rsi, 2),
                    "ema": round(ema, 2),
                },
            )

        return None