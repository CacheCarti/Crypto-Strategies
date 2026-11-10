from domains.strategy_contract import Strategy, BarContext, Signal
from typing import Optional, Dict, Any
import math

class BtcStreakReversal(Strategy):
    METADATA = {
        "name": "BtcStreakReversal",
        "domain": "btc_usdc",
        "declared_sl_bps": 220.0,
        "declared_tp_bps": 320.0,
        "declared_hold_seconds": 14400,
        "warmup_bars": 50,
        "required_features": ["fear_greed_index"],
    }

    def initialize(self, ctx: BarContext) -> None:
        self.min_streak = 5
        self.cooldown_bars = 4
        self.last_exit_bar = -999
        self.entry_bar = -999
        self.max_hold_bars = 6

    def _rsi(self, closes, period=14):
        if len(closes) < period + 1:
            return 50.0
        gains, losses = 0.0, 0.0
        for i in range(1, period + 1):
            diff = closes[-period - 1 + i] - closes[-period - 1 + i - 1]
            if diff > 0:
                gains += diff
            else:
                losses -= diff
        avg_gain = gains / period
        avg_loss = losses / period
        if avg_loss == 0.0:
            return 100.0
        rs = avg_gain / avg_loss
        return 100.0 - (100.0 / (1.0 + rs))

    def on_position_close(self, ctx: BarContext, position: Dict[str, Any]) -> None:
        self.last_exit_bar = ctx.bar_index

    def on_bar(self, ctx: BarContext) -> Optional[Signal]:
        closes = ctx.closes(50)
        opens = ctx.opens(50)
        if len(closes) < 30 or len(opens) < 30:
            return None

        # Manage open position exit logic
        if ctx.has_position():
            pos_dir = ctx.position_direction()
            bars_in_trade = ctx.bar_index - self.entry_bar
            curr_bar_bullish = ctx.bar.close > ctx.bar.open
            curr_bar_bearish = ctx.bar.close < ctx.bar.open

            # Snap exit: first counter-color bar or time exhaustion
            should_exit = False
            reason = ""
            if pos_dir == "long" and curr_bar_bullish:
                should_exit = True
                reason = "long_snap_bullish_close"
            elif pos_dir == "short" and curr_bar_bearish:
                should_exit = True
                reason = "short_snap_bearish_close"
            elif bars_in_trade >= self.max_hold_bars:
                should_exit = True
                reason = "max_hold_reached"

            if should_exit:
                return ctx.signal(
                    "flat",
                    confidence=0.6,
                    metadata={"reason": reason, "bars_in_trade": bars_in_trade, "close": ctx.bar.close}
                )
            return None

        # Cooldown guard after trade exit
        if ctx.bar_index - self.last_exit_bar < self.cooldown_bars:
            return None

        # Market regime filter: avoid Meltdown/Crisis runs where streaks sustain indefinitely
        market_regime = ctx.market.get("regime", "NORMAL")
        if market_regime in ("CRISIS", "MELTDOWN"):
            return None

        crisis_score = ctx.market.get("crisis_score", 0.0)
        if crisis_score > 0.65:
            return None

        # Count consecutive directional bars up to current closed bar
        up_streak = 0
        down_streak = 0
        n = len(closes)
        for i in range(1, n + 1):
            c = closes[-i]
            o = opens[-i]
            if c > o:
                if down_streak > 0:
                    break
                up_streak += 1
            elif c < o:
                if up_streak > 0:
                    break
                down_streak += 1
            else:
                break

        rsi_val = self._rsi(closes, 14)
        fear_greed = ctx.features.get("fear_greed_index", 50.0)

        # Reversal Long setup: consecutive down bars + RSI washed out
        if down_streak >= self.min_streak and rsi_val <= 38.0:
            confidence = min(0.95, 0.60 + 0.07 * (down_streak - self.min_streak))
            self.entry_bar = ctx.bar_index
            return ctx.signal(
                "long",
                confidence=confidence,
                stop_loss_bps=self.METADATA["declared_sl_bps"],
                take_profit_bps=self.METADATA["declared_tp_bps"],
                horizon_seconds=self.METADATA["declared_hold_seconds"],
                metadata={
                    "reason": "exhaustion_down_streak_snap",
                    "streak_count": down_streak,
                    "rsi": round(rsi_val, 2),
                    "fear_greed": fear_greed,
                    "regime": market_regime,
                }
            )

        # Reversal Short setup: consecutive up bars + RSI overbought
        if up_streak >= self.min_streak and rsi_val >= 62.0:
            confidence = min(0.95, 0.60 + 0.07 * (up_streak - self.min_streak))
            self.entry_bar = ctx.bar_index
            return ctx.signal(
                "short",
                confidence=confidence,
                stop_loss_bps=self.METADATA["declared_sl_bps"],
                take_profit_bps=self.METADATA["declared_tp_bps"],
                horizon_seconds=self.METADATA["declared_hold_seconds"],
                metadata={
                    "reason": "exhaustion_up_streak_snap",
                    "streak_count": up_streak,
                    "rsi": round(rsi_val, 2),
                    "fear_greed": fear_greed,
                    "regime": market_regime,
                }
            )

        return None