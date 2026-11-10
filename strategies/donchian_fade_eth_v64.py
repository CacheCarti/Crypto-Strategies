from domains.strategy_contract import Strategy, BarContext, Signal
from typing import Optional, Dict, Any
import math

class EthFailedBreakoutFade(Strategy):
    METADATA = {
        "name": "EthFailedBreakoutFade",
        "domain": "eth_usdc",
        "declared_sl_bps": 220.0,
        "declared_tp_bps": 340.0,
        "declared_hold_seconds": 18000,
        "warmup_bars": 35,
        "required_features": ["btc_return_pct"]
    }

    def initialize(self, ctx: BarContext) -> None:
        self.channel_period = 20
        self.atr_period = 14
        self.cooldown_bars = 6
        self.last_exit_bar = -999
        self.entry_bar = -999
        self.max_hold_bars = 5

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

    def on_bar(self, ctx: BarContext) -> Optional[Signal]:
        need_bars = self.channel_period + self.atr_period + 5
        closes = ctx.closes(need_bars)
        highs = ctx.highs(need_bars)
        lows = ctx.lows(need_bars)

        if len(closes) < need_bars:
            return None

        # Check existing position time stop
        if ctx.has_position():
            bars_held = ctx.bar_index - self.entry_bar
            if bars_held >= self.max_hold_bars:
                direction = ctx.position_direction()
                return ctx.signal("flat", confidence=0.5, metadata={
                    "reason": "time_based_fade_exit",
                    "bars_held": bars_held,
                    "direction": direction,
                    "close": ctx.bar.close
                })
            return None

        # Cooldown guard
        if (ctx.bar_index - self.last_exit_bar) < self.cooldown_bars:
            return None

        # Regime gating: avoid meltdown/crisis stop-runs
        crisis_score = ctx.market.get("crisis_score", 0.0)
        regime = ctx.market.get("regime", "NORMAL")
        if crisis_score > 0.4 or regime in ("CRISIS", "MELTDOWN"):
            return None

        atr = self._atr(highs, lows, closes, self.atr_period)
        if atr is None or atr <= 0:
            return None

        # Previous 20-bar Donchian channel (excluding current bar)
        prev_highs = highs[-self.channel_period - 1:-1]
        prev_lows = lows[-self.channel_period - 1:-1]
        channel_high = max(prev_highs)
        channel_low = min(prev_lows)

        current_high = ctx.bar.high
        current_low = ctx.bar.low
        current_close = ctx.bar.close

        min_wick = 0.25 * atr
        max_wick = 2.0 * atr

        btc_ret = ctx.features.get("btc_return_pct", 0.0)

        # Bull Trap / Failed Upside Breakout -> Go Short
        # Poked meaningfully above 20-bar high, but closed back strictly inside.
        # Divergence / confirmation: BTC is not in a strong bull expansion (> 1.2% daily gain)
        upper_penetration = current_high - channel_high
        if min_wick <= upper_penetration <= max_wick and current_close < channel_high:
            if btc_ret < 1.5:
                # Dynamic stop loss based on wick overshoot plus buffer
                sl_bps = min(300.0, max(120.0, ((current_high - current_close) / current_close) * 10000.0 + 35.0))
                tp_bps = sl_bps * 1.5
                self.entry_bar = ctx.bar_index
                return ctx.signal(
                    "short",
                    confidence=0.72,
                    stop_loss_bps=sl_bps,
                    take_profit_bps=tp_bps,
                    horizon_seconds=18000,
                    metadata={
                        "reason": "bull_trap_failed_breakout_fade",
                        "channel_high": round(channel_high, 2),
                        "upper_penetration": round(upper_penetration, 2),
                        "atr": round(atr, 2),
                        "btc_return_pct": round(btc_ret, 3),
                        "close": round(current_close, 2)
                    }
                )

        # Bear Trap / Failed Downside Breakout -> Go Long
        # Poked meaningfully below 20-bar low, but closed back strictly inside.
        # Divergence / confirmation: BTC is not in a free-fall (< -1.5% daily dump)
        lower_penetration = channel_low - current_low
        if min_wick <= lower_penetration <= max_wick and current_close > channel_low:
            if btc_ret > -1.5:
                sl_bps = min(300.0, max(120.0, ((current_close - current_low) / current_close) * 10000.0 + 35.0))
                tp_bps = sl_bps * 1.5
                self.entry_bar = ctx.bar_index
                return ctx.signal(
                    "long",
                    confidence=0.72,
                    stop_loss_bps=sl_bps,
                    take_profit_bps=tp_bps,
                    horizon_seconds=18000,
                    metadata={
                        "reason": "bear_trap_failed_breakout_fade",
                        "channel_low": round(channel_low, 2),
                        "lower_penetration": round(lower_penetration, 2),
                        "atr": round(atr, 2),
                        "btc_return_pct": round(btc_ret, 3),
                        "close": round(current_close, 2)
                    }
                )

        return None