from domains.strategy_contract import Strategy, BarContext, Signal
from typing import Optional, Dict, Any
import math


class TrendPullbackReclaim(Strategy):
    METADATA = {
        "name": "Trend Pullback Reclaim",
        "domain": "eth_usdc",
        "declared_sl_bps": 280.0,
        "declared_tp_bps": 560.0,
        "declared_hold_seconds": 28800,
        "warmup_bars": 70,
    }

    def initialize(self, ctx: BarContext) -> None:
        self.fast_period = 21
        self.slow_period = 55
        self.rsi_period = 14
        self.cooldown_bars = 12
        self.last_exit_bar = -999
        self.entry_bar = -999

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
        self.entry_bar = -999

    def on_bar(self, ctx: BarContext) -> Optional[Signal]:
        closes = ctx.closes(self.slow_period + 10)
        opens = ctx.opens(self.slow_period + 10)

        if len(closes) < self.slow_period + 2:
            return None

        # Filter out extreme crisis regimes to prevent erratic whipsaws
        market_regime = ctx.market.get("regime", "NORMAL")
        crisis_score = ctx.market.get("crisis_score", 0.0)
        if market_regime in ("CRISIS", "MELTDOWN") or crisis_score > 0.5 or ctx.regime == "crisis":
            return None

        ema_fast_curr = self._ema(closes, self.fast_period)
        ema_slow_curr = self._ema(closes, self.slow_period)
        ema_fast_prev = self._ema(closes[:-1], self.fast_period)
        rsi_curr = self._rsi(closes, self.rsi_period)

        if (
            ema_fast_curr is None
            or ema_slow_curr is None
            or ema_fast_prev is None
            or rsi_curr is None
        ):
            return None

        curr_close = closes[-1]
        curr_open = opens[-1]
        prev_close = closes[-2]

        has_pos = ctx.has_position()
        pos_dir = ctx.position_direction()

        # Exit logic when in position (with buffer and minimum hold to avoid 1-bar whipsaws)
        if has_pos:
            bars_held = ctx.bar_index - self.entry_bar if self.entry_bar > 0 else 5

            if pos_dir == "long":
                # Exit when closing decisively below fast EMA (buffer of 15 bps)
                if bars_held >= 2 and curr_close < (ema_fast_curr * 0.9985):
                    self.last_exit_bar = ctx.bar_index
                    return ctx.signal(
                        "flat",
                        confidence=0.75,
                        metadata={
                            "reason": "long_exit_decisive_close_below_fast_ema",
                            "close": curr_close,
                            "ema_fast": round(ema_fast_curr, 2),
                            "ema_slow": round(ema_slow_curr, 2),
                            "rsi": round(rsi_curr, 2),
                        },
                    )
            elif pos_dir == "short":
                # Exit when closing decisively above fast EMA (buffer of 15 bps)
                if bars_held >= 2 and curr_close > (ema_fast_curr * 1.0015):
                    self.last_exit_bar = ctx.bar_index
                    return ctx.signal(
                        "flat",
                        confidence=0.75,
                        metadata={
                            "reason": "short_exit_decisive_close_above_fast_ema",
                            "close": curr_close,
                            "ema_fast": round(ema_fast_curr, 2),
                            "ema_slow": round(ema_slow_curr, 2),
                            "rsi": round(rsi_curr, 2),
                        },
                    )
            return None

        # Hard cooldown guard after trade exit
        if ctx.bar_index - self.last_exit_bar < self.cooldown_bars:
            return None

        # Long Setup:
        # 1. Clear uptrend: fast EMA clearly above slow EMA (at least 15 bps separation)
        # 2. Meaningful pullback: previous bar closed below fast EMA
        # 3. Decisive reclaim: current bar is bullish and closed back above fast EMA
        # 4. RSI in healthy continuation zone (42 to 62)
        uptrend = (ema_fast_curr > ema_slow_curr * 1.0015) and (curr_close > ema_slow_curr)
        pullback_reclaim_long = (prev_close < ema_fast_prev) and (curr_close > ema_fast_curr) and (curr_close > curr_open)
        rsi_valid_long = 42.0 <= rsi_curr <= 62.0

        if uptrend and pullback_reclaim_long and rsi_valid_long:
            self.entry_bar = ctx.bar_index
            trend_dist_bps = ((curr_close - ema_slow_curr) / ema_slow_curr) * 10000.0
            confidence = min(0.85, max(0.60, 0.60 + (trend_dist_bps / 4000.0)))
            return ctx.signal(
                "long",
                confidence=round(confidence, 2),
                stop_loss_bps=280.0,
                take_profit_bps=560.0,
                horizon_seconds=28800,
                metadata={
                    "reason": "uptrend_decisive_pullback_reclaim",
                    "close": curr_close,
                    "ema_fast": round(ema_fast_curr, 2),
                    "ema_slow": round(ema_slow_curr, 2),
                    "rsi": round(rsi_curr, 2),
                    "prev_close": prev_close,
                },
            )

        # Short Setup:
        # 1. Clear downtrend: fast EMA clearly below slow EMA (at least 15 bps separation)
        # 2. Meaningful pullback: previous bar closed above fast EMA
        # 3. Decisive rejection: current bar is bearish and closed back below fast EMA
        # 4. RSI in healthy continuation zone (38 to 58)
        downtrend = (ema_fast_curr < ema_slow_curr * 0.9985) and (curr_close < ema_slow_curr)
        pullback_rejection_short = (prev_close > ema_fast_prev) and (curr_close < ema_fast_curr) and (curr_close < curr_open)
        rsi_valid_short = 38.0 <= rsi_curr <= 58.0

        if downtrend and pullback_rejection_short and rsi_valid_short:
            self.entry_bar = ctx.bar_index
            trend_dist_bps = ((ema_slow_curr - curr_close) / ema_slow_curr) * 10000.0
            confidence = min(0.85, max(0.60, 0.60 + (trend_dist_bps / 4000.0)))
            return ctx.signal(
                "short",
                confidence=round(confidence, 2),
                stop_loss_bps=280.0,
                take_profit_bps=560.0,
                horizon_seconds=28800,
                metadata={
                    "reason": "downtrend_decisive_pullback_rejection",
                    "close": curr_close,
                    "ema_fast": round(ema_fast_curr, 2),
                    "ema_slow": round(ema_slow_curr, 2),
                    "rsi": round(rsi_curr, 2),
                    "prev_close": prev_close,
                },
            )

        return None