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
        self.last_exit_bar = -100
        self.entry_bar = -1
        self.cooldown_bars = 18

    def _sma(self, values, period: int):
        if len(values) < period:
            return None
        return sum(values[-period:]) / period

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
        self.entry_bar = -1

    def on_bar(self, ctx: BarContext) -> Optional[Signal]:
        closes = ctx.closes(50)
        if len(closes) < 50:
            return None

        current_price = ctx.bar.close
        ts = ctx.bar.timestamp
        weekday = ts.weekday()
        hour = ts.hour

        if ctx.has_position():
            entry_price = ctx.position.get("entry_price", current_price) if ctx.position else current_price
            bars_held = ctx.bar_index - self.entry_bar if self.entry_bar > 0 else 1

            if entry_price > 0:
                pnl_bps = ((current_price - entry_price) / entry_price) * 10000.0
                if pnl_bps <= -self.METADATA["declared_sl_bps"]:
                    self.last_exit_bar = ctx.bar_index
                    self.entry_bar = -1
                    return ctx.signal("flat", confidence=0.8, metadata={
                        "reason": "stop_loss_hit",
                        "pnl_bps": round(pnl_bps, 1),
                        "bars_held": bars_held,
                        "price": current_price
                    })

            is_exit_window = (weekday == 0 and hour >= 21) or (weekday == 1) or (bars_held >= 26)
            if is_exit_window:
                self.last_exit_bar = ctx.bar_index
                self.entry_bar = -1
                return ctx.signal("flat", confidence=0.7, metadata={
                    "reason": "seasonal_target_window_closed",
                    "weekday": weekday,
                    "hour": hour,
                    "bars_held": bars_held,
                    "price": current_price
                })
            return None

        if (ctx.bar_index - self.last_exit_bar) < self.cooldown_bars:
            return None

        is_entry_window = (weekday == 6 and hour >= 18) or (weekday == 0 and hour <= 2)
        if not is_entry_window:
            return None

        regime = ctx.market.get("regime", "NORMAL")
        if regime in ("CRISIS", "MELTDOWN"):
            return None

        fear_greed = ctx.features.get("fear_greed_index", 50.0)
        if fear_greed < 20.0:
            return None

        trend_regime = ctx.market.get("trend_regime", "neutral")
        if trend_regime == "bear":
            return None

        sma20 = self._sma(closes, 20)
        sma50 = self._sma(closes, 50)
        rsi14 = self._rsi(closes, 14)

        if sma20 is None or sma50 is None or rsi14 is None:
            return None

        if current_price < sma50 * 0.94:
            return None
        if rsi14 > 68.0:
            return None

        base_conf = 0.65
        if current_price > sma20:
            base_conf += 0.10
        if fear_greed >= 45.0:
            base_conf += 0.05
        confidence = min(max(base_conf, 0.5), 0.85)

        self.entry_bar = ctx.bar_index
        return ctx.signal(
            "long",
            confidence=confidence,
            stop_loss_bps=self.METADATA["declared_sl_bps"],
            take_profit_bps=self.METADATA["declared_tp_bps"],
            horizon_seconds=self.METADATA["declared_hold_seconds"],
            metadata={
                "reason": "monday_drift_sol_recovery",
                "weekday": weekday,
                "hour": hour,
                "rsi14": round(rsi14, 2),
                "sma20": round(sma20, 2),
                "sma50": round(sma50, 2),
                "fear_greed": round(fear_greed, 1),
                "regime": regime,
                "trend_regime": trend_regime,
                "price": current_price
            }
        )