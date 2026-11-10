from domains.strategy_contract import Strategy, BarContext, Signal
from typing import Optional, Dict, Any
import math

class DonchianTrapFade(Strategy):
    METADATA = {
        "name": "DonchianTrapFade",
        "domain": "eth_usdc",
        "declared_sl_bps": 220.0,
        "declared_tp_bps": 380.0,
        "declared_hold_seconds": 18000,
        "warmup_bars": 35,
    }

    def initialize(self, ctx: BarContext) -> None:
        self.period = 20
        self.cooldown_bars = 4
        self.last_exit_bar = -999
        self.entry_bar = -999
        self.max_hold_bars = 6

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
        closes = ctx.closes(self.period + 10)
        highs = ctx.highs(self.period + 10)
        lows = ctx.lows(self.period + 10)

        if len(closes) < self.period + 2:
            return None

        # Manage existing position time-based stop/fade
        if ctx.has_position():
            bars_in_trade = ctx.bar_index - self.entry_bar
            if bars_in_trade >= self.max_hold_bars:
                return ctx.signal(
                    "flat",
                    confidence=0.6,
                    metadata={"reason": "time_exit_reached", "bars_held": bars_in_trade}
                )
            return None

        # Enforce strict cooldown to limit trade count and friction
        if ctx.bar_index - self.last_exit_bar < self.cooldown_bars:
            return None

        # Avoid crisis/meltdown regimes where stop hunts turn into multi-sigma trends
        regime = ctx.market.get("regime", "NORMAL")
        if regime in ("CRISIS", "MELTDOWN"):
            return None

        atr = self._atr(highs, lows, closes, period=14)
        if atr is None or atr <= 0:
            return None

        current_close = ctx.bar.close
        current_high = ctx.bar.high
        current_low = ctx.bar.low

        # Donchian channel formed by the previous 20 completed bars (excluding current bar)
        prior_highs = highs[-self.period - 1:-1]
        prior_lows = lows[-self.period - 1:-1]
        channel_high = max(prior_highs)
        channel_low = min(prior_lows)

        min_wick_atr = 0.25 * atr

        # Bull Trap / Failed Upside Breakout
        # Price swept above the 20-bar high by at least 0.25 ATR, but closed back firmly inside
        if current_high > channel_high + min_wick_atr and current_close < channel_high:
            upper_wick = current_high - max(current_close, ctx.bar.open)
            body = abs(current_close - ctx.bar.open)
            if upper_wick >= body * 0.8:
                stop_dist = max(current_high - current_close, atr * 0.8)
                sl_bps = min(max((stop_dist / current_close) * 10000.0, 140.0), 260.0)
                tp_bps = sl_bps * 1.6

                self.entry_bar = ctx.bar_index
                return ctx.signal(
                    "short",
                    confidence=0.75,
                    stop_loss_bps=sl_bps,
                    take_profit_bps=tp_bps,
                    horizon_seconds=self.METADATA["declared_hold_seconds"],
                    metadata={
                        "reason": "donchian_bull_trap_fade",
                        "channel_high": channel_high,
                        "high": current_high,
                        "close": current_close,
                        "atr": atr,
                    }
                )

        # Bear Trap / Failed Downside Breakdown
        # Price swept below the 20-bar low by at least 0.25 ATR, but closed back firmly inside
        if current_low < channel_low - min_wick_atr and current_close > channel_low:
            lower_wick = min(current_close, ctx.bar.open) - current_low
            body = abs(current_close - ctx.bar.open)
            if lower_wick >= body * 0.8:
                stop_dist = max(current_close - current_low, atr * 0.8)
                sl_bps = min(max((stop_dist / current_close) * 10000.0, 140.0), 260.0)
                tp_bps = sl_bps * 1.6

                self.entry_bar = ctx.bar_index
                return ctx.signal(
                    "long",
                    confidence=0.75,
                    stop_loss_bps=sl_bps,
                    take_profit_bps=tp_bps,
                    horizon_seconds=self.METADATA["declared_hold_seconds"],
                    metadata={
                        "reason": "donchian_bear_trap_fade",
                        "channel_low": channel_low,
                        "low": current_low,
                        "close": current_close,
                        "atr": atr,
                    }
                )

        return None