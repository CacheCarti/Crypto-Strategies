from domains.strategy_contract import Strategy, BarContext, Signal
from typing import Optional, Dict, Any

class AsiaDriftMomentum(Strategy):
    METADATA = {
        "name": "Asia Drift Momentum",
        "domain": "btc_usdc",
        "declared_sl_bps": 220.0,
        "declared_tp_bps": 440.0,
        "declared_hold_seconds": 28800,  # ~8 hours (close before Asia session)
        "warmup_bars": 50,
        "required_features": ["fear_greed_index"],
    }

    def initialize(self, ctx: BarContext) -> None:
        self.last_trade_day = -1
        self.asia_open_price = None
        self.last_exit_bar = -10
        self.cooldown_bars = 4

    def on_position_close(self, ctx: BarContext, position: Dict[str, Any]) -> None:
        self.last_exit_bar = ctx.bar_index

    def on_bar(self, ctx: BarContext) -> Optional[Signal]:
        closes = ctx.closes(50)
        if len(closes) < 50:
            return None

        ts = ctx.bar.timestamp
        current_day = ts.toordinal()
        current_hour = ts.hour
        current_price = ctx.bar.close

        # Track the start of the Asia session (UTC 00:00)
        if current_hour == 0:
            self.asia_open_price = ctx.bar.open

        # Exit any open position before the next Asia session opens (UTC 23:00)
        if ctx.has_position():
            if current_hour >= 23 or current_hour < 1:
                return ctx.signal(
                    "flat",
                    confidence=0.6,
                    metadata={
                        "reason": "pre_asia_session_close",
                        "hour": current_hour,
                        "price": current_price
                    }
                )
            return None

        # Cooldown guard after recent exit
        if ctx.bar_index - self.last_exit_bar < self.cooldown_bars:
            return None

        # Check for US Session open entry window (UTC 13:00 to 15:00)
        # Only 1 trade setup evaluated per calendar day
        if 13 <= current_hour <= 15 and self.last_trade_day != current_day:
            if self.asia_open_price is None or self.asia_open_price <= 0:
                return None

            # Calculate drift during the Asia session window
            # Asia session roughly ends around UTC 08:00
            # Compare current / mid-day price with Asia open
            asia_drift_bps = ((current_price - self.asia_open_price) / self.asia_open_price) * 10000.0

            # Regime filter: only take trend continuation in non-crisis and non-extreme-fear
            regime = ctx.market.get("regime", "NORMAL")
            fear_greed = ctx.features.get("fear_greed_index", 50)
            crisis_score = ctx.market.get("crisis_score", 0.0)

            # Trend sanity: ensure price is above 40-period SMA to confirm broader upward structure
            sma40 = sum(closes[-40:]) / 40.0

            # Asia drift must be positive and meaningful (> 25 bps) but not blown out (< 300 bps)
            if (
                25.0 <= asia_drift_bps <= 300.0
                and current_price > sma40
                and regime in ("NORMAL", "calm", "normal")
                and crisis_score < 0.35
                and fear_greed >= 35
            ):
                self.last_trade_day = current_day
                confidence = min(0.85, 0.55 + (asia_drift_bps / 1000.0))

                return ctx.signal(
                    "long",
                    confidence=confidence,
                    stop_loss_bps=self.METADATA["declared_sl_bps"],
                    take_profit_bps=self.METADATA["declared_tp_bps"],
                    horizon_seconds=self.METADATA["declared_hold_seconds"],
                    metadata={
                        "reason": "asia_positive_drift_us_open_continuation",
                        "asia_drift_bps": round(asia_drift_bps, 2),
                        "asia_open": self.asia_open_price,
                        "price": current_price,
                        "sma40": round(sma40, 2),
                        "fear_greed": fear_greed,
                        "hour": current_hour
                    }
                )

        return None