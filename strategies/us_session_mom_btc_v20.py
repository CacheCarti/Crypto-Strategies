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
        "required_features": ["fear_greed_index"],
    }

    def initialize(self, ctx: BarContext) -> None:
        self.entry_hour = 14       # Strict discrete event window: UTC 14:00 bar
        self.exit_hour = 21        # Hard session close at or after UTC 21:00
        self.lookback = 12         # 12-hour pre-session momentum window
        self.last_traded_day = -1  # Strictly enforces 1 trade per calendar day
        self.min_return_pct = 0.85 # High conviction drift threshold: ~85 bps move over 12h

    def _atr(self, highs, lows, closes, period=14):
        if len(closes) < period + 1:
            return None
        trs = []
        for i in range(1, len(closes)):
            tr = max(highs[i] - lows[i], abs(highs[i] - closes[i - 1]), abs(lows[i] - closes[i - 1]))
            trs.append(tr)
        return sum(trs[-period:]) / period

    def on_bar(self, ctx: BarContext) -> Optional[Signal]:
        closes = ctx.closes(self.lookback + 20)
        highs = ctx.highs(20)
        lows = ctx.lows(20)

        if len(closes) < self.lookback + 14:
            return None

        current_time = ctx.bar.timestamp
        current_hour = current_time.hour
        current_day = current_time.toordinal()

        # Hard Session Exit Logic: Flush any open position at/after exit_hour
        if ctx.has_position():
            if current_hour >= self.exit_hour or current_hour < self.entry_hour:
                return ctx.signal(
                    "flat",
                    confidence=0.8,
                    metadata={
                        "reason": "us_session_close",
                        "hour": current_hour,
                        "close": ctx.bar.close,
                    }
                )
            return None

        # Discrete Single-Bar Trigger: Exactly at entry_hour, max 1 trade per calendar day
        if current_hour != self.entry_hour or self.last_traded_day == current_day:
            return None

        # Filter out extreme crisis regimes where intraday session drift fails
        market_regime = ctx.market.get("regime", "NORMAL")
        if market_regime in ("CRISIS", "MELTDOWN"):
            return None

        # Calculate exact 12-hour drift
        past_close = closes[-self.lookback - 1]
        current_close = closes[-1]
        drift_pct = ((current_close - past_close) / past_close) * 100.0

        # Dynamic volatility confirmation: ensure move is not just sub-noise consolidation
        atr = self._atr(highs, lows, closes, period=14)
        if atr is None or atr == 0:
            return None
        atr_pct = (atr / current_close) * 100.0

        # Require drift to exceed 1x baseline hourly ATR and minimum threshold
        dynamic_threshold = max(self.min_return_pct, atr_pct)

        # Fear & Greed macro sentiment sanity check
        fgi = ctx.features.get("fear_greed_index", 50)

        if drift_pct >= dynamic_threshold and fgi > 25:
            self.last_traded_day = current_day
            confidence = min(0.9, 0.5 + (abs(drift_pct) / 4.0))
            return ctx.signal(
                "long",
                confidence=confidence,
                stop_loss_bps=self.METADATA["declared_sl_bps"],
                take_profit_bps=self.METADATA["declared_tp_bps"],
                horizon_seconds=self.METADATA["declared_hold_seconds"],
                metadata={
                    "reason": "us_session_bull_continuation",
                    "drift_pct": round(drift_pct, 3),
                    "threshold": round(dynamic_threshold, 3),
                    "fgi": fgi,
                    "hour": current_hour,
                }
            )

        if drift_pct <= -dynamic_threshold and fgi < 75:
            self.last_traded_day = current_day
            confidence = min(0.9, 0.5 + (abs(drift_pct) / 4.0))
            return ctx.signal(
                "short",
                confidence=confidence,
                stop_loss_bps=self.METADATA["declared_sl_bps"],
                take_profit_bps=self.METADATA["declared_tp_bps"],
                horizon_seconds=self.METADATA["declared_hold_seconds"],
                metadata={
                    "reason": "us_session_bear_continuation",
                    "drift_pct": round(drift_pct, 3),
                    "threshold": round(dynamic_threshold, 3),
                    "fgi": fgi,
                    "hour": current_hour,
                }
            )

        return None

    def on_position_close(self, ctx: BarContext, position: Dict[str, Any]) -> None:
        pass