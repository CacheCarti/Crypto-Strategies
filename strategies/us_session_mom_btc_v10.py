from domains.strategy_contract import Strategy, BarContext, Signal
from typing import Optional, Dict, Any
import math

class UsSessionMomentumBtc(Strategy):
    METADATA = {
        "name": "UsSessionMomentumBtc",
        "domain": "btc_usdc",
        "declared_sl_bps": 220.0,
        "declared_tp_bps": 400.0,
        "declared_hold_seconds": 21600,  # ~6 hours maximum session hold
        "warmup_bars": 30,
        "required_features": ["btc_return_pct"],
    }

    def initialize(self, ctx: BarContext) -> None:
        self.entry_hour = 14  # Strict trigger hour (14:00 UTC, after NY open stabilizes)
        self.exit_hour = 21   # Force session exit by 21:00 UTC
        self.lookback_bars = 12
        self.min_move_pct = 0.6  # Require at least 0.6% directional move pre-session
        self.last_traded_day = -1
        self.cooldown_until = 0

    def _atr(self, highs, lows, closes, period=14):
        if len(closes) < period + 1:
            return None
        trs = []
        for i in range(1, len(closes)):
            tr = max(highs[i] - lows[i], abs(highs[i] - closes[i - 1]), abs(lows[i] - closes[i - 1]))
            trs.append(tr)
        return sum(trs[-period:]) / period

    def on_bar(self, ctx: BarContext) -> Optional[Signal]:
        closes = ctx.closes(self.lookback_bars + 15)
        if len(closes) < self.lookback_bars + 15:
            return None

        current_time = ctx.bar.timestamp
        current_day = current_time.toordinal()
        current_hour = current_time.hour

        # 1. Mandatory Session Exit rule: close any open position by 21:00 UTC
        if ctx.has_position():
            if current_hour >= self.exit_hour or current_hour < 13:
                return ctx.signal(
                    "flat",
                    confidence=0.8,
                    metadata={
                        "reason": "session_close_boundary",
                        "hour": current_hour,
                        "close": ctx.bar.close,
                    }
                )
            return None

        # 2. Strict execution window: only evaluate at exact entry hour (14:00 UTC)
        if current_hour != self.entry_hour:
            return None

        # Cooldown & Single trade per calendar day guard
        if current_day == self.last_traded_day or ctx.bar_index < self.cooldown_until:
            return None

        # Crisis / extreme volatility safety filter
        crisis_score = ctx.market.get("crisis_score", 0.0)
        regime = ctx.market.get("regime", "NORMAL")
        if crisis_score > 0.7 or regime in ("CRISIS", "MELTDOWN"):
            return None

        # 3. Event boundary calculation: evaluate cumulative momentum over previous 12 hours
        prev_close = closes[-(self.lookback_bars + 1)]
        curr_close = closes[-1]
        pct_change = ((curr_close - prev_close) / prev_close) * 100.0

        # High/Low range check to ensure meaningful momentum, not just flat drift
        highs = ctx.highs(self.lookback_bars + 15)
        lows = ctx.lows(self.lookback_bars + 15)
        atr = self._atr(highs, lows, closes, period=14)
        if atr is None or atr <= 0:
            return None

        atr_pct = (atr / curr_close) * 100.0
        # Require momentum to be significant relative to prevailing hourly ATR
        threshold = max(self.min_move_pct, atr_pct * 0.75)

        # Cross-asset confirmation filter
        btc_24h_ret = ctx.features.get("btc_return_pct", 0.0)

        # Long continuation condition
        if pct_change >= threshold and btc_24h_ret > -2.0:
            self.last_traded_day = current_day
            self.cooldown_until = ctx.bar_index + 6
            confidence = min(0.9, 0.55 + (abs(pct_change) / 10.0))
            return ctx.signal(
                "long",
                confidence=confidence,
                stop_loss_bps=self.METADATA["declared_sl_bps"],
                take_profit_bps=self.METADATA["declared_tp_bps"],
                horizon_seconds=self.METADATA["declared_hold_seconds"],
                metadata={
                    "reason": "us_open_bullish_continuation",
                    "pre_session_pct": round(pct_change, 3),
                    "threshold": round(threshold, 3),
                    "atr": round(atr, 2),
                    "hour": current_hour,
                }
            )

        # Short continuation condition
        if pct_change <= -threshold and btc_24h_ret < 2.0:
            self.last_traded_day = current_day
            self.cooldown_until = ctx.bar_index + 6
            confidence = min(0.9, 0.55 + (abs(pct_change) / 10.0))
            return ctx.signal(
                "short",
                confidence=confidence,
                stop_loss_bps=self.METADATA["declared_sl_bps"],
                take_profit_bps=self.METADATA["declared_tp_bps"],
                horizon_seconds=self.METADATA["declared_hold_seconds"],
                metadata={
                    "reason": "us_open_bearish_continuation",
                    "pre_session_pct": round(pct_change, 3),
                    "threshold": round(threshold, 3),
                    "atr": round(atr, 2),
                    "hour": current_hour,
                }
            )

        return None

    def on_position_close(self, ctx: BarContext, position: Dict[str, Any]) -> None:
        # Enforce minimum cooldown of 3 bars after position close to avoid immediate re-entry
        self.cooldown_until = max(self.cooldown_until, ctx.bar_index + 3)