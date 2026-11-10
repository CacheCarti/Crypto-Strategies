from domains.strategy_contract import Strategy, BarContext, Signal
from typing import Optional, Dict, Any
import math

class SolMondaySeasonality(Strategy):
    METADATA = {
        "name": "SolMondaySeasonality",
        "domain": "sol_usdc",
        "declared_sl_bps": 350.0,
        "declared_tp_bps": 550.0,
        "declared_hold_seconds": 86400,
        "warmup_bars": 50,
        "required_features": ["fear_greed_index"],
    }

    def initialize(self, ctx: BarContext) -> None:
        self.last_exit_bar = -100
        self.last_traded_week = -1
        self.entry_price = 0.0

    def _ema(self, values, period: int) -> Optional[float]:
        if len(values) < period:
            return None
        k = 2.0 / (period + 1)
        ema = sum(values[:period]) / period
        for v in values[period:]:
            ema = v * k + ema * (1.0 - k)
        return ema

    def _rsi(self, closes, period: int = 14) -> Optional[float]:
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
        self.entry_price = 0.0

    def on_bar(self, ctx: BarContext) -> Optional[Signal]:
        closes = ctx.closes(55)
        if len(closes) < 50:
            return None

        current_price = ctx.bar.close
        ts = ctx.bar.timestamp
        weekday = ts.weekday()
        hour = ts.hour
        is_year, is_week, _ = ts.isocalendar()
        current_week_key = is_year * 100 + is_week

        ema24 = self._ema(closes, 24)
        ema48 = self._ema(closes, 48)
        rsi14 = self._rsi(closes, 14)

        if ema24 is None or ema48 is None or rsi14 is None:
            return None

        if ctx.has_position():
            if self.entry_price == 0.0:
                pos = ctx.position
                if pos and "entry_price" in pos and pos["entry_price"]:
                    self.entry_price = float(pos["entry_price"])
                else:
                    self.entry_price = current_price

            loss_bps = (self.entry_price - current_price) / self.entry_price * 10000.0 if self.entry_price > 0 else 0.0
            if loss_bps >= 300.0:
                return ctx.signal("flat", confidence=0.8, metadata={"reason": "early_cut_loss", "loss_bps": loss_bps, "price": current_price})

            if (weekday == 1 and hour >= 21) or weekday > 1 or weekday == 6:
                return ctx.signal("flat", confidence=0.7, metadata={"reason": "monday_window_close", "weekday": weekday, "hour": hour, "price": current_price})
            return None

        if ctx.bar_index - self.last_exit_bar < 6:
            return None

        if self.last_traded_week == current_week_key:
            return None

        is_sunday_late = (weekday == 6 and hour >= 18)
        is_monday_early = (weekday == 0 and hour <= 8)

        if is_sunday_late or is_monday_early:
            regime = ctx.market.get("regime", "NORMAL")
            if regime in ["CRISIS", "MELTDOWN"]:
                return None

            fear_greed = ctx.features.get("fear_greed_index", 50)
            if fear_greed < 15:
                return None

            trend_aligned = current_price >= ema48 * 0.985
            rsi_valid = 32.0 <= rsi14 <= 66.0

            if trend_aligned and rsi_valid:
                conf = 0.65
                if current_price > ema24:
                    conf += 0.1
                if fear_greed >= 45:
                    conf += 0.05
                conf = min(0.9, conf)

                self.last_traded_week = current_week_key
                self.entry_price = current_price

                return ctx.signal(
                    "long",
                    confidence=conf,
                    stop_loss_bps=self.METADATA["declared_sl_bps"],
                    take_profit_bps=self.METADATA["declared_tp_bps"],
                    horizon_seconds=self.METADATA["declared_hold_seconds"],
                    metadata={
                        "reason": "sunday_late_monday_drift_entry",
                        "weekday": weekday,
                        "hour": hour,
                        "rsi": rsi14,
                        "ema24": ema24,
                        "ema48": ema48,
                        "fear_greed": fear_greed,
                        "price": current_price,
                    },
                )

        return None