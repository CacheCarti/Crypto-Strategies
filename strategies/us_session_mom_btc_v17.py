from domains.strategy_contract import Strategy, BarContext, Signal
from typing import Optional, Dict, Any
import math

class UsSessionMomentumBtc(Strategy):
    METADATA = {
        "name": "UsSessionMomentumBtc",
        "domain": "btc_usdc",
        "declared_sl_bps": 220.0,
        "declared_tp_bps": 380.0,
        "declared_hold_seconds": 21600,  # ~6 hours max hold
        "warmup_bars": 30,
        "required_features": ["fear_greed_index"],
    }

    def initialize(self, ctx: BarContext) -> None:
        self.lookback = 12
        self.entry_start_hour = 13
        self.entry_end_hour = 15
        self.exit_hour = 21
        self.last_trade_day = -1
        self.atr_period = 14
        self.min_ret_threshold = 0.004  # 40 bps minimum pre-session move to establish clear bias

    def _atr(self, highs, lows, closes, period=14):
        if len(closes) < period + 1:
            return None
        trs = []
        for i in range(1, len(closes)):
            tr = max(highs[i] - lows[i], abs(highs[i] - closes[i-1]), abs(lows[i] - closes[i-1]))
            trs.append(tr)
        return sum(trs[-period:]) / period

    def on_position_close(self, ctx: BarContext, position: Dict[str, Any]) -> None:
        pass

    def on_bar(self, ctx: BarContext) -> Optional[Signal]:
        closes = ctx.closes(self.lookback + 2)
        highs = ctx.highs(self.lookback + 2)
        lows = ctx.lows(self.lookback + 2)

        if len(closes) < self.lookback + 1:
            return None

        current_time = ctx.bar.timestamp
        current_hour = current_time.hour
        current_day = current_time.toordinal()

        # Mandatory session exit before UTC 21:00
        if ctx.has_position():
            if current_hour >= self.exit_hour or current_hour < self.entry_start_hour:
                return ctx.signal(
                    "flat",
                    confidence=0.8,
                    metadata={
                        "reason": "us_session_end_time_exit",
                        "hour": current_hour,
                        "close": ctx.bar.close,
                    }
                )
            return None

        # One trade per calendar day guard
        if current_day == self.last_trade_day:
            return None

        # Entry window: UTC 13 to 15 only
        if not (self.entry_start_hour <= current_hour <= self.entry_end_hour):
            return None

        # Avoid extreme crisis spikes where trending continuation breaks down
        regime = ctx.market.get("regime", "NORMAL")
        if regime in ("CRISIS", "MELTDOWN"):
            return None

        # Directional momentum over last 12 bars (Asian + European lead-in)
        ref_close = closes[-self.lookback - 1]
        cur_close = closes[-1]
        session_return = (cur_close - ref_close) / ref_close

        atr = self._atr(highs, lows, closes, period=self.atr_period)
        if atr is None or atr <= 0:
            return None

        # Dynamically scale SL/TP based on volatility rather than fixed static bps
        vol_pct = atr / cur_close
        dynamic_sl_bps = max(140.0, min(300.0, vol_pct * 10000.0 * 1.5))
        dynamic_tp_bps = max(200.0, min(450.0, vol_pct * 10000.0 * 2.5))

        fgi = ctx.features.get("fear_greed_index", 50)

        # Bullish continuation: strong pre-US rally, not facing extreme greed exhaustion
        if session_return > self.min_ret_threshold and fgi < 85:
            self.last_trade_day = current_day
            confidence = min(0.9, 0.5 + abs(session_return) * 15.0)
            return ctx.signal(
                "long",
                confidence=confidence,
                stop_loss_bps=dynamic_sl_bps,
                take_profit_bps=dynamic_tp_bps,
                horizon_seconds=self.METADATA["declared_hold_seconds"],
                metadata={
                    "reason": "us_session_bull_continuation",
                    "pre_session_ret": session_return,
                    "fgi": fgi,
                    "atr": atr,
                    "hour": current_hour,
                }
            )

        # Bearish continuation: strong pre-US decline, not facing extreme panic exhaustion
        if session_return < -self.min_ret_threshold and fgi > 15:
            self.last_trade_day = current_day
            confidence = min(0.9, 0.5 + abs(session_return) * 15.0)
            return ctx.signal(
                "short",
                confidence=confidence,
                stop_loss_bps=dynamic_sl_bps,
                take_profit_bps=dynamic_tp_bps,
                horizon_seconds=self.METADATA["declared_hold_seconds"],
                metadata={
                    "reason": "us_session_bear_continuation",
                    "pre_session_ret": session_return,
                    "fgi": fgi,
                    "atr": atr,
                    "hour": current_hour,
                }
            )

        return None