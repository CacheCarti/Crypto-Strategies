from domains.strategy_contract import Strategy, BarContext, Signal
from typing import Optional, Dict, Any
import math

class UsSessionMomentumBtc(Strategy):
    METADATA = {
        "name": "UsSessionMomentumBtc",
        "domain": "btc_usdc",
        "declared_sl_bps": 220.0,
        "declared_tp_bps": 420.0,
        "declared_hold_seconds": 21600,  # ~6 hours max hold
        "warmup_bars": 35,
    }

    def initialize(self, ctx: BarContext) -> None:
        self.last_traded_date = None
        self.entry_bar_idx = -100
        self.cooldown_bars = 4

    def _ema(self, values, period: int):
        if len(values) < period:
            return None
        k = 2.0 / (period + 1)
        ema = sum(values[:period]) / period
        for v in values[period:]:
            ema = v * k + ema * (1 - k)
        return ema

    def _atr(self, highs, lows, closes, period: int = 14):
        if len(closes) < period + 1:
            return None
        trs = []
        for i in range(1, len(closes)):
            tr = max(highs[i] - lows[i], abs(highs[i] - closes[i - 1]), abs(lows[i] - closes[i - 1]))
            trs.append(tr)
        return sum(trs[-period:]) / period

    def on_bar(self, ctx: BarContext) -> Optional[Signal]:
        ts = ctx.bar.timestamp
        current_date = (ts.year, ts.month, ts.day)
        hour = ts.hour

        # Hard session close: Close everything at or after 21:00 UTC
        if ctx.has_position():
            if hour >= 21 or hour < 13:
                return ctx.signal(
                    "flat",
                    confidence=0.8,
                    metadata={"reason": "session_close_cutoff", "hour": hour, "price": ctx.bar.close}
                )
            return None

        # Warmup and Cooldown guards
        if ctx.bar_index < self.METADATA["warmup_bars"]:
            return None
        if ctx.bar_index - self.entry_bar_idx < self.cooldown_bars:
            return None

        # One trade per calendar day, triggered strictly during the opening window (13:00 - 15:00 UTC)
        if self.last_traded_date == current_date:
            return None
        if not (13 <= hour <= 15):
            return None

        # Avoid meltdown or extreme crisis conditions where continuation breaks down
        regime = ctx.market.get("regime", "NORMAL")
        if regime in ("CRISIS", "MELTDOWN"):
            return None

        closes = ctx.closes(30)
        highs = ctx.highs(30)
        lows = ctx.lows(30)

        # Lookback: Prior 12 bars (Asian + European pre-market momentum)
        lookback = 12
        price_now = closes[-1]
        price_prev = closes[-1 - lookback]
        return_pct = (price_now - price_prev) / price_prev

        # Trend filter using EMA 24 to align micro trend with macro flow
        ema24 = self._ema(closes, 24)
        atr14 = self._atr(highs, lows, closes, 14)
        if ema24 is None or atr14 is None:
            return None

        # Normalized momentum threshold based on ATR
        min_move_pct = (atr14 * 1.2) / price_now

        # Bullish continuation: Clean positive momentum + above EMA
        if return_pct > min_move_pct and price_now > ema24:
            self.last_traded_date = current_date
            self.entry_bar_idx = ctx.bar_index
            return ctx.signal(
                "long",
                confidence=0.75,
                stop_loss_bps=self.METADATA["declared_sl_bps"],
                take_profit_bps=self.METADATA["declared_tp_bps"],
                horizon_seconds=self.METADATA["declared_hold_seconds"],
                metadata={
                    "reason": "us_open_bullish_continuation",
                    "return_12b": return_pct,
                    "min_move_pct": min_move_pct,
                    "ema24": ema24,
                    "hour": hour
                }
            )

        # Bearish continuation: Clean negative momentum + below EMA
        if return_pct < -min_move_pct and price_now < ema24:
            self.last_traded_date = current_date
            self.entry_bar_idx = ctx.bar_index
            return ctx.signal(
                "short",
                confidence=0.75,
                stop_loss_bps=self.METADATA["declared_sl_bps"],
                take_profit_bps=self.METADATA["declared_tp_bps"],
                horizon_seconds=self.METADATA["declared_hold_seconds"],
                metadata={
                    "reason": "us_open_bearish_continuation",
                    "return_12b": return_pct,
                    "min_move_pct": min_move_pct,
                    "ema24": ema24,
                    "hour": hour
                }
            )

        return None

    def on_position_close(self, ctx: BarContext, position: Dict[str, Any]) -> None:
        self.entry_bar_idx = ctx.bar_index