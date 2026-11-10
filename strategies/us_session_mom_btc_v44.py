from domains.strategy_contract import Strategy, BarContext, Signal
from typing import Optional, Dict, Any
import math

class UsSessionMomentumBtc(Strategy):
    METADATA = {
        "name": "UsSessionMomentumBtc",
        "domain": "btc_usdc",
        "declared_sl_bps": 220.0,
        "declared_tp_bps": 420.0,
        "declared_hold_seconds": 18000,  # ~5 hours target hold during US session
        "warmup_bars": 35,
        "required_features": ["btc_return_pct"],
    }

    def initialize(self, ctx: BarContext) -> None:
        self.last_traded_date = None
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
        closes = ctx.closes(30)
        highs = ctx.highs(30)
        lows = ctx.lows(30)
        
        if len(closes) < self.METADATA["warmup_bars"]:
            return None

        dt = ctx.bar.timestamp
        current_hour = dt.hour
        current_date = dt.date()
        bar_idx = ctx.bar_index
        price = ctx.bar.close

        # Mandatory exit before US session close (close by UTC 21)
        if ctx.has_position():
            if current_hour >= 21 or current_hour < 13:
                self.cooldown_until_bar = bar_idx + 4
                return ctx.signal(
                    "flat",
                    confidence=0.8,
                    metadata={"reason": "session_close_utc21", "hour": current_hour, "price": price}
                )
            return None

        # Gated to US market open hours: trade window strictly UTC 13:00 - 15:00
        if current_hour not in (13, 14):
            return None

        # Cooldown guard and one-trade-per-day enforcement
        if bar_idx < self.cooldown_until_bar or self.last_traded_date == current_date:
            return None

        # Avoid crisis/meltdown regimes that invalidate continuation logic
        market_regime = ctx.market.get("regime", "NORMAL")
        if market_regime in ("CRISIS", "MELTDOWN"):
            return None

        # 12-bar pre-session displacement calculation (momentum leading into US session)
        disp_12 = (closes[-1] - closes[-13]) / closes[-13]
        
        # Volatility state check via ATR relative to price
        atr = self._atr(highs, lows, closes, 14)
        if atr is None:
            return None
        atr_pct = (atr / price) * 100.0

        # Discard dead/flat or hyper-volatile states; ensure sufficient energy to continue
        if atr_pct < 0.25 or atr_pct > 2.5:
            return None

        # Cross-asset / 24h momentum confirmation (prior evidence fix: avoid naive unconfirmed continuation)
        btc_24h_ret = ctx.features.get("btc_return_pct", 0.0)
        
        # 12-bar displacement threshold: at least 0.45% move prior to US entry
        threshold = 0.0045

        # Long entry setup: pre-session bullish surge aligned with 24h positive drift
        if disp_12 > threshold and btc_24h_ret > 0.0:
            self.last_traded_date = current_date
            self.cooldown_until_bar = bar_idx + 8
            confidence = min(0.9, 0.55 + abs(disp_12) * 20.0)
            return ctx.signal(
                "long",
                confidence=confidence,
                stop_loss_bps=self.METADATA["declared_sl_bps"],
                take_profit_bps=self.METADATA["declared_tp_bps"],
                horizon_seconds=self.METADATA["declared_hold_seconds"],
                metadata={
                    "reason": "us_open_bullish_continuation",
                    "disp_12": round(disp_12, 5),
                    "btc_return_pct": round(btc_24h_ret, 3),
                    "atr_pct": round(atr_pct, 4),
                    "hour": current_hour,
                    "price": price,
                }
            )

        # Short entry setup: pre-session bearish dump aligned with 24h negative drift
        elif disp_12 < -threshold and btc_24h_ret < 0.0:
            self.last_traded_date = current_date
            self.cooldown_until_bar = bar_idx + 8
            confidence = min(0.9, 0.55 + abs(disp_12) * 20.0)
            return ctx.signal(
                "short",
                confidence=confidence,
                stop_loss_bps=self.METADATA["declared_sl_bps"],
                take_profit_bps=self.METADATA["declared_tp_bps"],
                horizon_seconds=self.METADATA["declared_hold_seconds"],
                metadata={
                    "reason": "us_open_bearish_continuation",
                    "disp_12": round(disp_12, 5),
                    "btc_return_pct": round(btc_24h_ret, 3),
                    "atr_pct": round(atr_pct, 4),
                    "hour": current_hour,
                    "price": price,
                }
            )

        return None

    def on_position_close(self, ctx: BarContext, position: Dict[str, Any]) -> None:
        self.cooldown_until_bar = ctx.bar_index + 4