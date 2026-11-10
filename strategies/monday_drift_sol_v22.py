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
        "warmup_bars": 40,
    }

    def initialize(self, ctx: BarContext) -> None:
        self.last_exit_bar = -100
        self.bars_in_trade = 0

    def _ema(self, values, period):
        if len(values) < period:
            return None
        k = 2.0 / (period + 1)
        ema = sum(values[:period]) / period
        for v in values[period:]:
            ema = v * k + ema * (1 - k)
        return ema

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
        self.bars_in_trade = 0

    def on_bar(self, ctx: BarContext) -> Optional[Signal]:
        closes = ctx.closes(40)
        if len(closes) < 40:
            return None

        dt = ctx.bar.timestamp
        weekday = dt.weekday()
        hour = dt.hour

        ema24 = self._ema(closes, 24)
        rsi14 = self._rsi(closes, 14)
        if ema24 is None or rsi14 is None:
            return None

        current_price = ctx.bar.close

        if ctx.has_position():
            self.bars_in_trade += 1

            # Exit seasonal hold: Monday late US hours (>= 21:00 UTC) or anytime Tuesday
            if weekday == 1 or (weekday == 0 and hour >= 21) or self.bars_in_trade >= 24:
                return ctx.signal(
                    "flat",
                    confidence=0.65,
                    metadata={
                        "reason": "seasonal_target_window_completed",
                        "weekday": weekday,
                        "hour": hour,
                        "bars_in_trade": self.bars_in_trade,
                        "price": current_price
                    }
                )

            # Exit if structural support breaks decisively
            if current_price < ema24 * 0.965:
                return ctx.signal(
                    "flat",
                    confidence=0.8,
                    metadata={
                        "reason": "trend_invalidation_cut",
                        "current_price": current_price,
                        "ema24": round(ema24, 2)
                    }
                )

            return None

        # Mandatory cooldown between weekly cycles
        if ctx.bar_index - self.last_exit_bar < 20:
            return None

        # Regime gate from market context to avoid buying freefalling/crisis regimes
        crisis_score = ctx.market.get("crisis_score", 0.0)
        market_regime = ctx.market.get("regime", "NORMAL")
        if crisis_score > 0.45 or market_regime in ("CRISIS", "MELTDOWN"):
            return None

        # Seasonality window: Sunday late UTC (18:00 - 23:00 UTC)
        is_sunday_entry_window = (weekday == 6 and hour >= 18)

        # Tactical entry conditions: SOL must not be severely crashing and RSI should show healthy momentum/reversion
        price_support_ok = current_price >= ema24 * 0.985
        rsi_favorable = 38.0 <= rsi14 <= 68.0

        if is_sunday_entry_window and price_support_ok and rsi_favorable:
            conf = 0.65
            if current_price > ema24:
                conf += 0.15

            return ctx.signal(
                "long",
                confidence=min(conf, 0.95),
                stop_loss_bps=self.METADATA["declared_sl_bps"],
                take_profit_bps=self.METADATA["declared_tp_bps"],
                horizon_seconds=self.METADATA["declared_hold_seconds"],
                metadata={
                    "reason": "sunday_late_drift_long",
                    "weekday": weekday,
                    "hour": hour,
                    "rsi14": round(rsi14, 2),
                    "ema24": round(ema24, 2),
                    "price": current_price,
                    "crisis_score": crisis_score
                }
            )

        return None