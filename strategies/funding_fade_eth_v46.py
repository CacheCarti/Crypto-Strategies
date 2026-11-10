from domains.strategy_contract import Strategy, BarContext, Signal
from typing import Optional, Dict, Any
import math

class EthFundingContrarianSqueeze(Strategy):
    METADATA = {
        "name": "EthFundingContrarianSqueeze",
        "domain": "eth_usdc",
        "declared_sl_bps": 320.0,
        "declared_tp_bps": 520.0,
        "declared_hold_seconds": 21600,  # ~6 hours target swing hold
        "warmup_bars": 60,
        "required_features": ["funding_rate_ethusdt"],
    }

    def initialize(self, ctx: BarContext) -> None:
        self.warmup = 60
        self.cooldown_bars = 8
        self.last_exit_bar = -999
        self.persistence_required = 3
        
        # Extreme funding thresholds (8h rate scaled: 0.0003 = +0.03%, -0.00025 = -0.025%)
        self.long_funding_thresh = -0.00025
        self.short_funding_thresh = 0.00035
        self.norm_funding_band = 0.00008

        # Rolling history of funding rates
        self.funding_history = []

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
        val = sum(values[:period]) / period
        for v in values[period:]:
            val = v * k + val * (1 - k)
        return val

    def on_position_close(self, ctx: BarContext, position: Dict[str, Any]) -> None:
        self.last_exit_bar = ctx.bar_index

    def on_bar(self, ctx: BarContext) -> Optional[Signal]:
        closes = ctx.closes(self.warmup)
        if len(closes) < self.warmup:
            return None

        # Track funding rate persistence
        current_funding = ctx.features.get("funding_rate_ethusdt", 0.0)
        self.funding_history.append(current_funding)
        if len(self.funding_history) > 10:
            self.funding_history.pop(0)

        # Regimes and trend filters
        rsi = self._rsi(closes, 14)
        ema_trend = self._ema(closes, 50)
        if rsi is None or ema_trend is None:
            return None

        price = ctx.bar.close
        recent_funding = self.funding_history[-self.persistence_required:]
        funding_len = len(recent_funding)

        # 1. Manage Active Positions (Normalization / Contrarian Reversion Exits)
        if ctx.has_position():
            direction = ctx.position_direction()
            
            # Exit Long when funding returns to normal or crowd turns positive
            if direction == "long" and current_funding >= -self.norm_funding_band:
                return ctx.signal(
                    "flat",
                    confidence=0.6,
                    metadata={
                        "reason": "funding_normalized_long_exit",
                        "funding": current_funding,
                        "price": price,
                        "rsi": round(rsi, 2)
                    }
                )

            # Exit Short when funding returns to normal or crowd turns negative
            if direction == "short" and current_funding <= self.norm_funding_band:
                return ctx.signal(
                    "flat",
                    confidence=0.6,
                    metadata={
                        "reason": "funding_normalized_short_exit",
                        "funding": current_funding,
                        "price": price,
                        "rsi": round(rsi, 2)
                    }
                )
            return None

        # 2. Check Cooldown to avoid overtrading
        if (ctx.bar_index - self.last_exit_bar) < self.cooldown_bars:
            return None

        # Need sufficient funding history to confirm persistence
        if funding_len < self.persistence_required:
            return None

        # Market safety guard: avoid trading during market meltdown
        regime = ctx.market.get("regime", "NORMAL")
        if regime in ("CRISIS", "MELTDOWN"):
            return None

        # 3. Entry Logic
        # Squeeze Long: Funding persistently negative (crowd short) & RSI not overbought & not collapsed below trend
        persistent_negative = all(f <= self.long_funding_thresh for f in recent_funding)
        # Filter: avoid fading into a catastrophic downward runaway (require RSI >= 32 and price not >6% below EMA50)
        not_falling_knife = rsi >= 32.0 and (price >= ema_trend * 0.94)

        if persistent_negative and not_falling_knife and rsi <= 58.0:
            avg_fund = sum(recent_funding) / funding_len
            conf = min(0.85, 0.60 + abs(avg_fund) * 400.0)
            return ctx.signal(
                "long",
                confidence=conf,
                stop_loss_bps=self.METADATA["declared_sl_bps"],
                take_profit_bps=self.METADATA["declared_tp_bps"],
                horizon_seconds=self.METADATA["declared_hold_seconds"],
                metadata={
                    "reason": "persistent_negative_funding_squeeze",
                    "avg_funding": avg_fund,
                    "rsi": round(rsi, 2),
                    "price": price,
                    "ema50": round(ema_trend, 2)
                }
            )

        # Squeeze Short: Funding persistently positive (crowd over-leveraged long) & RSI not oversold & not blowing past trend
        persistent_positive = all(f >= self.short_funding_thresh for f in recent_funding)
        # Filter: avoid fading runaway bullish euphoria (require RSI <= 68 and price not >6% above EMA50)
        not_parabolic_surge = rsi <= 68.0 and (price <= ema_trend * 1.06)

        if persistent_positive and not_parabolic_surge and rsi >= 42.0:
            avg_fund = sum(recent_funding) / funding_len
            conf = min(0.85, 0.60 + avg_fund * 350.0)
            return ctx.signal(
                "short",
                confidence=conf,
                stop_loss_bps=self.METADATA["declared_sl_bps"],
                take_profit_bps=self.METADATA["declared_tp_bps"],
                horizon_seconds=self.METADATA["declared_hold_seconds"],
                metadata={
                    "reason": "persistent_positive_funding_exhaustion",
                    "avg_funding": avg_fund,
                    "rsi": round(rsi, 2),
                    "price": price,
                    "ema50": round(ema_trend, 2)
                }
            )

        return None