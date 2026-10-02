from domains.strategy_contract import Strategy, BarContext, Signal
from typing import Optional, Dict, Any
import math

class EthFundingFlipReversion(Strategy):
    METADATA = {
        "name": "EthFundingFlipReversion",
        "domain": "eth_usdc",
        "declared_sl_bps": 280.0,
        "declared_tp_bps": 480.0,
        "declared_hold_seconds": 14400,
        "warmup_bars": 35,
        "required_features": ["funding_rate_ethusdt", "fear_greed_index"],
    }

    def initialize(self, ctx: BarContext) -> None:
        self.period_rsi = 14
        self.period_ema = 21
        self.cooldown_bars = 6
        self.max_hold_bars = 12
        self.prev_funding: Optional[float] = None
        self.entry_bar_idx: int = 0
        self.cooldown_until: int = 0

    def _rsi(self, closes, period: int = 14) -> Optional[float]:
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

    def _ema(self, values, period: int) -> Optional[float]:
        if len(values) < period:
            return None
        k = 2.0 / (period + 1)
        ema_val = sum(values[:period]) / period
        for v in values[period:]:
            ema_val = v * k + ema_val * (1.0 - k)
        return ema_val

    def on_position_close(self, ctx: BarContext, position: Dict[str, Any]) -> None:
        self.cooldown_until = ctx.bar_index + self.cooldown_bars

    def on_bar(self, ctx: BarContext) -> Optional[Signal]:
        closes = ctx.closes(self.METADATA["warmup_bars"])
        if len(closes) < self.METADATA["warmup_bars"]:
            return None

        current_funding = ctx.features.get("funding_rate_ethusdt", 0.0)
        fg_index = ctx.features.get("fear_greed_index", 50.0)
        crisis_score = ctx.market.get("crisis_score", 0.0)

        # Skip during severe market breakdown
        if crisis_score > 0.7:
            if self.prev_funding is None:
                self.prev_funding = current_funding
            return None

        rsi = self._rsi(closes, self.period_rsi)
        ema = self._ema(closes, self.period_ema)
        if rsi is None or ema is None:
            self.prev_funding = current_funding
            return None

        price = ctx.bar.close

        # Position management
        if ctx.has_position():
            bars_in_pos = ctx.bar_index - self.entry_bar_idx
            if bars_in_pos >= self.max_hold_bars:
                self.prev_funding = current_funding
                return ctx.signal(
                    "flat",
                    confidence=0.6,
                    metadata={
                        "reason": "time_stop_exhaustion",
                        "bars_held": bars_in_pos,
                        "rsi": round(rsi, 2),
                        "funding_rate": current_funding,
                        "price": price,
                    },
                )
            self.prev_funding = current_funding
            return None

        # Check cooldown
        if ctx.bar_index < self.cooldown_until:
            self.prev_funding = current_funding
            return None

        if self.prev_funding is None:
            self.prev_funding = current_funding
            return None

        # Funding rate flip detection (with noise deadband of 1e-6)
        flip_to_positive = (self.prev_funding <= -1e-6) and (current_funding >= 1e-6)
        flip_to_negative = (self.prev_funding >= 1e-6) and (current_funding <= -1e-6)
        self.prev_funding = current_funding

        # Short trigger: Shorts capitulated / long crowding flip -> local top unwind
        if flip_to_positive and rsi >= 46.0 and fg_index > 25:
            conf = min(0.9, max(0.5, 0.5 + (rsi - 50.0) / 100.0))
            self.entry_bar_idx = ctx.bar_index
            return ctx.signal(
                "short",
                confidence=conf,
                stop_loss_bps=self.METADATA["declared_sl_bps"],
                take_profit_bps=self.METADATA["declared_tp_bps"],
                horizon_seconds=self.METADATA["declared_hold_seconds"],
                metadata={
                    "reason": "funding_neg_to_pos_shorts_capitulated",
                    "funding_rate": current_funding,
                    "rsi": round(rsi, 2),
                    "ema21": round(ema, 2),
                    "price": price,
                    "fear_greed": fg_index,
                },
            )

        # Long trigger: Longs flushed out / short crowding flip -> local bottom bounce
        if flip_to_negative and rsi <= 54.0 and fg_index < 75:
            conf = min(0.9, max(0.5, 0.5 + (50.0 - rsi) / 100.0))
            self.entry_bar_idx = ctx.bar_index
            return ctx.signal(
                "long",
                confidence=conf,
                stop_loss_bps=self.METADATA["declared_sl_bps"],
                take_profit_bps=self.METADATA["declared_tp_bps"],
                horizon_seconds=self.METADATA["declared_hold_seconds"],
                metadata={
                    "reason": "funding_pos_to_neg_longs_flushed",
                    "funding_rate": current_funding,
                    "rsi": round(rsi, 2),
                    "ema21": round(ema, 2),
                    "price": price,
                    "fear_greed": fg_index,
                },
            )

        return None