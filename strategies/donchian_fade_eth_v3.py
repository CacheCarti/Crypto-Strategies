from domains.strategy_contract import Strategy, BarContext, Signal
from typing import Optional, Dict, Any
import math

class FailedBreakoutFade(Strategy):
    METADATA = {
        "name": "FailedBreakoutFade",
        "domain": "eth_usdc",
        "declared_sl_bps": 260.0,
        "declared_tp_bps": 480.0,
        "declared_hold_seconds": 21600,
        "warmup_bars": 50,
        "required_features": ["funding_rate_ethusdt"],
    }

    def initialize(self, ctx: BarContext) -> None:
        self.lookback = 36
        self.atr_period = 14
        self.cooldown_bars = 10
        self.max_hold_bars = 8
        self.min_wick_atr_mult = 0.45
        self.min_wick_ratio = 0.50
        self.bars_in_position = 0
        self.last_exit_bar = -999

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
        if avg_loss == 0.0:
            return 100.0
        rs = avg_gain / avg_loss
        return 100.0 - (100.0 / (1.0 + rs))

    def on_position_close(self, ctx: BarContext, position: Dict[str, Any]) -> None:
        self.last_exit_bar = ctx.bar_index
        self.bars_in_position = 0

    def on_bar(self, ctx: BarContext) -> Optional[Signal]:
        closes = ctx.closes(self.lookback + self.atr_period + 2)
        highs = ctx.highs(self.lookback + self.atr_period + 2)
        lows = ctx.lows(self.lookback + self.atr_period + 2)

        if len(closes) < self.lookback + self.atr_period + 1:
            return None

        # Position duration management
        if ctx.has_position():
            self.bars_in_position += 1
            if self.bars_in_position >= self.max_hold_bars:
                self.last_exit_bar = ctx.bar_index
                return ctx.signal(
                    "flat",
                    confidence=0.55,
                    metadata={
                        "reason": "max_hold_timeout_exit",
                        "bars_held": self.bars_in_position,
                        "close": ctx.bar.close
                    }
                )
            return None
        else:
            self.bars_in_position = 0

        # Enforce multi-bar post-exit cooldown
        if ctx.bar_index - self.last_exit_bar < self.cooldown_bars:
            return None

        # Filter out high-stress market conditions
        crisis_score = ctx.market.get("crisis_score", 0.0)
        if crisis_score > 0.60 or ctx.regime == "crisis":
            return None

        atr = self._atr(highs, lows, closes, self.atr_period)
        rsi = self._rsi(closes, self.atr_period)
        if atr is None or rsi is None or atr <= 0.0:
            return None

        # Robust reference range (excluding the current bar)
        prior_highs = highs[-(self.lookback + 1):-1]
        prior_lows = lows[-(self.lookback + 1):-1]
        range_high = max(prior_highs)
        range_low = min(prior_lows)

        current_high = ctx.bar.high
        current_low = ctx.bar.low
        current_close = ctx.bar.close
        current_open = ctx.bar.open
        candle_range = max(current_high - current_low, 1e-6)

        funding_rate = ctx.features.get("funding_rate_ethusdt", 0.0)

        # Bull Trap Fade: Clear poke above 36-bar high, strong upper rejection wick, close back inside
        high_poke = current_high - range_high
        upper_wick = current_high - max(current_open, current_close)
        upper_wick_ratio = upper_wick / candle_range

        if (
            high_poke >= self.min_wick_atr_mult * atr
            and current_close < range_high
            and upper_wick_ratio >= self.min_wick_ratio
            and rsi >= 54.0
            and funding_rate >= -0.0002
        ):
            stop_dist = max(current_high - current_close, atr * 0.8)
            stop_bps = min(max((stop_dist / current_close) * 10000.0 * 1.1, 190.0), 320.0)
            tp_bps = stop_bps * 1.75

            return ctx.signal(
                "short",
                confidence=0.75,
                stop_loss_bps=stop_bps,
                take_profit_bps=tp_bps,
                metadata={
                    "reason": "bull_trap_liquidity_sweep_short",
                    "range_high": range_high,
                    "high_poke": high_poke,
                    "atr": atr,
                    "upper_wick_ratio": upper_wick_ratio,
                    "rsi": rsi,
                    "funding_rate": funding_rate
                }
            )

        # Bear Trap Fade: Clear poke below 36-bar low, strong lower rejection wick, close back inside
        low_poke = range_low - current_low
        lower_wick = min(current_open, current_close) - current_low
        lower_wick_ratio = lower_wick / candle_range

        if (
            low_poke >= self.min_wick_atr_mult * atr
            and current_close > range_low
            and lower_wick_ratio >= self.min_wick_ratio
            and rsi <= 46.0
            and funding_rate <= 0.0002
        ):
            stop_dist = max(current_close - current_low, atr * 0.8)
            stop_bps = min(max((stop_dist / current_close) * 10000.0 * 1.1, 190.0), 320.0)
            tp_bps = stop_bps * 1.75

            return ctx.signal(
                "long",
                confidence=0.75,
                stop_loss_bps=stop_bps,
                take_profit_bps=tp_bps,
                metadata={
                    "reason": "bear_trap_liquidity_sweep_long",
                    "range_low": range_low,
                    "low_poke": low_poke,
                    "atr": atr,
                    "lower_wick_ratio": lower_wick_ratio,
                    "rsi": rsi,
                    "funding_rate": funding_rate
                }
            )

        return None