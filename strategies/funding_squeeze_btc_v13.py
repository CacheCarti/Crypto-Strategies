from domains.strategy_contract import Strategy, BarContext, Signal
from typing import Optional, Dict, Any
import math

class BtcFundingSqueeze(Strategy):
    METADATA = {
        "name": "BtcFundingSqueeze",
        "domain": "btc_usdc",
        "declared_sl_bps": 220.0,
        "declared_tp_bps": 440.0,
        "declared_hold_seconds": 28800,  # 8 hours typical squeeze horizon
        "warmup_bars": 35,
        "required_features": ["funding_rate_btcusdt"],
    }

    def initialize(self, ctx: BarContext) -> None:
        self.lookback = 24
        self.cooldown_bars = 12
        self.last_exit_bar = -100
        self.entry_bar = -100
        self.max_hold_bars = 18

    def _rsi(self, closes, period=14):
        if len(closes) < period + 1:
            return None
        gains, losses = [], []
        for i in range(1, len(closes)):
            diff = closes[i] - closes[i-1]
            gains.append(max(diff, 0))
            losses.append(max(-diff, 0))
        avg_gain = sum(gains[-period:]) / period
        avg_loss = sum(losses[-period:]) / period
        if avg_loss == 0:
            return 100.0
        rs = avg_gain / avg_loss
        return 100.0 - (100.0 / (1.0 + rs))

    def on_position_close(self, ctx: BarContext, position: Dict[str, Any]) -> None:
        self.last_exit_bar = ctx.bar_index

    def on_bar(self, ctx: BarContext) -> Optional[Signal]:
        closes = ctx.closes(self.lookback + 10)
        if len(closes) < self.lookback + 2:
            return None

        current_price = ctx.bar.close
        ref_price = closes[-self.lookback]
        price_return = (current_price - ref_price) / ref_price

        # Check time-based exit if held too long
        if ctx.has_position():
            bars_held = ctx.bar_index - self.entry_bar
            if bars_held >= self.max_hold_bars:
                return ctx.signal(
                    "flat",
                    confidence=0.5,
                    metadata={"reason": "max_hold_reached", "bars_held": bars_held, "price": current_price}
                )
            return None

        # Cooldown guard
        if ctx.bar_index - self.last_exit_bar < self.cooldown_bars:
            return None

        # External funding feature
        funding = ctx.features.get("funding_rate_btcusdt", 0.0)

        # Volatility & Momentum confirmation
        rsi = self._rsi(closes, 14)
        if rsi is None:
            return None

        # Avoid entering during absolute crisis / meltdown
        regime = ctx.market.get("regime", "NORMAL")
        if regime in ("CRISIS", "MELTDOWN"):
            return None

        # Bullish Squeeze Setup:
        # Price is up significantly over the last 24 bars (>= 1.2%), yet funding is stubbornly negative (< -0.00005)
        # Shorts are trapped/paying funding while price moves against them. RSI confirm not excessively overbought.
        if price_return >= 0.012 and funding <= -0.00005 and rsi < 72.0:
            self.entry_bar = ctx.bar_index
            conf = min(0.85, 0.55 + abs(funding) * 1000 + (price_return * 5))
            return ctx.signal(
                "long",
                confidence=round(conf, 2),
                stop_loss_bps=self.METADATA["declared_sl_bps"],
                take_profit_bps=self.METADATA["declared_tp_bps"],
                horizon_seconds=self.METADATA["declared_hold_seconds"],
                metadata={
                    "reason": "short_squeeze_divergence",
                    "price_return_24h": round(price_return, 4),
                    "funding_rate": funding,
                    "rsi": round(rsi, 2),
                    "price": current_price,
                }
            )

        # Bearish Long-Liquidation Cascade Setup:
        # Price is down significantly over the last 24 bars (<= -1.2%), yet funding remains stubbornly positive (>= 0.00008)
        # Longs are trapped paying funding during a bleed. RSI confirm not deeply oversold.
        if price_return <= -0.012 and funding >= 0.00008 and rsi > 28.0:
            self.entry_bar = ctx.bar_index
            conf = min(0.85, 0.55 + funding * 1000 + (abs(price_return) * 5))
            return ctx.signal(
                "short",
                confidence=round(conf, 2),
                stop_loss_bps=self.METADATA["declared_sl_bps"],
                take_profit_bps=self.METADATA["declared_tp_bps"],
                horizon_seconds=self.METADATA["declared_hold_seconds"],
                metadata={
                    "reason": "long_cascade_divergence",
                    "price_return_24h": round(price_return, 4),
                    "funding_rate": funding,
                    "rsi": round(rsi, 2),
                    "price": current_price,
                }
            )

        return None