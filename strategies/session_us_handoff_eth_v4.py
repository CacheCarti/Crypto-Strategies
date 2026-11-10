from domains.strategy_contract import Strategy, BarContext, Signal
from typing import Optional, Dict, Any
import math

class USSessionHandoffETH(Strategy):
    METADATA = {
        "name": "US Session Handoff ETH",
        "domain": "eth_usdc",
        "declared_sl_bps": 200.0,
        "declared_tp_bps": 400.0,
        "declared_hold_seconds": 3600, # Intended hold through session handoff
        "warmup_bars": 50,
        "required_features": ["fear_greed_index"],
    }

    def initialize(self, ctx: BarContext) -> None:
        self.entry_hour_start = 13  # UTC 13:00 (US session open)
        self.entry_hour_end = 16    # UTC 16:00 (mid US session for impulse check)
        self.exit_hour = 21         # UTC 21:00 (before next session handoff)
        self.min_session_impulse_vol_mult = 1.5 # Min volume multiplier for impulse
        self.min_session_impulse_move_pct = 0.005 # Min price move for impulse (0.5%)
        self.cooldown_bars = 5 # Bars to wait after a trade before new entry
        self.last_trade_bar_index = -self.cooldown_bars

        # Indicator periods
        self.ema_fast_period = 12
        self.ema_slow_period = 26
        self.rsi_period = 14
        self.atr_period = 14

        # State for daily trade limit
        self.last_trade_day = -1
        self.last_trade_direction = None

    def _ema(self, values, period):
        if len(values) < period:
            return None
        k = 2.0 / (period + 1)
        ema_val = sum(values[:period]) / period
        for v in values[period:]:
            ema_val = v * k + ema_val * (1 - k)
        return ema_val

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

    def _atr(self, highs, lows, closes, period):
        if len(closes) < period + 1:
            return None
        trs = []
        for i in range(1, len(closes)):
            tr = max(highs[i]-lows[i], abs(highs[i]-closes[i-1]), abs(lows[i]-closes[i-1]))
            trs.append(tr)
        return sum(trs[-period:]) / period

    def on_bar(self, ctx: BarContext) -> Optional[Signal]:
        closes = ctx.closes(self.warmup_bars)
        highs = ctx.highs(self.warmup_bars)
        lows = ctx.lows(self.warmup_bars)
        volumes = ctx.volumes(self.warmup_bars)

        if len(closes) < self.warmup_bars:
            return None

        current_hour = ctx.bar.timestamp.hour
        current_day = ctx.bar.timestamp.day

        # Always check for exit condition first
        if ctx.has_position():
            if current_hour >= self.exit_hour:
                reason = "session_boundary_exit"
                if ctx.position_direction() == "long":
                    reason += "_long"
                else:
                    reason += "_short"
                return ctx.signal("flat", confidence=0.8, metadata={
                    "reason": reason,
                    "exit_hour": current_hour,
                    "entry_price": ctx.position["entry_price"],
                    "current_price": ctx.bar.close,
                    "bars_held": ctx.bar_index - self.last_trade_bar_index
                })

        # Cooldown period check
        if ctx.bar_index - self.last_trade_bar_index < self.cooldown_bars:
            return None

        # Only one trade per day/session
        if current_day == self.last_trade_day:
            return None

        # Entry logic within the specified US session window
        if self.entry_hour_start <= current_hour < self.entry_hour_end and not ctx.has_position():
            # Calculate session's impulse
            # We need bars from the start of the session up to current bar
            # This requires knowing the timestamp of the bar at self.entry_hour_start
            # For simplicity, we'll check the move over the last few hours within the window
            # A more robust approach would track the exact session open bar.

            # Let's define the "impulse window" as the bars from self.entry_hour_start up to current_hour
            # We need to find the open of the first bar of the session (current day, self.entry_hour_start)
            # This is hard with just ctx.closes() which gives relative history.
            # A simpler approach: check recent price action.

            # For a 1-hour bar, let's look at the last few bars within the entry window
            # Check price move and volume over the last 3 hours (3 bars)
            lookback_hours = 3
            if len(closes) < lookback_hours + 1:
                return None

            recent_closes = closes[-lookback_hours-1:]
            recent_volumes = volumes[-lookback_hours:]

            price_change_pct = (recent_closes[-1] - recent_closes[0]) / recent_closes[0]
            avg_volume_recent = sum(recent_volumes) / lookback_hours
            
            # Compare recent volume to average volume over a longer period (e.g., last 24 hours)
            if len(volumes) < 24: # Need at least 24 bars for average daily volume
                return None
            
            avg_volume_24h = sum(volumes[-24:]) / 24 if sum(volumes[-24:]) > 0 else 1 # Avoid div by zero
            volume_ratio = avg_volume_recent / avg_volume_24h

            # Regime filters
            fear_greed_index = ctx.features.get("fear_greed_index", 50)
            market_regime = ctx.market.get("regime", "NORMAL")
            trend_regime = ctx.market.get("trend_regime", "neutral")

            # Calculate indicators
            ema_fast = self._ema(closes, self.ema_fast_period)
            ema_slow = self._ema(closes, self.ema_slow_period)
            rsi_val = self._rsi(closes, self.rsi_period)
            atr_val = self._atr(highs, lows, closes, self.atr_period)

            if ema_fast is None or ema_slow is None or rsi_val is None or atr_val is None:
                return None

            signal_direction = None
            confidence = 0.0
            reason = ""
            
            # Long entry conditions
            if price_change_pct > self.min_session_impulse_move_pct and \
               volume_ratio > self.min_session_impulse_vol_mult and \
               ema_fast > ema_slow and \
               rsi_val > 50 and \
               market_regime in ["NORMAL", "HIGH_VOL"] and \
               fear_greed_index > 40: # Not too fearful
                
                signal_direction = "long"
                confidence = min(0.6 + (rsi_val - 50) / 50 * 0.3, 0.9) # Scale confidence with RSI
                reason = "us_session_bullish_impulse_ema_above_rsi_strong"

            # Short entry conditions
            elif price_change_pct < -self.min_session_impulse_move_pct and \
                 volume_ratio > self.min_session_impulse_vol_mult and \
                 ema_fast < ema_slow and \
                 rsi_val < 50 and \
                 market_regime in ["NORMAL", "HIGH_VOL"] and \
                 fear_greed_index < 60: # Not too greedy
                
                signal_direction = "short"
                confidence = min(0.6 + (50 - rsi_val) / 50 * 0.3, 0.9) # Scale confidence with RSI
                reason = "us_session_bearish_impulse_ema_below_rsi_strong"

            if signal_direction:
                self.last_trade_bar_index = ctx.bar_index
                self.last_trade_day = current_day
                self.last_trade_direction = signal_direction
                
                # Dynamic SL/TP based on ATR
                dynamic_sl_bps = max(self.METADATA["declared_sl_bps"], (atr_val / ctx.bar.close) * 10000 * 1.5)
                dynamic_tp_bps = max(self.METADATA["declared_tp_bps"], (atr_val / ctx.bar.close) * 10000 * 2.5)

                return ctx.signal(
                    signal_direction,
                    confidence=confidence,
                    stop_loss_bps=dynamic_sl_bps,
                    take_profit_bps=dynamic_tp_bps,
                    horizon_seconds=self.METADATA["declared_hold_seconds"],
                    metadata={
                        "reason": reason,
                        "current_hour": current_hour,
                        "price_change_pct": round(price_change_pct, 4),
                        "volume_ratio": round(volume_ratio, 2),
                        "ema_fast": round(ema_fast, 2),
                        "ema_slow": round(ema_slow, 2),
                        "rsi": round(rsi_val, 2),
                        "atr": round(atr_val, 2),
                        "market_regime": market_regime,
                        "trend_regime": trend_regime,
                        "fear_greed_index": fear_greed_index,
                        "dynamic_sl_bps": round(dynamic_sl_bps, 2),
                        "dynamic_tp_bps": round(dynamic_tp_bps, 2)
                    }
                )

        return None

    def on_position_close(self, ctx: BarContext, position: Dict[str, Any]) -> None:
        # Reset cooldown if position closed for any reason (SL, TP, horizon, flat signal)
        # This ensures we respect the cooldown after any trade completion.
        # We also reset the daily trade marker, allowing a trade on the next day.
        self.last_trade_bar_index = ctx.bar_index
        # self.last_trade_day = -1 # Allow trade on next bar (if new day) or next day