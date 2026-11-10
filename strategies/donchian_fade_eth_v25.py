from domains.strategy_contract import Strategy, BarContext, Signal
from typing import Optional, Dict, Any
import math

class DonchianTrapFade(Strategy):
    METADATA = {
        "name": "DonchianTrapFade",
        "domain": "eth_usdc",
        "declared_sl_bps": 220.0,
        "declared_tp_bps": 340.0,
        "declared_hold_seconds": 18000,  # ~5 hours average hold
        "warmup_bars": 35,
    }

    def initialize(self, ctx: BarContext) -> None:
        self.lookback = 20
        self.cooldown_bars = 6
        self.last_trade_bar = -999
        self.max_hold_bars = 8

    def _atr(self, highs, lows, closes, period=14):
        if len(closes) < period + 1:
            return None
        trs = []
        for i in range(1, len(closes)):
            tr = max(highs[i] - lows[i], abs(highs[i] - closes[i - 1]), abs(lows[i] - closes[i - 1]))
            trs.append(tr)
        return sum(trs[-period:]) / period

    def on_position_close(self, ctx: BarContext, position: Dict[str, Any]) -> None:
        self.last_trade_bar = ctx.bar_index

    def on_bar(self, ctx: BarContext) -> Optional[Signal]:
        if ctx.bar_index < self.METADATA["warmup_bars"]:
            return None

        # Check existing position time-based exit
        if ctx.has_position():
            bars_held = ctx.bar_index - self.last_trade_bar
            if bars_held >= self.max_hold_bars:
                return ctx.signal("flat", confidence=0.5, metadata={"reason": "time_exit_reached", "bars_held": bars_held})
            return None

        # Enforce strict cooldown between trades to keep total trades selective (~30-80)
        if ctx.bar_index - self.last_trade_bar < self.cooldown_bars:
            return None

        # Avoid extreme high-stress meltdown regimes
        market_regime = ctx.market.get("regime", "NORMAL")
        if market_regime in ("CRISIS", "MELTDOWN"):
            return None

        highs = ctx.highs(self.lookback + 2)
        lows = ctx.lows(self.lookback + 2)
        closes = ctx.closes(self.lookback + 2)

        if len(highs) < self.lookback + 1:
            return None

        atr = self._atr(highs, lows, closes, period=14)
        if atr is None or atr <= 0:
            return None

        # Range defined by the prior 20 bars (excluding current bar)
        prior_highs = highs[-self.lookback - 1:-1]
        prior_lows = lows[-self.lookback - 1:-1]
        prev_channel_high = max(prior_highs)
        prev_channel_low = min(prior_lows)

        curr_high = highs[-1]
        curr_low = lows[-1]
        curr_close = closes[-1]
        curr_open = ctx.bar.open

        # Require a meaningful probe beyond the channel (at least 0.25 * ATR)
        min_probe = 0.25 * atr

        # Bull Trap / Failed Breakout Short Setup:
        # 1. High breached previous channel high by significant wick
        # 2. Close ended back strictly inside or below the channel high
        # 3. Close is in bottom half of current bar (rejection confirmation)
        bar_range = curr_high - curr_low
        if bar_range > 0 and (curr_high - prev_channel_high) >= min_probe and curr_close < prev_channel_high:
            rejection_depth = (curr_high - curr_close) / bar_range
            if rejection_depth >= 0.55:
                # Stop just above the rejection wick
                sl_bps = max(120.0, min(300.0, ((curr_high - curr_close) / curr_close) * 10000.0 + 30.0))
                tp_bps = sl_bps * 1.5
                self.last_trade_bar = ctx.bar_index
                return ctx.signal(
                    "short",
                    confidence=0.75,
                    stop_loss_bps=sl_bps,
                    take_profit_bps=tp_bps,
                    horizon_seconds=self.METADATA["declared_hold_seconds"],
                    metadata={
                        "reason": "bull_trap_donchian_rejection",
                        "probe": round(curr_high - prev_channel_high, 2),
                        "channel_high": round(prev_channel_high, 2),
                        "rejection_depth": round(rejection_depth, 2),
                        "atr": round(atr, 2),
                    }
                )

        # Bear Trap / Failed Breakdown Long Setup:
        # 1. Low breached previous channel low by significant wick
        # 2. Close ended back strictly inside or above the channel low
        # 3. Close is in top half of current bar (buying absorption)
        if bar_range > 0 and (prev_channel_low - curr_low) >= min_probe and curr_close > prev_channel_low:
            bounce_depth = (curr_close - curr_low) / bar_range
            if bounce_depth >= 0.55:
                sl_bps = max(120.0, min(300.0, ((curr_close - curr_low) / curr_close) * 10000.0 + 30.0))
                tp_bps = sl_bps * 1.5
                self.last_trade_bar = ctx.bar_index
                return ctx.signal(
                    "long",
                    confidence=0.75,
                    stop_loss_bps=sl_bps,
                    take_profit_bps=tp_bps,
                    horizon_seconds=self.METADATA["declared_hold_seconds"],
                    metadata={
                        "reason": "bear_trap_donchian_rejection",
                        "probe": round(prev_channel_low - curr_low, 2),
                        "channel_low": round(prev_channel_low, 2),
                        "bounce_depth": round(bounce_depth, 2),
                        "atr": round(atr, 2),
                    }
                )

        return None