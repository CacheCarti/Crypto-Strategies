from domains.strategy_contract import Strategy, BarContext, Signal
from typing import Optional, Dict, Any
import math

class EthFundingContrarian(Strategy):
    METADATA = {
        "name": "ETH Funding Contrarian Mean Reversion",
        "domain": "eth_usdc",
        "declared_sl_bps": 220.0,
        "declared_tp_bps": 340.0,
        "declared_hold_seconds": 18000,  # ~5 hours target hold
        "warmup_bars": 40,
        "required_features": ["funding_rate_ethusdt"],
    }

    def initialize(self, ctx: BarContext) -> None:
        self.funding_history = []
        self.last_exit_bar = -999
        self.bars_in_pos = 0
        self.max_hold_bars = 10
        self.cooldown_bars = 6
        self.pos_direction = None

    def _sma(self, values, period):
        if len(values) < period:
            return None
        return sum(values[-period:]) / period

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
        self.bars_in_pos = 0
        self.pos_direction = None

    def on_bar(self, ctx: BarContext) -> Optional[Signal]:
        closes = ctx.closes(40)
        if len(closes) < 35:
            return None

        # Track persistent funding
        current_funding = ctx.features.get("funding_rate_ethusdt", 0.0)
        self.funding_history.append(current_funding)
        if len(self.funding_history) > 10:
            self.funding_history.pop(0)

        # Baseline indicators
        rsi = self._rsi(closes, 14)
        sma30 = self._sma(closes, 30)
        if rsi is None or sma30 is None:
            return None

        has_pos = ctx.has_position()
        if has_pos:
            self.bars_in_pos += 1
            direction = ctx.position_direction() or self.pos_direction

            # Exit criteria 1: Funding normalized
            funding_normalized = abs(current_funding) <= 0.00005
            # Exit criteria 2: Timeout
            timed_out = self.bars_in_pos >= self.max_hold_bars

            # Exit criteria 3: RSI extremes reached
            rsi_reverted = (direction == "long" and rsi >= 62.0) or (direction == "short" and rsi <= 38.0)

            if funding_normalized or timed_out or rsi_reverted:
                exit_reason = "funding_normalized" if funding_normalized else ("timed_out" if timed_out else "rsi_reverted")
                return ctx.signal(
                    "flat",
                    confidence=0.6,
                    metadata={
                        "reason": exit_reason,
                        "funding": current_funding,
                        "rsi": rsi,
                        "bars_held": self.bars_in_pos,
                    }
                )
            return None

        # Enforce post-trade cooldown
        if ctx.bar_index - self.last_exit_bar < self.cooldown_bars:
            return None

        # Check persistence across last 3 bars (avoid 1-bar spikes)
        if len(self.funding_history) < 3:
            return None
        recent_3 = self.funding_history[-3:]
        avg_funding_3 = sum(recent_3) / 3.0

        close = closes[-1]
        dist_sma_pct = (close - sma30) / sma30

        # Long Setup: funding is heavily negative, persistent, not in a runaway waterfall
        # crowd paying to short -> expect squeeze bounce
        if avg_funding_3 < -0.00015 and current_funding < -0.00010:
            # Price filter: mild dip or local consolidation, avoid outright collapse
            if rsi < 44.0 and dist_sma_pct > -0.045:
                self.pos_direction = "long"
                self.bars_in_pos = 0
                conf = min(0.9, 0.55 + abs(avg_funding_3) * 1500)
                return ctx.signal(
                    "long",
                    confidence=conf,
                    stop_loss_bps=220.0,
                    take_profit_bps=340.0,
                    horizon_seconds=18000,
                    metadata={
                        "reason": "negative_funding_squeeze_long",
                        "avg_funding_3": avg_funding_3,
                        "funding": current_funding,
                        "rsi": rsi,
                        "dist_sma_pct": dist_sma_pct,
                    }
                )

        # Short Setup: funding is heavily positive, persistent, not in parabolic breakout
        # crowd paying to long -> expect leverage flush
        if avg_funding_3 > 0.00025 and current_funding > 0.00018:
            # Price filter: extended or topping, avoid fading super-strong trending breakout
            if rsi > 58.0 and dist_sma_pct < 0.045:
                self.pos_direction = "short"
                self.bars_in_pos = 0
                conf = min(0.9, 0.55 + avg_funding_3 * 1200)
                return ctx.signal(
                    "short",
                    confidence=conf,
                    stop_loss_bps=220.0,
                    take_profit_bps=340.0,
                    horizon_seconds=18000,
                    metadata={
                        "reason": "positive_funding_exhaustion_short",
                        "avg_funding_3": avg_funding_3,
                        "funding": current_funding,
                        "rsi": rsi,
                        "dist_sma_pct": dist_sma_pct,
                    }
                )

        return None