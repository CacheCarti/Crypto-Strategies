from domains.strategy_contract import Strategy, BarContext, Signal
from typing import Optional, Dict, Any
import math

class BtcStreakReversal(Strategy):
    METADATA = {
        "name": "BtcStreakReversal",
        "domain": "btc_usdc",
        "declared_sl_bps": 220.0,
        "declared_tp_bps": 280.0,
        "declared_hold_seconds": 14400,  # 4 hours intended hold
        "warmup_bars": 35,
    }

    def initialize(self, ctx: BarContext) -> None:
        self.streak_threshold = 5
        self.cooldown_bars = 4
        self.max_hold_bars = 6
        self.last_exit_bar = -999
        self.entry_bar = -999

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
        self.entry_bar = -999

    def on_bar(self, ctx: BarContext) -> Optional[Signal]:
        closes = ctx.closes(30)
        opens = ctx.opens(30)
        highs = ctx.highs(30)
        lows = ctx.lows(30)

        if len(closes) < 25:
            return None

        # Manage open position: Volatility/Structure exit or time stop
        if ctx.has_position():
            pos_dir = ctx.position_direction()
            current_bar_dir = 1 if ctx.bar.close > ctx.bar.open else (-1 if ctx.bar.close < ctx.bar.open else 0)
            bars_held = ctx.bar_index - self.entry_bar if self.entry_bar > 0 else 1

            # Structural exhaustion snap: exit if streak snaps cleanly (opposite color)
            snapped = (pos_dir == "long" and current_bar_dir == 1) or (pos_dir == "short" and current_bar_dir == -1)
            time_expired = bars_held >= self.max_hold_bars

            if snapped or time_expired:
                reason = "streak_snap_target_reached" if snapped else "time_stop_exhaustion_expired"
                self.last_exit_bar = ctx.bar_index
                return ctx.signal(
                    "flat",
                    confidence=0.6,
                    metadata={"reason": reason, "bars_held": bars_held, "close": ctx.bar.close}
                )
            return None

        # Cooldown guard after trade exit
        if ctx.bar_index - self.last_exit_bar < self.cooldown_bars:
            return None

        # Avoid crisis/meltdown regimes for counter-trend mean reversion
        regime = ctx.market.get("regime", "NORMAL")
        if regime in ("CRISIS", "MELTDOWN"):
            return None

        # Count consecutive directional bars
        down_streak = 0
        up_streak = 0

        # Scan backwards from current bar
        for i in range(len(closes) - 1, -1, -1):
            c = closes[i]
            o = opens[i]
            if c < o and up_streak == 0:
                down_streak += 1
            elif c > o and down_streak == 0:
                up_streak += 1
            else:
                break

        atr = self._atr(highs, lows, closes, 14)
        if atr is None or ctx.bar.close == 0:
            return None

        # Dynamic volatility stop calibration in bps
        vol_sl_bps = max(150.0, min(350.0, (atr / ctx.bar.close) * 1.8 * 10000.0))
        vol_tp_bps = vol_sl_bps * 1.3

        # Entry logic: Exhaustion snaps
        if down_streak >= self.streak_threshold:
            # Scaled confidence based on streak depth (5 bars -> 0.65, 8+ bars -> 0.95)
            confidence = min(0.95, 0.65 + (down_streak - self.streak_threshold) * 0.10)
            self.entry_bar = ctx.bar_index
            return ctx.signal(
                "long",
                confidence=confidence,
                stop_loss_bps=vol_sl_bps,
                take_profit_bps=vol_tp_bps,
                horizon_seconds=14400,
                metadata={
                    "reason": "exhaustion_downstreak_reversal",
                    "streak_count": down_streak,
                    "atr": atr,
                    "vol_sl_bps": vol_sl_bps
                }
            )

        if up_streak >= self.streak_threshold:
            confidence = min(0.95, 0.65 + (up_streak - self.streak_threshold) * 0.10)
            self.entry_bar = ctx.bar_index
            return ctx.signal(
                "short",
                confidence=confidence,
                stop_loss_bps=vol_sl_bps,
                take_profit_bps=vol_tp_bps,
                horizon_seconds=14400,
                metadata={
                    "reason": "exhaustion_upstreak_reversal",
                    "streak_count": up_streak,
                    "atr": atr,
                    "vol_sl_bps": vol_sl_bps
                }
            )

        return None