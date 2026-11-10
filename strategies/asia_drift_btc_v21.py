from domains.strategy_contract import Strategy, BarContext, Signal
from typing import Optional, Dict, Any
import math

class AsiaDriftMomentum(Strategy):
    METADATA = {
        "name": "AsiaDriftMomentum",
        "domain": "btc_usdc",
        "declared_sl_bps": 220.0,
        "declared_tp_bps": 380.0,
        "declared_hold_seconds": 21600,  # ~6 hours hold
        "warmup_bars": 35,
        "required_features": ["fear_greed_index"],
    }

    def initialize(self, ctx: BarContext) -> None:
        self.last_traded_day = -1
        self.cooldown_until_bar = 0
        self.asia_open_price = None
        self.current_day = -1
        self.entry_bar = -1

    def _ema(self, values, period):
        if len(values) < period:
            return None
        k = 2.0 / (period + 1)
        ema = sum(values[:period]) / period
        for v in values[period:]:
            ema = v * k + ema * (1.0 - k)
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
        # Mandatory post-exit cooldown of 4 bars (4 hours)
        self.cooldown_until_bar = ctx.bar_index + 4

    def on_bar(self, ctx: BarContext) -> Optional[Signal]:
        closes = ctx.closes(35)
        highs = ctx.highs(35)
        lows = ctx.lows(35)

        if len(closes) < 35:
            return None

        bar_time = ctx.bar.timestamp
        current_day = bar_time.toordinal()
        current_hour = bar_time.hour
        price = ctx.bar.close

        # Track daily rollover
        if current_day != self.current_day:
            self.current_day = current_day
            self.asia_open_price = None

        # Capture Asia session open reference at 00:00 UTC
        if current_hour == 0 and self.asia_open_price is None:
            self.asia_open_price = ctx.bar.open

        # Position management
        if ctx.has_position():
            bars_held = ctx.bar_index - self.entry_bar
            # Target holding 5-7 hours, exit before late session/Asia reopen (by 21:00 UTC) or after 6 bars
            if current_hour >= 21 or bars_held >= 6:
                return ctx.signal(
                    "flat",
                    confidence=0.6,
                    metadata={
                        "reason": "session_close_pre_asia",
                        "current_hour": current_hour,
                        "bars_held": bars_held,
                        "price": price,
                    }
                )
            return None

        # Guard against trading during active cooldown
        if ctx.bar_index < self.cooldown_until_bar:
            return None

        # Ensure maximum one trade attempt per UTC day
        if self.last_traded_day == current_day:
            return None

        # Filter crisis or meltdown regimes
        market_regime = ctx.market.get("regime", "NORMAL")
        if market_regime in ("CRISIS", "MELTDOWN"):
            return None

        # Evaluate entry at the US pre-market/cash open window (13:00 to 15:00 UTC)
        if 13 <= current_hour <= 15:
            if self.asia_open_price is None or self.asia_open_price <= 0:
                return None

            asia_drift_bps = ((price - self.asia_open_price) / self.asia_open_price) * 10000.0

            # Require positive drift of at least 30 bps, capped at 300 bps to prevent chasing blow-offs
            if not (30.0 <= asia_drift_bps <= 300.0):
                return None

            # Trend alignment via EMA 30
            ema30 = self._ema(closes, 30)
            if ema30 is None or price <= ema30:
                return None

            # Volatility check: current bar range should not be completely exhausted
            atr = self._atr(highs, lows, closes, 14)
            if atr is None or atr <= 0:
                return None

            # Sentiment safety: skip if extreme fear (< 25)
            fear_greed = ctx.features.get("fear_greed_index", 50)
            if fear_greed < 25:
                return None

            self.last_traded_day = current_day
            self.entry_bar = ctx.bar_index

            confidence = 0.65
            if asia_drift_bps > 75.0 and fear_greed >= 50:
                confidence = 0.85

            return ctx.signal(
                "long",
                confidence=confidence,
                stop_loss_bps=self.METADATA["declared_sl_bps"],
                take_profit_bps=self.METADATA["declared_tp_bps"],
                horizon_seconds=self.METADATA["declared_hold_seconds"],
                metadata={
                    "reason": "asia_drift_continuation_us_open",
                    "asia_drift_bps": round(asia_drift_bps, 2),
                    "price": price,
                    "ema30": round(ema30, 2),
                    "atr": round(atr, 2),
                    "fear_greed": fear_greed,
                    "hour": current_hour,
                }
            )

        return None