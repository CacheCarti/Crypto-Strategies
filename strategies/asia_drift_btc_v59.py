from domains.strategy_contract import Strategy, BarContext, Signal
from typing import Optional, Dict, Any
import math

class AsiaDriftContinuation(Strategy):
    METADATA = {
        "name": "Asia Drift Continuation",
        "domain": "btc_usdc",
        "declared_sl_bps": 220.0,
        "declared_tp_bps": 400.0,
        "declared_hold_seconds": 28800,  # ~8 hours (US session hold)
        "warmup_bars": 50,
    }

    def initialize(self, ctx: BarContext) -> None:
        self.warmup_bars = 50
        self.last_traded_day = -1
        self.asia_open_price: Optional[float] = None
        self.asia_close_price: Optional[float] = None
        self.entry_bar = -1
        self.cooldown_bars = 4
        self.last_exit_bar = -10

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

    def on_bar(self, ctx: BarContext) -> Optional[Signal]:
        closes = ctx.closes(self.warmup_bars)
        if len(closes) < self.warmup_bars:
            return None

        current_time = ctx.bar.timestamp
        current_hour = current_time.hour
        current_day = current_time.toordinal()

        # Track session milestones
        if current_hour == 0:
            self.asia_open_price = ctx.bar.open
            self.asia_close_price = None

        if current_hour == 8:
            self.asia_close_price = ctx.bar.close

        # Close position prior to next Asia session (at 23:00 UTC) or end of day
        if ctx.has_position():
            if current_hour >= 23 or current_hour < 8:
                self.last_exit_bar = ctx.bar_index
                return ctx.signal(
                    "flat",
                    confidence=0.6,
                    metadata={
                        "reason": "session_close_pre_asia",
                        "hour": current_hour,
                        "price": ctx.bar.close
                    }
                )
            return None

        # Check cooldown and one-trade-per-day restriction
        if current_day == self.last_traded_day:
            return None
        if ctx.bar_index - self.last_exit_bar < self.cooldown_bars:
            return None

        # Entry window: US session open (13:00 - 15:00 UTC)
        if 13 <= current_hour <= 15:
            if self.asia_open_price is not None and self.asia_close_price is not None:
                asia_return_pct = (self.asia_close_price - self.asia_open_price) / self.asia_open_price

                # Normalized threshold via ATR to avoid static hardcoded return numbers
                highs = ctx.highs(20)
                lows = ctx.lows(20)
                atr = self._atr(highs, lows, closes, period=14)
                if atr is None or atr <= 0:
                    return None

                min_drift_ratio = (atr / ctx.bar.close) * 0.35

                # Trend alignment: Fast EMA > Slow EMA to prevent buying dead-cat bounces
                ema20 = self._ema(closes, 20)
                ema50 = self._ema(closes, 50)
                trend_support = (ema20 is not None and ema50 is not None and ema20 > ema50)

                # Avoid crisis/extreme volatility
                regime = ctx.market.get("regime", "NORMAL")
                if regime in ["CRISIS", "MELTDOWN"]:
                    return None

                # Entry trigger: Positive Asia drift exceeding ATR threshold & trend alignment
                if asia_return_pct > min_drift_ratio and trend_support and ctx.bar.close > ema20:
                    confidence = min(0.85, 0.55 + (asia_return_pct / (min_drift_ratio * 3.0)) * 0.3)
                    self.last_traded_day = current_day
                    self.entry_bar = ctx.bar_index

                    return ctx.signal(
                        "long",
                        confidence=confidence,
                        stop_loss_bps=self.METADATA["declared_sl_bps"],
                        take_profit_bps=self.METADATA["declared_tp_bps"],
                        horizon_seconds=self.METADATA["declared_hold_seconds"],
                        metadata={
                            "reason": "asia_drift_us_continuation",
                            "asia_return_pct": round(asia_return_pct * 100, 3),
                            "min_drift_ratio": round(min_drift_ratio * 100, 3),
                            "ema20": round(ema20, 2),
                            "price": ctx.bar.close,
                            "hour": current_hour
                        }
                    )
        return None

    def on_position_close(self, ctx: BarContext, position: Dict[str, Any]) -> None:
        self.last_exit_bar = ctx.bar_index