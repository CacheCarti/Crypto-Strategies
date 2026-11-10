from domains.strategy_contract import Strategy, BarContext, Signal
from typing import Optional, Dict, Any
import math

class SolMondaySeasonalityDrift(Strategy):
    METADATA = {
        "name": "SolMondaySeasonalityDrift",
        "domain": "sol_usdc",
        "declared_sl_bps": 400.0,
        "declared_tp_bps": 650.0,
        "declared_hold_seconds": 64800,  # ~18 hours hold target
        "warmup_bars": 35,
        "required_features": ["btc_return_pct"],
    }

    def initialize(self, ctx: BarContext) -> None:
        self.last_exit_bar = -100
        self.cooldown_bars = 12
        self.entry_bar = -100

    def _sma(self, values: list, period: int) -> Optional[float]:
        if len(values) < period:
            return None
        return sum(values[-period:]) / period

    def _atr(self, highs: list, lows: list, closes: list, period: int = 14) -> Optional[float]:
        if len(closes) < period + 1:
            return None
        trs = []
        for i in range(1, len(closes)):
            tr = max(highs[i] - lows[i], abs(highs[i] - closes[i-1]), abs(lows[i] - closes[i-1]))
            trs.append(tr)
        return sum(trs[-period:]) / period

    def on_position_close(self, ctx: BarContext, position: Dict[str, Any]) -> None:
        self.last_exit_bar = ctx.bar_index

    def on_bar(self, ctx: BarContext) -> Optional[Signal]:
        closes = ctx.closes(35)
        highs = ctx.highs(35)
        lows = ctx.lows(35)

        if len(closes) < self.METADATA["warmup_bars"]:
            return None

        current_time = ctx.bar.timestamp
        weekday = current_time.weekday()  # Monday is 0, Sunday is 6
        hour = current_time.hour
        price = ctx.bar.close

        # Position Management
        if ctx.has_position():
            bars_held = ctx.bar_index - self.entry_bar
            
            # Time-based exit: Monday night UTC (hour >= 20) or Tuesday early UTC
            is_exit_time = (weekday == 0 and hour >= 21) or (weekday == 1 and hour >= 0)
            
            # Technical exhaustion: price fell back below 12-period SMA after holding at least 6 bars
            sma12 = self._sma(closes, 12)
            momentum_loss = bars_held >= 6 and (sma12 is not None) and (price < sma12)
            
            # Max hold safety cutoff (24 hours)
            time_exhaustion = bars_held >= 24

            if is_exit_time or momentum_loss or time_exhaustion:
                reason = "monday_session_close" if is_exit_time else ("momentum_loss" if momentum_loss else "max_hold_exceeded")
                return ctx.signal(
                    "flat",
                    confidence=0.6,
                    metadata={
                        "reason": reason,
                        "bars_held": bars_held,
                        "exit_price": price,
                        "weekday": weekday,
                        "hour": hour
                    }
                )
            return None

        # Cooldown guard
        if (ctx.bar_index - self.last_exit_bar) < self.cooldown_bars:
            return None

        # Economic seasonal window: Sunday 18:00 UTC to Monday 04:00 UTC
        is_season_window = (weekday == 6 and hour >= 18) or (weekday == 0 and hour <= 4)
        if not is_season_window:
            return None

        # Regime & Crisis Filter (avoid entering during market-wide crashes)
        crisis_score = ctx.market.get("crisis_score", 0.0)
        regime = ctx.market.get("regime", "NORMAL")
        if crisis_score > 0.45 or regime in ["CRISIS", "MELTDOWN"]:
            return None

        # Cross-Asset Confirmation (BTC not dumping heavily)
        btc_ret = ctx.features.get("btc_return_pct", 0.0)
        if btc_ret < -3.5:
            return None

        # Volatility normalization & short-term trend filter
        atr = self._atr(highs, lows, closes, 14)
        sma24 = self._sma(closes, 24)
        sma8 = self._sma(closes, 8)

        if atr is None or sma24 is None or sma8 is None:
            return None

        # Confirm short-term stabilization / micro-trend reversal:
        # Price must be above SMA8 or short-term momentum turning up
        short_term_recovery = (price >= sma8) or (closes[-1] > closes[-3])
        # Avoid catching severe macro knives: price not more than 4 ATRs below 24h baseline
        not_deep_collapse = price >= (sma24 - 4.0 * atr)

        if short_term_recovery and not_deep_collapse:
            self.entry_bar = ctx.bar_index
            confidence = 0.7 if btc_ret >= 0.0 else 0.55
            
            return ctx.signal(
                "long",
                confidence=confidence,
                stop_loss_bps=self.METADATA["declared_sl_bps"],
                take_profit_bps=self.METADATA["declared_tp_bps"],
                horizon_seconds=self.METADATA["declared_hold_seconds"],
                metadata={
                    "reason": "monday_drift_seasonality_entry",
                    "weekday": weekday,
                    "hour": hour,
                    "price": price,
                    "sma8": sma8,
                    "sma24": sma24,
                    "btc_return_pct": btc_ret,
                    "crisis_score": crisis_score
                }
            )

        return None