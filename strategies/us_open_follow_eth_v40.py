from domains.strategy_contract import Strategy, BarContext, Signal
from typing import Optional, Dict, Any
import statistics

class UsOpenFollowEth(Strategy):
    METADATA = {
        "name": "US Open Continuation ETH",
        "domain": "eth_usdc",
        "declared_sl_bps": 220.0,
        "declared_tp_bps": 380.0,
        "declared_hold_seconds": 21600,  # Max 6 hours
        "warmup_bars": 35,
    }

    def initialize(self, ctx: BarContext) -> None:
        self.last_trade_date = None
        self.cooldown_until_bar = 0

    def _atr(self, highs, lows, closes, period=14):
        if len(closes) < period + 1:
            return None
        trs = []
        for i in range(1, len(closes)):
            tr = max(highs[i] - lows[i], abs(highs[i] - closes[i - 1]), abs(lows[i] - closes[i - 1]))
            trs.append(tr)
        return statistics.mean(trs[-period:])

    def _median_range(self, highs, lows, period=24):
        if len(highs) < period:
            return None
        ranges = [h - l for h, l in zip(highs[-period:], lows[-period:])]
        return statistics.median(ranges)

    def on_bar(self, ctx: BarContext) -> Optional[Signal]:
        ts = ctx.bar.timestamp
        current_date = ts.date()
        hour = ts.hour

        # 1. Force exit at UTC 21 or later
        if ctx.has_position():
            if hour >= 21 or hour < 13:
                pos = ctx.position
                entry_p = pos.get("entry_price", ctx.bar.close) if pos else ctx.bar.close
                pnl = (ctx.bar.close - entry_p) / entry_p if ctx.position_direction() == "long" else (entry_p - ctx.bar.close) / entry_p
                self.cooldown_until_bar = ctx.bar_index + 4
                return ctx.signal("flat", confidence=0.8, metadata={
                    "reason": "session_close_utc21",
                    "exit_price": ctx.bar.close,
                    "ret_bps": pnl * 10000,
                    "hour": hour
                })
            return None

        # 2. Warmup and cooldown checks
        closes = ctx.closes(30)
        highs = ctx.highs(30)
        lows = ctx.lows(30)
        if len(closes) < 30 or ctx.bar_index < self.cooldown_until_bar:
            return None

        # 3. Only trigger on the completion of the 13:00-15:00 window, evaluated at hour 15 (bar close)
        if hour != 15:
            return None

        # 4. Enforce strict 1-trade-per-day restriction
        if self.last_trade_date == current_date:
            return None

        # 5. Measure the opening move across the 2-hour window (13:00-15:00)
        # Bar at idx -1 corresponds to hour 14->15, idx -2 corresponds to 13->14. Open at idx -2 is 13:00 open.
        opens = ctx.opens(5)
        if len(opens) < 3:
            return None
        open_13 = opens[-2]
        close_15 = ctx.bar.close
        net_move = close_15 - open_13
        abs_move = abs(net_move)

        med_range = self._median_range(highs, lows, 24)
        atr_val = self._atr(highs, lows, closes, 14)
        if med_range is None or atr_val is None or med_range <= 0 or atr_val <= 0:
            return None

        # Requirement: Opening move must exceed recent median hourly range
        # And not be severely overextended (net move should not exceed 3.5x ATR)
        if abs_move < (1.1 * med_range) or abs_move > (3.5 * atr_val):
            return None

        # 6. Directional determination
        direction = "long" if net_move > 0 else "short"
        confidence = min(0.9, max(0.55, 0.55 + (abs_move / (4.0 * atr_val)) * 0.35))

        self.last_trade_date = current_date
        self.cooldown_until_bar = ctx.bar_index + 6

        return ctx.signal(
            direction,
            confidence=confidence,
            stop_loss_bps=self.METADATA["declared_sl_bps"],
            take_profit_bps=self.METADATA["declared_tp_bps"],
            horizon_seconds=self.METADATA["declared_hold_seconds"],
            metadata={
                "reason": "us_open_continuation",
                "net_move": net_move,
                "abs_move": abs_move,
                "med_range": med_range,
                "atr": atr_val,
                "hour": hour
            }
        )

    def on_position_close(self, ctx: BarContext, position: Dict[str, Any]) -> None:
        self.cooldown_until_bar = max(self.cooldown_until_bar, ctx.bar_index + 3)