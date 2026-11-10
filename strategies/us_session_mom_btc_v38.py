from domains.strategy_contract import Strategy, BarContext, Signal
from typing import Optional, Dict, Any
import math

class UsSessionMomentumBtc(Strategy):
    METADATA = {
        "name": "UsSessionMomentumBtc",
        "domain": "btc_usdc",
        "declared_sl_bps": 220.0,
        "declared_tp_bps": 440.0,
        "declared_hold_seconds": 21600,  # ~6 hours maximum session hold
        "warmup_bars": 30,
        "required_features": ["fear_greed_index"],
    }

    def initialize(self, ctx: BarContext) -> None:
        self.last_traded_day = -1
        self.cooldown_until_bar = 0
        self.entry_bar = 0

    def _atr(self, highs, lows, closes, period=14):
        if len(closes) < period + 1:
            return None
        trs = []
        for i in range(1, len(closes)):
            tr = max(highs[i] - lows[i], abs(highs[i] - closes[i-1]), abs(lows[i] - closes[i-1]))
            trs.append(tr)
        return sum(trs[-period:]) / period

    def on_position_close(self, ctx: BarContext, position: Dict[str, Any]) -> None:
        # Enforce a mandatory rest period after closing any trade
        self.cooldown_until_bar = ctx.bar_index + 6

    def on_bar(self, ctx: BarContext) -> Optional[Signal]:
        closes = ctx.closes(25)
        highs = ctx.highs(25)
        lows = ctx.lows(25)

        if len(closes) < 20:
            return None

        current_time = ctx.bar.timestamp
        current_hour = current_time.hour
        current_day = current_time.toordinal()

        # Exit condition: session cut-off at or after UTC 21:00
        if ctx.has_position():
            if current_hour >= 21 or (ctx.bar_index - self.entry_bar) >= 7:
                return ctx.signal(
                    "flat",
                    confidence=0.8,
                    metadata={
                        "reason": "session_close_cutoff",
                        "hour": current_hour,
                        "bars_held": ctx.bar_index - self.entry_bar
                    }
                )
            return None

        # Guard against trading multiple times a day or during cooldown
        if ctx.bar_index < self.cooldown_until_bar:
            return None
        if current_day == self.last_traded_day:
            return None

        # Entry window: strictly trigger at US session open (UTC 13 or 14)
        if current_hour not in (13, 14):
            return None

        # Regime & Crisis Filter
        regime = ctx.market.get("regime", "NORMAL")
        crisis_score = ctx.market.get("crisis_score", 0.0)
        if regime in ("CRISIS", "MELTDOWN") or crisis_score > 0.6:
            return None

        # Macro Sentiment Filter
        fg_index = ctx.features.get("fear_greed_index", 50)

        # 12-bar pre-session price displacement
        p_now = closes[-1]
        p_12_ago = closes[-13]
        displacement_bps = ((p_now - p_12_ago) / p_12_ago) * 10000.0

        # Volatility normalization via ATR(14)
        atr = self._atr(highs, lows, closes, 14)
        if atr is None or atr <= 0:
            return None
        atr_bps = (atr / p_now) * 10000.0

        # Require meaningful pre-session displacement relative to recent volatility
        # Threshold: pre-session momentum must exceed 0.8 * ATR_bps
        min_move_bps = max(60.0, 0.8 * atr_bps)

        # Dynamic Stop & Take Profit
        sl_bps = max(150.0, min(300.0, 1.2 * atr_bps))
        tp_bps = sl_bps * 2.0

        # Long Entry: Clear upward displacement + avoid extreme greed exhaustion
        if displacement_bps > min_move_bps and fg_index <= 80:
            self.last_traded_day = current_day
            self.entry_bar = ctx.bar_index
            confidence = min(1.0, 0.5 + abs(displacement_bps) / 400.0)
            return ctx.signal(
                "long",
                confidence=confidence,
                stop_loss_bps=sl_bps,
                take_profit_bps=tp_bps,
                horizon_seconds=21600,
                metadata={
                    "reason": "us_session_open_long_continuation",
                    "displacement_bps": round(displacement_bps, 2),
                    "atr_bps": round(atr_bps, 2),
                    "fear_greed": fg_index,
                    "hour": current_hour
                }
            )

        # Short Entry: Clear downward displacement + avoid extreme panic capitulation
        elif displacement_bps < -min_move_bps and fg_index >= 20:
            self.last_traded_day = current_day
            self.entry_bar = ctx.bar_index
            confidence = min(1.0, 0.5 + abs(displacement_bps) / 400.0)
            return ctx.signal(
                "short",
                confidence=confidence,
                stop_loss_bps=sl_bps,
                take_profit_bps=tp_bps,
                horizon_seconds=21600,
                metadata={
                    "reason": "us_session_open_short_continuation",
                    "displacement_bps": round(displacement_bps, 2),
                    "atr_bps": round(atr_bps, 2),
                    "fear_greed": fg_index,
                    "hour": current_hour
                }
            )

        return None