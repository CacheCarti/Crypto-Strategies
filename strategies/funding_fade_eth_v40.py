from domains.strategy_contract import Strategy, BarContext, Signal
from typing import Optional, Dict, Any
import math

class EthFundingContrarianSqueeze(Strategy):
    METADATA = {
        "name": "EthFundingContrarianSqueeze",
        "domain": "eth_usdc",
        "declared_sl_bps": 280.0,
        "declared_tp_bps": 420.0,
        "declared_hold_seconds": 14400,
        "warmup_bars": 72,
        "required_features": ["funding_rate_ethusdt"],
    }

    def initialize(self, ctx: BarContext) -> None:
        self.funding_history = []
        self.history_len = 168
        self.last_exit_bar = -999
        self.cooldown_bars = 6
        self.persist_bars = 3
        self.entry_bar = -999

    def _percentile(self, values: list, value: float) -> float:
        if not values:
            return 50.0
        count = sum(1 for v in values if v <= value)
        return (count / len(values)) * 100.0

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
        closes = ctx.closes(72)
        if len(closes) < 72:
            return None

        current_funding = ctx.features.get("funding_rate_ethusdt", 0.0)
        self.funding_history.append(current_funding)
        if len(self.funding_history) > self.history_len:
            self.funding_history.pop(0)

        if len(self.funding_history) < 48:
            return None

        recent_funding = self.funding_history[-self.persist_bars:]
        pct_rank = self._percentile(self.funding_history, current_funding)

        # In-position management
        if ctx.has_position():
            pos_dir = ctx.position_direction()
            bars_held = ctx.bar_index - self.entry_bar

            # Exit when funding mean-reverts or after horizon timeout
            if pos_dir == "long" and (pct_rank >= 45.0 or bars_held >= 24):
                return ctx.signal(
                    "flat",
                    confidence=0.6,
                    metadata={"reason": "funding_normalized_long_exit", "pct_rank": pct_rank, "bars_held": bars_held}
                )
            if pos_dir == "short" and (pct_rank <= 55.0 or bars_held >= 24):
                return ctx.signal(
                    "flat",
                    confidence=0.6,
                    metadata={"reason": "funding_normalized_short_exit", "pct_rank": pct_rank, "bars_held": bars_held}
                )
            return None

        # Entry cooldown guard
        if ctx.bar_index - self.last_exit_bar < self.cooldown_bars:
            return None

        ema_trend = self._ema(closes, 48)
        rsi = self._rsi(closes, 14)
        if ema_trend is None or rsi is None:
            return None

        current_price = ctx.bar.close
        regime = ctx.market.get("regime", "NORMAL")
        if regime in ["CRISIS", "MELTDOWN"]:
            return None

        # Long squeeze condition: persistent heavily negative funding (bottom 10%)
        # Filter against runaway knife: price not severely collapsed below EMA & RSI recovering from oversold
        long_persist = all(f < 0.0 for f in recent_funding)
        if long_persist and pct_rank <= 10.0:
            price_dist_bps = ((current_price - ema_trend) / ema_trend) * 10000.0
            if -350.0 <= price_dist_bps <= 150.0 and rsi > 30.0:
                self.entry_bar = ctx.bar_index
                return ctx.signal(
                    "long",
                    confidence=0.75,
                    stop_loss_bps=280.0,
                    take_profit_bps=420.0,
                    metadata={
                        "reason": "negative_funding_short_squeeze",
                        "pct_rank": pct_rank,
                        "current_funding": current_funding,
                        "rsi": rsi,
                        "dist_to_ema_bps": price_dist_bps
                    }
                )

        # Short squeeze fade: persistent heavily positive funding (top 90%)
        # Filter against runaway parabolic: price not extended too far above EMA & RSI not blown out
        short_persist = all(f > 0.0 for f in recent_funding)
        if short_persist and pct_rank >= 90.0:
            price_dist_bps = ((current_price - ema_trend) / ema_trend) * 10000.0
            if -150.0 <= price_dist_bps <= 350.0 and rsi < 70.0:
                self.entry_bar = ctx.bar_index
                return ctx.signal(
                    "short",
                    confidence=0.75,
                    stop_loss_bps=280.0,
                    take_profit_bps=420.0,
                    metadata={
                        "reason": "positive_funding_long_unwind",
                        "pct_rank": pct_rank,
                        "current_funding": current_funding,
                        "rsi": rsi,
                        "dist_to_ema_bps": price_dist_bps
                    }
                )

        return None