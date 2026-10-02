from domains.strategy_contract import Strategy, BarContext, Signal
from typing import Optional, Dict, Any
import math

class SolEmaRibbonTrend(Strategy):
    METADATA = {
        "name": "SOL EMA Ribbon Trend",
        "domain": "sol_usdc",
        "declared_sl_bps": 400.0,
        "declared_tp_bps": 900.0,
        "declared_hold_seconds": 28800,
        "warmup_bars": 65,
    }

    def initialize(self, ctx: BarContext) -> None:
        self.fast_period = 9
        self.mid_period = 21
        self.slow_period = 50
        self.rsi_period = 14
        self.cooldown_bars = 12
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
        # Filter out extreme crisis regimes to preserve capital
        if ctx.regime == "crisis" or ctx.market.get("regime", "NORMAL") in ["CRISIS", "MELTDOWN"]:
            return None

        closes = ctx.closes(self.slow_period + 30)
        if len(closes) < self.slow_period + 5:
            return None

        ema9 = self._ema(closes, self.fast_period)
        ema21 = self._ema(closes, self.mid_period)
        ema50 = self._ema(closes, self.slow_period)
        rsi = self._rsi(closes, self.rsi_period)

        if ema9 is None or ema21 is None or ema50 is None or rsi is None:
            return None

        current_close = ctx.bar.close
        current_bar = ctx.bar_index
        has_pos = ctx.has_position()
        pos_dir = ctx.position_direction()

        # Ribbon Alignment
        bullish_alignment = (ema9 > ema21 > ema50)
        bearish_alignment = (ema9 < ema21 < ema50)

        # Ribbon spread as percentage of slow EMA
        ribbon_spread = abs(ema9 - ema50) / ema50

        # Position Management: Exit on structural trend loss (fast crosses mid or price breaks mid)
        if has_pos:
            if pos_dir == "long":
                if ema9 < ema21 or current_close < ema21:
                    self.last_exit_bar = current_bar
                    return ctx.signal(
                        "flat",
                        confidence=0.75,
                        metadata={
                            "reason": "long_ribbon_structure_loss",
                            "close": current_close,
                            "ema9": ema9,
                            "ema21": ema21,
                            "ema50": ema50,
                            "rsi": rsi
                        }
                    )
            elif pos_dir == "short":
                if ema9 > ema21 or current_close > ema21:
                    self.last_exit_bar = current_bar
                    return ctx.signal(
                        "flat",
                        confidence=0.75,
                        metadata={
                            "reason": "short_ribbon_structure_loss",
                            "close": current_close,
                            "ema9": ema9,
                            "ema21": ema21,
                            "ema50": ema50,
                            "rsi": rsi
                        }
                    )
            return None

        # Hard cooldown guard to throttle trade frequency
        if (current_bar - self.last_exit_bar) < self.cooldown_bars:
            return None

        # Tighter entry thresholds: minimum 1.0% expansion + RSI momentum confirmation
        min_spread = 0.010
        confidence = min(0.90, max(0.55, 0.50 + (ribbon_spread * 15.0)))

        # Bullish setup: ribbon aligned, strong expansion, price leading, and RSI non-overbought momentum
        if bullish_alignment and current_close > ema9 and ribbon_spread >= min_spread:
            if 52.0 <= rsi <= 72.0:
                return ctx.signal(
                    "long",
                    confidence=confidence,
                    stop_loss_bps=self.METADATA["declared_sl_bps"],
                    take_profit_bps=self.METADATA["declared_tp_bps"],
                    horizon_seconds=self.METADATA["declared_hold_seconds"],
                    metadata={
                        "reason": "ema_ribbon_bull_expansion_confirmed",
                        "close": current_close,
                        "ema9": ema9,
                        "ema21": ema21,
                        "ema50": ema50,
                        "ribbon_spread": ribbon_spread,
                        "rsi": rsi
                    }
                )

        # Bearish setup: ribbon aligned down, strong expansion, price leading, and RSI non-oversold momentum
        if bearish_alignment and current_close < ema9 and ribbon_spread >= min_spread:
            if 28.0 <= rsi <= 48.0:
                return ctx.signal(
                    "short",
                    confidence=confidence,
                    stop_loss_bps=self.METADATA["declared_sl_bps"],
                    take_profit_bps=self.METADATA["declared_tp_bps"],
                    horizon_seconds=self.METADATA["declared_hold_seconds"],
                    metadata={
                        "reason": "ema_ribbon_bear_expansion_confirmed",
                        "close": current_close,
                        "ema9": ema9,
                        "ema21": ema21,
                        "ema50": ema50,
                        "ribbon_spread": ribbon_spread,
                        "rsi": rsi
                    }
                )

        return None