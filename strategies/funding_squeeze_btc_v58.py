from domains.strategy_contract import Strategy, BarContext, Signal
from typing import Optional, Dict, Any
import math

class FundingSqueezeEventBtc(Strategy):
    METADATA = {
        "name": "Funding Squeeze Event BTC",
        "domain": "btc_usdc",
        "declared_sl_bps": 220.0,
        "declared_tp_bps": 440.0,
        "declared_hold_seconds": 28800,  # ~8 hours hold for swing squeeze resolution
        "warmup_bars": 35,
        "required_features": ["funding_rate_btcusdt"],
    }

    def initialize(self, ctx: BarContext) -> None:
        self.lookback = 24
        self.min_ret_pct = 1.0  # 1.0% price move over lookback window
        self.cooldown_bars = 16  # hard cooldown after any trade to prevent clustering
        self.last_exit_bar = -999
        self.consecutive_funding_thresh = 18  # at least 18/24 bars persistent funding bias

    def _ema(self, values, period: int):
        if len(values) < period:
            return None
        k = 2.0 / (period + 1)
        ema = sum(values[:period]) / period
        for v in values[period:]:
            ema = v * k + ema * (1 - k)
        return ema

    def _rsi(self, closes, period: int = 14):
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
        closes = ctx.closes(self.lookback + 5)
        if len(closes) < self.lookback + 1:
            return None

        # Hard cooldown enforcement
        if (ctx.bar_index - self.last_exit_bar) < self.cooldown_bars:
            return None

        # Check market regime to avoid entering into full meltdown chaos
        regime = ctx.market.get("regime", "NORMAL")
        if regime == "MELTDOWN":
            return None

        price_now = closes[-1]
        price_past = closes[-self.lookback - 1]
        ret_24h_pct = ((price_now - price_past) / price_past) * 100.0

        current_funding = ctx.features.get("funding_rate_btcusdt", 0.0)
        
        # Indicator confirmation: trend alignment & momentum room
        ema_slow = self._ema(closes, 20)
        rsi = self._rsi(closes, 14)
        if ema_slow is None or rsi is None:
            return None

        # Squeeze logic:
        # Long Squeeze (Shorts Trapped): Price rallied >= 1.0% over 24h while funding is persistently negative (< -0.00005)
        # Price must also hold above EMA20 and RSI must have continuation room (45 < RSI < 68)
        if (
            not ctx.has_position()
            and ret_24h_pct >= self.min_ret_pct
            and current_funding < -0.00005
            and price_now > ema_slow
            and 45.0 < rsi < 68.0
        ):
            confidence = min(0.85, 0.55 + abs(current_funding) * 1500.0 + (ret_24h_pct / 10.0))
            return ctx.signal(
                "long",
                confidence=confidence,
                stop_loss_bps=220.0,
                take_profit_bps=440.0,
                horizon_seconds=28800,
                metadata={
                    "reason": "short_squeeze_continuation",
                    "ret_24h_pct": round(ret_24h_pct, 3),
                    "funding_rate": current_funding,
                    "rsi": round(rsi, 2),
                    "ema_slow": round(ema_slow, 2),
                    "price": price_now,
                },
            )

        # Short Squeeze (Longs Trapped / Long Liquidation Cascade): Price dropped <= -1.0% over 24h while funding is crowded positive (> 0.0002)
        # Price must stay below EMA20 and RSI not already oversold bottomed (32 < RSI < 55)
        if (
            not ctx.has_position()
            and ret_24h_pct <= -self.min_ret_pct
            and current_funding > 0.0002
            and price_now < ema_slow
            and 32.0 < rsi < 55.0
        ):
            confidence = min(0.85, 0.55 + abs(current_funding) * 1500.0 + (abs(ret_24h_pct) / 10.0))
            return ctx.signal(
                "short",
                confidence=confidence,
                stop_loss_bps=220.0,
                take_profit_bps=440.0,
                horizon_seconds=28800,
                metadata={
                    "reason": "long_crowd_liquidation_squeeze",
                    "ret_24h_pct": round(ret_24h_pct, 3),
                    "funding_rate": current_funding,
                    "rsi": round(rsi, 2),
                    "ema_slow": round(ema_slow, 2),
                    "price": price_now,
                },
            )

        # Dynamic exit if thesis breaks mid-position: funding flips strongly in direction of trend
        if ctx.has_position():
            direction = ctx.position_direction()
            if direction == "long" and (current_funding > 0.0003 or rsi > 78.0):
                return ctx.signal(
                    "flat",
                    confidence=0.6,
                    metadata={
                        "reason": "long_thesis_exhausted_funding_crowded",
                        "rsi": round(rsi, 2),
                        "funding_rate": current_funding,
                    },
                )
            if direction == "short" and (current_funding < -0.00015 or rsi < 24.0):
                return ctx.signal(
                    "flat",
                    confidence=0.6,
                    metadata={
                        "reason": "short_thesis_exhausted_funding_reset",
                        "rsi": round(rsi, 2),
                        "funding_rate": current_funding,
                    },
                )

        return None