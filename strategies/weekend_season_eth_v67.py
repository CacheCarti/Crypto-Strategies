from domains.strategy_contract import Strategy, BarContext, Signal
from typing import Optional, Dict, Any
import math

class EthWeekendSeasonality(Strategy):
    METADATA = {
        "name": "EthWeekendSeasonality",
        "domain": "eth_usdc",
        "declared_sl_bps": 550.0,
        "declared_tp_bps": 750.0,
        "declared_hold_seconds": 172800,  # ~48 hours hold
        "warmup_bars": 50,
        "required_features": ["fear_greed_index"],
    }

    def initialize(self, ctx: BarContext) -> None:
        self.last_exit_bar = -999
        self.cooldown_bars = 18
        self.entry_price = 0.0
        self.entry_bar = 0

    def _atr(self, highs, lows, closes, period=14):
        if len(closes) < period + 1:
            return None
        trs = []
        for i in range(1, len(closes)):
            tr = max(highs[i] - lows[i], abs(highs[i] - closes[i - 1]), abs(lows[i] - closes[i - 1]))
            trs.append(tr)
        return sum(trs[-period:]) / period

    def _ema(self, values, period):
        if len(values) < period:
            return None
        k = 2.0 / (period + 1)
        ema = sum(values[:period]) / period
        for v in values[period:]:
            ema = v * k + ema * (1 - k)
        return ema

    def on_position_close(self, ctx: BarContext, position: Dict[str, Any]) -> None:
        self.last_exit_bar = ctx.bar_index
        self.entry_price = 0.0

    def on_bar(self, ctx: BarContext) -> Optional[Signal]:
        closes = ctx.closes(50)
        highs = ctx.highs(50)
        lows = ctx.lows(50)

        if len(closes) < 50:
            return None

        current_time = ctx.bar.timestamp
        weekday = current_time.weekday()  # Monday=0, Friday=4, Saturday=5, Sunday=6
        hour = current_time.hour
        price = ctx.bar.close

        atr = self._atr(highs, lows, closes, 14)
        ema34 = self._ema(closes, 34)

        if atr is None or ema34 is None:
            return None

        # Position Management
        if ctx.has_position():
            bars_held = ctx.bar_index - self.entry_bar
            
            # Structural timed exit: Late Sunday (UTC 20:00-23:00) or early Monday
            is_sunday_exit = (weekday == 6 and hour >= 20)
            is_monday_exit = (weekday == 0 and hour >= 2)
            
            # Dynamic volatility trailing stop via ATR structure
            entry_px = self.entry_price if self.entry_price > 0 else price
            atr_sl_dist = 2.6 * atr
            dynamic_sl_price = entry_px - atr_sl_dist

            if is_sunday_exit or is_monday_exit:
                return ctx.signal(
                    "flat",
                    confidence=0.8,
                    metadata={
                        "reason": "weekend_seasonality_timed_exit",
                        "weekday": weekday,
                        "hour": hour,
                        "bars_held": bars_held,
                        "price": price,
                    }
                )

            if price < dynamic_sl_price:
                return ctx.signal(
                    "flat",
                    confidence=0.9,
                    metadata={
                        "reason": "dynamic_atr_structural_stop",
                        "price": price,
                        "sl_price": dynamic_sl_price,
                        "bars_held": bars_held,
                    }
                )

            return None

        # Cooldown guard after trade
        if (ctx.bar_index - self.last_exit_bar) < self.cooldown_bars:
            return None

        # Entry Window: Friday evening UTC (18:00 to 23:00)
        is_friday_window = (weekday == 4 and 18 <= hour <= 23)
        if not is_friday_window:
            return None

        # Macro/Regime Gating: Avoid opening into extreme market crisis
        crisis_score = ctx.market.get("crisis_score", 0.0)
        if crisis_score > 0.70:
            return None

        # Avoid entering during severe downtrend crashes (momentum/trend sanity filter)
        if price < (ema34 * 0.95):
            return None

        fg_index = ctx.features.get("fear_greed_index", 50)
        # Volatility-adjusted stop sizing
        sl_bps = max(350.0, min(650.0, (atr / price) * 20000.0))
        tp_bps = max(550.0, min(900.0, sl_bps * 1.5))

        self.entry_price = price
        self.entry_bar = ctx.bar_index

        return ctx.signal(
            "long",
            confidence=0.75,
            stop_loss_bps=sl_bps,
            take_profit_bps=tp_bps,
            metadata={
                "reason": "weekend_inflow_seasonal_drift",
                "weekday": weekday,
                "hour": hour,
                "price": price,
                "ema34": ema34,
                "atr": atr,
                "fear_greed": fg_index,
            }
        )