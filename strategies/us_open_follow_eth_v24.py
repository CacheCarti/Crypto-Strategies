from domains.strategy_contract import Strategy, BarContext, Signal
from typing import Optional, Dict, Any
import math

class UsOpenContinuationEth(Strategy):
    METADATA = {
        "name": "US Open Session Continuation ETH",
        "domain": "eth_usdc",
        "declared_sl_bps": 220.0,
        "declared_tp_bps": 380.0,
        "declared_hold_seconds": 21600,
        "warmup_bars": 40,
        "required_features": ["btc_return_pct"],
    }

    def initialize(self, ctx: BarContext) -> None:
        self.last_traded_day = -1
        self.entry_bar = -1
        self.atr_period = 20

    def _atr(self, highs, lows, closes, period=14):
        if len(closes) < period + 1:
            return None
        trs = []
        for i in range(1, len(closes)):
            tr = max(highs[i] - lows[i], abs(highs[i] - closes[i - 1]), abs(lows[i] - closes[i - 1]))
            trs.append(tr)
        return sum(trs[-period:]) / period

    def _ema(self, values, period):
        if len(values) < period:
            return None
        k = 2.0 / (period + 1)
        ema = sum(values[:period]) / period
        for v in values[period:]:
            ema = v * k + ema * (1 - k)
        return ema

    def on_bar(self, ctx: BarContext) -> Optional[Signal]:
        closes = ctx.closes(45)
        highs = ctx.highs(45)
        lows = ctx.lows(45)
        opens = ctx.opens(45)

        if len(closes) < 40:
            return None

        ts = ctx.bar.timestamp
        current_day = ts.toordinal()
        current_hour = ts.hour

        # Hard session close by UTC 21 or after 6 hours
        if ctx.has_position():
            bars_held = ctx.bar_index - self.entry_bar if self.entry_bar != -1 else 0
            if current_hour >= 21 or bars_held >= 6:
                return ctx.signal(
                    "flat",
                    confidence=0.6,
                    metadata={"reason": "session_close_utc21", "hour": current_hour, "bars_held": bars_held}
                )
            return None

        # One trade per day limit
        if current_day == self.last_traded_day:
            return None

        # Entry window: Evaluate specifically at 15:00 UTC (closing bar of the 13:00-15:00 2-hour window)
        if current_hour != 15:
            return None

        atr = self._atr(highs, lows, closes, self.atr_period)
        if atr is None or atr <= 0:
            return None

        # Opening 2-hour displacement: open of bar at index -2 (13:00 UTC) vs close of bar at index -1 (15:00 UTC)
        open_13utc = opens[-2]
        close_15utc = closes[-1]
        session_move = close_15utc - open_13utc
        move_bps = (session_move / open_13utc) * 10000.0

        # Opening move must exceed recent median/normalized volatility threshold (0.75 * ATR)
        # but not be completely exhausted (> 3.0 * ATR)
        move_abs = abs(session_move)
        min_move_threshold = 0.75 * atr
        max_move_threshold = 3.0 * atr

        if not (min_move_threshold <= move_abs <= max_move_threshold):
            return None

        # Cross-asset confirmation: BTC 24h momentum must not oppose the session move
        btc_perf = ctx.features.get("btc_return_pct", 0.0)

        # Macro trend regime confirmation using EMA 34
        ema_trend = self._ema(closes, 34)
        if ema_trend is None:
            return None

        # Long continuation setup
        if session_move > 0 and btc_perf > -1.5 and close_15utc > ema_trend:
            self.last_traded_day = current_day
            self.entry_bar = ctx.bar_index
            return ctx.signal(
                "long",
                confidence=0.75,
                stop_loss_bps=220.0,
                take_profit_bps=380.0,
                horizon_seconds=21600,
                metadata={
                    "reason": "us_open_long_continuation",
                    "move_bps": move_bps,
                    "atr": atr,
                    "btc_perf": btc_perf,
                    "ema_trend": ema_trend
                }
            )

        # Short continuation setup
        if session_move < 0 and btc_perf < 1.5 and close_15utc < ema_trend:
            self.last_traded_day = current_day
            self.entry_bar = ctx.bar_index
            return ctx.signal(
                "short",
                confidence=0.75,
                stop_loss_bps=220.0,
                take_profit_bps=380.0,
                horizon_seconds=21600,
                metadata={
                    "reason": "us_open_short_continuation",
                    "move_bps": move_bps,
                    "atr": atr,
                    "btc_perf": btc_perf,
                    "ema_trend": ema_trend
                }
            )

        return None

    def on_position_close(self, ctx: BarContext, position: Dict[str, Any]) -> None:
        pass