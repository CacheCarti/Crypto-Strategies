from domains.strategy_contract import Strategy, BarContext, Signal
from typing import Optional, Dict, Any
import math

class EthDonchianFailedBreakoutFade(Strategy):
    METADATA = {
        "name": "EthDonchianFailedBreakoutFade",
        "domain": "eth_usdc",
        "declared_sl_bps": 220.0,
        "declared_tp_bps": 380.0,
        "declared_hold_seconds": 18000,  # ~5 hours
        "warmup_bars": 35,
    }

    def initialize(self, ctx: BarContext) -> None:
        self.lookback = 20
        self.atr_period = 14
        self.max_hold_bars = 6
        self.cooldown_bars = 4
        
        self.entry_bar_idx = -1
        self.last_exit_idx = -100

    def _atr(self, highs, lows, closes, period=14):
        if len(closes) < period + 1:
            return None
        trs = []
        for i in range(1, len(closes)):
            tr = max(highs[i] - lows[i], abs(highs[i] - closes[i - 1]), abs(lows[i] - closes[i - 1]))
            trs.append(tr)
        return sum(trs[-period:]) / period

    def on_position_close(self, ctx: BarContext, position: Dict[str, Any]) -> None:
        self.last_exit_idx = ctx.bar_index
        self.entry_bar_idx = -1

    def on_bar(self, ctx: BarContext) -> Optional[Signal]:
        closes = ctx.closes(self.lookback + self.atr_period + 5)
        if len(closes) < self.lookback + self.atr_period + 2:
            return None

        highs = ctx.highs(len(closes))
        lows = ctx.lows(len(closes))

        current_close = ctx.bar.close
        current_high = ctx.bar.high
        current_low = ctx.bar.low

        atr = self._atr(highs, lows, closes, self.atr_period)
        if atr is None or atr <= 0:
            return None

        # Manage existing position via time-decay / structural stop
        if ctx.has_position():
            bars_held = ctx.bar_index - self.entry_bar_idx if self.entry_bar_idx > 0 else 0
            pos_dir = ctx.position_direction()

            # Time-based expiration for mean reversion
            if bars_held >= self.max_hold_bars:
                return ctx.signal(
                    "flat",
                    confidence=0.6,
                    metadata={"reason": "time_stop_reached", "bars_held": bars_held, "close": current_close}
                )

            # Reversion target reached: cross beyond mid-channel
            prior_highs = highs[-self.lookback - 1 : -1]
            prior_lows = lows[-self.lookback - 1 : -1]
            mid_channel = (max(prior_highs) + min(prior_lows)) / 2.0

            if pos_dir == "long" and current_close >= mid_channel:
                return ctx.signal(
                    "flat",
                    confidence=0.7,
                    metadata={"reason": "long_tp_mid_channel", "mid_channel": mid_channel, "close": current_close}
                )
            elif pos_dir == "short" and current_close <= mid_channel:
                return ctx.signal(
                    "flat",
                    confidence=0.7,
                    metadata={"reason": "short_tp_mid_channel", "mid_channel": mid_channel, "close": current_close}
                )
            return None

        # Mandatory cooldown check
        if (ctx.bar_index - self.last_exit_idx) < self.cooldown_bars:
            return None

        # Avoid extreme meltdown/crisis regimes for mean reversion
        regime = ctx.market.get("regime", "NORMAL")
        if regime in ("CRISIS", "MELTDOWN"):
            return None

        # Donchian channel excluding current bar
        prior_highs = highs[-self.lookback - 1 : -1]
        prior_lows = lows[-self.lookback - 1 : -1]
        donchian_high = max(prior_highs)
        donchian_low = min(prior_lows)

        # Minimum wick threshold to prove genuine stop-hunt (0.35 * ATR)
        min_penetration = 0.35 * atr

        # Setup 1: Failed Breakout High -> Bear Trap Reversal (Short)
        bull_trap = (current_high >= donchian_high + min_penetration) and (current_close < donchian_high)
        
        # Setup 2: Failed Breakdown Low -> Bull Trap Reversal (Long)
        bear_trap = (current_low <= donchian_low - min_penetration) and (current_close > donchian_low)

        if bull_trap:
            # Dynamic SL placed above the rejection wick with ATR buffer
            wick_overshoot = current_high - donchian_high
            sl_distance = (current_high - current_close) + (0.2 * atr)
            sl_bps = max(120.0, min(300.0, (sl_distance / current_close) * 10000.0))
            tp_bps = min(450.0, sl_bps * 1.8)

            self.entry_bar_idx = ctx.bar_index
            return ctx.signal(
                "short",
                confidence=0.75,
                stop_loss_bps=sl_bps,
                take_profit_bps=tp_bps,
                horizon_seconds=self.METADATA["declared_hold_seconds"],
                metadata={
                    "reason": "failed_breakout_high_wick_rejection",
                    "donchian_high": donchian_high,
                    "wick_high": current_high,
                    "wick_overshoot": wick_overshoot,
                    "atr": atr,
                    "sl_bps": sl_bps
                }
            )

        if bear_trap:
            # Dynamic SL placed below the rejection wick with ATR buffer
            wick_overshoot = donchian_low - current_low
            sl_distance = (current_close - current_low) + (0.2 * atr)
            sl_bps = max(120.0, min(300.0, (sl_distance / current_close) * 10000.0))
            tp_bps = min(450.0, sl_bps * 1.8)

            self.entry_bar_idx = ctx.bar_index
            return ctx.signal(
                "long",
                confidence=0.75,
                stop_loss_bps=sl_bps,
                take_profit_bps=tp_bps,
                horizon_seconds=self.METADATA["declared_hold_seconds"],
                metadata={
                    "reason": "failed_breakdown_low_wick_rejection",
                    "donchian_low": donchian_low,
                    "wick_low": current_low,
                    "wick_overshoot": wick_overshoot,
                    "atr": atr,
                    "sl_bps": sl_bps
                }
            )

        return None