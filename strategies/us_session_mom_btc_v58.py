from domains.strategy_contract import Strategy, BarContext, Signal
from typing import Optional, Dict, Any
import math

class UsSessionMomentum(Strategy):
    METADATA = {
        "name": "US Session Momentum Event",
        "domain": "btc_usdc",
        "declared_sl_bps": 220.0,
        "declared_tp_bps": 400.0,
        "declared_hold_seconds": 21600,  # Max ~6 hours intraday hold
        "warmup_bars": 35,
    }

    def initialize(self, ctx: BarContext) -> None:
        self.lookback = 12
        self.last_traded_day = -1
        self.min_ret_threshold = 0.0075  # 75 bps minimum 12h displacement to confirm conviction
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
        closes = ctx.closes(self.lookback + 2)
        if len(closes) < self.lookback + 1:
            return None

        bar_time = ctx.bar.timestamp
        current_hour = bar_time.hour
        current_day = bar_time.toordinal()

        # Hard session close: liquidate any remaining inventory at or after UTC 21:00
        if ctx.has_position():
            if current_hour >= 21 or current_hour < 13:
                return ctx.signal(
                    "flat",
                    confidence=1.0,
                    metadata={"reason": "session_close_cutoff", "hour": current_hour, "price": ctx.bar.close}
                )
            return None

        # Flat state checks
        # Cooldown guard after recent exit
        if ctx.bar_index - self.last_exit_bar < self.cooldown_bars:
            return None

        # Regime filter: avoid taking trend-continuation bets in chaotic meltdown regimes
        regime = ctx.market.get("regime", "NORMAL")
        if regime in ("CRISIS", "MELTDOWN"):
            return None

        # Entry window: US session open window between 13:00 and 15:00 UTC
        # Strict rule: exactly 1 entry attempt per calendar day
        if 13 <= current_hour <= 15 and self.last_traded_day != current_day:
            pre_session_start = closes[-self.lookback]
            current_price = closes[-1]
            displacement = (current_price - pre_session_start) / pre_session_start

            # Calculate 14-period normalized ATR to scale conviction
            highs = ctx.highs(16)
            lows = ctx.lows(16)
            recent_closes = ctx.closes(16)
            atr = self._atr(highs, lows, recent_closes, period=14) if len(recent_closes) >= 15 else None
            norm_atr = (atr / current_price) if (atr and current_price > 0) else 0.01

            # High momentum long continuation: strong upward drift into US session
            if displacement > max(self.min_ret_threshold, norm_atr * 0.8):
                self.last_traded_day = current_day
                confidence = min(0.9, 0.55 + abs(displacement) * 10)
                return ctx.signal(
                    "long",
                    confidence=confidence,
                    stop_loss_bps=self.METADATA["declared_sl_bps"],
                    take_profit_bps=self.METADATA["declared_tp_bps"],
                    horizon_seconds=self.METADATA["declared_hold_seconds"],
                    metadata={
                        "reason": "us_session_open_long_momentum",
                        "displacement_pct": displacement * 100.0,
                        "hour": current_hour,
                        "norm_atr": norm_atr,
                        "regime": regime
                    }
                )

            # High momentum short continuation: heavy downward drift into US session
            elif displacement < -max(self.min_ret_threshold, norm_atr * 0.8):
                self.last_traded_day = current_day
                confidence = min(0.9, 0.55 + abs(displacement) * 10)
                return ctx.signal(
                    "short",
                    confidence=confidence,
                    stop_loss_bps=self.METADATA["declared_sl_bps"],
                    take_profit_bps=self.METADATA["declared_tp_bps"],
                    horizon_seconds=self.METADATA["declared_hold_seconds"],
                    metadata={
                        "reason": "us_session_open_short_momentum",
                        "displacement_pct": displacement * 100.0,
                        "hour": current_hour,
                        "norm_atr": norm_atr,
                        "regime": regime
                    }
                )

        return None