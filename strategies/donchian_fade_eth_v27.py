from domains.strategy_contract import Strategy, BarContext, Signal
from typing import Optional, Dict, Any
import math

class DonchianFadeStructural(Strategy):
    METADATA = {
        "name": "Donchian Trap Fade",
        "domain": "eth_usdc",
        "declared_sl_bps": 220.0,
        "declared_tp_bps": 340.0,
        "declared_hold_seconds": 18000,
        "warmup_bars": 35,
    }

    def initialize(self, ctx: BarContext) -> None:
        self.lookback = 20
        self.atr_period = 14
        self.cooldown_bars = 4
        self.max_hold_bars = 5
        self.last_exit_bar = -100
        self.entry_bar = -100

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
        total_bars_needed = max(self.lookback, self.atr_period) + 2
        closes = ctx.closes(total_bars_needed)
        if len(closes) < total_bars_needed:
            return None

        highs = ctx.highs(total_bars_needed)
        lows = ctx.lows(total_bars_needed)

        cur_close = ctx.bar.close
        cur_high = ctx.bar.high
        cur_low = ctx.bar.low

        atr = self._atr(highs, lows, closes, self.atr_period)
        if atr is None or atr <= 0:
            return None

        # Manage existing open position: timed structural exit to prevent giveback chop
        if ctx.has_position():
            bars_held = ctx.bar_index - self.entry_bar
            if bars_held >= self.max_hold_bars:
                self.last_exit_bar = ctx.bar_index
                return ctx.signal(
                    "flat",
                    confidence=0.6,
                    metadata={
                        "reason": "timed_structural_exit",
                        "bars_held": bars_held,
                        "close": cur_close,
                        "atr": atr,
                    }
                )
            return None

        # Cooldown guard: strictly limit overtrading and friction drag
        if ctx.bar_index - self.last_exit_bar < self.cooldown_bars:
            return None

        # Market regime filter: do not fade breakouts in crisis or extreme volatility
        market_regime = ctx.market.get("regime", "NORMAL")
        if market_regime in ("CRISIS", "MELTDOWN"):
            return None

        # Exclude the current active bar from the Donchian channel
        prev_highs = highs[-(self.lookback + 1):-1]
        prev_lows = lows[-(self.lookback + 1):-1]
        prior_max = max(prev_highs)
        prior_min = min(prev_lows)

        # Trap threshold: wick poke must exceed 0.25 ATR to register as a genuine liquidity sweep
        min_penetration = 0.25 * atr

        # Bull Trap / Bearish Fade:
        # Price swept above 20-bar high by meaningful wick, then closed back below the channel high
        if cur_high >= prior_max + min_penetration and cur_close < prior_max:
            wick_overshoot = cur_high - prior_max
            wick_risk = max(cur_high - cur_close, atr * 0.5)
            sl_bps = max(100.0, min(260.0, (wick_risk / cur_close) * 10000.0 * 1.15))
            tp_bps = sl_bps * 1.5

            self.entry_bar = ctx.bar_index
            return ctx.signal(
                "short",
                confidence=0.72,
                stop_loss_bps=sl_bps,
                take_profit_bps=tp_bps,
                horizon_seconds=18000,
                metadata={
                    "reason": "failed_breakout_wick_trap_short",
                    "overshoot": wick_overshoot,
                    "prior_max": prior_max,
                    "high": cur_high,
                    "close": cur_close,
                    "atr": atr,
                    "sl_bps": sl_bps,
                }
            )

        # Bear Trap / Bullish Fade:
        # Price swept below 20-bar low by meaningful wick, then closed back above the channel low
        if cur_low <= prior_min - min_penetration and cur_close > prior_min:
            wick_undershoot = prior_min - cur_low
            wick_risk = max(cur_close - cur_low, atr * 0.5)
            sl_bps = max(100.0, min(260.0, (wick_risk / cur_close) * 10000.0 * 1.15))
            tp_bps = sl_bps * 1.5

            self.entry_bar = ctx.bar_index
            return ctx.signal(
                "long",
                confidence=0.72,
                stop_loss_bps=sl_bps,
                take_profit_bps=tp_bps,
                horizon_seconds=18000,
                metadata={
                    "reason": "failed_breakout_wick_trap_long",
                    "undershoot": wick_undershoot,
                    "prior_min": prior_min,
                    "low": cur_low,
                    "close": cur_close,
                    "atr": atr,
                    "sl_bps": sl_bps,
                }
            )

        return None