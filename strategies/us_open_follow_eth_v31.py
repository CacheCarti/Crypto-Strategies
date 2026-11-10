from domains.strategy_contract import Strategy, BarContext, Signal
from typing import Optional, Dict, Any
import statistics

class UsOpenContinuationEth(Strategy):
    METADATA = {
        "name": "US Open Continuation ETH",
        "domain": "eth_usdc",
        "declared_sl_bps": 220.0,
        "declared_tp_bps": 380.0,
        "declared_hold_seconds": 18000,
        "warmup_bars": 35,
    }

    def initialize(self, ctx: BarContext) -> None:
        self.last_traded_day = -1
        self.cooldown_until_bar = 0

    def _atr(self, highs, lows, closes, period=14):
        if len(closes) < period + 1:
            return None
        trs = []
        for i in range(1, len(closes)):
            tr = max(highs[i] - lows[i], abs(highs[i] - closes[i - 1]), abs(lows[i] - closes[i - 1]))
            trs.append(tr)
        return sum(trs[-period:]) / period

    def on_bar(self, ctx: BarContext) -> Optional[Signal]:
        closes = ctx.closes(35)
        highs = ctx.highs(35)
        lows = ctx.lows(35)

        if len(closes) < 35:
            return None

        current_time = ctx.bar.timestamp
        current_day = current_time.toordinal()
        current_hour = current_time.hour

        # Exit intraday before session wraps up (UTC 21:00 or later)
        if ctx.has_position():
            if current_hour >= 21 or current_hour < 13:
                return ctx.signal(
                    "flat",
                    confidence=0.6,
                    metadata={
                        "reason": "session_close_utc21",
                        "hour": current_hour,
                        "close": ctx.bar.close
                    }
                )
            return None

        # Cooldown guard & 1 trade per calendar day guard
        if ctx.bar_index < self.cooldown_until_bar:
            return None
        if current_day == self.last_traded_day:
            return None

        # Check entry trigger at UTC 15:00 (evaluates opening move across UTC 13:00 - 15:00)
        if current_hour == 15:
            # Context ranges and movement over the 2-hour opening window
            two_hr_move = closes[-1] - closes[-3]
            open_price_13 = closes[-3]
            move_pct = (two_hr_move / open_price_13) * 10000.0  # in bps

            # Recent hourly ranges (past 24 bars) to compute median hourly range
            ranges = [highs[i] - lows[i] for i in range(-24, 0)]
            med_range = statistics.median(ranges)
            two_hr_range = max(highs[-2:]) - min(lows[-2:])

            atr = self._atr(highs, lows, closes, period=14)
            if atr is None or atr <= 0:
                return None

            atr_bps = (atr / closes[-1]) * 10000.0
            
            # Avoid crisis regimes
            market_regime = ctx.market.get("regime", "NORMAL")
            if market_regime in ("CRISIS", "MELTDOWN"):
                return None

            # Condition: Opening move must be clear, exceed median range, but not be completely exhausted
            # Minimum move: 0.75 * ATR; Maximum move: 2.75 * ATR (not overextended)
            min_thrust = atr * 0.75
            max_thrust = atr * 2.75

            # Long Setup: Clear upward thrust that exceeds typical median volatility
            if two_hr_move > min_thrust and two_hr_move < max_thrust and two_hr_range > med_range * 1.2:
                self.last_traded_day = current_day
                self.cooldown_until_bar = ctx.bar_index + 6
                conf = min(0.85, max(0.55, 0.5 + (abs(two_hr_move) / (max_thrust * 2))))
                return ctx.signal(
                    "long",
                    confidence=conf,
                    stop_loss_bps=self.METADATA["declared_sl_bps"],
                    take_profit_bps=self.METADATA["declared_tp_bps"],
                    horizon_seconds=self.METADATA["declared_hold_seconds"],
                    metadata={
                        "reason": "us_open_bullish_continuation",
                        "move_bps": move_pct,
                        "two_hr_range": two_hr_range,
                        "med_range": med_range,
                        "atr_bps": atr_bps
                    }
                )

            # Short Setup: Clear downward thrust that exceeds typical median volatility
            if two_hr_move < -min_thrust and two_hr_move > -max_thrust and two_hr_range > med_range * 1.2:
                self.last_traded_day = current_day
                self.cooldown_until_bar = ctx.bar_index + 6
                conf = min(0.85, max(0.55, 0.5 + (abs(two_hr_move) / (max_thrust * 2))))
                return ctx.signal(
                    "short",
                    confidence=conf,
                    stop_loss_bps=self.METADATA["declared_sl_bps"],
                    take_profit_bps=self.METADATA["declared_tp_bps"],
                    horizon_seconds=self.METADATA["declared_hold_seconds"],
                    metadata={
                        "reason": "us_open_bearish_continuation",
                        "move_bps": move_pct,
                        "two_hr_range": two_hr_range,
                        "med_range": med_range,
                        "atr_bps": atr_bps
                    }
                )

        return None

    def on_position_close(self, ctx: BarContext, position: Dict[str, Any]) -> None:
        self.cooldown_until_bar = ctx.bar_index + 3