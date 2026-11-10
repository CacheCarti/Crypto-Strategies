from domains.strategy_contract import Strategy, BarContext, Signal
from typing import Optional, Dict, Any
import math

class BtcStreakExhaustionReversal(Strategy):
    METADATA = {
        "name": "BtcStreakExhaustionReversal",
        "domain": "btc_usdc",
        "declared_sl_bps": 220.0,
        "declared_tp_bps": 340.0,
        "declared_hold_seconds": 10800,  # 3 hours
        "warmup_bars": 30,
        "required_features": ["funding_rate_btcusdt"],
    }

    def initialize(self, ctx: BarContext) -> None:
        self.streak_threshold = 5
        self.cooldown_bars = 4
        self.last_exit_bar = -999
        self.rsi_period = 14

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
        if avg_loss == 0:
            return 100.0
        rs = avg_gain / avg_loss
        return 100.0 - (100.0 / (1.0 + rs))

    def on_position_close(self, ctx: BarContext, position: Dict[str, Any]) -> None:
        self.last_exit_bar = ctx.bar_index

    def on_bar(self, ctx: BarContext) -> Optional[Signal]:
        closes = ctx.closes(30)
        opens = ctx.opens(30)
        if len(closes) < 20 or len(opens) < 20:
            return None

        # Manage active position: exit on first counter-streak bar or time stop
        if ctx.has_position():
            pos_dir = ctx.position_direction()
            is_green = closes[-1] > opens[-1]
            is_red = closes[-1] < opens[-1]

            if pos_dir == "long" and is_red:
                return ctx.signal(
                    "flat",
                    confidence=0.7,
                    metadata={"reason": "long_counter_color_exit", "close": closes[-1], "open": opens[-1]}
                )
            elif pos_dir == "short" and is_green:
                return ctx.signal(
                    "flat",
                    confidence=0.7,
                    metadata={"reason": "short_counter_color_exit", "close": closes[-1], "open": opens[-1]}
                )
            return None

        # Mandatory cooldown guard
        if ctx.bar_index - self.last_exit_bar < self.cooldown_bars:
            return None

        # Avoid crisis/meltdown regimes where streaks are runaway cascades
        market_regime = ctx.market.get("regime", "NORMAL")
        if market_regime in ("CRISIS", "MELTDOWN"):
            return None

        # Count consecutive directional bars
        down_streak = 0
        up_streak = 0

        # Scan backwards up to 12 bars
        idx = len(closes) - 1
        while idx >= 0:
            if closes[idx] < opens[idx]:
                if up_streak > 0:
                    break
                down_streak += 1
            elif closes[idx] > opens[idx]:
                if down_streak > 0:
                    break
                up_streak += 1
            else:
                break
            idx -= 1

        rsi = self._rsi(closes, self.rsi_period)
        if rsi is None:
            return None

        funding = ctx.features.get("funding_rate_btcusdt", 0.0)

        # Long exhaustion reversal: 5+ down bars, RSI stretched low, funding not heavily negative
        if down_streak >= self.streak_threshold:
            if rsi < 36.0 and funding > -0.0005:
                confidence = min(0.60 + 0.08 * (down_streak - self.streak_threshold), 0.95)
                return ctx.signal(
                    "long",
                    confidence=confidence,
                    stop_loss_bps=self.METADATA["declared_sl_bps"],
                    take_profit_bps=self.METADATA["declared_tp_bps"],
                    horizon_seconds=self.METADATA["declared_hold_seconds"],
                    metadata={
                        "reason": "streak_down_exhaustion_snap",
                        "streak": down_streak,
                        "rsi": round(rsi, 2),
                        "funding": round(funding, 6),
                    }
                )

        # Short exhaustion reversal: 5+ up bars, RSI stretched high, funding not excessively positive
        if up_streak >= self.streak_threshold:
            if rsi > 64.0 and funding < 0.0005:
                confidence = min(0.60 + 0.08 * (up_streak - self.streak_threshold), 0.95)
                return ctx.signal(
                    "short",
                    confidence=confidence,
                    stop_loss_bps=self.METADATA["declared_sl_bps"],
                    take_profit_bps=self.METADATA["declared_tp_bps"],
                    horizon_seconds=self.METADATA["declared_hold_seconds"],
                    metadata={
                        "reason": "streak_up_exhaustion_snap",
                        "streak": up_streak,
                        "rsi": round(rsi, 2),
                        "funding": round(funding, 6),
                    }
                )

        return None