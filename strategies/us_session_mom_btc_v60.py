from domains.strategy_contract import Strategy, BarContext, Signal
from typing import Optional, Dict, Any
import math

class UsSessionMomentumBtc(Strategy):
    METADATA = {
        "name": "UsSessionMomentumBtc",
        "domain": "btc_usdc",
        "declared_sl_bps": 220.0,
        "declared_tp_bps": 400.0,
        "declared_hold_seconds": 21600,  # ~6 hours (session duration)
        "warmup_bars": 30,
        "required_features": ["fear_greed_index"],
    }

    def initialize(self, ctx: BarContext) -> None:
        self.last_traded_date = None
        self.entry_threshold_bps = 80.0  # Min 12-bar trend displacement (~0.8%)
        self.cooldown_bars = 4
        self.last_exit_bar = -999

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
        closes = ctx.closes(20)
        highs = ctx.highs(20)
        lows = ctx.lows(20)

        if len(closes) < 15:
            return None

        current_time = ctx.bar.timestamp
        current_date = current_time.date()
        current_hour = current_time.hour

        # 1. Mandatory Session Exit: Close all positions at or after UTC 20:00 before UTC 21:00 close
        if ctx.has_position():
            if current_hour >= 20:
                pos_dir = ctx.position_direction()
                return ctx.signal(
                    "flat",
                    confidence=0.8,
                    metadata={
                        "reason": "session_close_pre_21utc",
                        "hour": current_hour,
                        "bar_index": ctx.bar_index,
                        "closed_direction": pos_dir,
                    }
                )
            return None

        # 2. Strict Event Boundary Entry Check:
        # Only evaluate entry precisely at the US opening window (UTC 13:00 or 14:00)
        if current_hour not in (13, 14):
            return None

        # Enforce max one trade per day
        if self.last_traded_date == current_date:
            return None

        # Mandatory exit cooldown
        if (ctx.bar_index - self.last_exit_bar) < self.cooldown_bars:
            return None

        # Regime safety filter: skip entering during extreme crisis meltdown
        regime = ctx.market.get("regime", "NORMAL")
        if regime in ("CRISIS", "MELTDOWN"):
            return None

        # 3. Measure pre-session 12-bar trend displacement
        # closes[-1] is the bar just closing prior to session entry; closes[-13] is 12 bars back
        pre_close = closes[-13]
        now_close = closes[-1]
        move_bps = ((now_close - pre_close) / pre_close) * 10000.0

        # Dynamic volatility check: ensure move is meaningful relative to ATR
        atr = self._atr(highs, lows, closes, period=14)
        if atr is None or atr <= 0:
            return None
        min_move_required = max(self.entry_threshold_bps, (atr / now_close) * 10000.0 * 0.75)

        # Gating via Fear & Greed to avoid counter-sentiment traps
        fg = ctx.features.get("fear_greed_index", 50.0)

        # Long Trigger: Pre-session bullish momentum above threshold + not extreme greed trap
        if move_bps > min_move_required and fg < 85.0:
            self.last_traded_date = current_date
            confidence = min(0.9, max(0.55, 0.55 + (move_bps / 400.0) * 0.25))
            return ctx.signal(
                "long",
                confidence=confidence,
                stop_loss_bps=self.METADATA["declared_sl_bps"],
                take_profit_bps=self.METADATA["declared_tp_bps"],
                horizon_seconds=self.METADATA["declared_hold_seconds"],
                metadata={
                    "reason": "us_open_bullish_continuation",
                    "move_bps": round(move_bps, 2),
                    "min_move_required": round(min_move_required, 2),
                    "hour": current_hour,
                    "fear_greed": fg,
                    "close": now_close,
                }
            )

        # Short Trigger: Pre-session bearish momentum below threshold + not extreme fear trap
        if move_bps < -min_move_required and fg > 15.0:
            self.last_traded_date = current_date
            confidence = min(0.9, max(0.55, 0.55 + (abs(move_bps) / 400.0) * 0.25))
            return ctx.signal(
                "short",
                confidence=confidence,
                stop_loss_bps=self.METADATA["declared_sl_bps"],
                take_profit_bps=self.METADATA["declared_tp_bps"],
                horizon_seconds=self.METADATA["declared_hold_seconds"],
                metadata={
                    "reason": "us_open_bearish_continuation",
                    "move_bps": round(move_bps, 2),
                    "min_move_required": round(min_move_required, 2),
                    "hour": current_hour,
                    "fear_greed": fg,
                    "close": now_close,
                }
            )

        return None