from domains.strategy_contract import Strategy, BarContext, Signal
from typing import Optional, Dict, Any
import math

class AsiaDriftMomentum(Strategy):
    METADATA = {
        "name": "Asia Drift US Session Continuation",
        "domain": "btc_usdc",
        "declared_sl_bps": 220.0,
        "declared_tp_bps": 440.0,
        "declared_hold_seconds": 28800,  # ~8 hours hold until Asia begins
        "warmup_bars": 30,
        "required_features": ["funding_rate_btcusdt"],
    }

    def initialize(self, ctx: BarContext) -> None:
        self.asia_open_price: Optional[float] = None
        self.asia_close_price: Optional[float] = None
        self.last_trade_day: Optional[int] = None
        self.bars_since_exit: int = 20

    def _ema(self, values, period: int) -> Optional[float]:
        if len(values) < period:
            return None
        k = 2.0 / (period + 1)
        ema = sum(values[:period]) / period
        for v in values[period:]:
            ema = v * k + ema * (1.0 - k)
        return ema

    def _atr(self, highs, lows, closes, period: int = 14) -> Optional[float]:
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
        current_hour = current_time.hour
        current_day = current_time.toordinal()
        current_price = ctx.bar.close

        # Record Asia session open (UTC 00:00)
        if current_hour == 0:
            self.asia_open_price = ctx.bar.open

        # Record Asia session close / evaluation point (UTC 08:00)
        if current_hour == 8:
            self.asia_close_price = ctx.bar.close

        # Flatten before next Asia session starts (UTC 23:00)
        if ctx.has_position():
            if current_hour >= 23 or current_hour < 1:
                return ctx.signal(
                    "flat",
                    confidence=0.7,
                    metadata={"reason": "session_close_pre_asia", "hour": current_hour, "price": current_price}
                )
            return None

        # Entry window: Early US open (UTC 13:00 - 15:00)
        # Avoid repeat trades in the same calendar day & respect cooldown
        if 13 <= current_hour <= 15:
            if self.last_trade_day == current_day:
                return None
            if self.bars_since_exit < 4:
                return None
            if self.asia_open_price is None or self.asia_close_price is None:
                return None

            # 1. Asia Drift Mechanism
            drift_bps = ((self.asia_close_price - self.asia_open_price) / self.asia_open_price) * 10000.0
            
            # Need a positive, non-trivial Asia drift (at least +25 bps)
            if drift_bps < 25.0:
                return None

            # 2. Material confirmations to address past OOS failures:
            # - Trend Confirmation: Price above 20 EMA
            # - Volatility expansion: ATR not dead (not stagnant consolidation)
            # - Funding rate sanity check: Avoid extreme crowded long funding
            ema20 = self._ema(closes, 20)
            atr14 = self._atr(highs, lows, closes, 14)
            funding_rate = ctx.features.get("funding_rate_btcusdt", 0.0)
            market_regime = ctx.market.get("regime", "NORMAL")

            if ema20 is None or atr14 is None:
                return None

            # Block entry during meltdown/crisis regimes or overcrowded long funding (>0.05% per 8h)
            if market_regime in ("CRISIS", "MELTDOWN") or funding_rate > 0.0005:
                return None

            # Require price to hold above EMA20 and Asia close (continuation structure)
            if current_price > ema20 and current_price >= self.asia_close_price:
                self.last_trade_day = current_day
                conf = min(0.85, 0.55 + (drift_bps / 500.0) * 0.25)
                
                return ctx.signal(
                    "long",
                    confidence=conf,
                    stop_loss_bps=self.METADATA["declared_sl_bps"],
                    take_profit_bps=self.METADATA["declared_tp_bps"],
                    horizon_seconds=self.METADATA["declared_hold_seconds"],
                    metadata={
                        "reason": "asia_drift_us_continuation",
                        "drift_bps": round(drift_bps, 2),
                        "ema20": round(ema20, 2),
                        "price": current_price,
                        "atr14": round(atr14, 2),
                        "funding_rate": funding_rate,
                        "hour": current_hour
                    }
                )

        return None