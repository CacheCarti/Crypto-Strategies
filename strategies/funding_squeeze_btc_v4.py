from domains.strategy_contract import Strategy, BarContext, Signal
from typing import Optional, Dict, Any, List
import math

class BtcFundingSqueezeDivergence(Strategy):
    METADATA = {
        "name": "BTC Funding Squeeze Divergence",
        "domain": "btc_usdc",
        "declared_sl_bps": 250.0,
        "declared_tp_bps": 500.0,
        "declared_hold_seconds": 14400,
        "warmup_bars": 25,
        "required_features": ["funding_rate_btcusdt"],
    }

    def initialize(self, ctx: BarContext) -> None:
        self.lookback_price = 12
        self.cooldown_bars = 4
        self.last_trade_bar = -100

    def _rsi(self, closes: List[float], period: int = 14) -> Optional[float]:
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

    def _ema(self, values: List[float], period: int) -> Optional[float]:
        if len(values) < period:
            return None
        k = 2.0 / (period + 1)
        ema = sum(values[:period]) / period
        for v in values[period:]:
            ema = v * k + ema * (1.0 - k)
        return ema

    def on_bar(self, ctx: BarContext) -> Optional[Signal]:
        closes = ctx.closes(self.lookback_price + 16)
        if len(closes) < self.lookback_price + 15:
            return None

        current_price = closes[-1]
        ref_price = closes[-self.lookback_price]
        price_change_pct = (current_price - ref_price) / ref_price

        funding = ctx.features.get("funding_rate_btcusdt", 0.0)
        rsi_val = self._rsi(closes, 14)
        ema_fast = self._ema(closes, 12)

        if rsi_val is None or ema_fast is None:
            return None

        # Position exit management
        if ctx.has_position():
            pos_dir = ctx.position_direction()
            if pos_dir == "long":
                if rsi_val > 75.0 or current_price < ema_fast * 0.985:
                    self.last_trade_bar = ctx.bar_index
                    return ctx.signal(
                        "flat",
                        confidence=0.7,
                        metadata={
                            "reason": "exit_long_momentum_exhaustion",
                            "rsi": round(rsi_val, 2),
                            "funding": round(funding, 6),
                            "price": round(current_price, 2),
                        },
                    )
            elif pos_dir == "short":
                if rsi_val < 25.0 or current_price > ema_fast * 1.015:
                    self.last_trade_bar = ctx.bar_index
                    return ctx.signal(
                        "flat",
                        confidence=0.7,
                        metadata={
                            "reason": "exit_short_momentum_exhaustion",
                            "rsi": round(rsi_val, 2),
                            "funding": round(funding, 6),
                            "price": round(current_price, 2),
                        },
                    )
            return None

        # Enforce cooldown between entries
        if (ctx.bar_index - self.last_trade_bar) < self.cooldown_bars:
            return None

        # Long Squeeze: Price pushing up while funding is neutral to negative (shorts trapped / paying or hesitant)
        if price_change_pct >= 0.004 and funding <= 0.00005:
            if rsi_val < 68.0:
                self.last_trade_bar = ctx.bar_index
                conf = 0.65 if funding > 0.0 else 0.80
                return ctx.signal(
                    "long",
                    confidence=conf,
                    stop_loss_bps=250.0,
                    take_profit_bps=500.0,
                    horizon_seconds=14400,
                    metadata={
                        "reason": "short_squeeze_continuation",
                        "price_change_pct": round(price_change_pct * 100, 2),
                        "funding": round(funding, 6),
                        "rsi": round(rsi_val, 2),
                        "ema12": round(ema_fast, 2),
                        "price": round(current_price, 2),
                    },
                )

        # Short Breakdown: Price breaking down while funding is crowded positive (longs paying & trapped)
        if price_change_pct <= -0.004 and funding >= 0.00008:
            if rsi_val > 32.0:
                self.last_trade_bar = ctx.bar_index
                conf = 0.65 if funding < 0.00015 else 0.80
                return ctx.signal(
                    "short",
                    confidence=conf,
                    stop_loss_bps=250.0,
                    take_profit_bps=500.0,
                    horizon_seconds=14400,
                    metadata={
                        "reason": "long_cascade_continuation",
                        "price_change_pct": round(price_change_pct * 100, 2),
                        "funding": round(funding, 6),
                        "rsi": round(rsi_val, 2),
                        "ema12": round(ema_fast, 2),
                        "price": round(current_price, 2),
                    },
                )

        return None

    def on_position_close(self, ctx: BarContext, position: Dict[str, Any]) -> None:
        self.last_trade_bar = ctx.bar_index