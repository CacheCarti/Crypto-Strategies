from domains.strategy_contract import Strategy, BarContext, Signal
from typing import Optional, Dict, Any
import math

class DonchianFailedBreakoutFade(Strategy):
    METADATA = {
        "name": "Donchian Failed Breakout Fade",
        "domain": "eth_usdc",
        "declared_sl_bps": 220.0,
        "declared_tp_bps": 350.0,
        "declared_hold_seconds": 14400,  # 4 hours average hold
        "warmup_bars": 35,
    }

    def initialize(self, ctx: BarContext) -> None:
        self.lookback = 20
        self.atr_period = 14
        self.min_penetration_atr = 0.25  # Require genuine stop hunt penetration
        self.reentry_buffer_atr = 0.10   # Close must convincingly fall back inside
        self.cooldown_bars = 6
        self.last_exit_bar = -999
        self.max_hold_bars = 5

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
        n_needed = self.lookback + self.atr_period + 2
        closes = ctx.closes(n_needed)
        if len(closes) < n_needed:
            return None

        highs = ctx.highs(n_needed)
        lows = ctx.lows(n_needed)

        # Time-based exit for open positions
        if ctx.has_position():
            bars_held = 0
            if ctx.position and "entry_bar" in ctx.position:
                bars_held = ctx.bar_index - ctx.position["entry_bar"]
            if bars_held >= self.max_hold_bars:
                self.last_exit_bar = ctx.bar_index
                return ctx.signal(
                    "flat",
                    confidence=0.6,
                    metadata={"reason": "max_bars_exhaustion", "bars_held": bars_held, "close": ctx.bar.close}
                )
            return None

        # Mandatory cooldown between events to prevent overtrading and fee bleed
        if (ctx.bar_index - self.last_exit_bar) < self.cooldown_bars:
            return None

        # Calculate ATR for volatility-adjusted event criteria
        atr = self._atr(highs, lows, closes, self.atr_period)
        if atr is None or atr <= 0:
            return None

        # Prior lookback window excluding current bar
        prior_highs = highs[-(self.lookback + 1):-1]
        prior_lows = lows[-(self.lookback + 1):-1]
        channel_high = max(prior_highs)
        channel_low = min(prior_lows)

        current_high = ctx.bar.high
        current_low = ctx.bar.low
        current_close = ctx.bar.close

        # Volatility filters: avoid trading into crisis or runaway expansion
        regime = ctx.market.get("regime", "NORMAL")
        if regime in ("CRISIS", "MELTDOWN"):
            return None

        min_wick = self.min_penetration_atr * atr
        buffer = self.reentry_buffer_atr * atr

        # Setup 1: Bull Trap (Wick breaches channel high, close falls back below channel high - buffer)
        if (current_high - channel_high >= min_wick) and (current_close <= channel_high - buffer):
            risk_dist = max(current_high - current_close, atr * 0.5)
            sl_bps = min(max((risk_dist / current_close) * 10000.0, 120.0), 300.0)
            tp_bps = sl_bps * 1.6  # Favorable asymmetric payoff

            penetration_ratio = (current_high - channel_high) / atr
            confidence = min(0.55 + (penetration_ratio * 0.15), 0.90)

            return ctx.signal(
                "short",
                confidence=confidence,
                stop_loss_bps=sl_bps,
                take_profit_bps=tp_bps,
                horizon_seconds=self.METADATA["declared_hold_seconds"],
                metadata={
                    "reason": "bull_trap_reversion",
                    "channel_high": channel_high,
                    "wick_penetration": current_high - channel_high,
                    "atr": atr,
                    "close": current_close,
                    "sl_bps": sl_bps,
                    "tp_bps": tp_bps
                }
            )

        # Setup 2: Bear Trap (Wick breaches channel low, close pushes back above channel low + buffer)
        if (channel_low - current_low >= min_wick) and (current_close >= channel_low + buffer):
            risk_dist = max(current_close - current_low, atr * 0.5)
            sl_bps = min(max((risk_dist / current_close) * 10000.0, 120.0), 300.0)
            tp_bps = sl_bps * 1.6

            penetration_ratio = (channel_low - current_low) / atr
            confidence = min(0.55 + (penetration_ratio * 0.15), 0.90)

            return ctx.signal(
                "long",
                confidence=confidence,
                stop_loss_bps=sl_bps,
                take_profit_bps=tp_bps,
                horizon_seconds=self.METADATA["declared_hold_seconds"],
                metadata={
                    "reason": "bear_trap_reversion",
                    "channel_low": channel_low,
                    "wick_penetration": channel_low - current_low,
                    "atr": atr,
                    "close": current_close,
                    "sl_bps": sl_bps,
                    "tp_bps": tp_bps
                }
            )

        return None