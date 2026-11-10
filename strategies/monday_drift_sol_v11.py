from domains.strategy_contract import Strategy, BarContext, Signal
from typing import Optional, Dict, Any
import math

class SolMondaySeasonality(Strategy):
    METADATA = {
        "name": "SolMondaySeasonality",
        "domain": "sol_usdc",
        "declared_sl_bps": 350.0,
        "declared_tp_bps": 550.0,
        "declared_hold_seconds": 64800,  # ~18 hours
        "warmup_bars": 35,
    }

    def initialize(self, ctx: BarContext) -> None:
        self.period_ema = 24
        self.period_rsi = 14
        self.cooldown_bars = 8
        self.last_exit_bar = -999
        self.entry_bar = -999

    def _ema(self, values, period: int):
        if len(values) < period:
            return None
        k = 2.0 / (period + 1)
        ema = sum(values[:period]) / period
        for v in values[period:]:
            ema = v * k + ema * (1.0 - k)
        return ema

    def _rsi(self, closes, period: int = 14):
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

    def on_bar(self, ctx: BarContext) -> Optional[Signal]:
        closes = ctx.closes(self.METADATA["warmup_bars"])
        if len(closes) < self.METADATA["warmup_bars"]:
            return None

        current_price = ctx.bar.close
        timestamp = ctx.bar.timestamp
        weekday = timestamp.weekday()
        hour = timestamp.hour

        ema24 = self._ema(closes, self.period_ema)
        rsi14 = self._rsi(closes, self.period_rsi)
        if ema24 is None or rsi14 is None:
            return None

        # Manage open position
        if ctx.has_position():
            bars_held = ctx.bar_index - self.entry_bar
            
            # Scheduled exit: Monday close (22:00-23:00 UTC) or Tuesday morning
            is_scheduled_exit = (weekday == 0 and hour >= 22) or (weekday == 1 and hour >= 2)
            
            # Momentum breakdown: Price drops substantially below trend or RSI rolls over
            momentum_exit = (rsi14 < 40.0 and current_price < ema24 * 0.985) or (bars_held >= 22)

            if is_scheduled_exit or momentum_exit:
                reason = "scheduled_monday_close" if is_scheduled_exit else "momentum_breakdown_or_max_bars"
                return ctx.signal(
                    "flat",
                    confidence=0.6,
                    metadata={
                        "reason": reason,
                        "bars_held": bars_held,
                        "rsi14": round(rsi14, 2),
                        "price": current_price,
                        "ema24": round(ema24, 2),
                        "weekday": weekday,
                        "hour": hour
                    }
                )
            return None

        # Check cooldown post-exit
        if ctx.bar_index - self.last_exit_bar < self.cooldown_bars:
            return None

        # Entry window: Sunday late UTC (18:00 - 23:00) or early Monday dip (00:00 - 05:00)
        is_sunday_entry = (weekday == 6 and hour >= 18)
        is_monday_entry = (weekday == 0 and hour <= 5)
        
        if not (is_sunday_entry or is_monday_entry):
            return None

        # Market regime gating
        regime = ctx.market.get("regime", "NORMAL")
        crisis_score = ctx.market.get("crisis_score", 0.0)
        if regime in ("CRISIS", "MELTDOWN") or crisis_score > 0.4:
            return None

        # Technical confirmation: avoid buying deeply extended tops or completely broken structures
        # RSI must show room to run, and price shouldn't be collapsing far below short-term EMA
        if 38.0 <= rsi14 <= 66.0 and current_price >= ema24 * 0.98:
            confidence = 0.65
            if current_price > ema24:
                confidence += 0.1
            if rsi14 < 50.0:
                confidence += 0.05

            self.entry_bar = ctx.bar_index
            return ctx.signal(
                "long",
                confidence=min(confidence, 0.9),
                stop_loss_bps=self.METADATA["declared_sl_bps"],
                take_profit_bps=self.METADATA["declared_tp_bps"],
                horizon_seconds=self.METADATA["declared_hold_seconds"],
                metadata={
                    "reason": "sunday_monday_seasonality_long",
                    "rsi14": round(rsi14, 2),
                    "ema24": round(ema24, 2),
                    "price": current_price,
                    "weekday": weekday,
                    "hour": hour,
                    "regime": regime
                }
            )

        return None