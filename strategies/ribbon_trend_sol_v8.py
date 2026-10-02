from domains.strategy_contract import Strategy, BarContext, Signal
from typing import Optional, Dict, Any
import math

class SolEmaRibbonTrend(Strategy):
    METADATA = {
        "name": "SOL EMA Ribbon Trend",
        "domain": "sol_usdc",
        "declared_sl_bps": 450.0,
        "declared_tp_bps": 950.0,
        "declared_hold_seconds": 21600,
        "warmup_bars": 80,
    }

    def initialize(self, ctx: BarContext) -> None:
        self.fast_period = 12
        self.med_period = 26
        self.slow_period = 60
        self.rsi_period = 14
        self.min_spread_bps = 75.0
        self.cooldown_bars = 16
        self.last_exit_bar = -999

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
        if avg_loss == 0:
            return 100.0
        rs = avg_gain / avg_loss
        return 100.0 - (100.0 / (1.0 + rs))

    def on_position_close(self, ctx: BarContext, position: Dict[str, Any]) -> None:
        self.last_exit_bar = ctx.bar_index

    def on_bar(self, ctx: BarContext) -> Optional[Signal]:
        closes = ctx.closes(self.slow_period + 20)
        if len(closes) < self.slow_period + 10:
            return None

        current_price = ctx.bar.close
        ema_fast = self._ema(closes, self.fast_period)
        ema_med = self._ema(closes, self.med_period)
        ema_slow = self._ema(closes, self.slow_period)
        rsi = self._rsi(closes, self.rsi_period)

        if ema_fast is None or ema_med is None or ema_slow is None or ema_slow == 0 or rsi is None:
            return None

        bullish_alignment = (ema_fast > ema_med) and (ema_med > ema_slow)
        bearish_alignment = (ema_fast < ema_med) and (ema_med < ema_slow)
        spread_bps = (abs(ema_fast - ema_slow) / ema_slow) * 10000.0

        crisis_score = ctx.market.get("crisis_score", 0.0)

        # Position management
        if ctx.has_position():
            pos_dir = ctx.position_direction()
            # Exit long on ribbon inversion or fast crossing below medium
            if pos_dir == "long":
                if ema_fast < ema_med or current_price < ema_slow:
                    self.last_exit_bar = ctx.bar_index
                    return ctx.signal(
                        "flat",
                        confidence=0.7,
                        metadata={
                            "reason": "long_ribbon_inversion_exit",
                            "price": current_price,
                            "ema_fast": round(ema_fast, 3),
                            "ema_med": round(ema_med, 3),
                            "ema_slow": round(ema_slow, 3),
                            "rsi": round(rsi, 2),
                        }
                    )
            # Exit short on ribbon inversion or fast crossing above medium
            elif pos_dir == "short":
                if ema_fast > ema_med or current_price > ema_slow:
                    self.last_exit_bar = ctx.bar_index
                    return ctx.signal(
                        "flat",
                        confidence=0.7,
                        metadata={
                            "reason": "short_ribbon_inversion_exit",
                            "price": current_price,
                            "ema_fast": round(ema_fast, 3),
                            "ema_med": round(ema_med, 3),
                            "ema_slow": round(ema_slow, 3),
                            "rsi": round(rsi, 2),
                        }
                    )
            return None

        # Hard multi-bar cooldown to prevent overtrading and fee drag
        if (ctx.bar_index - self.last_exit_bar) < self.cooldown_bars:
            return None

        # Regime / noise filter: require distinct ribbon expansion and normal market state
        if spread_bps < self.min_spread_bps or crisis_score > 0.65:
            return None

        confidence = min(0.88, max(0.60, 0.60 + (spread_bps - self.min_spread_bps) / 250.0))

        # Long Entry: Bullish ribbon + price above fast EMA + strong but not exhausted RSI
        if bullish_alignment and current_price > ema_fast and (52.0 <= rsi <= 72.0):
            return ctx.signal(
                "long",
                confidence=confidence,
                stop_loss_bps=self.METADATA["declared_sl_bps"],
                take_profit_bps=self.METADATA["declared_tp_bps"],
                horizon_seconds=self.METADATA["declared_hold_seconds"],
                metadata={
                    "reason": "ema_ribbon_bull_expansion_confirmed",
                    "price": current_price,
                    "ema_fast": round(ema_fast, 3),
                    "ema_med": round(ema_med, 3),
                    "ema_slow": round(ema_slow, 3),
                    "spread_bps": round(spread_bps, 2),
                    "rsi": round(rsi, 2),
                }
            )

        # Short Entry: Bearish ribbon + price below fast EMA + bearish but not oversold RSI
        if bearish_alignment and current_price < ema_fast and (28.0 <= rsi <= 48.0):
            return ctx.signal(
                "short",
                confidence=confidence,
                stop_loss_bps=self.METADATA["declared_sl_bps"],
                take_profit_bps=self.METADATA["declared_tp_bps"],
                horizon_seconds=self.METADATA["declared_hold_seconds"],
                metadata={
                    "reason": "ema_ribbon_bear_expansion_confirmed",
                    "price": current_price,
                    "ema_fast": round(ema_fast, 3),
                    "ema_med": round(ema_med, 3),
                    "ema_slow": round(ema_slow, 3),
                    "spread_bps": round(spread_bps, 2),
                    "rsi": round(rsi, 2),
                }
            )

        return None