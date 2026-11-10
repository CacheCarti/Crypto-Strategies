from domains.strategy_contract import Strategy, BarContext, Signal
from typing import Optional, Dict, Any
import math

class DonchianTrapFade(Strategy):
    METADATA = {
        "name": "DonchianTrapFade",
        "domain": "eth_usdc",
        "declared_sl_bps": 220.0,
        "declared_tp_bps": 330.0,
        "declared_hold_seconds": 18000,  # ~5 bars on 1h
        "warmup_bars": 35,
    }

    def initialize(self, ctx: BarContext) -> None:
        self.lookback = 20
        self.min_wick_ratio = 0.20  # Wick must be at least 20% of ATR
        self.cooldown_bars = 4
        self.last_exit_bar = -999
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

        # Manage open position time-based stop
        if ctx.has_position():
            pos = ctx.position
            # If position held too long, exit to prevent adverse drift
            if ctx.bar_index - self.last_exit_bar >= self.max_hold_bars:
                return ctx.signal(
                    "flat",
                    confidence=0.5,
                    metadata={"reason": "time_stop_expired", "bar_index": ctx.bar_index}
                )
            return None

        # Mandatory cooldown guard
        if ctx.bar_index - self.last_exit_bar < self.cooldown_bars:
            return None

        # Avoid extreme crisis / meltdown regime
        crisis_score = ctx.market.get("crisis_score", 0.0)
        if crisis_score > 0.65:
            return None

        # ATR calculation for wick significance
        atr_val = self._atr(highs, lows, closes, period=14)
        if atr_val is None or atr_val <= 0:
            return None

        # 20-bar high/low prior to current bar
        prior_highs = highs[-self.lookback - 1 : -1]
        prior_lows = lows[-self.lookback - 1 : -1]
        
        range_high = max(prior_highs)
        range_low = min(prior_lows)

        curr_bar = ctx.bar
        curr_high = curr_bar.high
        curr_low = curr_bar.low
        curr_close = curr_bar.close
        curr_open = curr_bar.open

        min_wick = atr_val * self.min_wick_ratio

        # Bull Trap (Failed Breakout High) -> Short signal
        # Poke above range high, close back inside range, with significant upper wick
        if curr_high > range_high and curr_close < range_high:
            upper_wick = curr_high - max(curr_open, curr_close)
            if upper_wick >= min_wick:
                # Dynamic SL based on wick poke distance
                poke_dist = curr_high - curr_close
                sl_bps = max(100.0, min(300.0, (poke_dist / curr_close) * 10000.0 * 1.25))
                tp_bps = sl_bps * 1.5

                return ctx.signal(
                    "short",
                    confidence=0.72,
                    stop_loss_bps=sl_bps,
                    take_profit_bps=tp_bps,
                    metadata={
                        "reason": "bull_trap_rejection",
                        "range_high": range_high,
                        "bar_high": curr_high,
                        "bar_close": curr_close,
                        "upper_wick": upper_wick,
                        "atr": atr_val,
                        "sl_bps": sl_bps,
                        "tp_bps": tp_bps,
                    }
                )

        # Bear Trap (Failed Breakdown Low) -> Long signal
        # Poke below range low, close back inside range, with significant lower wick
        if curr_low < range_low and curr_close > range_low:
            lower_wick = min(curr_open, curr_close) - curr_low
            if lower_wick >= min_wick:
                poke_dist = curr_close - curr_low
                sl_bps = max(100.0, min(300.0, (poke_dist / curr_close) * 10000.0 * 1.25))
                tp_bps = sl_bps * 1.5

                return ctx.signal(
                    "long",
                    confidence=0.72,
                    stop_loss_bps=sl_bps,
                    take_profit_bps=tp_bps,
                    metadata={
                        "reason": "bear_trap_rejection",
                        "range_low": range_low,
                        "bar_low": curr_low,
                        "bar_close": curr_close,
                        "lower_wick": lower_wick,
                        "atr": atr_val,
                        "sl_bps": sl_bps,
                        "tp_bps": tp_bps,
                    }
                )

        return None