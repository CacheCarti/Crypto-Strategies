from domains.strategy_contract import Strategy, BarContext, Signal
from typing import Optional, Dict, Any
import math

class StreakReversalAdaptive(Strategy):
    METADATA = {
        "name": "StreakReversalAdaptive",
        "domain": "btc_usdc",
        "declared_sl_bps": 220.0,
        "declared_tp_bps": 340.0,
        "declared_hold_seconds": 14400,
        "warmup_bars": 60,
        "required_features": ["funding_rate_btcusdt"],
    }

    def initialize(self, ctx: BarContext) -> None:
        self.cooldown_period = 4
        self.last_exit_bar = -999
        self.rsi_period = 14
        self.window_streak = 50
        self.min_streak_floor = 5

    def _rsi(self, closes, period=14):
        if len(closes) < period + 1:
            return 50.0
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
        closes = ctx.closes(self.window_streak + 20)
        opens = ctx.opens(self.window_streak + 20)
        
        if len(closes) < self.METADATA["warmup_bars"]:
            return None

        # Determine consecutive directional streak
        curr_streak = 0
        streak_direction = 0  # 1 for up, -1 for down
        
        # Check latest completed bar sequence backwards
        last_diff = closes[-1] - opens[-1]
        if last_diff > 0:
            streak_direction = 1
        elif last_diff < 0:
            streak_direction = -1
        else:
            streak_direction = 0

        if streak_direction != 0:
            for c, o in zip(reversed(closes), reversed(opens)):
                diff = c - o
                if (streak_direction == 1 and diff > 0) or (streak_direction == -1 and diff < 0):
                    curr_streak += 1
                else:
                    break

        # In-position management
        if ctx.has_position():
            pos_dir = ctx.position_direction()
            latest_diff = closes[-1] - opens[-1]
            
            # Reversal bar snapped the streak: exit promptly
            if pos_dir == "long" and latest_diff > 0:
                self.last_exit_bar = ctx.bar_index
                return ctx.signal("flat", confidence=0.7, metadata={"reason": "streak_snapped_green_exit", "streak": curr_streak})
            elif pos_dir == "short" and latest_diff < 0:
                self.last_exit_bar = ctx.bar_index
                return ctx.signal("flat", confidence=0.7, metadata={"reason": "streak_snapped_red_exit", "streak": curr_streak})
            return None

        # Cooldown guard
        if ctx.bar_index - self.last_exit_bar < self.cooldown_period:
            return None

        # Regime and market filters to avoid exhaustion failures during market meltdowns
        market_regime = ctx.market.get("regime", "NORMAL")
        if market_regime in ("MELTDOWN", "CRISIS"):
            return None

        funding = ctx.features.get("funding_rate_btcusdt", 0.0)
        rsi = self._rsi(closes, self.rsi_period)

        # Dynamic streak threshold based on historical streaks in rolling window
        hist_streaks = []
        tmp_streak = 1
        tmp_dir = 1 if (closes[1] - opens[1]) > 0 else -1
        for i in range(2, len(closes) - 1):
            d = 1 if (closes[i] - opens[i]) > 0 else (-1 if (closes[i] - opens[i]) < 0 else 0)
            if d == tmp_dir and d != 0:
                tmp_streak += 1
            else:
                if tmp_streak >= 2:
                    hist_streaks.append(tmp_streak)
                tmp_streak = 1
                tmp_dir = d

        # Dynamic 90th percentile streak length or fallback to 5
        streak_threshold = self.min_streak_floor
        if len(hist_streaks) >= 10:
            hist_streaks.sort()
            idx = int(0.90 * len(hist_streaks))
            streak_threshold = max(self.min_streak_floor, hist_streaks[idx])

        # Entry logic: Long exhaustion after consecutive red bars
        if streak_direction == -1 and curr_streak >= streak_threshold:
            if rsi < 42.0 and funding < 0.0003:
                conf = min(0.95, 0.55 + 0.08 * (curr_streak - streak_threshold))
                return ctx.signal(
                    "long",
                    confidence=conf,
                    stop_loss_bps=self.METADATA["declared_sl_bps"],
                    take_profit_bps=self.METADATA["declared_tp_bps"],
                    horizon_seconds=self.METADATA["declared_hold_seconds"],
                    metadata={
                        "reason": "down_streak_exhaustion_long",
                        "streak": curr_streak,
                        "streak_threshold": streak_threshold,
                        "rsi": round(rsi, 2),
                        "funding": funding,
                    }
                )

        # Entry logic: Short exhaustion after consecutive green bars
        if streak_direction == 1 and curr_streak >= streak_threshold:
            if rsi > 58.0 and funding > -0.0003:
                conf = min(0.95, 0.55 + 0.08 * (curr_streak - streak_threshold))
                return ctx.signal(
                    "short",
                    confidence=conf,
                    stop_loss_bps=self.METADATA["declared_sl_bps"],
                    take_profit_bps=self.METADATA["declared_tp_bps"],
                    horizon_seconds=self.METADATA["declared_hold_seconds"],
                    metadata={
                        "reason": "up_streak_exhaustion_short",
                        "streak": curr_streak,
                        "streak_threshold": streak_threshold,
                        "rsi": round(rsi, 2),
                        "funding": funding,
                    }
                )

        return None