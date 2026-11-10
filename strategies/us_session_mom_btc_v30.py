from domains.strategy_contract import Strategy, BarContext, Signal
from typing import Optional, Dict, Any
import math

class USSessionMomentumBTC(Strategy):
    METADATA = {
        "name": "USSessionMomentumBTC",
        "domain": "btc_usdc",
        "declared_sl_bps": 220.0,
        "declared_tp_bps": 380.0,
        "declared_hold_seconds": 21600,  # Max ~6 hours intraday hold
        "warmup_bars": 30,
        "required_features": ["fear_greed_index"],
    }

    def initialize(self, ctx: BarContext) -> None:
        self.entry_window_start = 13  # US open window (UTC 13)
        self.session_end_hour = 21    # Hard session close
        self.lookback = 12            # Pre-session momentum lookback
        self.min_mom_bps = 55.0       # Threshold: pre-session move must exceed 0.55%
        self.last_traded_date = None
        self.cooldown_bars = 4
        self.last_exit_bar = -999

    def _atr(self, highs, lows, closes, period=14):
        if len(closes) < period + 1:
            return 0.0
        trs = []
        for i in range(1, len(closes)):
            tr = max(highs[i] - lows[i], abs(highs[i] - closes[i-1]), abs(lows[i] - closes[i-1]))
            trs.append(tr)
        return sum(trs[-period:]) / period

    def on_position_close(self, ctx: BarContext, position: Dict[str, Any]) -> None:
        self.last_exit_bar = ctx.bar_index

    def on_bar(self, ctx: BarContext) -> Optional[Signal]:
        closes = ctx.closes(self.lookback + 2)
        if len(closes) < self.lookback + 1:
            return None

        current_time = ctx.bar.timestamp
        current_date = current_time.date()
        current_hour = current_time.hour

        # 1. Mandatory Session Exit at/after UTC 21
        if ctx.has_position():
            if current_hour >= self.session_end_hour or current_hour < self.entry_window_start:
                return ctx.signal("flat", confidence=0.8, metadata={
                    "reason": "us_session_end_close",
                    "hour": current_hour,
                    "price": ctx.bar.close
                })
            return None

        # 2. Strict Entry Boundary: Enter exactly once per day at UTC 13
        if current_hour != self.entry_window_start:
            return None

        if self.last_traded_date == current_date:
            return None

        if (ctx.bar_index - self.last_exit_bar) < self.cooldown_bars:
            return None

        # Crisis / Meltdown Filter
        crisis_score = ctx.market.get("crisis_score", 0.0)
        if crisis_score > 0.70:
            return None

        # Fear & Greed sentiment gating
        fg_index = ctx.features.get("fear_greed_index", 50)

        # 3. Pre-session 12-hour momentum measurement
        p_current = closes[-1]
        p_start = closes[-self.lookback - 1]
        mom_bps = ((p_current - p_start) / p_start) * 10000.0

        # Breakout verification against 12-bar range
        highs = ctx.highs(self.lookback + 1)
        lows = ctx.lows(self.lookback + 1)
        pre_high = max(highs[:-1])
        pre_low = min(lows[:-1])

        # Directional entry with strict boundary check
        if mom_bps > self.min_mom_bps and p_current >= pre_high and fg_index > 25:
            self.last_traded_date = current_date
            confidence = min(0.9, max(0.5, 0.5 + abs(mom_bps) / 400.0))
            return ctx.signal(
                "long",
                confidence=confidence,
                stop_loss_bps=self.METADATA["declared_sl_bps"],
                take_profit_bps=self.METADATA["declared_tp_bps"],
                metadata={
                    "reason": "us_session_bull_continuation",
                    "pre_session_mom_bps": mom_bps,
                    "p_current": p_current,
                    "pre_high": pre_high,
                    "fg_index": fg_index
                }
            )

        if mom_bps < -self.min_mom_bps and p_current <= pre_low and fg_index < 75:
            self.last_traded_date = current_date
            confidence = min(0.9, max(0.5, 0.5 + abs(mom_bps) / 400.0))
            return ctx.signal(
                "short",
                confidence=confidence,
                stop_loss_bps=self.METADATA["declared_sl_bps"],
                take_profit_bps=self.METADATA["declared_tp_bps"],
                metadata={
                    "reason": "us_session_bear_continuation",
                    "pre_session_mom_bps": mom_bps,
                    "p_current": p_current,
                    "pre_low": pre_low,
                    "fg_index": fg_index
                }
            )

        return None