from domains.strategy_contract import Strategy, BarContext, Signal
from typing import Optional, Dict, Any
import math

class UsSessionMomentumBTC(Strategy):
    METADATA = {
        "name": "US Session Momentum Breakout BTC",
        "domain": "btc_usdc",
        "declared_sl_bps": 220.0,
        "declared_tp_bps": 450.0,
        "declared_hold_seconds": 21600,  # Max 6 hours intraday hold
        "warmup_bars": 30,
        "required_features": ["funding_rate_btcusdt"],
    }

    def initialize(self, ctx: BarContext) -> None:
        self.last_traded_day: Optional[int] = None
        self.entry_bar: Optional[int] = None

    def _atr(self, highs, lows, closes, period: int = 14) -> Optional[float]:
        if len(closes) < period + 1:
            return None
        trs = []
        for i in range(1, len(closes)):
            tr = max(highs[i] - lows[i], abs(highs[i] - closes[i - 1]), abs(lows[i] - closes[i - 1]))
            trs.append(tr)
        return sum(trs[-period:]) / period

    def on_bar(self, ctx: BarContext) -> Optional[Signal]:
        closes = ctx.closes(25)
        if len(closes) < 25:
            return None

        ts = ctx.bar.timestamp
        current_day = ts.toordinal()
        current_hour = ts.hour
        has_pos = ctx.has_position()

        # Session Exit: Close intraday positions strictly by 21:00 UTC
        if has_pos:
            if current_hour >= 21 or current_hour < 13:
                return ctx.signal(
                    "flat",
                    confidence=0.8,
                    metadata={"reason": "session_close_eod", "hour": current_hour, "price": ctx.bar.close}
                )

        # Enforce 1 trade per day limit and entry window (UTC 13-15)
        if self.last_traded_day == current_day or has_pos:
            return None

        if not (13 <= current_hour <= 15):
            return None

        # Prior evidence failure fix: filter out choppy/crisis regimes and avoid pure momentum chasing
        regime = ctx.market.get("regime", "NORMAL")
        crisis_score = ctx.market.get("crisis_score", 0.0)
        if regime in ("CRISIS", "MELTDOWN") or crisis_score > 0.65:
            return None

        # Cross-asset / Market structure confirmation via funding rate
        # Extreme funding against the trade warns of crowded exhaustion
        funding = ctx.features.get("funding_rate_btcusdt", 0.0)

        highs = ctx.highs(25)
        lows = ctx.lows(25)
        atr = self._atr(highs, lows, closes, period=14)
        if atr is None or atr <= 0:
            return None

        # Measure 12-bar pre-US session direction and breakout over the pre-session range
        pre_closes = closes[-13:-1]
        pre_high = max(highs[-13:-1])
        pre_low = min(lows[-13:-1])
        pre_return = (closes[-1] - pre_closes[0]) / pre_closes[0]
        
        # Volatility normalization: move must exceed 0.75 ATR to establish clear momentum
        move_magnitude = abs(closes[-1] - pre_closes[0])
        if move_magnitude < 0.75 * atr:
            return None

        # Long continuation: clearly up, breaking pre-session high, non-crowded funding
        if pre_return > 0.005 and ctx.bar.close >= pre_high and funding < 0.0004:
            self.last_traded_day = current_day
            self.entry_bar = ctx.bar_index
            return ctx.signal(
                "long",
                confidence=0.75,
                stop_loss_bps=220.0,
                take_profit_bps=450.0,
                horizon_seconds=21600,
                metadata={
                    "reason": "us_session_long_breakout",
                    "pre_return": pre_return,
                    "funding": funding,
                    "atr": atr,
                    "hour": current_hour
                }
            )

        # Short continuation: clearly down, breaking pre-session low, non-crowded short funding
        if pre_return < -0.005 and ctx.bar.close <= pre_low and funding > -0.0004:
            self.last_traded_day = current_day
            self.entry_bar = ctx.bar_index
            return ctx.signal(
                "short",
                confidence=0.75,
                stop_loss_bps=220.0,
                take_profit_bps=450.0,
                horizon_seconds=21600,
                metadata={
                    "reason": "us_session_short_breakdown",
                    "pre_return": pre_return,
                    "funding": funding,
                    "atr": atr,
                    "hour": current_hour
                }
            )

        return None

    def on_position_close(self, ctx: BarContext, position: Dict[str, Any]) -> None:
        self.entry_bar = None