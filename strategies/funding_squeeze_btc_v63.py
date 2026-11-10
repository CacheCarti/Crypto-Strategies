from domains.strategy_contract import Strategy, BarContext, Signal
from typing import Optional, Dict, Any
import math

class BtcFundingSqueezeDivergence(Strategy):
    METADATA = {
        "name": "BtcFundingSqueezeDivergence",
        "domain": "btc_usdc",
        "declared_sl_bps": 220.0,
        "declared_tp_bps": 380.0,
        "declared_hold_seconds": 18000,  # ~5 hours
        "warmup_bars": 35,
        "required_features": ["funding_rate_btcusdt"],
    }

    def initialize(self, ctx: BarContext) -> None:
        self.lookback = 24
        self.cooldown_bars = 6
        self.last_exit_bar = -999
        self.consecutive_losses = 0

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
        ret = position.get("return_bps", 0.0)
        if ret < 0:
            self.consecutive_losses = min(self.consecutive_losses + 1, 3)
        else:
            self.consecutive_losses = 0

    def on_bar(self, ctx: BarContext) -> Optional[Signal]:
        if ctx.bar_index < self.METADATA["warmup_bars"]:
            return None

        closes = ctx.closes(self.lookback + 5)
        if len(closes) < self.lookback + 1:
            return None

        rsi = self._rsi(closes, 14)
        if rsi is None:
            return None

        # Check existing position for exit logic
        if ctx.has_position():
            pos_dir = ctx.position_direction()
            # Mean reversion or extreme overshoot exits
            if pos_dir == "long" and rsi > 76.0:
                return ctx.signal(
                    "flat",
                    confidence=0.6,
                    metadata={"reason": "long_overbought_exhaustion", "rsi": round(rsi, 2)}
                )
            elif pos_dir == "short" and rsi < 24.0:
                return ctx.signal(
                    "flat",
                    confidence=0.6,
                    metadata={"reason": "short_oversold_exhaustion", "rsi": round(rsi, 2)}
                )
            return None

        # Enforce mandatory post-exit cooldown
        if (ctx.bar_index - self.last_exit_bar) < self.cooldown_bars:
            return None

        # Feature reading
        funding = ctx.features.get("funding_rate_btcusdt", 0.0)
        crisis = ctx.market.get("crisis_score", 0.0)
        if crisis > 0.6:
            return None

        # 24-bar price return
        current_close = closes[-1]
        prior_close = closes[-(self.lookback + 1)]
        price_ret_24 = (current_close - prior_close) / prior_close

        # Adaptive confidence scaling based on loss streak
        base_confidence = max(0.55, 0.75 - (self.consecutive_losses * 0.07))

        # Long Squeeze: Price is pushing up, but funding is persistently negative (shorts trapped)
        # Moderate momentum confirmation: 1.0% to 5.5% run over 24h, RSI not yet overheated (< 68)
        if price_ret_24 >= 0.012 and funding <= -0.00003 and rsi < 68.0:
            return ctx.signal(
                "long",
                confidence=base_confidence,
                stop_loss_bps=self.METADATA["declared_sl_bps"],
                take_profit_bps=self.METADATA["declared_tp_bps"],
                horizon_seconds=self.METADATA["declared_hold_seconds"],
                metadata={
                    "reason": "short_funding_squeeze_continuation",
                    "price_ret_24": round(price_ret_24, 4),
                    "funding_rate": round(funding, 6),
                    "rsi": round(rsi, 2),
                    "consecutive_losses": self.consecutive_losses,
                }
            )

        # Short Squeeze/Long Liquidation cascade: Price is falling, but funding is persistently positive (longs trapped)
        # Moderate downward momentum: -1.0% to -5.5% run over 24h, RSI not yet washed out (> 32)
        if price_ret_24 <= -0.012 and funding >= 0.00008 and rsi > 32.0:
            return ctx.signal(
                "short",
                confidence=base_confidence,
                stop_loss_bps=self.METADATA["declared_sl_bps"],
                take_profit_bps=self.METADATA["declared_tp_bps"],
                horizon_seconds=self.METADATA["declared_hold_seconds"],
                metadata={
                    "reason": "long_funding_squeeze_breakdown",
                    "price_ret_24": round(price_ret_24, 4),
                    "funding_rate": round(funding, 6),
                    "rsi": round(rsi, 2),
                    "consecutive_losses": self.consecutive_losses,
                }
            )

        return None