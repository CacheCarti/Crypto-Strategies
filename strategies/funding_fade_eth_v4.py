from domains.strategy_contract import Strategy, BarContext, Signal
from typing import Optional, Dict, Any
import math

class FundingRateSqueezeContrarian(Strategy):
    METADATA = {
        "name": "FundingRateSqueezeContrarian",
        "domain": "eth_usdc",
        "declared_sl_bps": 260.0,
        "declared_tp_bps": 420.0,
        "declared_hold_seconds": 14400,
        "warmup_bars": 35,
        "required_features": ["funding_rate_ethusdt"],
    }

    def initialize(self, ctx: BarContext) -> None:
        self.rsi_period = 14
        self.ema_fast_period = 12
        self.ema_slow_period = 26
        self.funding_window = 3
        self.cooldown_bars = 4
        self.last_exit_bar = -999
        self.funding_history = []
        
        # Funding rate thresholds (per 8h standard basis, scaled in features)
        self.funding_long_thresh = -0.00003  # Negative funding: shorts paying longs
        self.funding_short_thresh = 0.00022  # High positive funding: crowd overly levered long
        self.funding_neutral_upper = 0.00008
        self.funding_neutral_lower = 0.00000

    def _rsi(self, closes, period=14):
        if len(closes) < period + 1:
            return None
        gains, losses = [], []
        for i in range(1, len(closes)):
            diff = closes[i] - closes[i-1]
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

    def on_position_close(self, ctx: BarContext, position: Dict[str, Any]) -> None:
        self.last_exit_bar = ctx.bar_index

    def on_bar(self, ctx: BarContext) -> Optional[Signal]:
        closes = ctx.closes(self.METADATA["warmup_bars"])
        if len(closes) < self.METADATA["warmup_bars"]:
            return None

        current_funding = ctx.features.get("funding_rate_ethusdt", 0.0)
        self.funding_history.append(current_funding)
        if len(self.funding_history) > 10:
            self.funding_history.pop(0)

        # Average persistent funding over recent window
        recent_funding = self.funding_history[-self.funding_window:]
        avg_funding = sum(recent_funding) / len(recent_funding)

        rsi = self._rsi(closes, self.rsi_period)
        ema_fast = self._ema(closes, self.ema_fast_period)
        ema_slow = self._ema(closes, self.ema_slow_period)

        if rsi is None or ema_fast is None or ema_slow is None:
            return None

        current_price = ctx.bar.close
        has_pos = ctx.has_position()
        pos_dir = ctx.position_direction()

        # Cooldown guard after exits
        in_cooldown = (ctx.bar_index - self.last_exit_bar) < self.cooldown_bars

        # Check Active Position Exits
        if has_pos:
            if pos_dir == "long":
                # Exit when funding normalizes or reaches high positive, or technical target hit
                funding_normalized = avg_funding >= self.funding_neutral_upper
                tech_overbought = rsi >= 68.0
                trend_break = current_price < ema_fast and rsi < 44.0

                if (funding_normalized and rsi > 52.0) or tech_overbought or trend_break:
                    return ctx.signal(
                        "flat",
                        confidence=0.75,
                        metadata={
                            "reason": "long_exit_funding_normalized_or_tech_target",
                            "avg_funding": avg_funding,
                            "rsi": rsi,
                            "price": current_price,
                            "ema_fast": ema_fast,
                        }
                    )
            elif pos_dir == "short":
                # Exit when funding drops to neutral/negative, or technical oversold hit
                funding_normalized = avg_funding <= self.funding_neutral_lower
                tech_oversold = rsi <= 32.0
                trend_break = current_price > ema_fast and rsi > 56.0

                if (funding_normalized and rsi < 48.0) or tech_oversold or trend_break:
                    return ctx.signal(
                        "flat",
                        confidence=0.75,
                        metadata={
                            "reason": "short_exit_funding_normalized_or_tech_target",
                            "avg_funding": avg_funding,
                            "rsi": rsi,
                            "price": current_price,
                            "ema_fast": ema_fast,
                        }
                    )
            return None

        if in_cooldown:
            return None

        # Entry Logic (Contrarian squeeze setups)
        # Condition A: Persistent Negative Funding squeeze (Crowd is short, primed for short squeeze)
        # Price must not be in catastrophic free-fall (RSI > 25) and not yet overextended (RSI < 60)
        long_funding_condition = avg_funding < self.funding_long_thresh and current_funding < self.funding_long_thresh
        long_price_filter = (rsi > 27.0 and rsi < 58.0) and (current_price >= closes[-2] or rsi > 36.0)

        if long_funding_condition and long_price_filter:
            # Scale confidence by funding severity
            severity = min(abs(avg_funding) / 0.0003, 1.0)
            confidence = min(0.60 + (0.35 * severity), 0.95)

            return ctx.signal(
                "long",
                confidence=confidence,
                stop_loss_bps=self.METADATA["declared_sl_bps"],
                take_profit_bps=self.METADATA["declared_tp_bps"],
                horizon_seconds=self.METADATA["declared_hold_seconds"],
                metadata={
                    "reason": "contrarian_short_squeeze_entry",
                    "avg_funding": avg_funding,
                    "current_funding": current_funding,
                    "rsi": rsi,
                    "ema_fast": ema_fast,
                    "ema_slow": ema_slow,
                    "price": current_price,
                }
            )

        # Condition B: Persistent High Positive Funding fade (Crowd is long/greedy, ripe for long flush)
        # Price must not be in a parabolic breakout (RSI < 75) and not already dumped (RSI > 42)
        short_funding_condition = avg_funding > self.funding_short_thresh and current_funding > self.funding_short_thresh
        short_price_filter = (rsi < 73.0 and rsi > 42.0) and (current_price <= closes[-2] or rsi < 64.0)

        if short_funding_condition and short_price_filter:
            severity = min(avg_funding / 0.0005, 1.0)
            confidence = min(0.60 + (0.35 * severity), 0.95)

            return ctx.signal(
                "short",
                confidence=confidence,
                stop_loss_bps=self.METADATA["declared_sl_bps"],
                take_profit_bps=self.METADATA["declared_tp_bps"],
                horizon_seconds=self.METADATA["declared_hold_seconds"],
                metadata={
                    "reason": "contrarian_long_flush_entry",
                    "avg_funding": avg_funding,
                    "current_funding": current_funding,
                    "rsi": rsi,
                    "ema_fast": ema_fast,
                    "ema_slow": ema_slow,
                    "price": current_price,
                }
            )

        return None