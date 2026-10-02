from domains.strategy_contract import Strategy, BarContext, Signal
from typing import Optional, Dict, Any
import math


class FailedBreakoutFade(Strategy):
    METADATA = {
        "name": "FailedBreakoutFade",
        "domain": "eth_usdc",
        "declared_sl_bps": 280.0,
        "declared_tp_bps": 560.0,
        "declared_hold_seconds": 21600,  # 6 hours
        "warmup_bars": 50,
    }

    def initialize(self, ctx: BarContext) -> None:
        self.lookback = 36
        self.atr_period = 14
        self.min_wick_atr_mult = 0.45  # Stricter: require clear stop hunt wick
        self.cooldown_bars = 16        # Hard cooldown between trades
        self.max_hold_bars = 8
        
        self.last_exit_bar = -999
        self.entry_bar = -999

    def _atr(self, highs, lows, closes, period=14):
        if len(closes) < period + 1:
            return None
        trs = []
        for i in range(1, len(closes)):
            tr = max(highs[i] - lows[i], abs(highs[i] - closes[i - 1]), abs(lows[i] - closes[i - 1]))
            trs.append(tr)
        return sum(trs[-period:]) / period

    def _rsi(self, closes, period=14):
        if len(closes) < period + 1:
            return None
        gains, losses = [], []
        for i in range(1, len(closes)):
            diff = closes[i] - closes[i - 1]
            gains.append(max(diff, 0.0))
            losses.append(max(-diff, 0.0))
        avg_gain = sum(gains[-period:]) / period
        avg_loss = sum(losses[-period:]) / period
        if avg_loss == 0:
            return 100.0
        rs = avg_gain / avg_loss
        return 100.0 - (100.0 / (1.0 + rs))

    def on_position_close(self, ctx: BarContext, position: Dict[str, Any]) -> None:
        self.last_exit_bar = ctx.bar_index
        self.entry_bar = -999

    def on_bar(self, ctx: BarContext) -> Optional[Signal]:
        total_needed = max(self.lookback + 2, self.atr_period + 2)
        closes = ctx.closes(total_needed)
        if len(closes) < total_needed:
            return None

        highs = ctx.highs(total_needed)
        lows = ctx.lows(total_needed)

        atr = self._atr(highs, lows, closes, self.atr_period)
        rsi = self._rsi(closes, 14)
        if atr is None or atr <= 0 or rsi is None:
            return None

        # Position management: time-based exit
        if ctx.has_position():
            bars_in_trade = ctx.bar_index - self.entry_bar
            if bars_in_trade >= self.max_hold_bars:
                self.last_exit_bar = ctx.bar_index
                return ctx.signal(
                    "flat",
                    confidence=0.5,
                    metadata={
                        "reason": "max_hold_time_reached",
                        "bars_held": bars_in_trade,
                        "close": ctx.bar.close,
                        "atr": atr,
                        "rsi": rsi,
                    },
                )
            return None

        # Hard post-exit / post-entry cooldown
        if (ctx.bar_index - self.last_exit_bar) < self.cooldown_bars:
            return None

        # Regime safety filter: avoid fading high-volatility melt runs or crisis
        market_regime = ctx.market.get("regime", "NORMAL")
        if market_regime in ("CRISIS", "MELTDOWN") or ctx.regime == "crisis":
            return None

        # Lookback range excluding the current bar
        prior_highs = highs[-(self.lookback + 1):-1]
        prior_lows = lows[-(self.lookback + 1):-1]
        
        range_high = max(prior_highs)
        range_low = min(prior_lows)

        curr_high = ctx.bar.high
        curr_low = ctx.bar.low
        curr_close = ctx.bar.close
        min_wick = self.min_wick_atr_mult * atr

        # Bull trap (Failed upside breakout -> Short)
        # Price swept above multi-period high with significant wick, closed firmly below
        if curr_high > range_high and (curr_high - range_high) >= min_wick and curr_close < range_high:
            if rsi >= 50.0:  # Must have had upward momentum prior to rejection
                stop_dist = max(curr_high - curr_close + (atr * 0.2), atr * 0.8)
                stop_bps = min(max((stop_dist / curr_close) * 10000.0, 200.0), 380.0)
                tp_bps = stop_bps * 1.8

                self.entry_bar = ctx.bar_index
                return ctx.signal(
                    "short",
                    confidence=0.80,
                    stop_loss_bps=stop_bps,
                    take_profit_bps=tp_bps,
                    metadata={
                        "reason": "bull_trap_failed_breakout_short",
                        "range_high": range_high,
                        "wick_extension": curr_high - range_high,
                        "min_wick_required": min_wick,
                        "atr": atr,
                        "rsi": rsi,
                        "stop_loss_bps": stop_bps,
                        "take_profit_bps": tp_bps,
                    },
                )

        # Bear trap (Failed downside breakdown -> Long)
        # Price swept below multi-period low with significant wick, closed firmly above
        if curr_low < range_low and (range_low - curr_low) >= min_wick and curr_close > range_low:
            if rsi <= 50.0:  # Must have had downward pressure prior to rejection
                stop_dist = max(curr_close - curr_low + (atr * 0.2), atr * 0.8)
                stop_bps = min(max((stop_dist / curr_close) * 10000.0, 200.0), 380.0)
                tp_bps = stop_bps * 1.8

                self.entry_bar = ctx.bar_index
                return ctx.signal(
                    "long",
                    confidence=0.80,
                    stop_loss_bps=stop_bps,
                    take_profit_bps=tp_bps,
                    metadata={
                        "reason": "bear_trap_failed_breakdown_long",
                        "range_low": range_low,
                        "wick_extension": range_low - curr_low,
                        "min_wick_required": min_wick,
                        "atr": atr,
                        "rsi": rsi,
                        "stop_loss_bps": stop_bps,
                        "take_profit_bps": tp_bps,
                    },
                )

        return None