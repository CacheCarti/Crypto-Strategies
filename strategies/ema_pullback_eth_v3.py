from domains.strategy_contract import Strategy, BarContext, Signal
from typing import Optional, Dict, Any
import math


class EmaPullbackReclaim(Strategy):
    METADATA = {
        "name": "EMA Pullback Reclaim",
        "domain": "eth_usdc",
        "declared_sl_bps": 320.0,
        "declared_tp_bps": 650.0,
        "declared_hold_seconds": 43200,  # ~12 hours intended hold
        "warmup_bars": 70,
    }

    def initialize(self, ctx: BarContext) -> None:
        self.fast_period = 20
        self.slow_period = 60
        self.rsi_period = 14
        self.cooldown_bars = 10
        self.last_exit_bar = -100

    def _ema(self, values: list, period: int) -> Optional[float]:
        if len(values) < period:
            return None
        k = 2.0 / (period + 1)
        ema = sum(values[:period]) / period
        for v in values[period:]:
            ema = v * k + ema * (1.0 - k)
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
        closes = ctx.closes(self.slow_period + 15)
        highs = ctx.highs(self.slow_period + 15)
        lows = ctx.lows(self.slow_period + 15)

        if len(closes) < self.slow_period + 5:
            return None

        fast_ema = self._ema(closes, self.fast_period)
        slow_ema = self._ema(closes, self.slow_period)
        rsi = self._rsi(closes, self.rsi_period)

        if fast_ema is None or slow_ema is None or rsi is None:
            return None

        curr_close = closes[-1]
        prev_close = closes[-2]
        curr_low = lows[-1]
        curr_high = highs[-1]

        # Manage existing open position exits
        if ctx.has_position():
            pos_dir = ctx.position_direction()
            # Clear trend breakdown exit with a buffer to avoid micro-whipsaws
            if pos_dir == "long" and curr_close < fast_ema * 0.990:
                return ctx.signal(
                    "flat",
                    confidence=0.7,
                    metadata={
                        "reason": "long_trend_break_exit",
                        "close": curr_close,
                        "fast_ema": round(fast_ema, 2),
                        "slow_ema": round(slow_ema, 2),
                        "rsi": round(rsi, 2),
                    },
                )
            elif pos_dir == "short" and curr_close > fast_ema * 1.010:
                return ctx.signal(
                    "flat",
                    confidence=0.7,
                    metadata={
                        "reason": "short_trend_break_exit",
                        "close": curr_close,
                        "fast_ema": round(fast_ema, 2),
                        "slow_ema": round(slow_ema, 2),
                        "rsi": round(rsi, 2),
                    },
                )
            return None

        # Hard multi-bar cooldown after exit to eliminate overtrading
        if ctx.bar_index - self.last_exit_bar < self.cooldown_bars:
            return None

        # Skip high-volatility/crisis breakdown regimes
        regime = ctx.market.get("regime", "NORMAL")
        if regime in ["CRISIS", "MELTDOWN"]:
            return None

        # Long Setup:
        # 1. Distinct bullish trend separation: fast EMA noticeably above slow EMA
        # 2. Strict pullback & reclaim: previous bar closed at/below fast EMA (or dipped well below),
        #    and current bar confirms reclaim by closing clearly above fast EMA
        # 3. RSI in constructive pullback zone (not overbought, healthy momentum)
        is_uptrend = fast_ema > slow_ema * 1.004 and curr_close > slow_ema
        long_reclaim = (
            is_uptrend
            and (prev_close <= fast_ema * 1.001 or lows[-2] <= fast_ema * 0.995)
            and curr_close > fast_ema * 1.002
            and (42.0 <= rsi <= 64.0)
        )

        if long_reclaim:
            trend_gap = (fast_ema - slow_ema) / slow_ema
            confidence = min(0.85, max(0.60, 0.65 + trend_gap * 15.0))
            return ctx.signal(
                "long",
                confidence=confidence,
                stop_loss_bps=self.METADATA["declared_sl_bps"],
                take_profit_bps=self.METADATA["declared_tp_bps"],
                horizon_seconds=self.METADATA["declared_hold_seconds"],
                metadata={
                    "reason": "uptrend_ema_pullback_reclaim",
                    "close": curr_close,
                    "fast_ema": round(fast_ema, 2),
                    "slow_ema": round(slow_ema, 2),
                    "rsi": round(rsi, 2),
                    "trend_gap_bps": round(trend_gap * 10000, 1),
                },
            )

        # Short Setup:
        # 1. Distinct bearish trend separation: fast EMA noticeably below slow EMA
        # 2. Strict rally & rejection: previous bar closed at/above fast EMA (or spiked well above),
        #    and current bar confirms rejection by closing clearly below fast EMA
        # 3. RSI in constructive pullback zone (not oversold, healthy downward momentum)
        is_downtrend = fast_ema < slow_ema * 0.996 and curr_close < slow_ema
        short_rejection = (
            is_downtrend
            and (prev_close >= fast_ema * 0.999 or highs[-2] >= fast_ema * 1.005)
            and curr_close < fast_ema * 0.998
            and (36.0 <= rsi <= 58.0)
        )

        if short_rejection:
            trend_gap = (slow_ema - fast_ema) / slow_ema
            confidence = min(0.85, max(0.60, 0.65 + trend_gap * 15.0))
            return ctx.signal(
                "short",
                confidence=confidence,
                stop_loss_bps=self.METADATA["declared_sl_bps"],
                take_profit_bps=self.METADATA["declared_tp_bps"],
                horizon_seconds=self.METADATA["declared_hold_seconds"],
                metadata={
                    "reason": "downtrend_ema_pullback_rejection",
                    "close": curr_close,
                    "fast_ema": round(fast_ema, 2),
                    "slow_ema": round(slow_ema, 2),
                    "rsi": round(rsi, 2),
                    "trend_gap_bps": round(trend_gap * 10000, 1),
                },
            )

        return None