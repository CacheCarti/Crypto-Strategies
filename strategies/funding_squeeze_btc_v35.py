from domains.strategy_contract import Strategy, BarContext, Signal
from typing import Optional, Dict, Any
import math

class FundingSqueeze(Strategy):
    METADATA = {
        "name": "BTC Funding Squeeze High Conviction",
        "domain": "btc_usdc",
        "declared_sl_bps": 280.0,
        "declared_tp_bps": 480.0,
        "declared_hold_seconds": 21600,  # ~6 hours intended hold
        "warmup_bars": 40,
        "required_features": ["funding_rate_btcusdt"]
    }

    def initialize(self, ctx: BarContext) -> None:
        self.lookback = 24
        self.cooldown_bars = 12
        self.last_exit_bar = -100
        self.min_price_move_pct = 0.015  # 1.5% minimum 24-bar move for strong momentum
        self.funding_thresh = 0.00003    # 0.003% clear funding skew

    def _ema(self, values, period):
        if len(values) < period:
            return None
        k = 2.0 / (period + 1)
        ema = sum(values[:period]) / period
        for v in values[period:]:
            ema = v * k + ema * (1 - k)
        return ema

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

    def on_position_close(self, ctx: BarContext, position: Dict[str, Any]) -> None:
        self.last_exit_bar = ctx.bar_index

    def on_bar(self, ctx: BarContext) -> Optional[Signal]:
        closes = ctx.closes(self.lookback + 15)
        if len(closes) < self.lookback + 10:
            return None

        # Respect cooldown to limit overtrading and friction drag
        if ctx.bar_index - self.last_exit_bar < self.cooldown_bars:
            return None

        current_close = closes[-1]
        past_close = closes[-self.lookback]
        price_return = (current_close - past_close) / past_close

        funding = ctx.features.get("funding_rate_btcusdt", 0.0)
        rsi = self._rsi(closes, 14)
        ema20 = self._ema(closes, 20)
        regime = ctx.market.get("regime", "NORMAL")

        # In severe crisis or meltdown, avoid trend squeeze continuations
        if regime in ("CRISIS", "MELTDOWN"):
            if ctx.has_position():
                return ctx.signal(
                    "flat",
                    confidence=0.8,
                    metadata={"reason": "exit_crisis_regime", "regime": regime}
                )
            return None

        # Position management
        if ctx.has_position():
            pos_dir = ctx.position_direction()
            # Dynamic unwinding: if funding aligns or squeeze momentum stalls
            if pos_dir == "long":
                if funding > self.funding_thresh or (rsi is not None and rsi > 78):
                    return ctx.signal(
                        "flat",
                        confidence=0.6,
                        metadata={
                            "reason": "long_take_profit_or_funding_flip",
                            "funding": funding,
                            "rsi": rsi
                        }
                    )
            elif pos_dir == "short":
                if funding < -self.funding_thresh or (rsi is not None and rsi < 22):
                    return ctx.signal(
                        "flat",
                        confidence=0.6,
                        metadata={
                            "reason": "short_take_profit_or_funding_flip",
                            "funding": funding,
                            "rsi": rsi
                        }
                    )
            return None

        # Gated high-conviction entries
        if rsi is None or ema20 is None:
            return None

        # Long squeeze setup: Price surging over 24h while funding stays persistently negative (shorts trapped)
        # Technical filter: Price must remain above short-term EMA20, RSI not yet severely exhausted (<68)
        if (price_return >= self.min_price_move_pct and 
            funding <= -self.funding_thresh and 
            current_close > ema20 and 
            48 < rsi < 68):
            
            confidence = min(0.9, 0.6 + abs(funding) * 500.0)
            return ctx.signal(
                "long",
                confidence=confidence,
                stop_loss_bps=self.METADATA["declared_sl_bps"],
                take_profit_bps=self.METADATA["declared_tp_bps"],
                horizon_seconds=self.METADATA["declared_hold_seconds"],
                metadata={
                    "reason": "short_squeeze_continuation",
                    "price_24h_return": round(price_return, 4),
                    "funding_rate": funding,
                    "rsi": round(rsi, 2),
                    "close": current_close,
                    "ema20": round(ema20, 2)
                }
            )

        # Short squeeze setup: Price dumping over 24h while funding stays persistently positive (longs trapped)
        # Technical filter: Price must remain below short-term EMA20, RSI not yet severely exhausted (>32)
        if (price_return <= -self.min_price_move_pct and 
            funding >= self.funding_thresh and 
            current_close < ema20 and 
            32 < rsi < 52):
            
            confidence = min(0.9, 0.6 + abs(funding) * 500.0)
            return ctx.signal(
                "short",
                confidence=confidence,
                stop_loss_bps=self.METADATA["declared_sl_bps"],
                take_profit_bps=self.METADATA["declared_tp_bps"],
                horizon_seconds=self.METADATA["declared_hold_seconds"],
                metadata={
                    "reason": "long_liquidation_continuation",
                    "price_24h_return": round(price_return, 4),
                    "funding_rate": funding,
                    "rsi": round(rsi, 2),
                    "close": current_close,
                    "ema20": round(ema20, 2)
                }
            )

        return None