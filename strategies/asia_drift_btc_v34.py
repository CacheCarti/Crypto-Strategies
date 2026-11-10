from domains.strategy_contract import Strategy, BarContext, Signal
from typing import Optional, Dict, Any
import math

class AsiaDriftMomentum(Strategy):
    METADATA = {
        "name": "Asia Drift Momentum",
        "domain": "btc_usdc",
        "declared_sl_bps": 220.0,
        "declared_tp_bps": 400.0,
        "declared_hold_seconds": 28800,  # ~8 hours hold until Asia close/reset
        "warmup_bars": 30,
        "required_features": ["funding_rate_btcusdt"],
    }

    def initialize(self, ctx: BarContext) -> None:
        self.asia_open_price: Optional[float] = None
        self.last_traded_date: Optional[str] = None
        self.bars_since_exit: int = 10
        self.min_cooldown: int = 6

    def _atr(self, highs, lows, closes, period=14) -> Optional[float]:
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

        if len(closes) < self.METADATA["warmup_bars"]:
            return None

        current_time = ctx.bar.timestamp
        current_date = current_time.strftime("%Y-%m-%d")
        hour = current_time.hour
        price = ctx.bar.close

        # Track Asia open price (UTC 00:00)
        if hour == 0:
            self.asia_open_price = ctx.bar.open

        # Close position before next Asia session begins (UTC 23:00)
        if ctx.has_position() and hour >= 23:
            return ctx.signal(
                "flat",
                confidence=0.7,
                metadata={
                    "reason": "session_close_pre_asia",
                    "hour": hour,
                    "price": price,
                }
            )

        # Only evaluate entries at US session open (UTC 13:00 or 14:00 window)
        if not ctx.has_position():
            if hour in (13, 14) and self.last_traded_date != current_date:
                if self.bars_since_exit >= self.min_cooldown and self.asia_open_price is not None:
                    # Measure Asia drift from 00:00 UTC open
                    drift_bps = ((price - self.asia_open_price) / self.asia_open_price) * 10000.0

                    # Materially different confirmation: Volatility state (ATR expanding) + Funding not overheated
                    atr = self._atr(highs, lows, closes, period=14)
                    if atr is None or price <= 0:
                        return None

                    atr_bps = (atr / price) * 10000.0
                    funding = ctx.features.get("funding_rate_btcusdt", 0.0)

                    # Regime filter: avoid meltdown/crisis environments
                    regime = ctx.market.get("regime", "NORMAL")
                    crisis_score = ctx.market.get("crisis_score", 0.0)

                    if regime not in ("CRISIS", "MELTDOWN") and crisis_score < 0.6:
                        # Asia drift must be solidly positive (continuation setup),
                        # ATR must reflect active liquidity (> 25 bps), and funding not excessively crowded (> 0.04%)
                        if drift_bps > 40.0 and atr_bps > 25.0 and funding < 0.0004:
                            self.last_traded_date = current_date
                            confidence = min(0.9, max(0.55, 0.6 + (drift_bps / 500.0)))
                            return ctx.signal(
                                "long",
                                confidence=confidence,
                                stop_loss_bps=self.METADATA["declared_sl_bps"],
                                take_profit_bps=self.METADATA["declared_tp_bps"],
                                horizon_seconds=self.METADATA["declared_hold_seconds"],
                                metadata={
                                    "reason": "us_open_asia_drift_continuation",
                                    "drift_bps": round(drift_bps, 2),
                                    "atr_bps": round(atr_bps, 2),
                                    "funding": funding,
                                    "hour": hour,
                                    "price": price,
                                }
                            )

        return None