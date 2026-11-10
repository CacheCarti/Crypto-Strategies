from domains.strategy_contract import Strategy, BarContext, Signal
from typing import Optional, Dict, Any
import math

class UsSessionMomentumBtc(Strategy):
    METADATA = {
        "name": "US Session Momentum Continuation BTC",
        "domain": "btc_usdc",
        "declared_sl_bps": 220.0,
        "declared_tp_bps": 400.0,
        "declared_hold_seconds": 21600,
        "warmup_bars": 35,
        "required_features": ["fear_greed_index"],
    }

    def initialize(self, ctx: BarContext) -> None:
        self.lookback = 12
        self.entry_start_hour = 13
        self.entry_end_hour = 15
        self.session_close_hour = 21
        self.last_traded_day = -1
        self.min_ret_threshold = 0.0075

    def _ema(self, values, period):
        if len(values) < period:
            return None
        k = 2.0 / (period + 1)
        ema = sum(values[:period]) / period
        for v in values[period:]:
            ema = v * k + ema * (1 - k)
        return ema

    def _atr(self, highs, lows, closes, period=14):
        if len(closes) < period + 1:
            return None
        trs = []
        for i in range(1, len(closes)):
            tr = max(highs[i] - lows[i], abs(highs[i] - closes[i - 1]), abs(lows[i] - closes[i - 1]))
            trs.append(tr)
        return sum(trs[-period:]) / period

    def on_bar(self, ctx: BarContext) -> Optional[Signal]:
        closes = ctx.closes(self.lookback + 20)
        if len(closes) < self.lookback + 20:
            return None

        timestamp = ctx.bar.timestamp
        current_hour = timestamp.hour
        current_day = timestamp.timetuple().tm_yday

        if ctx.has_position():
            if current_hour >= self.session_close_hour or current_hour < self.entry_start_hour:
                return ctx.signal(
                    "flat",
                    confidence=0.8,
                    metadata={
                        "reason": "us_session_end_flatten",
                        "hour": current_hour,
                        "close": ctx.bar.close,
                    }
                )
            return None

        if current_day == self.last_traded_day:
            return None

        if not (self.entry_start_hour <= current_hour <= self.entry_end_hour):
            return None

        market_regime = ctx.market.get("regime", "NORMAL")
        if market_regime in ("CRISIS", "MELTDOWN"):
            return None

        highs = ctx.highs(25)
        lows = ctx.lows(25)
        atr = self._atr(highs, lows, closes, period=14)
        if atr is None:
            return None

        ema20 = self._ema(closes, 20)
        if ema20 is None:
            return None

        current_close = closes[-1]
        past_close = closes[-self.lookback]
        ret_12 = (current_close - past_close) / past_close

        fear_greed = ctx.features.get("fear_greed_index", 50.0)

        # Volatility-scaled dynamic stops (minimum 160 bps, max 320 bps)
        atr_bps = (atr / current_close) * 10000.0
        sl_bps = max(160.0, min(320.0, atr_bps * 1.5))
        tp_bps = max(240.0, min(480.0, sl_bps * 1.8))

        if ret_12 >= self.min_ret_threshold and current_close > ema20 and fear_greed >= 35.0:
            self.last_traded_day = current_day
            conf = min(0.85, 0.55 + (ret_12 / 0.03) * 0.2)
            return ctx.signal(
                "long",
                confidence=conf,
                stop_loss_bps=sl_bps,
                take_profit_bps=tp_bps,
                horizon_seconds=21600,
                metadata={
                    "reason": "us_open_bullish_continuation",
                    "ret_12": round(ret_12, 4),
                    "ema20": round(ema20, 2),
                    "fear_greed": fear_greed,
                    "hour": current_hour,
                }
            )

        if ret_12 <= -self.min_ret_threshold and current_close < ema20 and fear_greed <= 65.0:
            self.last_traded_day = current_day
            conf = min(0.85, 0.55 + (abs(ret_12) / 0.03) * 0.2)
            return ctx.signal(
                "short",
                confidence=conf,
                stop_loss_bps=sl_bps,
                take_profit_bps=tp_bps,
                horizon_seconds=21600,
                metadata={
                    "reason": "us_open_bearish_continuation",
                    "ret_12": round(ret_12, 4),
                    "ema20": round(ema20, 2),
                    "fear_greed": fear_greed,
                    "hour": current_hour,
                }
            )

        return None

    def on_position_close(self, ctx: BarContext, position: Dict[str, Any]) -> None:
        pass