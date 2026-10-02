from domains.strategy_contract import Strategy, BarContext, Signal
from typing import Optional, Dict, Any
import math

class EthFundingUnwind(Strategy):
    METADATA = {
        "name": "ETH Funding Rate Unwind",
        "domain": "eth_usdc",
        "declared_sl_bps": 280.0,
        "declared_tp_bps": 420.0,
        "declared_hold_seconds": 21600,  # 6 hours
        "warmup_bars": 35,
        "required_features": ["funding_rate_ethusdt"]
    }

    def initialize(self, ctx: BarContext) -> None:
        self.prev_funding = None
        self.last_trade_bar = -999
        self.cooldown_bars = 6
        self.max_hold_bars = 8
        self.entry_bar = 0
        self.funding_deadband = 0.000015  # Avoid micro-noise around absolute zero

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
            ema = v * k + ema * (1 - k)
        return ema

    def on_position_close(self, ctx: BarContext, position: Dict[str, Any]) -> None:
        self.last_trade_bar = ctx.bar_index

    def on_bar(self, ctx: BarContext) -> Optional[Signal]:
        closes = ctx.closes(35)
        if len(closes) < self.METADATA["warmup_bars"]:
            return None

        current_funding = ctx.features.get("funding_rate_ethusdt", 0.0)
        rsi = self._rsi(closes, 14)
        ema24 = self._ema(closes, 24)
        current_price = ctx.bar.close

        if rsi is None or ema24 is None:
            self.prev_funding = current_funding
            return None

        # Check existing position management
        if ctx.has_position():
            bars_in_pos = ctx.bar_index - self.entry_bar
            pos_dir = ctx.position_direction()

            # Time-based unwind or indicator exhaustion
            should_exit = False
            exit_reason = ""

            if bars_in_pos >= self.max_hold_bars:
                should_exit = True
                exit_reason = "time_horizon_reached"
            elif pos_dir == "long" and rsi > 68.0:
                should_exit = True
                exit_reason = "long_rsi_overbought_exhaustion"
            elif pos_dir == "short" and rsi < 32.0:
                should_exit = True
                exit_reason = "short_rsi_oversold_exhaustion"

            if should_exit:
                self.prev_funding = current_funding
                self.last_trade_bar = ctx.bar_index
                return ctx.signal(
                    "flat",
                    confidence=0.65,
                    metadata={
                        "reason": exit_reason,
                        "bars_held": bars_in_pos,
                        "rsi": round(rsi, 2),
                        "funding": current_funding,
                        "price": current_price
                    }
                )

            self.prev_funding = current_funding
            return None

        # Cooldown check
        if ctx.bar_index - self.last_trade_bar < self.cooldown_bars:
            self.prev_funding = current_funding
            return None

        # Detect funding flip event
        signal = None
        if self.prev_funding is not None:
            # Long flush -> Funding flips from positive to negative (excess longs wiped out)
            if self.prev_funding > self.funding_deadband and current_funding < -self.funding_deadband:
                if rsi < 58.0:  # Avoid longing into extreme overbought
                    self.entry_bar = ctx.bar_index
                    self.last_trade_bar = ctx.bar_index
                    conf = min(0.85, 0.60 + abs(current_funding) * 1000)
                    signal = ctx.signal(
                        "long",
                        confidence=conf,
                        stop_loss_bps=self.METADATA["declared_sl_bps"],
                        take_profit_bps=self.METADATA["declared_tp_bps"],
                        horizon_seconds=self.METADATA["declared_hold_seconds"],
                        metadata={
                            "reason": "funding_flip_positive_to_negative_flush",
                            "prev_funding": self.prev_funding,
                            "current_funding": current_funding,
                            "rsi": round(rsi, 2),
                            "ema24": round(ema24, 2),
                            "price": current_price
                        }
                    )

            # Short squeeze / top flush -> Funding flips from negative to positive (shorts capitulated)
            elif self.prev_funding < -self.funding_deadband and current_funding > self.funding_deadband:
                if rsi > 42.0:  # Avoid shorting into extreme oversold
                    self.entry_bar = ctx.bar_index
                    self.last_trade_bar = ctx.bar_index
                    conf = min(0.85, 0.60 + abs(current_funding) * 1000)
                    signal = ctx.signal(
                        "short",
                        confidence=conf,
                        stop_loss_bps=self.METADATA["declared_sl_bps"],
                        take_profit_bps=self.METADATA["declared_tp_bps"],
                        horizon_seconds=self.METADATA["declared_hold_seconds"],
                        metadata={
                            "reason": "funding_flip_negative_to_positive_capitulation",
                            "prev_funding": self.prev_funding,
                            "current_funding": current_funding,
                            "rsi": round(rsi, 2),
                            "ema24": round(ema24, 2),
                            "price": current_price
                        }
                    )

        self.prev_funding = current_funding
        return signal