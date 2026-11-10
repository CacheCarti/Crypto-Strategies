from domains.strategy_contract import Strategy, BarContext, Signal
from typing import Optional, Dict, Any
import math

class SolMondaySeasonality(Strategy):
    METADATA = {
        "name": "SolMondaySeasonality",
        "domain": "sol_usdc",
        "declared_sl_bps": 320.0,
        "declared_tp_bps": 520.0,
        "declared_hold_seconds": 72000,
        "warmup_bars": 50,
        "required_features": ["fear_greed_index"],
    }

    def initialize(self, ctx: BarContext) -> None:
        self.entry_bar = -1
        self.last_exit_bar = -1
        self.cooldown_bars = 12

    def _sma(self, values, period):
        if len(values) < period:
            return None
        return sum(values[-period:]) / period

    def _rsi(self, closes, period=14):
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
        self.last_exit_bar = ctx.bar_index
        self.entry_bar = -1

    def on_bar(self, ctx: BarContext) -> Optional[Signal]:
        closes = ctx.closes(55)
        if len(closes) < 50:
            return None

        current_price = ctx.bar.close
        ts = ctx.bar.timestamp
        weekday = ts.weekday()
        hour = ts.hour

        # Exit logic if in position
        if ctx.has_position():
            bars_held = ctx.bar_index - self.entry_bar if self.entry_bar > 0 else 0
            
            # Scheduled window close: Monday 21:00 UTC through Tuesday 00:00 UTC
            time_exit = (weekday == 0 and hour >= 21) or (weekday == 1 and hour < 2)
            
            # Momentum breakdown exit
            rsi = self._rsi(closes, 14)
            rsi_exit = rsi is not None and rsi < 35.0 and bars_held >= 4

            if time_exit or rsi_exit:
                exit_reason = "seasonality_window_closed" if time_exit else "momentum_breakdown"
                return ctx.signal(
                    "flat",
                    confidence=0.7,
                    metadata={
                        "reason": exit_reason,
                        "weekday": weekday,
                        "hour": hour,
                        "bars_held": bars_held,
                        "rsi": rsi,
                    }
                )
            return None

        # Cooldown guard after recent exit
        if ctx.bar_index - self.last_exit_bar < self.cooldown_bars:
            return None

        # Entry Window: Sunday late UTC (18:00 to 23:00)
        is_sunday_entry = (weekday == 6 and hour >= 18)
        if not is_sunday_entry:
            return None

        # Structural trend filter: Avoid opening counter to severe medium-term selloffs
        sma40 = self._sma(closes, 40)
        if sma40 is None or current_price < sma40 * 0.95:
            return None

        # Regime & Macro Sentiment filter
        market_regime = ctx.market.get("regime", "NORMAL")
        crisis_score = ctx.market.get("crisis_score", 0.0)
        if market_regime in ("CRISIS", "MELTDOWN") or crisis_score > 0.65:
            return None

        fg_index = ctx.features.get("fear_greed_index", 50)
        if fg_index < 18:
            return None

        # Price-based momentum condition: Ensure baseline buying interest (not in freefall)
        rsi = self._rsi(closes, 14)
        if rsi is None or rsi < 38.0 or rsi > 70.0:
            return None

        # Passed all gates: Fire Long entry
        self.entry_bar = ctx.bar_index
        confidence = 0.75 if (fg_index >= 40 and current_price >= sma40) else 0.60

        return ctx.signal(
            "long",
            confidence=confidence,
            stop_loss_bps=320.0,
            take_profit_bps=520.0,
            horizon_seconds=72000,
            metadata={
                "reason": "sunday_into_monday_drift_recovery",
                "weekday": weekday,
                "hour": hour,
                "rsi": rsi,
                "sma40": sma40,
                "fear_greed": fg_index,
                "market_regime": market_regime,
            }
        )