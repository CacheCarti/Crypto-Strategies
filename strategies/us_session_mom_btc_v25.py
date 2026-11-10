from domains.strategy_contract import Strategy, BarContext, Signal
from typing import Optional, Dict, Any
import math

class UsSessionMomentumBtc(Strategy):
    METADATA = {
        "name": "US Session Momentum BTC",
        "domain": "btc_usdc",
        "declared_sl_bps": 220.0,
        "declared_tp_bps": 400.0,
        "declared_hold_seconds": 21600,  # ~6 hours max hold
        "warmup_bars": 40,
        "required_features": ["btc_return_pct"],
    }

    def initialize(self, ctx: BarContext) -> None:
        self.lookback = 12
        self.entry_hour = 14       # UTC 14:00 (confirming initial NY open flow)
        self.exit_hour = 21        # UTC 21:00 (strict daily close)
        self.min_mom_bps = 70.0    # Required 12-bar displacement (0.7%)
        self.last_trade_day = -1
        self.cooldown_until_bar = 0

    def _atr(self, highs, lows, closes, period=14):
        if len(closes) < period + 1:
            return None
        trs = []
        for i in range(1, len(closes)):
            tr = max(highs[i] - lows[i], abs(highs[i] - closes[i - 1]), abs(lows[i] - closes[i - 1]))
            trs.append(tr)
        return sum(trs[-period:]) / period

    def on_bar(self, ctx: BarContext) -> Optional[Signal]:
        closes = ctx.closes(40)
        if len(closes) < self.METADATA["warmup_bars"]:
            return None

        current_time = ctx.bar.timestamp
        current_hour = current_time.hour
        day_of_year = current_time.timetuple().tm_yday

        # 1. Mandatory Session Exit at or after exit_hour
        if ctx.has_position():
            if current_hour >= self.exit_hour or current_hour < self.entry_hour:
                return ctx.signal(
                    "flat",
                    confidence=1.0,
                    metadata={"reason": "session_close_eod", "hour": current_hour, "price": ctx.bar.close}
                )
            return None

        # 2. Check Cooldown & Session Filter
        if ctx.bar_index < self.cooldown_until_bar:
            return None

        # Only evaluate entry at the designated session open hour, once per calendar day
        if current_hour != self.entry_hour:
            return None

        if self.last_trade_day == day_of_year:
            return None

        # 3. Market Regime Filter (Avoid entering in full crisis/meltdown)
        market_regime = ctx.market.get("regime", "NORMAL")
        if market_regime in ("CRISIS", "MELTDOWN"):
            return None

        # 4. Momentum & Quality Check
        close_now = closes[-1]
        close_prior = closes[-1 - self.lookback]
        mom_bps = ((close_now - close_prior) / close_prior) * 10000.0

        highs = ctx.highs(20)
        lows = ctx.lows(20)
        atr = self._atr(highs, lows, closes, period=14)
        if atr is None or atr <= 0:
            return None

        # Normalized momentum vs volatility (must exceed 0.8 ATR equivalents over 12 bars)
        atr_bps = (atr / close_now) * 10000.0
        normalized_strength = abs(mom_bps) / max(atr_bps, 1.0)

        if abs(mom_bps) < self.min_mom_bps or normalized_strength < 0.75:
            return None

        # BTC 24h trend alignment check
        btc_24h_ret = ctx.features.get("btc_return_pct", 0.0)

        # 5. Signal Generation
        if mom_bps > 0 and btc_24h_ret >= -0.5:
            self.last_trade_day = day_of_year
            self.cooldown_until_bar = ctx.bar_index + 10
            return ctx.signal(
                "long",
                confidence=min(0.5 + normalized_strength * 0.15, 0.95),
                stop_loss_bps=self.METADATA["declared_sl_bps"],
                take_profit_bps=self.METADATA["declared_tp_bps"],
                horizon_seconds=self.METADATA["declared_hold_seconds"],
                metadata={
                    "reason": "us_open_bullish_continuation",
                    "mom_bps": mom_bps,
                    "normalized_strength": normalized_strength,
                    "btc_24h_ret": btc_24h_ret,
                    "price": close_now,
                }
            )

        elif mom_bps < 0 and btc_24h_ret <= 0.5:
            self.last_trade_day = day_of_year
            self.cooldown_until_bar = ctx.bar_index + 10
            return ctx.signal(
                "short",
                confidence=min(0.5 + normalized_strength * 0.15, 0.95),
                stop_loss_bps=self.METADATA["declared_sl_bps"],
                take_profit_bps=self.METADATA["declared_tp_bps"],
                horizon_seconds=self.METADATA["declared_hold_seconds"],
                metadata={
                    "reason": "us_open_bearish_continuation",
                    "mom_bps": mom_bps,
                    "normalized_strength": normalized_strength,
                    "btc_24h_ret": btc_24h_ret,
                    "price": close_now,
                }
            )

        return None

    def on_position_close(self, ctx: BarContext, position: Dict[str, Any]) -> None:
        # Extra cooldown buffer if position stopped out
        ret = position.get("return_bps", 0.0)
        if ret < 0:
            self.cooldown_until_bar = ctx.bar_index + 14
        else:
            self.cooldown_until_bar = ctx.bar_index + 6