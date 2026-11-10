from domains.strategy_contract import Strategy, BarContext, Signal
from typing import Optional, Dict, Any
import math

class EthFailedBreakoutFade(Strategy):
    METADATA = {
        "name": "EthFailedBreakoutFade",
        "domain": "eth_usdc",
        "declared_sl_bps": 220.0,
        "declared_tp_bps": 340.0,
        "declared_hold_seconds": 18000,  # ~5 hours target hold
        "warmup_bars": 35,
        "required_features": ["btc_return_pct"]
    }

    def initialize(self, ctx: BarContext) -> None:
        self.channel_period = 20
        self.atr_period = 14
        self.cooldown_bars = 6
        self.max_hold_bars = 8
        self.last_exit_bar = -999
        self.entry_bar = -999

    def _atr(self, highs, lows, closes, period=14):
        if len(closes) < period + 1:
            return None
        trs = []
        for i in range(1, len(closes)):
            tr = max(highs[i] - lows[i], abs(highs[i] - closes[i - 1]), abs(lows[i] - closes[i - 1]))
            trs.append(tr)
        return sum(trs[-period:]) / period

    def on_position_close(self, ctx: BarContext, position: Dict[str, Any]) -> None:
        self.last_exit_bar = ctx.bar_index
        self.entry_bar = -999

    def on_bar(self, ctx: BarContext) -> Optional[Signal]:
        warmup = max(self.channel_period, self.atr_period) + 2
        if ctx.bar_index < warmup:
            return None

        closes = ctx.closes(self.channel_period + 2)
        highs = ctx.highs(self.channel_period + 2)
        lows = ctx.lows(self.channel_period + 2)
        
        if len(closes) < self.channel_period + 2:
            return None

        # Manage existing position (time-based horizon exit)
        if ctx.has_position():
            bars_held = ctx.bar_index - self.entry_bar if self.entry_bar > 0 else 0
            if bars_held >= self.max_hold_bars:
                return ctx.signal("flat", confidence=0.6, metadata={"reason": "max_hold_time_reached", "bars_held": bars_held})
            return None

        # Mandatory cooldown check
        if (ctx.bar_index - self.last_exit_bar) < self.cooldown_bars:
            return None

        # Volatility state via ATR
        atr = self._atr(highs, lows, closes, self.atr_period)
        if atr is None or atr <= 0:
            return None

        # Prior 20-bar extremes (excluding current bar)
        prior_highs = highs[-(self.channel_period + 1):-1]
        prior_lows = lows[-(self.channel_period + 1):-1]
        range_high = max(prior_highs)
        range_low = min(prior_lows)

        current_high = ctx.bar.high
        current_low = ctx.bar.low
        current_close = ctx.bar.close

        # Volatility-scaled wick requirement: at least 0.25 ATR outside the breakout level
        min_penetration = 0.25 * atr

        # Cross-asset momentum confirmation from BTC
        btc_ret = ctx.features.get("btc_return_pct", 0.0)

        # Bull Trap / Failed Breakout High -> Fade Short
        # Criteria: High pierced above range + min_penetration, closed back strictly below range_high
        # Confirmation: BTC momentum is not explosively running (cross-asset check prevents fading true macro breakouts)
        if current_high > (range_high + min_penetration) and current_close < range_high:
            if btc_ret < 3.5:  # ensure it's not a runaway market-wide meltup
                stop_bps = max(120.0, min(320.0, ((current_high - current_close) / current_close) * 10000.0 + 30.0))
                tp_bps = stop_bps * 1.6
                self.entry_bar = ctx.bar_index
                return ctx.signal(
                    "short",
                    confidence=0.75,
                    stop_loss_bps=stop_bps,
                    take_profit_bps=tp_bps,
                    metadata={
                        "reason": "failed_breakout_high_wick_rejection",
                        "range_high": range_high,
                        "wick_high": current_high,
                        "close": current_close,
                        "atr": atr,
                        "btc_return_pct": btc_ret
                    }
                )

        # Bear Trap / Failed Breakdown Low -> Fade Long
        # Criteria: Low pierced below range - min_penetration, closed back strictly above range_low
        # Confirmation: BTC momentum is not in freefall panic
        if current_low < (range_low - min_penetration) and current_close > range_low:
            if btc_ret > -3.5:  # ensure it's not an aggregate cascade selloff
                stop_bps = max(120.0, min(320.0, ((current_close - current_low) / current_close) * 10000.0 + 30.0))
                tp_bps = stop_bps * 1.6
                self.entry_bar = ctx.bar_index
                return ctx.signal(
                    "long",
                    confidence=0.75,
                    stop_loss_bps=stop_bps,
                    take_profit_bps=tp_bps,
                    metadata={
                        "reason": "failed_breakout_low_wick_rejection",
                        "range_low": range_low,
                        "wick_low": current_low,
                        "close": current_close,
                        "atr": atr,
                        "btc_return_pct": btc_ret
                    }
                )

        return None