from domains.strategy_contract import Strategy, BarContext, Signal
from typing import Optional, Dict, Any
import math

class DonchianTrapFade(Strategy):
    METADATA = {
        "name": "DonchianTrapFade",
        "domain": "eth_usdc",
        "declared_sl_bps": 220.0,
        "declared_tp_bps": 380.0,
        "declared_hold_seconds": 18000,  # ~5 bars intended hold
        "warmup_bars": 35,
    }

    def initialize(self, ctx: BarContext) -> None:
        self.lookback = 20
        self.atr_period = 14
        self.cooldown_bars = 6
        self.max_hold_bars = 6
        self.last_trade_bar = -999
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
        self.last_trade_bar = ctx.bar_index

    def on_bar(self, ctx: BarContext) -> Optional[Signal]:
        total_needed = self.lookback + self.atr_period + 2
        closes = ctx.closes(total_needed)
        if len(closes) < total_needed:
            return None

        highs = ctx.highs(total_needed)
        lows = ctx.lows(total_needed)

        # Handle active position time-based exit
        if ctx.has_position():
            bars_held = ctx.bar_index - self.entry_bar
            if bars_held >= self.max_hold_bars:
                self.last_trade_bar = ctx.bar_index
                return ctx.signal(
                    "flat",
                    confidence=0.6,
                    metadata={"reason": "time_stop_reached", "bars_held": bars_held, "price": ctx.bar.close}
                )
            return None

        # Mandatory cooldown check
        if (ctx.bar_index - self.last_trade_bar) < self.cooldown_bars:
            return None

        # Avoid extreme crisis conditions
        crisis_score = ctx.market.get("crisis_score", 0.0)
        if crisis_score > 0.65:
            return None

        atr = self._atr(highs, lows, closes, self.atr_period)
        if atr is None or atr <= 0:
            return None

        # Reference Donchian channel over previous 20 completed bars (excluding current bar)
        prior_highs = highs[-(self.lookback + 1):-1]
        prior_lows = lows[-(self.lookback + 1):-1]
        donchian_high = max(prior_highs)
        donchian_low = min(prior_lows)

        current_high = ctx.bar.high
        current_low = ctx.bar.low
        current_close = ctx.bar.close

        # Volatility-scaled dynamic penetration threshold (0.25 to 1.8 ATR poke)
        min_penetration = 0.25 * atr
        max_penetration = 1.80 * atr

        # Bull Trap / Bearish Fade: High pierced above previous Donchian high, but closed back inside
        upper_wick = current_high - donchian_high
        is_bull_trap = (
            current_high > donchian_high
            and current_close < donchian_high
            and upper_wick >= min_penetration
            and upper_wick <= max_penetration
            and current_close <= (current_high + current_low) / 2.0  # weak close (lower half of range)
        )

        # Bear Trap / Bullish Fade: Low pierced below previous Donchian low, but closed back inside
        lower_wick = donchian_low - current_low
        is_bear_trap = (
            current_low < donchian_low
            and current_close > donchian_low
            and lower_wick >= min_penetration
            and lower_wick <= max_penetration
            and current_close >= (current_high + current_low) / 2.0  # strong close (upper half of range)
        )

        if is_bull_trap:
            # Stop loss placed beyond the rejection wick + buffer
            sl_distance = max(current_high - current_close + 0.1 * atr, 0.007 * current_close)
            sl_bps = min(max((sl_distance / current_close) * 10000.0, 140.0), 280.0)
            tp_bps = sl_bps * 1.6

            self.entry_bar = ctx.bar_index
            self.last_trade_bar = ctx.bar_index
            return ctx.signal(
                "short",
                confidence=0.75,
                stop_loss_bps=sl_bps,
                take_profit_bps=tp_bps,
                horizon_seconds=21600,
                metadata={
                    "reason": "failed_breakout_bull_trap_short",
                    "donchian_high": donchian_high,
                    "wick_penetration": upper_wick,
                    "atr": atr,
                    "close": current_close,
                }
            )

        if is_bear_trap:
            # Stop loss placed beyond the rejection wick + buffer
            sl_distance = max(current_close - current_low + 0.1 * atr, 0.007 * current_close)
            sl_bps = min(max((sl_distance / current_close) * 10000.0, 140.0), 280.0)
            tp_bps = sl_bps * 1.6

            self.entry_bar = ctx.bar_index
            self.last_trade_bar = ctx.bar_index
            return ctx.signal(
                "long",
                confidence=0.75,
                stop_loss_bps=sl_bps,
                take_profit_bps=tp_bps,
                horizon_seconds=21600,
                metadata={
                    "reason": "failed_breakout_bear_trap_long",
                    "donchian_low": donchian_low,
                    "wick_penetration": lower_wick,
                    "atr": atr,
                    "close": current_close,
                }
            )

        return None