from domains.strategy_contract import Strategy, BarContext, Signal
from typing import Optional, Dict, Any
import math

class UsOpenContinuationEth(Strategy):
    METADATA = {
        "name": "US Open Directional Continuation",
        "domain": "eth_usdc",
        "declared_sl_bps": 220.0,
        "declared_tp_bps": 380.0,
        "declared_hold_seconds": 21600,
        "warmup_bars": 40,
    }

    def initialize(self, ctx: BarContext) -> None:
        self.last_trade_day: Optional[int] = None
        self.open_ref_price: Optional[float] = None
        self.entry_bar_idx: int = -1

    def _atr(self, highs, lows, closes, period: int = 14) -> Optional[float]:
        if len(closes) < period + 1:
            return None
        trs = []
        for i in range(1, len(closes)):
            tr = max(highs[i] - lows[i], abs(highs[i] - closes[i - 1]), abs(lows[i] - closes[i - 1]))
            trs.append(tr)
        return sum(trs[-period:]) / period

    def _ema(self, values, period: int) -> Optional[float]:
        if len(values) < period:
            return None
        k = 2.0 / (period + 1)
        ema = sum(values[:period]) / period
        for v in values[period:]:
            ema = v * k + ema * (1 - k)
        return ema

    def on_position_close(self, ctx: BarContext, position: Dict[str, Any]) -> None:
        pass

    def on_bar(self, ctx: BarContext) -> Optional[Signal]:
        closes = ctx.closes(40)
        highs = ctx.highs(40)
        lows = ctx.lows(40)
        if len(closes) < 40:
            return None

        current_time = ctx.bar.timestamp
        hour = current_time.hour
        day_key = current_time.toordinal()

        # Hard intraday session exit at or after UTC 21:00
        if ctx.has_position():
            if hour >= 21 or hour < 13:
                pos = ctx.position or {}
                return ctx.signal(
                    "flat",
                    confidence=0.8,
                    metadata={
                        "reason": "session_close_utc21",
                        "hour": hour,
                        "entry_price": pos.get("entry_price", 0.0),
                        "exit_price": ctx.bar.close,
                    }
                )
            return None

        # Reset daily reference anchor if day changed
        if hour < 13:
            self.open_ref_price = None

        # Anchor price at start of US session (UTC 13:00 bar open)
        if hour == 13:
            self.open_ref_price = ctx.bar.open

        # Evaluate continuation only at completion of opening window: UTC 15:00
        if hour == 15 and self.last_trade_day != day_key:
            if self.open_ref_price is None or self.open_ref_price <= 0:
                # Fallback to bar open 2 bars ago if anchor missed
                opens = ctx.opens(3)
                self.open_ref_price = opens[0] if len(opens) >= 3 else ctx.bar.open

            atr_val = self._atr(highs, lows, closes, 14)
            ema_trend = self._ema(closes, 30)

            if atr_val is None or ema_trend is None or atr_val <= 0:
                return None

            current_close = ctx.bar.close
            session_move = current_close - self.open_ref_price
            atr_fraction = abs(session_move) / atr_val

            # Regime filter: avoid trading during market crisis or meltdown
            regime = ctx.market.get("regime", "NORMAL")
            if regime in ("CRISIS", "MELTDOWN"):
                return None

            # High-conviction requirement:
            # Move must be substantial (>0.75 ATR) but not exhausted (<2.2 ATR)
            if 0.75 <= atr_fraction <= 2.2:
                # Bullish continuation: upward opening impulse aligning above trend EMA
                if session_move > 0 and current_close > ema_trend:
                    self.last_trade_day = day_key
                    self.entry_bar_idx = ctx.bar_index
                    conf = min(0.9, 0.55 + (atr_fraction - 0.75) * 0.2)
                    return ctx.signal(
                        "long",
                        confidence=conf,
                        stop_loss_bps=220.0,
                        take_profit_bps=380.0,
                        horizon_seconds=21600,
                        metadata={
                            "reason": "us_open_long_continuation",
                            "session_move": session_move,
                            "atr": atr_val,
                            "atr_fraction": atr_fraction,
                            "ema_trend": ema_trend,
                            "price": current_close,
                        }
                    )
                # Bearish continuation: downward opening impulse aligning below trend EMA
                elif session_move < 0 and current_close < ema_trend:
                    self.last_trade_day = day_key
                    self.entry_bar_idx = ctx.bar_index
                    conf = min(0.9, 0.55 + (atr_fraction - 0.75) * 0.2)
                    return ctx.signal(
                        "short",
                        confidence=conf,
                        stop_loss_bps=220.0,
                        take_profit_bps=380.0,
                        horizon_seconds=21600,
                        metadata={
                            "reason": "us_open_short_continuation",
                            "session_move": session_move,
                            "atr": atr_val,
                            "atr_fraction": atr_fraction,
                            "ema_trend": ema_trend,
                            "price": current_close,
                        }
                    )

        return None