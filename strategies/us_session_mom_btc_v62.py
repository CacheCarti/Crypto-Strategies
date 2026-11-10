from domains.strategy_contract import Strategy, BarContext, Signal
from typing import Optional, Dict, Any
import math

class UsSessionMomentumBtc(Strategy):
    METADATA = {
        "name": "UsSessionMomentumBtc",
        "domain": "btc_usdc",
        "declared_sl_bps": 220.0,
        "declared_tp_bps": 380.0,
        "declared_hold_seconds": 21600,  # ~6 hours max hold inside US window
        "warmup_bars": 35,
        "required_features": ["fear_greed_index"],
    }

    def initialize(self, ctx: BarContext) -> None:
        self.lookback = 12
        self.min_return_bps = 45.0  # Clear directional move over prior 12h
        self.entry_start_utc = 13   # US pre-market / cash open
        self.entry_end_utc = 15     # Only initiate early in the session
        self.session_close_utc = 21 # Hard close before Asian/off-hours
        self.last_trade_day = -1
        self.exit_cooldown_bars = 4
        self.bars_since_exit = 10

    def _atr(self, highs, lows, closes, period=14):
        if len(closes) < period + 1:
            return None
        trs = []
        for i in range(1, len(closes)):
            tr = max(highs[i] - lows[i], abs(highs[i] - closes[i-1]), abs(lows[i] - closes[i-1]))
            trs.append(tr)
        return sum(trs[-period:]) / period

    def on_position_close(self, ctx: BarContext, position: Dict[str, Any]) -> None:
        self.bars_since_exit = 0

    def on_bar(self, ctx: BarContext) -> Optional[Signal]:
        self.bars_since_exit += 1

        closes = ctx.closes(self.lookback + 20)
        highs = ctx.highs(self.lookback + 20)
        lows = ctx.lows(self.lookback + 20)

        if len(closes) < self.METADATA["warmup_bars"]:
            return None

        current_time = ctx.bar.timestamp
        current_hour = current_time.hour
        current_day = current_time.timetuple().tm_yday

        # 1. Close everything at end of US session (UTC 21)
        if ctx.has_position():
            if current_hour >= self.session_close_utc:
                return ctx.signal("flat", confidence=0.7, metadata={
                    "reason": "us_session_close",
                    "hour": current_hour,
                    "close": ctx.bar.close
                })
            return None

        # 2. Strict Session & Cooldown constraints
        if self.bars_since_exit < self.exit_cooldown_bars:
            return None

        if current_day == self.last_trade_day:
            return None

        # Only open entries during the target opening window
        if not (self.entry_start_utc <= current_hour <= self.entry_end_utc):
            return None

        # Avoid weekend illiquid traps (5 = Saturday, 6 = Sunday)
        if current_time.weekday() >= 5:
            return None

        # 3. Macro Regime & Volatility Filtering (Addressing prior OOS failure)
        # Avoid MELTDOWN and hyper-stressed markets where intraday continuation breaks down
        market_regime = ctx.market.get("regime", "NORMAL")
        crisis_score = ctx.market.get("crisis_score", 0.0)
        if market_regime in ("CRISIS", "MELTDOWN") or crisis_score > 0.65:
            return None

        atr = self._atr(highs, lows, closes, period=14)
        if atr is None or atr <= 0:
            return None
        atr_pct = (atr / ctx.bar.close) * 10000.0

        # Discard ultra-compressed conditions where friction eats moves
        if atr_pct < 25.0:
            return None

        # 4. Measure past 12-hour momentum
        start_price = closes[-self.lookback]
        current_price = ctx.bar.close
        mom_bps = ((current_price - start_price) / start_price) * 10000.0

        # Sentiment alignment filter
        fear_greed = ctx.features.get("fear_greed_index", 50)

        # 5. Long Entry Logic
        if mom_bps >= self.min_return_bps and fear_greed >= 25:
            self.last_trade_day = current_day
            confidence = min(0.9, max(0.55, 0.55 + (mom_bps / 500.0) * 0.25))
            return ctx.signal(
                "long",
                confidence=confidence,
                stop_loss_bps=self.METADATA["declared_sl_bps"],
                take_profit_bps=self.METADATA["declared_tp_bps"],
                horizon_seconds=self.METADATA["declared_hold_seconds"],
                metadata={
                    "reason": "us_session_open_long_continuation",
                    "mom_bps": round(mom_bps, 2),
                    "hour": current_hour,
                    "fear_greed": fear_greed,
                    "regime": market_regime
                }
            )

        # 6. Short Entry Logic
        if mom_bps <= -self.min_return_bps and fear_greed <= 75:
            self.last_trade_day = current_day
            confidence = min(0.9, max(0.55, 0.55 + (abs(mom_bps) / 500.0) * 0.25))
            return ctx.signal(
                "short",
                confidence=confidence,
                stop_loss_bps=self.METADATA["declared_sl_bps"],
                take_profit_bps=self.METADATA["declared_tp_bps"],
                horizon_seconds=self.METADATA["declared_hold_seconds"],
                metadata={
                    "reason": "us_session_open_short_continuation",
                    "mom_bps": round(mom_bps, 2),
                    "hour": current_hour,
                    "fear_greed": fear_greed,
                    "regime": market_regime
                }
            )

        return None