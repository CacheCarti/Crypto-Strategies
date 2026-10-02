from domains.strategy_contract import Strategy, BarContext, Signal
from typing import Optional, Dict, Any
import math


class ScalpPullbackTrend(Strategy):
    METADATA = {
        "name": "ScalpPullbackTrend",
        "domain": "eth_usdc_scalp",
        "declared_sl_bps": 95.0,
        "declared_tp_bps": 180.0,
        "declared_hold_seconds": 1800,
        "warmup_bars": 130,
    }

    def initialize(self, ctx: BarContext) -> None:
        self.fast_period = 9
        self.slow_period = 36
        self.trend_period = 120
        self.rsi_period = 14
        self.cooldown_bars = 48
        self.max_hold_bars = 10
        self.last_trade_bar = -999
        self.entry_bar = 0

    def _ema(self, values, period: int) -> Optional[float]:
        if len(values) < period:
            return None
        k = 2.0 / (period + 1)
        ema = sum(values[:period]) / period
        for v in values[period:]:
            ema = v * k + ema * (1 - k)
        return ema

    def _rsi(self, closes, period: int = 14) -> Optional[float]:
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
        self.last_trade_bar = ctx.bar_index

    def on_bar(self, ctx: BarContext) -> Optional[Signal]:
        if ctx.bar_index < self.METADATA["warmup_bars"]:
            return None

        closes = ctx.closes(self.trend_period + 10)
        highs = ctx.highs(self.trend_period + 10)
        lows = ctx.lows(self.trend_period + 10)
        opens = ctx.opens(self.trend_period + 10)

        if not closes or len(closes) < self.trend_period:
            return None

        fast_ema = self._ema(closes, self.fast_period)
        slow_ema = self._ema(closes, self.slow_period)
        trend_ema = self._ema(closes, self.trend_period)
        rsi = self._rsi(closes, self.rsi_period)

        if fast_ema is None or slow_ema is None or trend_ema is None or rsi is None:
            return None

        current_close = ctx.bar.close
        current_open = ctx.bar.open
        current_high = ctx.bar.high
        current_low = ctx.bar.low

        # Handle active position management
        if ctx.has_position():
            bars_held = ctx.bar_index - self.entry_bar
            direction = ctx.position_direction()

            if direction == "long":
                recent_swing_high = max(highs[-7:-1]) if len(highs) >= 7 else current_close
                if current_high >= recent_swing_high and current_close > fast_ema:
                    self.last_trade_bar = ctx.bar_index
                    return ctx.signal(
                        "flat",
                        confidence=0.75,
                        metadata={
                            "reason": "long_swing_target_hit",
                            "price": current_close,
                            "swing_high": recent_swing_high,
                            "bars_held": bars_held,
                        },
                    )
                if bars_held >= self.max_hold_bars:
                    self.last_trade_bar = ctx.bar_index
                    return ctx.signal(
                        "flat",
                        confidence=0.5,
                        metadata={
                            "reason": "long_max_hold_exit",
                            "price": current_close,
                            "bars_held": bars_held,
                        },
                    )

            elif direction == "short":
                recent_swing_low = min(lows[-7:-1]) if len(lows) >= 7 else current_close
                if current_low <= recent_swing_low and current_close < fast_ema:
                    self.last_trade_bar = ctx.bar_index
                    return ctx.signal(
                        "flat",
                        confidence=0.75,
                        metadata={
                            "reason": "short_swing_target_hit",
                            "price": current_close,
                            "swing_low": recent_swing_low,
                            "bars_held": bars_held,
                        },
                    )
                if bars_held >= self.max_hold_bars:
                    self.last_trade_bar = ctx.bar_index
                    return ctx.signal(
                        "flat",
                        confidence=0.5,
                        metadata={
                            "reason": "short_max_hold_exit",
                            "price": current_close,
                            "bars_held": bars_held,
                        },
                    )

            return None

        # Mandatory multi-bar cooldown between trades
        if (ctx.bar_index - self.last_trade_bar) < self.cooldown_bars:
            return None

        # Regime & Crisis Filter
        regime = ctx.market.get("regime", "NORMAL")
        if regime in ("CRISIS", "MELTDOWN"):
            return None

        crisis_score = ctx.market.get("crisis_score", 0.0)
        if crisis_score > 0.4:
            return None

        # Long Setup: Clear macro & micro uptrend, pullback dipped into fast EMA, bullish reversal bar
        uptrend = (fast_ema > slow_ema * 1.0008) and (slow_ema > trend_ema) and (current_close > trend_ema)
        dipped_prior = lows[-2] <= fast_ema or current_low <= fast_ema
        bullish_bounce = current_close > fast_ema and current_close > current_open
        pullback_rsi_bull = 38.0 <= rsi <= 52.0

        if uptrend and dipped_prior and bullish_bounce and pullback_rsi_bull:
            self.entry_bar = ctx.bar_index
            self.last_trade_bar = ctx.bar_index
            return ctx.signal(
                "long",
                confidence=0.8,
                stop_loss_bps=self.METADATA["declared_sl_bps"],
                take_profit_bps=self.METADATA["declared_tp_bps"],
                horizon_seconds=self.METADATA["declared_hold_seconds"],
                metadata={
                    "reason": "macro_aligned_ema_pullback_bounce",
                    "price": current_close,
                    "fast_ema": round(fast_ema, 2),
                    "slow_ema": round(slow_ema, 2),
                    "trend_ema": round(trend_ema, 2),
                    "rsi": round(rsi, 2),
                },
            )

        # Short Setup: Clear macro & micro downtrend, pullback dipped into fast EMA, bearish reversal bar
        downtrend = (fast_ema < slow_ema * 0.9992) and (slow_ema < trend_ema) and (current_close < trend_ema)
        popped_prior = highs[-2] >= fast_ema or current_high >= fast_ema
        bearish_rejection = current_close < fast_ema and current_close < current_open
        pullback_rsi_bear = 48.0 <= rsi <= 62.0

        if downtrend and popped_prior and bearish_rejection and pullback_rsi_bear:
            self.entry_bar = ctx.bar_index
            self.last_trade_bar = ctx.bar_index
            return ctx.signal(
                "short",
                confidence=0.8,
                stop_loss_bps=self.METADATA["declared_sl_bps"],
                take_profit_bps=self.METADATA["declared_tp_bps"],
                horizon_seconds=self.METADATA["declared_hold_seconds"],
                metadata={
                    "reason": "macro_aligned_ema_pullback_reject",
                    "price": current_close,
                    "fast_ema": round(fast_ema, 2),
                    "slow_ema": round(slow_ema, 2),
                    "trend_ema": round(trend_ema, 2),
                    "rsi": round(rsi, 2),
                },
            )

        return None