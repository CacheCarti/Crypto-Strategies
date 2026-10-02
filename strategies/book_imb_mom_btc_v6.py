from domains.strategy_contract import Strategy, BarContext, Signal
from typing import Optional, Dict, Any
import math


class BtcOrderFlowMomentum(Strategy):
    METADATA = {
        "name": "BtcOrderFlowMomentum",
        "domain": "btc_usdc",
        "declared_sl_bps": 320.0,
        "declared_tp_bps": 650.0,
        "declared_hold_seconds": 28800,  # 8 hours average hold
        "warmup_bars": 55,
        "required_features": ["book_imbalance_btcusdt"],
    }

    def initialize(self, ctx: BarContext) -> None:
        self.breakout_period = 24       # 24-hour channel breakout
        self.exit_period = 12           # 12-hour trailing channel for exit
        self.rsi_period = 14
        self.trend_ema_period = 50      # Trend regime baseline
        self.imbalance_threshold = 0.08  # Stricter orderbook imbalance filter
        self.cooldown_bars = 14         # Hard multi-bar cooldown after exit
        self.last_exit_bar = -999

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

    def _ema(self, values, period):
        if len(values) < period:
            return None
        k = 2.0 / (period + 1)
        ema = sum(values[:period]) / period
        for v in values[period:]:
            ema = v * k + ema * (1.0 - k)
        return ema

    def on_bar(self, ctx: BarContext) -> Optional[Signal]:
        closes = ctx.closes(self.warmup_bars + 5)
        highs = ctx.highs(self.warmup_bars + 5)
        lows = ctx.lows(self.warmup_bars + 5)

        if len(closes) < self.METADATA["warmup_bars"]:
            return None

        current_close = closes[-1]
        imbalance = ctx.features.get("book_imbalance_btcusdt", 0.0)
        rsi_val = self._rsi(closes, self.rsi_period) or 50.0
        trend_ema = self._ema(closes, self.trend_ema_period) or current_close
        regime = ctx.market.get("regime", "NORMAL")

        # 1. Position Management & Momentum Stall Exit Logic
        if ctx.has_position():
            pos_dir = ctx.position_direction()
            exit_signal = False
            exit_reason = ""

            if pos_dir == "long":
                # Exit only on a distinct structural reversal or sharp loss of momentum
                exit_low = min(lows[-(self.exit_period + 1):-1])
                if current_close < exit_low:
                    exit_signal = True
                    exit_reason = "momentum_stall_lower_channel_breakdown"
                elif rsi_val < 42.0 and current_close < trend_ema:
                    exit_signal = True
                    exit_reason = "momentum_stall_rsi_and_trend_loss"
            elif pos_dir == "short":
                exit_high = max(highs[-(self.exit_period + 1):-1])
                if current_close > exit_high:
                    exit_signal = True
                    exit_reason = "momentum_stall_upper_channel_breakout"
                elif rsi_val > 58.0 and current_close > trend_ema:
                    exit_signal = True
                    exit_reason = "momentum_stall_rsi_and_trend_gain"

            if exit_signal:
                self.last_exit_bar = ctx.bar_index
                return ctx.signal(
                    "flat",
                    confidence=0.65,
                    metadata={
                        "reason": exit_reason,
                        "price": current_close,
                        "rsi": round(rsi_val, 2),
                        "imbalance": round(imbalance, 4),
                        "trend_ema": round(trend_ema, 2),
                    },
                )
            return None

        # 2. Strict Cooldown Gate to Prevent Friction Suicide
        if (ctx.bar_index - self.last_exit_bar) < self.cooldown_bars:
            return None

        # 3. Market Stress Filter
        if regime in ("CRISIS", "MELTDOWN"):
            return None

        # 4. Filtered Breakout Entry Signals
        prev_high = max(highs[-(self.breakout_period + 1):-1])
        prev_low = min(lows[-(self.breakout_period + 1):-1])

        # Long: 24h high breakout + price above EMA50 + supportive RSI + strong positive book imbalance
        if (
            current_close > prev_high
            and current_close > trend_ema
            and 52.0 <= rsi_val <= 75.0
            and imbalance > self.imbalance_threshold
        ):
            confidence = min(0.85, 0.55 + min(abs(imbalance), 0.5) * 0.6)
            return ctx.signal(
                "long",
                confidence=confidence,
                stop_loss_bps=self.METADATA["declared_sl_bps"],
                take_profit_bps=self.METADATA["declared_tp_bps"],
                horizon_seconds=self.METADATA["declared_hold_seconds"],
                metadata={
                    "reason": "confirmed_bullish_breakout_orderflow",
                    "price": current_close,
                    "breakout_level": round(prev_high, 2),
                    "imbalance": round(imbalance, 4),
                    "rsi": round(rsi_val, 2),
                    "trend_ema": round(trend_ema, 2),
                },
            )

        # Short: 24h low breakdown + price below EMA50 + supportive RSI + strong negative book imbalance
        if (
            current_close < prev_low
            and current_close < trend_ema
            and 25.0 <= rsi_val <= 48.0
            and imbalance < -self.imbalance_threshold
        ):
            confidence = min(0.85, 0.55 + min(abs(imbalance), 0.5) * 0.6)
            return ctx.signal(
                "short",
                confidence=confidence,
                stop_loss_bps=self.METADATA["declared_sl_bps"],
                take_profit_bps=self.METADATA["declared_tp_bps"],
                horizon_seconds=self.METADATA["declared_hold_seconds"],
                metadata={
                    "reason": "confirmed_bearish_breakdown_orderflow",
                    "price": current_close,
                    "breakout_level": round(prev_low, 2),
                    "imbalance": round(imbalance, 4),
                    "rsi": round(rsi_val, 2),
                    "trend_ema": round(trend_ema, 2),
                },
            )

        return None

    def on_position_close(self, ctx: BarContext, position: Dict[str, Any]) -> None:
        self.last_exit_bar = ctx.bar_index