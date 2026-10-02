from domains.strategy_contract import Strategy, BarContext, Signal
from typing import Optional, Dict, Any
import math

class AsiaDriftUsContinuation(Strategy):
    METADATA = {
        "name": "AsiaDriftUsContinuation",
        "domain": "btc_usdc",
        "declared_sl_bps": 220.0,
        "declared_tp_bps": 440.0,
        "declared_hold_seconds": 28800,
        "warmup_bars": 35,
    }

    def initialize(self, ctx: BarContext) -> None:
        self.warmup_bars = 35
        self.min_asia_drift_pct = 0.0025  # +25 bps minimum drift during Asia
        self.rsi_period = 14
        self.ema_fast_period = 12
        self.ema_slow_period = 26
        self.us_entry_hour = 13          # 13:00 UTC (US pre-market / session open)
        self.session_exit_hour = 22      # 22:00 UTC (close prior to next Asia open)
        
        # State tracking
        self.current_day = None
        self.asia_open_price = None
        self.asia_close_price = None
        self.traded_today = False
        self.bars_since_exit = 10

    def _ema(self, values: list, period: int) -> Optional[float]:
        if len(values) < period:
            return None
        k = 2.0 / (period + 1)
        ema = sum(values[:period]) / period
        for v in values[period:]:
            ema = v * k + ema * (1.0 - k)
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
        self.bars_since_exit = 0

    def on_bar(self, ctx: BarContext) -> Optional[Signal]:
        closes = ctx.closes(self.warmup_bars)
        if len(closes) < self.warmup_bars:
            return None

        self.bars_since_exit += 1
        ts = ctx.bar.timestamp
        current_hour = ts.hour
        today_date = ts.date() if hasattr(ts, "date") else ts.day

        # Reset daily tracking on new UTC day
        if self.current_day != today_date:
            self.current_day = today_date
            self.asia_open_price = ctx.bar.open
            self.asia_close_price = None
            self.traded_today = False

        # Mark Asia session close at 08:00 UTC
        if current_hour == 8 and self.asia_close_price is None:
            self.asia_close_price = ctx.bar.close

        # Time-based exit before next Asia session
        if ctx.has_position():
            if current_hour >= self.session_exit_hour or current_hour < 2:
                current_price = ctx.bar.close
                return ctx.signal(
                    "flat",
                    confidence=0.75,
                    metadata={
                        "reason": "session_close_pre_asia",
                        "hour": current_hour,
                        "price": current_price,
                        "bars_held": ctx.bar_index,
                    }
                )
            return None

        # Guard: Only one entry attempt per calendar day and respect post-exit cooldown
        if self.traded_today or self.bars_since_exit < 3:
            return None

        # Entry window check (US Open at 13:00 or 14:00 UTC)
        if current_hour == self.us_entry_hour:
            # Fallback if 08:00 bar was missed or open just initialized
            if self.asia_open_price is None:
                self.asia_open_price = closes[-14] if len(closes) >= 14 else ctx.bar.open
            
            asia_end = self.asia_close_price if self.asia_close_price is not None else closes[-5]
            asia_drift = (asia_end - self.asia_open_price) / self.asia_open_price

            # Technical confirmation filters
            rsi = self._rsi(closes, self.rsi_period)
            ema_fast = self._ema(closes, self.ema_fast_period)
            ema_slow = self._ema(closes, self.ema_slow_period)

            if rsi is None or ema_fast is None or ema_slow is None:
                return None

            # Condition: Positive Asia drift with upward momentum and healthy RSI
            trend_aligned = ema_fast >= ema_slow and ctx.bar.close > ema_fast
            rsi_valid = 45.0 <= rsi <= 72.0

            if asia_drift >= self.min_asia_drift_pct and trend_aligned and rsi_valid:
                self.traded_today = True
                
                # Confidence scaled by drift strength and RSI stance
                drift_score = min(max(asia_drift / 0.015, 0.5), 0.95)
                confidence = round(drift_score * (0.8 + 0.2 * (rsi / 70.0)), 2)
                confidence = min(max(confidence, 0.55), 0.95)

                return ctx.signal(
                    "long",
                    confidence=confidence,
                    stop_loss_bps=self.METADATA["declared_sl_bps"],
                    take_profit_bps=self.METADATA["declared_tp_bps"],
                    horizon_seconds=self.METADATA["declared_hold_seconds"],
                    metadata={
                        "reason": "asia_positive_drift_us_continuation",
                        "asia_drift_bps": round(asia_drift * 10000.0, 1),
                        "rsi": round(rsi, 2),
                        "ema_fast": round(ema_fast, 2),
                        "ema_slow": round(ema_slow, 2),
                        "price": ctx.bar.close,
                        "entry_hour": current_hour,
                    }
                )

        return None