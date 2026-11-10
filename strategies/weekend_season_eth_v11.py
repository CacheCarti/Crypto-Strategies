from domains.strategy_contract import Strategy, BarContext, Signal
from typing import Optional, Dict, Any
import math

class WeekendSeasonalityEth(Strategy):
    METADATA = {
        "name": "Weekend Seasonality ETH",
        "domain": "eth_usdc",
        "declared_sl_bps": 320.0,
        "declared_tp_bps": 520.0,
        "declared_hold_seconds": 129600,  # ~36 hours max intended hold
        "warmup_bars": 35,
        "required_features": ["fear_greed_index"],
    }

    def initialize(self, ctx: BarContext) -> None:
        self.last_exit_bar = -999
        self.last_entry_bar = -999
        self.cooldown_bars = 16
        self.ema_period = 24
        self.atr_period = 14

    def _ema(self, values, period: int) -> Optional[float]:
        if len(values) < period:
            return None
        k = 2.0 / (period + 1)
        ema = sum(values[:period]) / period
        for v in values[period:]:
            ema = v * k + ema * (1.0 - k)
        return ema

    def _atr(self, highs, lows, closes, period: int = 14) -> Optional[float]:
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
        closes = ctx.closes(self.METADATA["warmup_bars"])
        if len(closes) < self.METADATA["warmup_bars"]:
            return None

        # Cooldown guard after any recent exit
        if (ctx.bar_index - self.last_exit_bar) < self.cooldown_bars:
            return None

        ts = ctx.bar.timestamp
        weekday = ts.weekday()  # Monday=0 ... Friday=4, Saturday=5, Sunday=6
        hour = ts.hour
        current_price = ctx.bar.close

        ema_val = self._ema(closes, self.ema_period)
        if ema_val is None:
            return None

        # Check existing open position for exit rule
        if ctx.has_position():
            entry_price = ctx.position.get("entry_price", current_price) if ctx.position else current_price
            bars_held = ctx.bar_index - self.last_entry_bar

            # Late Sunday drift exit (Sunday >= 20:00 UTC) or Monday morning rebalance
            is_weekend_close = (weekday == 6 and hour >= 20) or (weekday == 0 and hour <= 2)
            # Technical exhaustion exit: sharp drop below EMA after holding > 12h
            trend_broken = bars_held >= 12 and current_price < ema_val * 0.985

            if is_weekend_close or trend_broken:
                ret_bps = ((current_price - entry_price) / entry_price) * 10000.0 if entry_price > 0 else 0.0
                return ctx.signal(
                    "flat",
                    confidence=0.7,
                    metadata={
                        "reason": "weekend_cycle_completed" if is_weekend_close else "trend_invalidation_exit",
                        "weekday": weekday,
                        "hour": hour,
                        "bars_held": bars_held,
                        "return_bps": ret_bps,
                        "current_price": current_price,
                        "ema": ema_val,
                    }
                )
            return None

        # Entry logic: enter weekend drift during Friday window (16:00 - 23:00 UTC)
        # Avoid firing every bar in the window by checking cooldown from last entry
        is_friday_window = (weekday == 4 and 16 <= hour <= 23)
        if not is_friday_window:
            return None

        if (ctx.bar_index - self.last_entry_bar) < self.cooldown_bars:
            return None

        # Price-based trend and volatility filters to ensure favorable context
        highs = ctx.highs(self.atr_period + 1)
        lows = ctx.lows(self.atr_period + 1)
        atr_val = self._atr(highs, lows, closes, self.atr_period)
        if atr_val is None:
            return None

        # Filter: Avoid entering when market is dumping sharply into Friday close
        # ETH must hold above or within 1.0% of the 24h EMA
        price_filter = current_price >= (ema_val * 0.990)
        
        # Macro sentiment guard: avoid buying into extreme capitulation
        fear_greed = ctx.features.get("fear_greed_index", 50.0)
        sentiment_ok = fear_greed >= 15.0

        # Avoid crisis/meltdown market conditions
        market_regime = ctx.market.get("regime", "NORMAL")
        regime_ok = market_regime not in ("CRISIS", "MELTDOWN")

        if price_filter and sentiment_ok and regime_ok:
            # Dynamic confidence based on trend alignment and sentiment
            base_conf = 0.65
            if current_price > ema_val:
                base_conf += 0.1
            if fear_greed > 45.0:
                base_conf += 0.05
            conf = min(0.85, base_conf)

            self.last_entry_bar = ctx.bar_index
            return ctx.signal(
                "long",
                confidence=conf,
                stop_loss_bps=self.METADATA["declared_sl_bps"],
                take_profit_bps=self.METADATA["declared_tp_bps"],
                horizon_seconds=self.METADATA["declared_hold_seconds"],
                metadata={
                    "reason": "friday_weekend_drift_entry",
                    "weekday": weekday,
                    "hour": hour,
                    "price": current_price,
                    "ema24": ema_val,
                    "atr": atr_val,
                    "fear_greed": fear_greed,
                    "regime": market_regime,
                }
            )

        return None