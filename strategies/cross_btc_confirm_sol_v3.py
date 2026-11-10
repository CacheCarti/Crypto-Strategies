from domains.strategy_contract import Strategy, BarContext, Signal
from typing import Optional, Dict, Any
import math

class CrossAssetSolTrend(Strategy):
    METADATA = {
        "name": "CrossAssetSolTrend",
        "domain": "sol_usdc",
        "declared_sl_bps": 200.0,
        "declared_tp_bps": 400.0,
        "declared_hold_seconds": 3600,
        "warmup_bars": 50, # Needs enough bars for EMA and recent returns
        "required_features": ["btc_return_pct", "sol_return_pct"],
    }

    def initialize(self, ctx: BarContext) -> None:
        self.fast_ema_period = 10
        self.slow_ema_period = 30
        self.trend_threshold = 0.005 # 0.5% return for trend confirmation
        self.cooldown_bars = 5
        self.last_signal_bar = -self.cooldown_bars
        self.last_btc_trend = 0 # -1 for down, 0 for neutral, 1 for up
        self.last_sol_trend = 0
        self.last_signal_direction = None

    def _ema(self, values, period):
        if len(values) < period:
            return None
        k = 2.0 / (period + 1)
        ema = sum(values[:period]) / period
        for v in values[period:]:
            ema = v * k + ema * (1 - k)
        return ema

    def on_bar(self, ctx: BarContext) -> Optional[Signal]:
        closes = ctx.closes(self.slow_ema_period + 1)
        if len(closes) < self.slow_ema_period + 1:
            return None

        # Cooldown period after a trade
        if ctx.bar_index - self.last_signal_bar < self.cooldown_bars:
            return None

        current_price = ctx.bar.close
        fast_ema = self._ema(closes, self.fast_ema_period)
        slow_ema = self._ema(closes, self.slow_ema_period)

        if fast_ema is None or slow_ema is None:
            return None

        btc_return_pct = ctx.features.get("btc_return_pct", 0.0)
        sol_return_pct = ctx.features.get("sol_return_pct", 0.0)

        current_btc_trend = 0
        if btc_return_pct > self.trend_threshold:
            current_btc_trend = 1
        elif btc_return_pct < -self.trend_threshold:
            current_btc_trend = -1

        current_sol_trend = 0
        if sol_return_pct > self.trend_threshold:
            current_sol_trend = 1
        elif sol_return_pct < -self.trend_threshold:
            current_sol_trend = -1

        # Check for long entry
        if not ctx.has_position():
            # BTC is trending up AND SOL is trending up
            # And we haven't seen this specific BTC trend recently
            if current_btc_trend == 1 and current_sol_trend == 1 and self.last_btc_trend != 1:
                # Price must be above fast EMA and fast EMA above slow EMA for confirmation
                if current_price > fast_ema and fast_ema > slow_ema:
                    self.last_signal_bar = ctx.bar_index
                    self.last_signal_direction = "long"
                    self.last_btc_trend = current_btc_trend
                    self.last_sol_trend = current_sol_trend
                    return ctx.signal(
                        "long",
                        confidence=0.7,
                        metadata={
                            "reason": "btc_sol_positive_cross_asset_trend",
                            "btc_return_pct": btc_return_pct,
                            "sol_return_pct": sol_return_pct,
                            "fast_ema": fast_ema,
                            "slow_ema": slow_ema,
                            "price": current_price,
                        }
                    )

            # Check for short entry
            # BTC is trending down AND SOL is trending down
            # And we haven't seen this specific BTC trend recently
            elif current_btc_trend == -1 and current_sol_trend == -1 and self.last_btc_trend != -1:
                # Price must be below fast EMA and fast EMA below slow EMA for confirmation
                if current_price < fast_ema and fast_ema < slow_ema:
                    self.last_signal_bar = ctx.bar_index
                    self.last_signal_direction = "short"
                    self.last_btc_trend = current_btc_trend
                    self.last_sol_trend = current_sol_trend
                    return ctx.signal(
                        "short",
                        confidence=0.7,
                        metadata={
                            "reason": "btc_sol_negative_cross_asset_trend",
                            "btc_return_pct": btc_return_pct,
                            "sol_return_pct": sol_return_pct,
                            "fast_ema": fast_ema,
                            "slow_ema": slow_ema,
                            "price": current_price,
                        }
                    )
        else: # Position is open
            # Exit if BTC trend disagrees with our position
            if ctx.position_direction() == "long" and current_btc_trend == -1:
                self.last_signal_bar = ctx.bar_index
                self.last_signal_direction = "flat"
                return ctx.signal(
                    "flat",
                    confidence=0.8,
                    metadata={
                        "reason": "btc_trend_disagrees_with_long",
                        "btc_return_pct": btc_return_pct,
                        "sol_return_pct": sol_return_pct,
                        "price": current_price,
                    }
                )
            elif ctx.position_direction() == "short" and current_btc_trend == 1:
                self.last_signal_bar = ctx.bar_index
                self.last_signal_direction = "flat"
                return ctx.signal(
                    "flat",
                    confidence=0.8,
                    metadata={
                        "reason": "btc_trend_disagrees_with_short",
                        "btc_return_pct": btc_return_pct,
                        "sol_return_pct": sol_return_pct,
                        "price": current_price,
                    }
                )

        # Update last trend for transition detection even if no signal fires
        self.last_btc_trend = current_btc_trend
        self.last_sol_trend = current_sol_trend

        return None

    def on_position_close(self, ctx: BarContext, position: Dict[str, Any]) -> None:
        # Reset cooldown on close to ensure the next signal can fire after cooldown_bars
        self.last_signal_bar = ctx.bar_index
        self.last_signal_direction = None
        pass