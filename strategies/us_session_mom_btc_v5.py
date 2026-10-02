from domains.strategy_contract import Strategy, BarContext, Signal
from typing import Optional, Dict, Any
import math

class UsSessionMomentumContinuation(Strategy):
    METADATA = {
        "name": "US Session Momentum Continuation",
        "domain": "btc_usdc",
        "declared_sl_bps": 220.0,
        "declared_tp_bps": 420.0,
        "declared_hold_seconds": 21600,
        "warmup_bars": 35,
    }

    def initialize(self, ctx: BarContext) -> None:
        self.lookback_bars = 12
        self.trend_ema_period = 24
        self.min_momentum_bps = 70.0
        self.entry_start_hour = 13
        self.entry_end_hour = 15
        self.session_close_hour = 21
        self.last_trade_day = None
        self.last_exit_bar = -999
        self.cooldown_bars = 4

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
        self.last_exit_bar = ctx.bar_index

    def on_bar(self, ctx: BarContext) -> Optional[Signal]:
        closes = ctx.closes(self.lookback_bars + self.trend_ema_period + 2)
        if len(closes) < self.lookback_bars + self.trend_ema_period:
            return None

        current_time = ctx.bar.timestamp
        current_hour = current_time.hour
        current_day = current_time.date() if hasattr(current_time, "date") else current_time.day

        has_pos = ctx.has_position()

        # Session mandatory flat exit
        if has_pos and current_hour >= self.session_close_hour:
            return ctx.signal(
                "flat",
                confidence=0.75,
                metadata={
                    "reason": "us_session_close",
                    "hour": current_hour,
                    "close": ctx.bar.close,
                    "position_dir": ctx.position_direction()
                }
            )

        if has_pos:
            return None

        # Check cooldown and single trade per day limit
        if (ctx.bar_index - self.last_exit_bar) < self.cooldown_bars:
            return None

        if self.last_trade_day == current_day:
            return None

        # Check US session entry window (UTC 13-15)
        if not (self.entry_start_hour <= current_hour <= self.entry_end_hour):
            return None

        # Calculate momentum across lookback bars (12 hours)
        ref_price = closes[-self.lookback_bars]
        current_price = closes[-1]
        momentum_bps = ((current_price - ref_price) / ref_price) * 10000.0

        ema_val = self._ema(closes, self.trend_ema_period)
        if ema_val is None:
            return None

        highs = ctx.highs(20)
        lows = ctx.lows(20)
        atr = self._atr(highs, lows, closes, period=14)
        atr_pct = (atr / current_price) if (atr and current_price > 0) else 0.01

        # Long Setup: Positive momentum exceeding threshold and price supported by trend EMA
        if momentum_bps >= self.min_momentum_bps and current_price > ema_val:
            self.last_trade_day = current_day
            conf = min(0.90, max(0.55, 0.55 + (momentum_bps / 500.0)))
            return ctx.signal(
                "long",
                confidence=conf,
                stop_loss_bps=self.METADATA["declared_sl_bps"],
                take_profit_bps=self.METADATA["declared_tp_bps"],
                horizon_seconds=self.METADATA["declared_hold_seconds"],
                metadata={
                    "reason": "us_session_momentum_continuation_long",
                    "momentum_bps": round(momentum_bps, 2),
                    "ema24": round(ema_val, 2),
                    "price": current_price,
                    "hour": current_hour,
                    "atr_pct": round(atr_pct, 4)
                }
            )

        # Short Setup: Negative momentum exceeding threshold and price below trend EMA
        if momentum_bps <= -self.min_momentum_bps and current_price < ema_val:
            self.last_trade_day = current_day
            conf = min(0.90, max(0.55, 0.55 + (abs(momentum_bps) / 500.0)))
            return ctx.signal(
                "short",
                confidence=conf,
                stop_loss_bps=self.METADATA["declared_sl_bps"],
                take_profit_bps=self.METADATA["declared_tp_bps"],
                horizon_seconds=self.METADATA["declared_hold_seconds"],
                metadata={
                    "reason": "us_session_momentum_continuation_short",
                    "momentum_bps": round(momentum_bps, 2),
                    "ema24": round(ema_val, 2),
                    "price": current_price,
                    "hour": current_hour,
                    "atr_pct": round(atr_pct, 4)
                }
            )

        return None