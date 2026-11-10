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
        "warmup_bars": 50,
    }

    def initialize(self, ctx: BarContext) -> None:
        self.last_trade_bar = -100
        self.cooldown_bars = 24
        self.traded_this_cycle = False

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
        self.last_trade_bar = ctx.bar_index

    def on_bar(self, ctx: BarContext) -> Optional[Signal]:
        closes = ctx.closes(50)
        if len(closes) < self.METADATA["warmup_bars"]:
            return None

        dt = ctx.bar.timestamp
        weekday = dt.weekday()
        hour = dt.hour

        sma40 = self._sma(closes, 40)
        rsi14 = self._rsi(closes, 14)
        if sma40 is None or rsi14 is None:
            return None

        current_price = ctx.bar.close

        # Reset seasonal cycle flag every Friday
        if weekday == 4:
            self.traded_this_cycle = False

        # Manage open position exit logic
        if ctx.has_position():
            # Mandatory exit: Monday after US cash market close (>= 22:00 UTC) or anytime Tuesday/later
            is_exit_time = (weekday == 0 and hour >= 22) or (weekday == 1)
            # Dynamic momentum exhaustion exit
            is_exhaustion = rsi14 > 75.0

            if is_exit_time or is_exhaustion:
                reason = "seasonality_window_closed" if is_exit_time else "rsi_exhaustion_exit"
                return ctx.signal(
                    "flat",
                    confidence=0.6,
                    metadata={"reason": reason, "rsi": rsi14, "weekday": weekday, "hour": hour}
                )
            return None

        # Check cooldown
        if ctx.bar_index - self.last_trade_bar < self.cooldown_bars:
            return None

        # Regime & Crisis safety checks
        regime = ctx.market.get("regime", "NORMAL")
        crisis_score = ctx.market.get("crisis_score", 0.0)
        if regime in ("CRISIS", "MELTDOWN") or crisis_score > 0.65:
            return None

        # Seasonal Entry Window: Sunday late UTC (18:00 to 23:00)
        is_sunday_late = (weekday == 6 and hour >= 18)

        if is_sunday_late and not self.traded_this_cycle:
            # Entry condition: Not severely overbought and price shows stability or mild pullback
            # Adaptive state logic avoids fixed single-price thresholds
            price_relative_to_sma = (current_price - sma40) / sma40
            if rsi14 < 65.0 and price_relative_to_sma > -0.05:
                self.traded_this_cycle = True
                self.last_trade_bar = ctx.bar_index
                
                # Modulate confidence based on RSI (value accumulation preferred)
                confidence = 0.75 if rsi14 < 45.0 else 0.65
                
                return ctx.signal(
                    "long",
                    confidence=confidence,
                    stop_loss_bps=self.METADATA["declared_sl_bps"],
                    take_profit_bps=self.METADATA["declared_tp_bps"],
                    horizon_seconds=self.METADATA["declared_hold_seconds"],
                    metadata={
                        "reason": "sunday_late_drift_accumulation",
                        "rsi": rsi14,
                        "sma40": sma40,
                        "price": current_price,
                        "weekday": weekday,
                        "hour": hour
                    }
                )

        return None