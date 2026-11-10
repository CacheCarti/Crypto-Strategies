from domains.strategy_contract import Strategy, BarContext, Signal
from typing import Optional, Dict, Any
import math

class SolMondaySeasonality(Strategy):
    METADATA = {
        "name": "SolMondaySeasonality",
        "domain": "sol_usdc",
        "declared_sl_bps": 320.0,
        "declared_tp_bps": 520.0,
        "declared_hold_seconds": 72000,
        "warmup_bars": 35,
        "required_features": ["btc_return_pct"],
    }

    def initialize(self, ctx: BarContext) -> None:
        self.cooldown_bars = 12
        self.last_exit_bar = -999
        self.entry_bar_idx = -999

    def _ema(self, values, period: int):
        if len(values) < period:
            return None
        k = 2.0 / (period + 1)
        ema = sum(values[:period]) / period
        for v in values[period:]:
            ema = v * k + ema * (1 - k)
        return ema

    def _rsi(self, closes, period: int = 14):
        if len(closes) < period + 1:
            return None
        gains, losses = [], []
        for i in range(1, len(closes)):
            diff = closes[i] - closes[i - 1]
            gains.append(max(diff, 0.0))
            losses.append(max(-diff, 0.0))
        avg_gain = sum(gains[-period:]) / period
        avg_loss = sum(losses[-period:]) / period
        if avg_loss == 0.0:
            return 100.0
        rs = avg_gain / avg_loss
        return 100.0 - (100.0 / (1.0 + rs))

    def on_bar(self, ctx: BarContext) -> Optional[Signal]:
        closes = ctx.closes(35)
        if len(closes) < 35:
            return None

        bar_time = ctx.bar.timestamp
        weekday = bar_time.weekday()
        hour = bar_time.hour
        current_price = ctx.bar.close

        ema24 = self._ema(closes, 24)
        rsi14 = self._rsi(closes, 14)
        if ema24 is None or rsi14 is None:
            return None

        btc_ret = ctx.features.get("btc_return_pct", 0.0)
        crisis_score = ctx.market.get("crisis_score", 0.0)

        if ctx.has_position():
            # Mandatory exit condition: hold through Monday US close, exit late Monday / early Tuesday
            is_monday_end = (weekday == 0 and hour >= 22) or (weekday == 1 and hour >= 1)
            bars_held = ctx.bar_index - self.entry_bar_idx

            if is_monday_end or bars_held >= 26:
                self.last_exit_bar = ctx.bar_index
                return ctx.signal(
                    "flat",
                    confidence=0.75,
                    metadata={
                        "reason": "monday_drift_window_complete",
                        "weekday": weekday,
                        "hour": hour,
                        "bars_held": bars_held,
                        "price": current_price
                    }
                )
            return None

        # Cooldown guard to eliminate over-trading
        if (ctx.bar_index - self.last_exit_bar) < self.cooldown_bars:
            return None

        # Window: Sunday late UTC (18:00 - 23:00) into Monday early UTC (00:00 - 04:00)
        is_entry_window = (weekday == 6 and hour >= 18) or (weekday == 0 and hour <= 4)
        if not is_entry_window:
            return None

        # Avoid macro crisis shocks
        if crisis_score > 0.45:
            return None

        # Cross-asset stability + momentum confirmation:
        # 1. Price stabilizing above/near 24h EMA (recovering from weekend dip)
        # 2. RSI not severely overbought (>65) and recovering (>38)
        # 3. BTC 24h return showing broad market support (> -2.5%)
        trend_recovering = current_price >= (ema24 * 0.995)
        rsi_valid = 38.0 <= rsi14 <= 65.0
        cross_asset_support = btc_ret >= -2.5

        if trend_recovering and rsi_valid and cross_asset_support:
            self.entry_bar_idx = ctx.bar_index
            conf = 0.65
            if btc_ret > 0.5:
                conf = 0.80

            return ctx.signal(
                "long",
                confidence=conf,
                stop_loss_bps=self.METADATA["declared_sl_bps"],
                take_profit_bps=self.METADATA["declared_tp_bps"],
                horizon_seconds=self.METADATA["declared_hold_seconds"],
                metadata={
                    "reason": "sunday_monday_seasonality_rebound",
                    "weekday": weekday,
                    "hour": hour,
                    "rsi": round(rsi14, 2),
                    "ema24": round(ema24, 2),
                    "btc_ret": round(btc_ret, 2),
                    "price": current_price
                }
            )

        return None

    def on_position_close(self, ctx: BarContext, position: Dict[str, Any]) -> None:
        self.last_exit_bar = ctx.bar_index