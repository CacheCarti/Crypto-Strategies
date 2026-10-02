from domains.strategy_contract import Strategy, BarContext, Signal
from typing import Optional, Dict, Any
import math

class TrendPullbackEMA(Strategy):
    METADATA = {
        "name": "Trend Pullback EMA",
        "domain": "eth_usdc",
        "declared_sl_bps": 300.0,
        "declared_tp_bps": 650.0,
        "declared_hold_seconds": 36000,
        "warmup_bars": 70,
    }

    def initialize(self, ctx: BarContext) -> None:
        self.fast_period = 20
        self.slow_period = 50
        self.rsi_period = 14
        self.cooldown_bars = 10
        self.last_exit_bar = -100

    def _ema(self, values: list, period: int) -> Optional[float]:
        if len(values) < period:
            return None
        k = 2.0 / (period + 1)
        ema = sum(values[:period]) / period
        for v in values[period:]:
            ema = v * k + ema * (1 - k)
        return ema

    def _rsi(self, closes: list, period: int = 14) -> Optional[float]:
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

    def on_bar(self, ctx: BarContext) -> Optional[Signal]:
        history_len = self.slow_period + 20
        closes = ctx.closes(history_len)
        opens = ctx.opens(history_len)
        highs = ctx.highs(history_len)
        lows = ctx.lows(history_len)

        if len(closes) < history_len:
            return None

        # Filter out extreme crisis regimes
        market_regime = ctx.market.get("regime", "NORMAL")
        if ctx.regime == "crisis" or market_regime in ("CRISIS", "MELTDOWN"):
            if ctx.has_position():
                self.last_exit_bar = ctx.bar_index
                return ctx.signal(
                    "flat",
                    confidence=0.9,
                    metadata={"reason": "crisis_regime_emergency_exit", "regime": ctx.regime}
                )
            return None

        # Calculate Indicators
        ema20_curr = self._ema(closes, self.fast_period)
        ema50_curr = self._ema(closes, self.slow_period)
        ema50_past = self._ema(closes[:-5], self.slow_period)
        rsi_curr = self._rsi(closes, self.rsi_period)

        if ema20_curr is None or ema50_curr is None or ema50_past is None or rsi_curr is None:
            return None

        curr_close = closes[-1]
        curr_open = opens[-1]
        prev_close = closes[-2]
        prev_low = lows[-2]
        prev_high = highs[-2]

        has_pos = ctx.has_position()
        pos_dir = ctx.position_direction()

        # 1. Position Management & Confirmed Exits
        if has_pos:
            # Long Exit: Require confirmed 2-bar close below EMA20 to avoid premature noise whip-saws
            if pos_dir == "long":
                if curr_close < ema20_curr and prev_close < ema20_curr:
                    self.last_exit_bar = ctx.bar_index
                    return ctx.signal(
                        "flat",
                        confidence=0.75,
                        metadata={
                            "reason": "confirmed_ema20_bearish_cross_exit",
                            "close": curr_close,
                            "ema20": ema20_curr,
                            "rsi": rsi_curr
                        }
                    )
            # Short Exit: Require confirmed 2-bar close above EMA20
            elif pos_dir == "short":
                if curr_close > ema20_curr and prev_close > ema20_curr:
                    self.last_exit_bar = ctx.bar_index
                    return ctx.signal(
                        "flat",
                        confidence=0.75,
                        metadata={
                            "reason": "confirmed_ema20_bullish_cross_exit",
                            "close": curr_close,
                            "ema20": ema20_curr,
                            "rsi": rsi_curr
                        }
                    )
            return None

        # 2. Hard Multi-Bar Cooldown Guard
        if (ctx.bar_index - self.last_exit_bar) < self.cooldown_bars:
            return None

        # 3. Macro Trend Identification with Slope Confirmation
        uptrend = (ema20_curr > ema50_curr) and (ema50_curr > ema50_past) and (curr_close > ema50_curr)
        downtrend = (ema20_curr < ema50_curr) and (ema50_curr < ema50_past) and (curr_close < ema50_curr)

        # 4. Strict Pullback & Reversal Triggers
        # Long Setup:
        # - Uptrend intact
        # - Previous bar dipped below or tested EMA20 (low <= EMA20)
        # - Current bar closes bullish (close > open) and firmly back above EMA20
        # - RSI in healthy pullback zone (38 <= RSI <= 58)
        long_pullback = (prev_low <= ema20_curr) and (curr_close > ema20_curr) and (curr_close > curr_open)
        long_rsi_valid = 38.0 <= rsi_curr <= 58.0

        if uptrend and long_pullback and long_rsi_valid:
            self.last_exit_bar = ctx.bar_index
            return ctx.signal(
                "long",
                confidence=0.85,
                stop_loss_bps=self.METADATA["declared_sl_bps"],
                take_profit_bps=self.METADATA["declared_tp_bps"],
                horizon_seconds=self.METADATA["declared_hold_seconds"],
                metadata={
                    "reason": "uptrend_ema20_pullback_rebound",
                    "close": curr_close,
                    "prev_low": prev_low,
                    "ema20": ema20_curr,
                    "ema50": ema50_curr,
                    "rsi": rsi_curr
                }
            )

        # Short Setup:
        # - Downtrend intact
        # - Previous bar probed above or tested EMA20 (high >= EMA20)
        # - Current bar closes bearish (close < open) and firmly back below EMA20
        # - RSI in healthy rejection zone (42 <= RSI <= 62)
        short_pullback = (prev_high >= ema20_curr) and (curr_close < ema20_curr) and (curr_close < curr_open)
        short_rsi_valid = 42.0 <= rsi_curr <= 62.0

        if downtrend and short_pullback and short_rsi_valid:
            self.last_exit_bar = ctx.bar_index
            return ctx.signal(
                "short",
                confidence=0.85,
                stop_loss_bps=self.METADATA["declared_sl_bps"],
                take_profit_bps=self.METADATA["declared_tp_bps"],
                horizon_seconds=self.METADATA["declared_hold_seconds"],
                metadata={
                    "reason": "downtrend_ema20_pullback_rejection",
                    "close": curr_close,
                    "prev_high": prev_high,
                    "ema20": ema20_curr,
                    "ema50": ema50_curr,
                    "rsi": rsi_curr
                }
            )

        return None