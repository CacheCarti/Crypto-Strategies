from domains.strategy_contract import Strategy, BarContext, Signal
from typing import Optional, Dict, Any
import math

class EthFailedBreakoutFade(Strategy):
    METADATA = {
        "name": "EthFailedBreakoutFade",
        "domain": "eth_usdc",
        "declared_sl_bps": 220.0,
        "declared_tp_bps": 340.0,
        "declared_hold_seconds": 18000,  # ~5 hours
        "warmup_bars": 35,
        "required_features": ["btc_return_pct"],
    }

    def initialize(self, ctx: BarContext) -> None:
        self.lookback = 20
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
        closes = ctx.closes(self.lookback + 10)
        highs = ctx.highs(self.lookback + 10)
        lows = ctx.lows(self.lookback + 10)

        if len(closes) < self.lookback + 5:
            return None

        # Time-based exit for open positions
        if ctx.has_position():
            bars_held = ctx.bar_index - self.entry_bar
            if bars_held >= self.max_hold_bars:
                return ctx.signal(
                    "flat",
                    confidence=0.6,
                    metadata={"reason": "time_based_fade_exit", "bars_held": bars_held}
                )
            return None

        # Mandatory post-exit cooldown to avoid churn
        if ctx.bar_index - self.last_exit_bar < self.cooldown_bars:
            return None

        # Volatility state check via ATR
        atr = self._atr(highs, lows, closes, period=14)
        if atr is None or atr <= 0:
            return None

        current_close = ctx.bar.close
        current_high = ctx.bar.high
        current_low = ctx.bar.low

        # 20-bar reference range excluding current bar
        prior_highs = highs[-self.lookback - 1 : -1]
        prior_lows = lows[-self.lookback - 1 : -1]
        range_high = max(prior_highs)
        range_low = min(prior_lows)

        # Cross-asset momentum confirmation: BTC return %
        btc_ret = ctx.features.get("btc_return_pct", 0.0)

        # Minimum required wick extension beyond level (0.25 * ATR) to capture genuine stop-runs
        min_penetration = 0.25 * atr

        # Setup 1: Bull Trap / Failed Upside Breakout -> Go Short
        # Poked meaningfully above range_high, but closed below it.
        # Cross-asset confirm: BTC not aggressively pumping (btc_ret < 1.2%)
        is_bull_trap = (
            current_high >= range_high + min_penetration
            and current_close < range_high
            and current_close < ctx.bar.open  # bearish close
            and btc_ret < 1.2
        )

        # Setup 2: Bear Trap / Failed Downside Breakout -> Go Long
        # Poked meaningfully below range_low, but closed above it.
        # Cross-asset confirm: BTC not dumping heavily (btc_ret > -1.2%)
        is_bear_trap = (
            current_low <= range_low - min_penetration
            and current_close > range_low
            and current_close > ctx.bar.open  # bullish close
            and btc_ret > -1.2
        )

        if is_bull_trap:
            wick_bps = ((current_high - current_close) / current_close) * 10000.0
            stop_loss = max(120.0, min(wick_bps + 30.0, 250.0))
            take_profit = stop_loss * 1.6
            self.entry_bar = ctx.bar_index
            return ctx.signal(
                "short",
                confidence=0.72,
                stop_loss_bps=stop_loss,
                take_profit_bps=take_profit,
                horizon_seconds=18000,
                metadata={
                    "reason": "failed_breakout_fade_short",
                    "range_high": range_high,
                    "bar_high": current_high,
                    "bar_close": current_close,
                    "atr": atr,
                    "btc_return_pct": btc_ret,
                },
            )

        if is_bear_trap:
            wick_bps = ((current_close - current_low) / current_close) * 10000.0
            stop_loss = max(120.0, min(wick_bps + 30.0, 250.0))
            take_profit = stop_loss * 1.6
            self.entry_bar = ctx.bar_index
            return ctx.signal(
                "long",
                confidence=0.72,
                stop_loss_bps=stop_loss,
                take_profit_bps=take_profit,
                horizon_seconds=18000,
                metadata={
                    "reason": "failed_breakout_fade_long",
                    "range_low": range_low,
                    "bar_low": current_low,
                    "bar_close": current_close,
                    "atr": atr,
                    "btc_return_pct": btc_ret,
                },
            )

        return None