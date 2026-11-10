from domains.strategy_contract import Strategy, BarContext, Signal
from typing import Optional, Dict, Any
import math

class UsSessionMomentumBtc(Strategy):
    METADATA = {
        "name": "UsSessionMomentumBtc",
        "domain": "btc_usdc",
        "declared_sl_bps": 220.0,
        "declared_tp_bps": 400.0,
        "declared_hold_seconds": 21600,  # ~6 hours intended session hold
        "warmup_bars": 30,
        "required_features": ["btc_return_pct", "eth_return_pct"],
    }

    def initialize(self, ctx: BarContext) -> None:
        self.last_traded_day = None
        self.cooldown_until_bar = 0

    def _atr(self, highs, lows, closes, period=14):
        if len(closes) < period + 1:
            return None
        trs = []
        for i in range(1, len(closes)):
            tr = max(highs[i] - lows[i], abs(highs[i] - closes[i - 1]), abs(lows[i] - closes[i - 1]))
            trs.append(tr)
        return sum(trs[-period:]) / period

    def on_position_close(self, ctx: BarContext, position: Dict[str, Any]) -> None:
        self.cooldown_until_bar = ctx.bar_index + 4

    def on_bar(self, ctx: BarContext) -> Optional[Signal]:
        closes = ctx.closes(30)
        highs = ctx.highs(30)
        lows = ctx.lows(30)

        if len(closes) < 30:
            return None

        current_time = ctx.bar.timestamp
        current_hour = current_time.hour
        current_day = current_time.date()

        # Flat out at or after 21:00 UTC (End of US session)
        if ctx.has_position():
            if current_hour >= 21 or current_hour < 13:
                return ctx.signal(
                    "flat",
                    confidence=0.8,
                    metadata={"reason": "session_close_flat", "hour": current_hour, "price": ctx.bar.close}
                )
            return None

        # Cooldown guard
        if ctx.bar_index < self.cooldown_until_bar:
            return None

        # Limit to 1 entry per calendar day, evaluated at session open window (13:00 - 15:00 UTC)
        if self.last_traded_day == current_day:
            return None

        if not (13 <= current_hour <= 15):
            return None

        # Volatility state check via ATR: avoid entering during dead or extreme crisis volatility
        atr = self._atr(highs, lows, closes, 14)
        if atr is None or atr <= 0:
            return None
        atr_pct = (atr / ctx.bar.close) * 10000.0
        # Require reasonable intraday volatility: 25 bps <= ATR <= 200 bps
        if atr_pct < 25.0 or atr_pct > 200.0:
            return None

        # Market regime filter from context (do not trade during MELTDOWN)
        market_regime = ctx.market.get("regime", "NORMAL")
        if market_regime == "MELTDOWN":
            return None

        # Prior 12 bars direction & magnitude
        prev_12_close = closes[-13]
        current_close = closes[-1]
        mom_12_bps = ((current_close - prev_12_close) / prev_12_close) * 10000.0

        # Cross-asset confirmation: BTC and ETH 24h return direction must align with momentum
        btc_ret = ctx.features.get("btc_return_pct", 0.0)
        eth_ret = ctx.features.get("eth_return_pct", 0.0)

        # Bullish session continuation: past 12h clearly up (> 45 bps) + cross-asset confirmation
        if mom_12_bps > 45.0 and btc_ret > 0.0 and eth_ret > -0.5:
            self.last_traded_day = current_day
            confidence = min(0.85, 0.55 + (mom_12_bps / 500.0))
            return ctx.signal(
                "long",
                confidence=confidence,
                stop_loss_bps=220.0,
                take_profit_bps=400.0,
                metadata={
                    "reason": "us_session_open_long_continuation",
                    "mom_12_bps": round(mom_12_bps, 2),
                    "btc_ret": round(btc_ret, 2),
                    "eth_ret": round(eth_ret, 2),
                    "atr_pct": round(atr_pct, 2),
                    "hour": current_hour,
                }
            )

        # Bearish session continuation: past 12h clearly down (< -45 bps) + cross-asset confirmation
        if mom_12_bps < -45.0 and btc_ret < 0.0 and eth_ret < 0.5:
            self.last_traded_day = current_day
            confidence = min(0.85, 0.55 + (abs(mom_12_bps) / 500.0))
            return ctx.signal(
                "short",
                confidence=confidence,
                stop_loss_bps=220.0,
                take_profit_bps=400.0,
                metadata={
                    "reason": "us_session_open_short_continuation",
                    "mom_12_bps": round(mom_12_bps, 2),
                    "btc_ret": round(btc_ret, 2),
                    "eth_ret": round(eth_ret, 2),
                    "atr_pct": round(atr_pct, 2),
                    "hour": current_hour,
                }
            )

        return None