from domains.strategy_contract import Strategy, BarContext, Signal
from typing import Optional, Dict, Any
import math

class DonchianTrapFade(Strategy):
    METADATA = {
        "name": "DonchianTrapFade",
        "domain": "eth_usdc",
        "declared_sl_bps": 220.0,
        "declared_tp_bps": 340.0,
        "declared_hold_seconds": 18000,
        "warmup_bars": 40,
    }

    def initialize(self, ctx: BarContext) -> None:
        self.channel_period = 24
        self.atr_period = 14
        self.cooldown_bars = 10
        self.last_exit_bar = -999
        self.max_hold_bars = 6

    def _atr(self, highs, lows, closes, period):
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
        total_needed = self.channel_period + 2
        closes = ctx.closes(total_needed)
        if len(closes) < total_needed:
            return None

        highs = ctx.highs(total_needed)
        lows = ctx.lows(total_needed)

        # Time-based exit for open positions
        if ctx.has_position():
            pos = ctx.position
            if pos is not None and "bars_held" in pos:
                if pos["bars_held"] >= self.max_hold_bars:
                    return ctx.signal("flat", confidence=0.5, metadata={"reason": "max_hold_reached", "bars_held": pos["bars_held"]})
            return None

        # Mandatory cooldown guard
        if ctx.bar_index - self.last_exit_bar < self.cooldown_bars:
            return None

        # Filter out extreme crisis regimes to prevent toxic adverse selection
        regime = ctx.market.get("regime", "NORMAL")
        if regime in ("CRISIS", "MELTDOWN"):
            return None

        atr = self._atr(highs, lows, closes, self.atr_period)
        if atr is None or atr <= 0.0:
            return None

        curr_bar = ctx.bar
        prior_highs = highs[-(self.channel_period + 1):-1]
        prior_lows = lows[-(self.channel_period + 1):-1]

        donchian_high = max(prior_highs)
        donchian_low = min(prior_lows)

        # Meaningful wick requirement (at least 0.35 ATR beyond boundary)
        wick_threshold = 0.35 * atr

        # Bull Trap Fade: High penetrated prior high by wick_threshold, but closed well back inside
        if curr_bar.high > donchian_high + wick_threshold and curr_bar.close < donchian_high:
            upper_rejection = curr_bar.high - curr_bar.close
            body_range = curr_bar.high - curr_bar.low
            if body_range > 0 and (upper_rejection / body_range) >= 0.50:
                wick_bps = ((curr_bar.high - curr_bar.close) / curr_bar.close) * 10000.0
                sl_bps = max(140.0, min(300.0, wick_bps + 40.0))
                tp_bps = sl_bps * 1.55
                return ctx.signal(
                    "short",
                    confidence=0.75,
                    stop_loss_bps=sl_bps,
                    take_profit_bps=tp_bps,
                    horizon_seconds=18000,
                    metadata={
                        "reason": "donchian_bull_trap_fade",
                        "donchian_high": donchian_high,
                        "high": curr_bar.high,
                        "close": curr_bar.close,
                        "atr": atr,
                        "sl_bps": sl_bps
                    }
                )

        # Bear Trap Fade: Low penetrated prior low by wick_threshold, but closed well back inside
        if curr_bar.low < donchian_low - wick_threshold and curr_bar.close > donchian_low:
            lower_rejection = curr_bar.close - curr_bar.low
            body_range = curr_bar.high - curr_bar.low
            if body_range > 0 and (lower_rejection / body_range) >= 0.50:
                wick_bps = ((curr_bar.close - curr_bar.low) / curr_bar.close) * 10000.0
                sl_bps = max(140.0, min(300.0, wick_bps + 40.0))
                tp_bps = sl_bps * 1.55
                return ctx.signal(
                    "long",
                    confidence=0.75,
                    stop_loss_bps=sl_bps,
                    take_profit_bps=tp_bps,
                    horizon_seconds=18000,
                    metadata={
                        "reason": "donchian_bear_trap_fade",
                        "donchian_low": donchian_low,
                        "low": curr_bar.low,
                        "close": curr_bar.close,
                        "atr": atr,
                        "sl_bps": sl_bps
                    }
                )

        return None