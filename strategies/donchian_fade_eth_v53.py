from domains.strategy_contract import Strategy, BarContext, Signal
from typing import Optional, Dict, Any
import math

class DonchianTrapFade(Strategy):
    METADATA = {
        "name": "DonchianTrapFade",
        "domain": "eth_usdc",
        "declared_sl_bps": 220.0,
        "declared_tp_bps": 330.0,
        "declared_hold_seconds": 18000,  # ~5 hours average hold
        "warmup_bars": 35,
    }

    def initialize(self, ctx: BarContext) -> None:
        self.channel_period = 20
        self.atr_period = 14
        self.last_exit_bar = -999
        self.entry_bar = -999
        self.cooldown_bars = 4
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
        self.entry_bar = -999

    def on_bar(self, ctx: BarContext) -> Optional[Signal]:
        req_bars = self.channel_period + 2
        closes = ctx.closes(req_bars)
        highs = ctx.highs(req_bars)
        lows = ctx.lows(req_bars)

        if closes is None or len(closes) < req_bars:
            return None

        # Manage existing position time-based exit
        if ctx.has_position():
            bars_held = ctx.bar_index - self.entry_bar if self.entry_bar > 0 else 0
            if bars_held >= self.max_hold_bars:
                return ctx.signal(
                    "flat",
                    confidence=0.6,
                    metadata={"reason": "max_bars_exhaustion", "bars_held": bars_held, "price": ctx.bar.close}
                )
            return None

        # Cooldown guard after exits to prevent rapid re-entry / churn
        if ctx.bar_index - self.last_exit_bar < self.cooldown_bars:
            return None

        # Regime safety: avoid entering trap fades during full market meltdown/crisis
        regime = ctx.market.get("regime", "NORMAL")
        if regime in ("CRISIS", "MELTDOWN"):
            return None

        atr = self._atr(highs, lows, closes, self.atr_period)
        if atr is None or atr <= 0:
            return None

        curr_close = ctx.bar.close
        curr_high = ctx.bar.high
        curr_low = ctx.bar.low

        # 20-bar prior range (excluding current bar)
        prior_highs = highs[-(self.channel_period + 1):-1]
        prior_lows = lows[-(self.channel_period + 1):-1]
        range_high = max(prior_highs)
        range_low = min(prior_lows)

        # Minimum wick protrusion threshold to ensure real stop-hunt / liquidity sweep
        min_penetration = 0.25 * atr

        # Bull Trap / Failed Upside Breakout -> Go Short
        # Poked strictly above 20-bar high by at least min_penetration, but closed back under range high
        if curr_high > range_high + min_penetration and curr_close < range_high:
            # Wick must be upper rejection: close in bottom 60% of the bar's range
            bar_range = curr_high - curr_low
            if bar_range > 0 and (curr_close - curr_low) / bar_range <= 0.60:
                # Dynamic stop-loss based on distance beyond the peak plus buffer
                wick_excess_bps = ((curr_high - curr_close) / curr_close) * 10000.0
                sl_bps = max(140.0, min(260.0, wick_excess_bps + 25.0))
                tp_bps = sl_bps * 1.5

                self.entry_bar = ctx.bar_index
                return ctx.signal(
                    "short",
                    confidence=0.72,
                    stop_loss_bps=sl_bps,
                    take_profit_bps=tp_bps,
                    metadata={
                        "reason": "donchian_bull_trap_short",
                        "range_high": range_high,
                        "high": curr_high,
                        "close": curr_close,
                        "penetration_atr": (curr_high - range_high) / atr,
                        "atr": atr
                    }
                )

        # Bear Trap / Failed Downside Breakout -> Go Long
        # Poked strictly below 20-bar low by at least min_penetration, but closed back above range low
        if curr_low < range_low - min_penetration and curr_close > range_low:
            # Wick must be lower rejection: close in top 60% of the bar's range
            bar_range = curr_high - curr_low
            if bar_range > 0 and (curr_close - curr_low) / bar_range >= 0.40:
                wick_excess_bps = ((curr_close - curr_low) / curr_close) * 10000.0
                sl_bps = max(140.0, min(260.0, wick_excess_bps + 25.0))
                tp_bps = sl_bps * 1.5

                self.entry_bar = ctx.bar_index
                return ctx.signal(
                    "long",
                    confidence=0.72,
                    stop_loss_bps=sl_bps,
                    take_profit_bps=tp_bps,
                    metadata={
                        "reason": "donchian_bear_trap_long",
                        "range_low": range_low,
                        "low": curr_low,
                        "close": curr_close,
                        "penetration_atr": (range_low - curr_low) / atr,
                        "atr": atr
                    }
                )

        return None