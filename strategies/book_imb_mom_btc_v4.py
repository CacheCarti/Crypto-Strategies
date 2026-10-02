from domains.strategy_contract import Strategy, BarContext, Signal
from typing import Optional, Dict, Any
import math

class OrderFlowMomentumBreakout(Strategy):
    METADATA = {
        "name": "OrderFlowMomentumBreakout",
        "domain": "btc_usdc",
        "declared_sl_bps": 300.0,
        "declared_tp_bps": 600.0,
        "declared_hold_seconds": 28800,
        "warmup_bars": 55,
        "required_features": ["book_imbalance_btcusdt"]
    }

    def initialize(self, ctx: BarContext) -> None:
        self.breakout_period = 24
        self.trend_ema_period = 50
        self.cooldown_period = 14
        self.last_exit_bar = -999
        self.entry_bar = -999
        self.min_imbalance = 0.15

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
        closes = ctx.closes(self.trend_ema_period + 10)
        highs = ctx.highs(self.breakout_period + 5)
        lows = ctx.lows(self.breakout_period + 5)
        volumes = ctx.volumes(20)

        if (
            len(closes) < self.trend_ema_period + 1
            or len(highs) < self.breakout_period + 2
            or len(volumes) < 20
        ):
            return None

        current_close = ctx.bar.close
        trend_ema = self._ema(closes, self.trend_ema_period)
        rsi = self._rsi(closes, 14)
        avg_vol = sum(volumes) / len(volumes)

        if trend_ema is None or rsi is None or avg_vol == 0:
            return None

        # 24-bar high/low channel excluding current bar
        prior_highs = highs[-(self.breakout_period + 1):-1]
        prior_lows = lows[-(self.breakout_period + 1):-1]
        highest_high = max(prior_highs)
        lowest_low = min(prior_lows)

        imbalance = ctx.features.get("book_imbalance_btcusdt", 0.0)

        # 1. Manage Active Positions (Let winners run; exit on trend break or exhaustion)
        if ctx.has_position():
            pos_dir = ctx.position_direction()
            bars_held = ctx.bar_index - self.entry_bar

            if pos_dir == "long":
                if current_close < trend_ema or (bars_held >= 6 and rsi > 78):
                    self.last_exit_bar = ctx.bar_index
                    return ctx.signal(
                        "flat",
                        confidence=0.75,
                        metadata={
                            "reason": "long_trend_break_or_rsi_overbought",
                            "close": current_close,
                            "trend_ema": trend_ema,
                            "rsi": rsi,
                            "bars_held": bars_held
                        }
                    )

            elif pos_dir == "short":
                if current_close > trend_ema or (bars_held >= 6 and rsi < 22):
                    self.last_exit_bar = ctx.bar_index
                    return ctx.signal(
                        "flat",
                        confidence=0.75,
                        metadata={
                            "reason": "short_trend_break_or_rsi_oversold",
                            "close": current_close,
                            "trend_ema": trend_ema,
                            "rsi": rsi,
                            "bars_held": bars_held
                        }
                    )

            return None

        # 2. Hard Multi-Bar Cooldown Filter
        if (ctx.bar_index - self.last_exit_bar) < self.cooldown_period:
            return None

        # 3. High-Conviction Entries (Breakout + Trend Alignment + Order Flow + Volume Confirmation)
        vol_confirmed = ctx.bar.volume >= avg_vol * 1.1

        # Long: 24h Breakout above Trend EMA with Bullish Imbalance & Volume
        if (
            current_close > highest_high
            and current_close > trend_ema
            and imbalance >= self.min_imbalance
            and vol_confirmed
            and 52.0 <= rsi <= 72.0
        ):
            self.entry_bar = ctx.bar_index
            conf = min(0.9, 0.65 + min(abs(imbalance), 0.5) * 0.4)
            return ctx.signal(
                "long",
                confidence=conf,
                stop_loss_bps=self.METADATA["declared_sl_bps"],
                take_profit_bps=self.METADATA["declared_tp_bps"],
                horizon_seconds=self.METADATA["declared_hold_seconds"],
                metadata={
                    "reason": "long_high_conviction_breakout",
                    "close": current_close,
                    "highest_high": highest_high,
                    "trend_ema": trend_ema,
                    "imbalance": imbalance,
                    "rsi": rsi,
                    "vol_ratio": ctx.bar.volume / avg_vol
                }
            )

        # Short: 24h Breakdown below Trend EMA with Bearish Imbalance & Volume
        if (
            current_close < lowest_low
            and current_close < trend_ema
            and imbalance <= -self.min_imbalance
            and vol_confirmed
            and 28.0 <= rsi <= 48.0
        ):
            self.entry_bar = ctx.bar_index
            conf = min(0.9, 0.65 + min(abs(imbalance), 0.5) * 0.4)
            return ctx.signal(
                "short",
                confidence=conf,
                stop_loss_bps=self.METADATA["declared_sl_bps"],
                take_profit_bps=self.METADATA["declared_tp_bps"],
                horizon_seconds=self.METADATA["declared_hold_seconds"],
                metadata={
                    "reason": "short_high_conviction_breakdown",
                    "close": current_close,
                    "lowest_low": lowest_low,
                    "trend_ema": trend_ema,
                    "imbalance": imbalance,
                    "rsi": rsi,
                    "vol_ratio": ctx.bar.volume / avg_vol
                }
            )

        return None