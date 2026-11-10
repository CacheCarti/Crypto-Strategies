from domains.strategy_contract import Strategy, BarContext, Signal
from typing import Optional, Dict, Any
import math

class EthFundingContrarian(Strategy):
    METADATA = {
        "name": "EthFundingContrarian",
        "domain": "eth_usdc",
        "declared_sl_bps": 220.0,
        "declared_tp_bps": 380.0,
        "declared_hold_seconds": 21600,  # ~6 hours
        "warmup_bars": 50,
        "required_features": ["funding_rate_ethusdt"],
    }

    def initialize(self, ctx: BarContext) -> None:
        self.warmup = 50
        self.cooldown_bars = 6
        self.last_exit_bar = -999
        self.funding_history = []
        self.history_len = 5
        self.long_funding_thresh = -0.00015   # -0.015% per 8h (crowd heavily short)
        self.short_funding_thresh = 0.00030  # +0.030% per 8h (crowd heavily long)
        self.exit_norm_low = -0.00003
        self.exit_norm_high = 0.00008

    def _sma(self, values, period: int):
        if len(values) < period:
            return None
        return sum(values[-period:]) / period

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
        closes = ctx.closes(self.warmup)
        if len(closes) < self.warmup:
            return None

        # Track rolling funding rate (feature updates hourly on 1h bars)
        current_fr = ctx.features.get("funding_rate_ethusdt", 0.0)
        self.funding_history.append(current_fr)
        if len(self.funding_history) > self.history_len:
            self.funding_history.pop(0)

        # Require funding history to be established
        if len(self.funding_history) < 3:
            return None

        avg_funding_3 = sum(self.funding_history[-3:]) / 3.0
        rsi = self._rsi(closes, 14)
        sma30 = self._sma(closes, 30)

        if rsi is None or sma30 is None:
            return None

        curr_close = ctx.bar.close

        # Handle active position exit condition: funding normalization
        if ctx.has_position():
            direction = ctx.position_direction()
            should_exit = False
            exit_reason = ""

            if direction == "long" and avg_funding_3 >= self.exit_norm_low:
                should_exit = True
                exit_reason = "funding_normalized_long"
            elif direction == "short" and avg_funding_3 <= self.exit_norm_high:
                should_exit = True
                exit_reason = "funding_normalized_short"

            if should_exit:
                return ctx.signal(
                    "flat",
                    confidence=0.6,
                    metadata={
                        "reason": exit_reason,
                        "avg_funding": avg_funding_3,
                        "rsi": round(rsi, 2),
                        "price": curr_close
                    }
                )
            return None

        # Cooldown guard: wait bars between trades to prevent excessive churning
        if ctx.bar_index - self.last_exit_bar < self.cooldown_bars:
            return None

        # Skip high crisis regime to avoid liquidations
        crisis_score = ctx.market.get("crisis_score", 0.0)
        if crisis_score > 0.65:
            return None

        # LONG SETUP: Crowded short (funding strongly negative for 3 bars),
        # but RSI is not in freefall / price not crashing aggressively below trend.
        # RSI between 30 and 48 suggests dip without structural collapse.
        if (
            avg_funding_3 <= self.long_funding_thresh
            and all(f <= self.long_funding_thresh * 0.7 for f in self.funding_history[-3:])
            and 28.0 <= rsi <= 52.0
            and curr_close > sma30 * 0.965  # Don't catch falling knives in severe breakdown
        ):
            confidence = min(0.85, 0.55 + abs(avg_funding_3) * 1000.0)
            return ctx.signal(
                "long",
                confidence=confidence,
                stop_loss_bps=220.0,
                take_profit_bps=380.0,
                horizon_seconds=21600,
                metadata={
                    "reason": "funding_negative_squeeze_reversion",
                    "avg_funding": avg_funding_3,
                    "rsi": round(rsi, 2),
                    "sma30": round(sma30, 2),
                    "price": curr_close
                }
            )

        # SHORT SETUP: Crowded long (funding strongly positive for 3 bars),
        # RSI not in euphoric parabolic continuation (>75), price not over extended.
        if (
            avg_funding_3 >= self.short_funding_thresh
            and all(f >= self.short_funding_thresh * 0.7 for f in self.funding_history[-3:])
            and 48.0 <= rsi <= 72.0
            and curr_close < sma30 * 1.035  # Avoid shorting hyper-momentum blowoffs
        ):
            confidence = min(0.85, 0.55 + avg_funding_3 * 800.0)
            return ctx.signal(
                "short",
                confidence=confidence,
                stop_loss_bps=220.0,
                take_profit_bps=380.0,
                horizon_seconds=21600,
                metadata={
                    "reason": "funding_positive_crowded_long_reversion",
                    "avg_funding": avg_funding_3,
                    "rsi": round(rsi, 2),
                    "sma30": round(sma30, 2),
                    "price": curr_close
                }
            )

        return None