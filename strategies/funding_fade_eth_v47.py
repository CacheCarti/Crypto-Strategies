from domains.strategy_contract import Strategy, BarContext, Signal
from typing import Optional, Dict, Any
import math

class EthFundingContrarian(Strategy):
    METADATA = {
        "name": "EthFundingContrarian",
        "domain": "eth_usdc",
        "declared_sl_bps": 220.0,
        "declared_tp_bps": 400.0,
        "declared_hold_seconds": 21600,  # ~6 hours intended hold
        "warmup_bars": 40,
        "required_features": ["funding_rate_ethusdt"],
    }

    def initialize(self, ctx: BarContext) -> None:
        self.funding_hist = []
        self.last_exit_bar = -999
        self.cooldown_bars = 6
        self.persist_bars = 3
        self.pos_entry_bar = -999
        self.max_hold_bars = 16

    def _ema(self, values, period):
        if len(values) < period:
            return None
        k = 2.0 / (period + 1)
        ema = sum(values[:period]) / period
        for v in values[period:]:
            ema = v * k + ema * (1 - k)
        return ema

    def _rsi(self, closes, period=14):
        if len(closes) < period + 1:
            return None
        gains, losses = [], []
        for i in range(1, len(closes)):
            diff = closes[i] - closes[i-1]
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
        closes = ctx.closes(40)
        if len(closes) < 40:
            return None

        # Track persistent funding rate
        cur_funding = ctx.features.get("funding_rate_ethusdt", 0.0)
        self.funding_hist.append(cur_funding)
        if len(self.funding_hist) > 20:
            self.funding_hist.pop(0)

        # Light price filter: 30-period EMA for micro-trend, RSI-14 to avoid direct knife-catching
        ema30 = self._ema(closes, 30)
        rsi14 = self._rsi(closes, 14)
        if ema30 is None or rsi14 is None:
            return None

        price = ctx.bar.close

        # Position management
        if ctx.has_position():
            pos_dir = ctx.position_direction()
            bars_held = ctx.bar_index - self.pos_entry_bar

            # Normalization exit: crowd pressure subsided
            if pos_dir == "long" and cur_funding >= -0.00002:
                return ctx.signal(
                    "flat",
                    confidence=0.7,
                    metadata={"reason": "funding_normalized_long_exit", "funding": cur_funding, "bars_held": bars_held}
                )
            if pos_dir == "short" and cur_funding <= 0.00005:
                return ctx.signal(
                    "flat",
                    confidence=0.7,
                    metadata={"reason": "funding_normalized_short_exit", "funding": cur_funding, "bars_held": bars_held}
                )

            # Max hold time safeguard
            if bars_held >= self.max_hold_bars:
                return ctx.signal(
                    "flat",
                    confidence=0.5,
                    metadata={"reason": "max_bars_timeout", "funding": cur_funding, "bars_held": bars_held}
                )
            return None

        # Cooldown guard after trade exit
        if (ctx.bar_index - self.last_exit_bar) < self.cooldown_bars:
            return None

        # Check funding persistence over the last N bars
        if len(self.funding_hist) < self.persist_bars:
            return None

        recent_funding = self.funding_hist[-self.persist_bars:]

        # High negative funding: shorts overcrowded, paying longs
        crowd_extreme_short = all(f < -0.00015 for f in recent_funding)
        # High positive funding: longs overcrowded, paying shorts
        crowd_extreme_long = all(f > 0.00025 for f in recent_funding)

        # Regimes filter out meltdown/extreme stress where funding can disconnect
        regime = ctx.market.get("regime", "NORMAL")
        if regime in ("CRISIS", "MELTDOWN"):
            return None

        # Long entry: crowd short squeeze expected, not crashing violently (RSI > 32 and price within 3% of EMA30)
        if crowd_extreme_short and rsi14 > 32.0 and price >= (ema30 * 0.97):
            self.pos_entry_bar = ctx.bar_index
            conf = min(0.9, 0.65 + abs(cur_funding) * 500)
            return ctx.signal(
                "long",
                confidence=conf,
                stop_loss_bps=220.0,
                take_profit_bps=400.0,
                metadata={
                    "reason": "negative_funding_short_squeeze",
                    "funding": cur_funding,
                    "rsi": rsi14,
                    "ema30": ema30,
                    "price": price
                }
            )

        # Short entry: crowd long squeeze expected, not blasting in parabolic uptrend (RSI < 68 and price within 3% of EMA30)
        if crowd_extreme_long and rsi14 < 68.0 and price <= (ema30 * 1.03):
            self.pos_entry_bar = ctx.bar_index
            conf = min(0.9, 0.65 + cur_funding * 400)
            return ctx.signal(
                "short",
                confidence=conf,
                stop_loss_bps=220.0,
                take_profit_bps=400.0,
                metadata={
                    "reason": "positive_funding_long_unwind",
                    "funding": cur_funding,
                    "rsi": rsi14,
                    "ema30": ema30,
                    "price": price
                }
            )

        return None