from domains.strategy_contract import Strategy, BarContext, Signal
from typing import Optional, Dict, Any
import math

class SolMondaySeasonality(Strategy):
    METADATA = {
        "name": "SolMondaySeasonality",
        "domain": "sol_usdc",
        "declared_sl_bps": 350.0,
        "declared_tp_bps": 550.0,
        "declared_hold_seconds": 72000,
        "warmup_bars": 35,
    }

    def initialize(self, ctx: BarContext) -> None:
        self.warmup_bars = 35
        self.cooldown_bars = 6
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
        closes = ctx.closes(self.warmup_bars)
        if len(closes) < self.warmup_bars:
            return None

        bar_time = ctx.bar.timestamp
        weekday = bar_time.weekday()
        hour = bar_time.hour
        price = ctx.bar.close

        regime = ctx.market.get("regime", "NORMAL")
        crisis_score = ctx.market.get("crisis_score", 0.0)

        # Manage existing open position
        if ctx.has_position():
            # Scheduled time exit: Exit late Monday UTC (hours 20 to 23) or anytime Tuesday+
            is_monday_late = (weekday == 0 and hour >= 20)
            is_post_monday = (weekday >= 1 and weekday < 6)
            is_weekend = (weekday == 5)
            
            # Risk exit if crisis score spikes
            crisis_exit = (regime in ("CRISIS", "MELTDOWN") or crisis_score > 0.65)
            
            if is_monday_late or is_post_monday or is_weekend or crisis_exit:
                reason = "crisis_exit" if crisis_exit else "monday_close_seasonality_target"
                return ctx.signal(
                    "flat",
                    confidence=0.75,
                    metadata={
                        "reason": reason,
                        "weekday": weekday,
                        "hour": hour,
                        "price": price,
                        "crisis_score": crisis_score,
                    }
                )
            return None

        # Mandatory cooldown check after exit
        if (ctx.bar_index - self.last_exit_bar) < self.cooldown_bars:
            return None

        # Filter out extreme crisis regimes
        if regime in ("CRISIS", "MELTDOWN") or crisis_score > 0.60:
            return None

        # Entry Window: Late Sunday UTC (18:00 - 23:00) or Early Monday UTC (00:00 - 05:00)
        is_entry_window = (weekday == 6 and hour >= 18) or (weekday == 0 and hour <= 5)
        if not is_entry_window:
            return None

        # Technical indicators for trend qualification and avoiding exhausted moves
        ema20 = self._ema(closes, 20)
        rsi14 = self._rsi(closes, 14)

        if ema20 is None or rsi14 is None:
            return None

        # Avoid chasing if already excessively overbought
        if rsi14 > 68.0:
            return None

        # Momentum qualification: require price to be stabilizing near or above short-term trend
        # Allowing a 0.5% tolerance below EMA20 to capture early bottoming turns
        if price < (ema20 * 0.995):
            return None

        # Calculate entry confidence based on trend alignment and RSI sweet spot
        confidence = 0.60
        if price > ema20:
            confidence += 0.10
        if 40.0 <= rsi14 <= 60.0:
            confidence += 0.10

        self.entry_bar = ctx.bar_index
        return ctx.signal(
            "long",
            confidence=min(0.85, confidence),
            stop_loss_bps=self.METADATA["declared_sl_bps"],
            take_profit_bps=self.METADATA["declared_tp_bps"],
            horizon_seconds=self.METADATA["declared_hold_seconds"],
            metadata={
                "reason": "sunday_monday_seasonality_expansion",
                "weekday": weekday,
                "hour": hour,
                "price": price,
                "rsi14": round(rsi14, 2),
                "ema20": round(ema20, 2),
                "regime": regime,
            }
        )