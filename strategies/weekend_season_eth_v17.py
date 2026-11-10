from domains.strategy_contract import Strategy, BarContext, Signal
from typing import Optional, Dict, Any
import math

class WeekendSeasonalityETH(Strategy):
    METADATA = {
        "name": "WeekendSeasonalityETH",
        "domain": "eth_usdc",
        "declared_sl_bps": 550.0,
        "declared_tp_bps": 750.0,
        "declared_hold_seconds": 172800,  # ~48 hours hold
        "warmup_bars": 50,
        "required_features": ["fear_greed_index"],
    }

    def initialize(self, ctx: BarContext) -> None:
        self.warmup = 50
        self.atr_period = 14
        self.ema_period = 40
        self.last_trade_week = -1
        self.entry_price = 0.0
        self.bars_in_position = 0

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
            ema = v * k + ema * (1.0 - k)
        return ema

    def on_position_close(self, ctx: BarContext, position: Dict[str, Any]) -> None:
        self.entry_price = 0.0
        self.bars_in_position = 0

    def on_bar(self, ctx: BarContext) -> Optional[Signal]:
        closes = ctx.closes(self.warmup)
        if len(closes) < self.warmup:
            return None

        highs = ctx.highs(self.warmup)
        lows = ctx.lows(self.warmup)

        ts = ctx.bar.timestamp
        weekday = ts.weekday()  # Monday is 0, Friday is 4, Saturday is 5, Sunday is 6
        hour = ts.hour
        iso_year, iso_week, _ = ts.isocalendar()

        has_pos = ctx.has_position()
        curr_price = ctx.bar.close

        # Manage open position
        if has_pos:
            self.bars_in_position += 1

            # Timed Exit: Sunday evening (20:00 to 23:00 UTC)
            if weekday == 6 and hour >= 20:
                return ctx.signal(
                    "flat",
                    confidence=0.8,
                    metadata={
                        "reason": "weekend_target_window_close",
                        "bars_held": self.bars_in_position,
                        "close": curr_price,
                        "weekday": weekday,
                        "hour": hour
                    }
                )

            # Max duration emergency guard (e.g. held past Monday 04:00 UTC)
            if weekday == 0 and hour >= 4:
                return ctx.signal(
                    "flat",
                    confidence=0.8,
                    metadata={
                        "reason": "monday_hard_cutoff",
                        "bars_held": self.bars_in_position,
                        "close": curr_price
                    }
                )

            return None

        # Entry Window: Friday 18:00 UTC to 23:00 UTC
        if weekday == 4 and 18 <= hour <= 23:
            # Prevent re-entering in the same calendar week
            if self.last_trade_week == (iso_year, iso_week):
                return None

            # Regime filter: Skip extreme crisis
            market_regime = ctx.market.get("regime", "NORMAL")
            if market_regime in ("CRISIS", "MELTDOWN"):
                return None

            # Sentiment guard: Avoid severe market fear spirals
            fg_index = ctx.features.get("fear_greed_index", 50)
            if fg_index < 18:
                return None

            # Technical trend structure: ETH above/near medium-term EMA to ensure baseline health
            ema40 = self._ema(closes, self.ema_period)
            if ema40 is None or curr_price < ema40 * 0.985:
                return None

            # Dynamic Volatility Stop calculation via ATR
            atr = self._atr(highs, lows, closes, self.atr_period)
            if atr is None or curr_price <= 0:
                sl_bps = 550.0
            else:
                # 3x ATR structural cushion, bounded within reasonable swing risk bounds
                atr_pct = (atr * 3.0) / curr_price
                sl_bps = max(350.0, min(800.0, atr_pct * 10000.0))

            tp_bps = sl_bps * 1.5

            self.last_trade_week = (iso_year, iso_week)
            self.entry_price = curr_price
            self.bars_in_position = 0

            return ctx.signal(
                "long",
                confidence=0.75,
                stop_loss_bps=sl_bps,
                take_profit_bps=tp_bps,
                horizon_seconds=172800,
                metadata={
                    "reason": "weekend_seasonality_friday_entry",
                    "price": curr_price,
                    "ema40": ema40,
                    "atr": atr,
                    "fear_greed": fg_index,
                    "calculated_sl_bps": sl_bps,
                    "weekday": weekday,
                    "hour": hour
                }
            )

        return None