from domains.strategy_contract import Strategy, BarContext, Signal
from typing import Optional, Dict, Any
import math

class UsSessionMomentumBtc(Strategy):
    METADATA = {
        "name": "UsSessionMomentumBtc",
        "domain": "btc_usdc",
        "declared_sl_bps": 220.0,
        "declared_tp_bps": 380.0,
        "declared_hold_seconds": 21600,  # ~6 hours
        "warmup_bars": 50,
        "required_features": ["btc_return_pct"],
    }

    def initialize(self, ctx: BarContext) -> None:
        self.lookback = 12
        self.min_mom_bps = 70.0
        self.last_traded_day = -1
        self.bars_since_exit = 10
        self.atr_period = 14

    def _atr(self, highs, lows, closes, period=14):
        if len(closes) < period + 1:
            return None
        trs = []
        for i in range(1, len(closes)):
            tr = max(highs[i] - lows[i], abs(highs[i] - closes[i - 1]), abs(lows[i] - closes[i - 1]))
            trs.append(tr)
        return sum(trs[-period:]) / period

    def on_position_close(self, ctx: BarContext, position: Dict[str, Any]) -> None:
        self.bars_since_exit = 0

    def on_bar(self, ctx: BarContext) -> Optional[Signal]:
        self.bars_since_exit += 1
        closes = ctx.closes(self.lookback + self.atr_period + 5)
        if len(closes) < self.lookback + self.atr_period:
            return None

        current_time = ctx.bar.timestamp
        hour = current_time.hour
        day_of_year = current_time.timetuple().tm_yday

        # Time-based session flattening: mandatory exit by UTC 20-21
        if ctx.has_position():
            if hour >= 21 or hour < 13:
                return ctx.signal(
                    "flat",
                    confidence=0.8,
                    metadata={"reason": "session_close_eod", "hour": hour, "close": ctx.bar.close}
                )
            return None

        # Cooldown guard: must wait at least 3 bars after any exit
        if self.bars_since_exit < 3:
            return None

        # Only evaluate entries between UTC 13 and 15 (US Session Open window)
        # Strictly enforce at most one position per calendar day
        if not (13 <= hour <= 15):
            return None
        if self.last_traded_day == day_of_year:
            return None

        # Filter out extreme crisis regimes
        regime = ctx.market.get("regime", "NORMAL")
        if regime in ("CRISIS", "MELTDOWN"):
            return None

        # Volatility check to avoid trading in completely dead or chaotic markets
        highs = ctx.highs(self.atr_period + 2)
        lows = ctx.lows(self.atr_period + 2)
        atr = self._atr(highs, lows, closes, self.atr_period)
        if atr is None:
            return None
        
        atr_bps = (atr / ctx.bar.close) * 10000.0
        if atr_bps < 40.0:
            return None

        # Measure pre-session momentum (last 12 bars)
        p_now = closes[-1]
        p_past = closes[-self.lookback]
        mom_bps = ((p_now - p_past) / p_past) * 10000.0

        # Cross-asset filter / daily momentum confirmation via btc_return_pct
        btc_24h_ret = ctx.features.get("btc_return_pct", 0.0)

        # Dynamic SL/TP based on ATR to adapt to market volatility
        sl_bps = max(150.0, min(350.0, atr_bps * 1.8))
        tp_bps = max(250.0, min(500.0, atr_bps * 2.8))

        # Long continuation setup
        if mom_bps >= self.min_mom_bps and btc_24h_ret > -1.5:
            self.last_traded_day = day_of_year
            conf = min(0.9, 0.5 + (abs(mom_bps) / 400.0))
            return ctx.signal(
                "long",
                confidence=conf,
                stop_loss_bps=sl_bps,
                take_profit_bps=tp_bps,
                horizon_seconds=21600,
                metadata={
                    "reason": "us_open_bullish_momentum_continuation",
                    "mom_bps": mom_bps,
                    "atr_bps": atr_bps,
                    "hour": hour,
                    "btc_return_pct": btc_24h_ret
                }
            )

        # Short continuation setup
        if mom_bps <= -self.min_mom_bps and btc_24h_ret < 1.5:
            self.last_traded_day = day_of_year
            conf = min(0.9, 0.5 + (abs(mom_bps) / 400.0))
            return ctx.signal(
                "short",
                confidence=conf,
                stop_loss_bps=sl_bps,
                take_profit_bps=tp_bps,
                horizon_seconds=21600,
                metadata={
                    "reason": "us_open_bearish_momentum_continuation",
                    "mom_bps": mom_bps,
                    "atr_bps": atr_bps,
                    "hour": hour,
                    "btc_return_pct": btc_24h_ret
                }
            )

        return None