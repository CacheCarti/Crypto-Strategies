from domains.strategy_contract import Strategy, BarContext, Signal
from typing import Optional, Dict, Any
import math

class SolMondaySeasonality(Strategy):
    METADATA = {
        "name": "SolMondaySeasonality",
        "domain": "sol_usdc",
        "declared_sl_bps": 400.0,
        "declared_tp_bps": 750.0,
        "declared_hold_seconds": 86400,
        "warmup_bars": 40,
    }

    def initialize(self, ctx: BarContext) -> None:
        self.cooldown_bars = 12
        self.last_exit_bar = -100
        self.entry_bar_idx = -1
        self.entry_atr = 0.0

    def _atr(self, highs, lows, closes, period=14):
        if len(closes) < period + 1:
            return None
        trs = []
        for i in range(1, len(closes)):
            tr = max(highs[i] - lows[i], abs(highs[i] - closes[i - 1]), abs(lows[i] - closes[i - 1]))
            trs.append(tr)
        return sum(trs[-period:]) / period

    def _ema(self, values, period):
        if len(values) < period:
            return None
        k = 2.0 / (period + 1)
        ema = sum(values[:period]) / period
        for v in values[period:]:
            ema = v * k + ema * (1 - k)
        return ema

    def on_position_close(self, ctx: BarContext, position: Dict[str, Any]) -> None:
        self.last_exit_bar = ctx.bar_index
        self.entry_bar_idx = -1
        self.entry_atr = 0.0

    def on_bar(self, ctx: BarContext) -> Optional[Signal]:
        closes = ctx.closes(40)
        highs = ctx.highs(40)
        lows = ctx.lows(40)

        if len(closes) < self.METADATA["warmup_bars"]:
            return None

        current_time = ctx.bar.timestamp
        weekday = current_time.weekday()
        hour = current_time.hour
        bar_idx = ctx.bar_index

        atr_val = self._atr(highs, lows, closes, 14)
        if atr_val is None or atr_val <= 0:
            return None

        ema_24 = self._ema(closes, 24)
        has_pos = ctx.has_position()

        if has_pos:
            entry_price = ctx.position.get("entry_price", ctx.bar.close) if ctx.position else ctx.bar.close
            
            # Structural/volatility time-based trailing logic
            # Exit 1: Timed exit at end of Monday US session (Monday >= 21:00 UTC or Tuesday)
            if weekday == 0 and hour >= 21:
                return ctx.signal("flat", confidence=0.7, metadata={
                    "reason": "monday_session_end_exit",
                    "weekday": weekday,
                    "hour": hour,
                    "close": ctx.bar.close
                })
            elif weekday == 1:
                return ctx.signal("flat", confidence=0.8, metadata={
                    "reason": "tuesday_hard_cutoff",
                    "weekday": weekday,
                    "hour": hour,
                    "close": ctx.bar.close
                })

            # Exit 2: Dynamic ATR-based structure stop protection
            vol_stop_distance = 2.5 * (self.entry_atr if self.entry_atr > 0 else atr_val)
            if ctx.bar.close < (entry_price - vol_stop_distance):
                return ctx.signal("flat", confidence=0.9, metadata={
                    "reason": "dynamic_vol_stop_triggered",
                    "entry_price": entry_price,
                    "close": ctx.bar.close,
                    "vol_stop_distance": vol_stop_distance
                })

            return None

        # Entry logic: strictly flat
        if bar_idx - self.last_exit_bar < self.cooldown_bars:
            return None

        # Window: Late Sunday UTC (18:00 - 23:00) into Early Monday (00:00 - 04:00)
        is_sunday_window = (weekday == 6 and hour >= 18)
        is_monday_early = (weekday == 0 and hour <= 4)
        
        if not (is_sunday_window or is_monday_early):
            return None

        # Market regime safeguard: avoid crisis meltdowns
        crisis_score = ctx.market.get("crisis_score", 0.0)
        if crisis_score > 0.65:
            return None

        # Price confirmation filter: Price holding above short-term momentum baseline
        # Sol must not be in complete freefall relative to its recent 24-bar baseline
        if ema_24 is not None and ctx.bar.close < (ema_24 * 0.96):
            return None

        # Calculate dynamic stop and take profit in bps via ATR
        atr_bps = (atr_val / ctx.bar.close) * 10000.0
        stop_bps = max(250.0, min(500.0, atr_bps * 2.2))
        tp_bps = max(400.0, min(900.0, stop_bps * 1.8))

        self.entry_bar_idx = bar_idx
        self.entry_atr = atr_val

        confidence = 0.75
        if ema_24 is not None and ctx.bar.close >= ema_24:
            confidence = 0.85

        return ctx.signal(
            "long",
            confidence=confidence,
            stop_loss_bps=stop_bps,
            take_profit_bps=tp_bps,
            horizon_seconds=self.METADATA["declared_hold_seconds"],
            metadata={
                "reason": "sunday_monday_seasonality_long",
                "weekday": weekday,
                "hour": hour,
                "close": ctx.bar.close,
                "atr": atr_val,
                "ema_24": ema_24,
                "crisis_score": crisis_score
            }
        )