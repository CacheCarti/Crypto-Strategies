from domains.strategy_contract import Strategy, BarContext, Signal
from typing import Optional, Dict, Any
import math

class UsOpenContinuationEth(Strategy):
    METADATA = {
        "name": "US Open Directional Continuation ETH",
        "domain": "eth_usdc",
        "declared_sl_bps": 220.0,
        "declared_tp_bps": 400.0,
        "declared_hold_seconds": 21600,
        "warmup_bars": 50,
    }

    def initialize(self, ctx: BarContext) -> None:
        self.last_trade_day: Optional[int] = None
        self.entry_bar: int = -999
        self.cooldown_bars: int = 4
        self.exit_bar: int = -999

    def _atr(self, highs, lows, closes, period: int = 14) -> Optional[float]:
        if len(closes) < period + 1:
            return None
        trs = []
        for i in range(1, len(closes)):
            tr = max(highs[i] - lows[i], abs(highs[i] - closes[i - 1]), abs(lows[i] - closes[i - 1]))
            trs.append(tr)
        return sum(trs[-period:]) / period

    def on_position_close(self, ctx: BarContext, position: Dict[str, Any]) -> None:
        self.exit_bar = ctx.bar_index

    def on_bar(self, ctx: BarContext) -> Optional[Signal]:
        closes = ctx.closes(50)
        highs = ctx.highs(50)
        lows = ctx.lows(50)
        
        if len(closes) < 40:
            return None

        current_dt = ctx.bar.timestamp
        current_hour = current_dt.hour
        current_day = current_dt.toordinal()

        # Exit logic: Force flat by UTC 21:00 or after max intraday hold
        if ctx.has_position():
            bars_held = ctx.bar_index - self.entry_bar
            if current_hour >= 21 or bars_held >= 6:
                return ctx.signal(
                    "flat",
                    confidence=0.6,
                    metadata={
                        "reason": "session_close_utc21" if current_hour >= 21 else "max_intraday_hold_reached",
                        "bars_held": bars_held,
                        "current_hour": current_hour,
                        "price": ctx.bar.close
                    }
                )
            return None

        # Guard: Check cooldown and single trade per day limit
        if (ctx.bar_index - self.exit_bar) < self.cooldown_bars:
            return None
        if self.last_trade_day == current_day:
            return None

        # Entry window: Evaluate specifically at UTC 15 (after 13:00 and 14:00 hourly bars complete)
        if current_hour != 15:
            return None

        # Avoid high crisis periods
        crisis = ctx.market.get("crisis_score", 0.0)
        if crisis > 0.65:
            return None

        atr = self._atr(highs, lows, closes, 14)
        if atr is None or atr <= 0:
            return None

        # Two-hour session window: open at 13:00 (closes[-3]), close at 15:00 (ctx.bar.close)
        open_price_13 = closes[-3]
        current_close = ctx.bar.close
        session_net_move = current_close - open_price_13
        abs_net_move = abs(session_net_move)

        # Measure 2-bar total high-low expansion
        session_high = max(highs[-2:])
        session_low = min(lows[-2:])
        session_gross_range = session_high - session_low

        # Relative move vs ATR (percentile/relative expansion rather than fixed bps)
        move_to_atr = abs_net_move / atr
        efficiency = abs_net_move / session_gross_range if session_gross_range > 0 else 0.0

        # Require significant displacement (> 0.7 ATR) and clean directional bar (> 55% directional efficiency)
        # Avoid overextended blowoffs (> 2.8 ATR)
        if move_to_atr < 0.70 or move_to_atr > 2.80:
            return None
        if efficiency < 0.55:
            return None

        # Trend alignment check using 30-bar baseline
        sma30 = sum(closes[-30:]) / 30.0
        confidence = min(0.85, max(0.55, 0.50 + (efficiency * 0.35)))

        if session_net_move > 0 and current_close >= sma30:
            self.last_trade_day = current_day
            self.entry_bar = ctx.bar_index
            return ctx.signal(
                "long",
                confidence=confidence,
                stop_loss_bps=self.METADATA["declared_sl_bps"],
                take_profit_bps=self.METADATA["declared_tp_bps"],
                horizon_seconds=self.METADATA["declared_hold_seconds"],
                metadata={
                    "reason": "us_open_bullish_continuation",
                    "move_to_atr": round(move_to_atr, 2),
                    "efficiency": round(efficiency, 2),
                    "price": current_close,
                    "sma30": round(sma30, 2),
                    "hour": current_hour
                }
            )
        elif session_net_move < 0 and current_close <= sma30:
            self.last_trade_day = current_day
            self.entry_bar = ctx.bar_index
            return ctx.signal(
                "short",
                confidence=confidence,
                stop_loss_bps=self.METADATA["declared_sl_bps"],
                take_profit_bps=self.METADATA["declared_tp_bps"],
                horizon_seconds=self.METADATA["declared_hold_seconds"],
                metadata={
                    "reason": "us_open_bearish_continuation",
                    "move_to_atr": round(move_to_atr, 2),
                    "efficiency": round(efficiency, 2),
                    "price": current_close,
                    "sma30": round(sma30, 2),
                    "hour": current_hour
                }
            )

        return None