from domains.strategy_contract import Strategy, BarContext, Signal
from typing import Optional, Dict, Any
import math

class UsSessionMomentumBTC(Strategy):
    METADATA = {
        "name": "US Session Momentum BTC",
        "domain": "btc_usdc",
        "declared_sl_bps": 220.0,
        "declared_tp_bps": 360.0,
        "declared_hold_seconds": 21600,  # ~6 hours hold into session close
        "warmup_bars": 40,
        "required_features": ["btc_return_pct", "eth_return_pct"]
    }

    def initialize(self, ctx: BarContext) -> None:
        self.last_traded_day = None
        self.bars_since_exit = 10
        self.entry_bar = 0

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
        closes = ctx.closes(30)
        highs = ctx.highs(30)
        lows = ctx.lows(30)

        if len(closes) < 30:
            return None

        current_time = ctx.bar.timestamp
        current_hour = current_time.hour
        current_day = current_time.date()

        # Hard exit prior to US market close (at or after 21 UTC)
        if ctx.has_position():
            if current_hour >= 21 or current_hour < 13:
                return ctx.signal(
                    "flat",
                    confidence=0.8,
                    metadata={"reason": "session_close_flat", "hour": current_hour, "price": ctx.bar.close}
                )
            return None

        # Respect cooldown and strictly trade once per calendar day
        if self.bars_since_exit < 4 or self.last_traded_day == current_day:
            return None

        # Filter out extreme crisis regimes
        regime = ctx.market.get("regime", "NORMAL")
        if regime in ("CRISIS", "MELTDOWN"):
            return None

        # Session transition window: enter specifically around US open (UTC 13 or 14)
        if current_hour not in (13, 14):
            return None

        # Pre-session momentum (last 12 bars)
        mom_12 = (closes[-1] - closes[-13]) / closes[-13]

        # Volatility expansion check via ATR to ensure dynamic session transition
        atr = self._atr(highs, lows, closes, 14)
        if atr is None or atr == 0:
            return None
        atr_pct = atr / closes[-1]

        # Require significant momentum relative to recent volatility (at least 0.65% move)
        if abs(mom_12) < 0.0065 or atr_pct < 0.003:
            return None

        # Cross-asset confirmation using ETH 24h return vs BTC 24h return
        btc_ret = ctx.features.get("btc_return_pct", 0.0)
        eth_ret = ctx.features.get("eth_return_pct", 0.0)

        # Bullish setup: Pre-market run-up confirmed by aligned ETH momentum
        if mom_12 > 0.0065 and eth_ret > -0.5 and btc_ret > 0:
            self.last_traded_day = current_day
            self.entry_bar = ctx.bar_index
            return ctx.signal(
                "long",
                confidence=0.75,
                stop_loss_bps=220.0,
                take_profit_bps=360.0,
                horizon_seconds=21600,
                metadata={
                    "reason": "us_session_bull_continuation",
                    "mom_12": round(mom_12, 4),
                    "btc_ret": round(btc_ret, 2),
                    "eth_ret": round(eth_ret, 2),
                    "atr_pct": round(atr_pct, 4)
                }
            )

        # Bearish setup: Pre-market dump confirmed by aligned ETH weakness
        if mom_12 < -0.0065 and eth_ret < 0.5 and btc_ret < 0:
            self.last_traded_day = current_day
            self.entry_bar = ctx.bar_index
            return ctx.signal(
                "short",
                confidence=0.75,
                stop_loss_bps=220.0,
                take_profit_bps=360.0,
                horizon_seconds=21600,
                metadata={
                    "reason": "us_session_bear_continuation",
                    "mom_12": round(mom_12, 4),
                    "btc_ret": round(btc_ret, 2),
                    "eth_ret": round(eth_ret, 2),
                    "atr_pct": round(atr_pct, 4)
                }
            )

        return None