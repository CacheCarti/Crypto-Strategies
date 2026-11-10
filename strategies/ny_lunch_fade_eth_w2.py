from domains.strategy_contract import Strategy, BarContext, Signal
from typing import Optional, Dict, Any
import math

class IntradayLiquidityPause(Strategy):
    METADATA = {
        "name": "Intraday Liquidity Pause",
        "domain": "eth_usdc",
        "declared_sl_bps": 200.0,
        "declared_tp_bps": 400.0,
        "declared_hold_seconds": 7200, # 2-3 hours
        "warmup_bars": 50,
        "required_features": ["fear_greed_index"], # For crisis regime filtering
    }

    def initialize(self, ctx: BarContext) -> None:
        self.long_ema_period = 20
        self.short_ema_period = 5
        self.rsi_period = 10
        self.min_move_pct = 0.5 # Minimum 0.5% move in US morning
        self.cooldown_bars = 5
        self.last_signal_bar = -self.cooldown_bars - 1 # Initialize to allow immediate signal

    def _ema(self, values, period):
        if len(values) < period:
            return None
        k = 2.0 / (period + 1)
        ema = sum(values[:period]) / period
        for v in values[period:]:
            ema = v * k + ema * (1 - k)
        return ema

    def _rsi(self, closes, period):
        if len(closes) < period + 1:
            return None
        gains, losses = [], []
        for i in range(1, len(closes)):
            diff = closes[i] - closes[i-1]
            gains.append(max(diff, 0))
            losses.append(max(-diff, 0))

        avg_gain = sum(gains[-period:]) / period
        avg_loss = sum(losses[-period:]) / period

        if avg_loss == 0:
            return 100.0 if avg_gain > 0 else 50.0
        rs = avg_gain / avg_loss
        return 100.0 - (100.0 / (1.0 + rs))

    def on_bar(self, ctx: BarContext) -> Optional[Signal]:
        closes = ctx.closes(self.long_ema_period + self.rsi_period)
        if len(closes) < self.METADATA["warmup_bars"]:
            return None

        current_hour = ctx.bar.timestamp.hour

        # Cooldown logic
        if ctx.bar_index <= self.last_signal_bar + self.cooldown_bars:
            return None

        # Regime filter: Skip trading during crisis or strong trend regimes
        # Crisis score check (using both ctx.regime and ctx.market.get("crisis_score"))
        if ctx.regime == "crisis" or ctx.market.get("crisis_score", 0.0) > 0.5:
            return None
        # Trend regime check
        if ctx.market.get("trend_regime_confidence", 0.0) > 0.6 and ctx.market.get("trend_regime") != "neutral":
            return None

        # Entry logic: US morning move (UTC 13-16)
        # Look for a strong unidirectional move between 13:00 and 16:00 UTC
        # This means the current bar is 16:00 UTC
        if current_hour == 16:
            # Need at least 3 bars (13, 14, 15) before 16 to assess the move
            if len(closes) < 4: # current bar + previous 3
                return None

            # Get closes for 13, 14, 15, 16 UTC (index -4 to -1)
            morning_closes = ctx.closes(4) # last 4 closes including current bar
            open_13_utc = ctx.opens(4)[0] # open of the 13:00 bar
            close_16_utc = morning_closes[-1] # close of the 16:00 bar

            total_move_pct = (close_16_utc - open_13_utc) / open_13_utc * 100

            # Check for significant one-sided move
            if abs(total_move_pct) >= self.min_move_pct:
                # Fade the move during the 16-19 UTC liquidity lull
                if not ctx.has_position():
                    signal_direction = None
                    if total_move_pct > 0: # Strong bullish morning, fade with short
                        signal_direction = "short"
                    elif total_move_pct < 0: # Strong bearish morning, fade with long
                        signal_direction = "long"

                    if signal_direction:
                        self.last_signal_bar = ctx.bar_index
                        return ctx.signal(
                            signal_direction,
                            confidence=0.7,
                            horizon_seconds=self.METADATA["declared_hold_seconds"],
                            metadata={
                                "reason": f"fade_morning_move_{signal_direction}",
                                "morning_move_pct": round(total_move_pct, 2),
                                "current_price": ctx.bar.close,
                                "entry_hour": current_hour
                            }
                        )
        
        # Exit logic: Exit by UTC 21 or if half the morning move retraces
        if ctx.has_position() and ctx.position_direction() is not None:
            pos_meta = ctx.position.get("metadata", {})
            entry_hour = pos_meta.get("entry_hour")
            morning_move_pct = pos_meta.get("morning_move_pct")
            entry_price = ctx.position["entry_price"]
            current_price = ctx.bar.close

            # Exit by UTC 21 (end of 21:00 bar)
            if current_hour >= 21:
                self.last_signal_bar = ctx.bar_index
                return ctx.signal(
                    "flat",
                    confidence=0.5,
                    metadata={
                        "reason": "time_based_exit_end_of_day",
                        "current_hour": current_hour,
                        "entry_price": entry_price,
                        "current_price": current_price
                    }
                )

            # Exit if half the morning move retraces
            if morning_move_pct is not None and entry_price is not None:
                half_retrace_threshold = 0.5 * morning_move_pct
                current_retrace_pct = (current_price - entry_price) / entry_price * 100

                if (ctx.position_direction() == "short" and current_retrace_pct >= half_retrace_threshold) or \
                   (ctx.position_direction() == "long" and current_retrace_pct <= half_retrace_threshold):
                    self.last_signal_bar = ctx.bar_index
                    return ctx.signal(
                        "flat",
                        confidence=0.6,
                        metadata={
                            "reason": "half_morning_move_retrace_exit",
                            "morning_move_pct": round(morning_move_pct, 2),
                            "current_retrace_pct": round(current_retrace_pct, 2),
                            "entry_price": entry_price,
                            "current_price": current_price
                        }
                    )

        return None

    def on_position_close(self, ctx: BarContext, position: Dict[str, Any]) -> None:
        # No adaptive logic for this strategy currently, just acknowledge close
        pass