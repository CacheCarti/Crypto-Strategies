from domains.strategy_contract import Strategy, BarContext, Signal
from typing import Optional, Dict, Any
import math

class BtcStreakExhaustionReversal(Strategy):
    METADATA = {
        "name": "BtcStreakExhaustionReversal",
        "domain": "btc_usdc",
        "declared_sl_bps": 250.0,
        "declared_tp_bps": 350.0,
        "declared_hold_seconds": 14400,  # ~4 hours swing target
        "warmup_bars": 35,
    }

    def initialize(self, ctx: BarContext) -> None:
        self.min_streak = 5
        self.cooldown_bars = 4
        self.last_exit_bar = -100
        self.entry_bar = -100

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

    def _atr(self, highs, lows, closes, period=14):
        if len(closes) < period + 1:
            return None
        trs = []
        for i in range(1, len(closes)):
            tr = max(highs[i] - lows[i], abs(highs[i] - closes[i - 1]), abs(lows[i] - closes[i - 1]))
            trs.append(tr)
        return sum(trs[-period:]) / period

    def on_position_close(self, ctx: BarContext, position: Dict[str, Any]) -> None:
        self.last_exit_bar = ctx.bar_index

    def on_bar(self, ctx: BarContext) -> Optional[Signal]:
        closes = ctx.closes(35)
        opens = ctx.opens(35)
        highs = ctx.highs(35)
        lows = ctx.lows(35)

        if len(closes) < 35:
            return None

        current_close = ctx.bar.close
        current_open = ctx.bar.open
        has_pos = ctx.has_position()
        pos_dir = ctx.position_direction()

        # Handle Open Positions: exit on first counter-color bar or 5-bar max hold
        if has_pos:
            bars_held = ctx.bar_index - self.entry_bar
            is_green = current_close > current_open
            is_red = current_close < current_open

            if (pos_dir == "long" and is_red) or (pos_dir == "short" and is_green) or bars_held >= 5:
                reason = "streak_snap_exit" if bars_held < 5 else "max_hold_exhaustion_exit"
                return ctx.signal("flat", confidence=0.7, metadata={
                    "reason": reason,
                    "bars_held": bars_held,
                    "close": current_close,
                    "open": current_open
                })
            return None

        # Mandatory Cooldown Gate
        if ctx.bar_index - self.last_exit_bar < self.cooldown_bars:
            return None

        # Count Consecutive Directional Bars up to current bar
        streak_down = 0
        streak_up = 0
        
        # Check backward from current bar
        for i in range(len(closes) - 1, -1, -1):
            if closes[i] < opens[i]:
                if streak_up > 0:
                    break
                streak_down += 1
            elif closes[i] > opens[i]:
                if streak_down > 0:
                    break
                streak_up += 1
            else:
                break

        if streak_down < self.min_streak and streak_up < self.min_streak:
            return None

        # Volatility confirmation: require elevated current candle range relative to ATR
        atr = self._atr(highs, lows, closes, 14)
        if atr is None or atr <= 0.0:
            return None

        current_range = ctx.bar.high - ctx.bar.low
        # Volume/Volatility expansion check (blowoff/capitulation candle)
        is_vol_expansion = current_range > (atr * 0.9)

        # Technical oscillator confirmation: RSI momentum divergence
        rsi = self._rsi(closes, 14)
        if rsi is None:
            return None

        # Market regime filter: avoid fading strong trending crises
        regime = ctx.market.get("regime", "NORMAL")
        crisis_score = ctx.market.get("crisis_score", 0.0)
        if regime in ["CRISIS", "MELTDOWN"] or crisis_score > 0.6:
            return None

        # Long exhaustion entry: 5+ down bars + RSI oversold/extended + volatility range
        if streak_down >= self.min_streak and is_vol_expansion and rsi < 36.0:
            confidence = min(0.60 + (streak_down - self.min_streak) * 0.10, 0.95)
            self.entry_bar = ctx.bar_index
            return ctx.signal(
                "long",
                confidence=confidence,
                stop_loss_bps=self.METADATA["declared_sl_bps"],
                take_profit_bps=self.METADATA["declared_tp_bps"],
                metadata={
                    "reason": "exhaustion_down_streak_snap",
                    "streak_down": streak_down,
                    "rsi": round(rsi, 2),
                    "atr": round(atr, 2),
                    "range_to_atr": round(current_range / atr, 2),
                }
            )

        # Short exhaustion entry: 5+ up bars + RSI overbought/extended + volatility range
        if streak_up >= self.min_streak and is_vol_expansion and rsi > 64.0:
            confidence = min(0.60 + (streak_up - self.min_streak) * 0.10, 0.95)
            self.entry_bar = ctx.bar_index
            return ctx.signal(
                "short",
                confidence=confidence,
                stop_loss_bps=self.METADATA["declared_sl_bps"],
                take_profit_bps=self.METADATA["declared_tp_bps"],
                metadata={
                    "reason": "exhaustion_up_streak_snap",
                    "streak_up": streak_up,
                    "rsi": round(rsi, 2),
                    "atr": round(atr, 2),
                    "range_to_atr": round(current_range / atr, 2),
                }
            )

        return None