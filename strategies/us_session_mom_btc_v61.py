from domains.strategy_contract import Strategy, BarContext, Signal
from typing import Optional, Dict, Any
import math

class UsSessionMomentumBtc(Strategy):
    METADATA = {
        "name": "US Session Momentum BTC",
        "domain": "btc_usdc",
        "declared_sl_bps": 220.0,
        "declared_tp_bps": 420.0,
        "declared_hold_seconds": 21600,
        "warmup_bars": 35,
    }

    def initialize(self, ctx: BarContext) -> None:
        self.last_traded_date = None
        self.cooldown_until_bar = 0
        self.lookback_bars = 12
        self.threshold_bps = 50.0

    def _atr(self, highs, lows, closes, period=14):
        if len(closes) < period + 1:
            return None
        trs = []
        for i in range(1, len(closes)):
            tr = max(highs[i] - lows[i], abs(highs[i] - closes[i - 1]), abs(lows[i] - closes[i - 1]))
            trs.append(tr)
        return sum(trs[-period:]) / period

    def on_position_close(self, ctx: BarContext, position: Dict[str, Any]) -> None:
        self.cooldown_until_bar = ctx.bar_index + 3

    def on_bar(self, ctx: BarContext) -> Optional[Signal]:
        closes = ctx.closes(self.lookback_bars + 20)
        highs = ctx.highs(25)
        lows = ctx.lows(25)

        if len(closes) < self.METADATA["warmup_bars"]:
            return None

        current_time = ctx.bar.timestamp
        current_hour = current_time.hour
        current_date = current_time.date()

        has_pos = ctx.has_position()

        # Session close rule: close all open positions at or after 21:00 UTC
        if has_pos and current_hour >= 21:
            return ctx.signal(
                "flat",
                confidence=0.75,
                metadata={
                    "reason": "us_session_end_exit",
                    "hour": current_hour,
                    "close": ctx.bar.close
                }
            )

        if ctx.bar_index < self.cooldown_until_bar:
            return None

        # Session entry window: 13:00 to 16:00 UTC (early US session)
        if not has_pos:
            if 13 <= current_hour <= 16:
                if self.last_traded_date == current_date:
                    return None

                past_close = closes[-self.lookback_bars - 1]
                curr_close = closes[-1]
                return_bps = ((curr_close - past_close) / past_close) * 10000.0

                atr = self._atr(highs, lows, closes, period=14)
                if atr is None:
                    return None

                # Minimum volatility check to ensure genuine momentum
                atr_bps = (atr / curr_close) * 10000.0
                if atr_bps < 30.0:
                    return None

                # Dynamic SL/TP based on market volatility
                sl_bps = max(150.0, min(350.0, atr_bps * 1.5))
                tp_bps = max(280.0, min(550.0, atr_bps * 2.8))

                if return_bps > self.threshold_bps:
                    self.last_traded_date = current_date
                    confidence = min(1.0, 0.55 + (return_bps / 500.0) * 0.35)
                    return ctx.signal(
                        "long",
                        confidence=confidence,
                        stop_loss_bps=sl_bps,
                        take_profit_bps=tp_bps,
                        horizon_seconds=21600,
                        metadata={
                            "reason": "us_session_continuation_long",
                            "return_12b_bps": round(return_bps, 2),
                            "atr_bps": round(atr_bps, 2),
                            "hour": current_hour
                        }
                    )
                elif return_bps < -self.threshold_bps:
                    self.last_traded_date = current_date
                    confidence = min(1.0, 0.55 + (abs(return_bps) / 500.0) * 0.35)
                    return ctx.signal(
                        "short",
                        confidence=confidence,
                        stop_loss_bps=sl_bps,
                        take_profit_bps=tp_bps,
                        horizon_seconds=21600,
                        metadata={
                            "reason": "us_session_continuation_short",
                            "return_12b_bps": round(return_bps, 2),
                            "atr_bps": round(atr_bps, 2),
                            "hour": current_hour
                        }
                    )

        return None