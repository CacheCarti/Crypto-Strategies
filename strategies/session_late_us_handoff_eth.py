from domains.strategy_contract import Strategy, BarContext, Signal
from typing import Optional, Dict, Any
import math

class EthSessionHandoff(Strategy):
    METADATA = {
        "name": "EthSessionHandoff",
        "domain": "eth_usdc",
        "declared_sl_bps": 200.0,
        "declared_tp_bps": 400.0,
        "declared_hold_seconds": 3600, # Intended hold: one session (1-6 hours)
        "warmup_bars": 30,
        "required_features": [], # No external features used for this hypothesis
    }

    def initialize(self, ctx: BarContext) -> None:
        self.entry_hour_start = 18 # UTC 18:00 (late US session)
        self.entry_hour_end = 23   # UTC 23:00 (last bar of late US session)
        self.exit_hour = 0         # UTC 00:00 (start of new day/session)
        self.cooldown_bars = 6     # Cooldown for 6 hours after a trade
        self.last_trade_bar_index = -self.cooldown_bars - 1 # Initialize to allow immediate trade

        self.session_impulse_period = 6 # Hours for session impulse calculation (18:00 to 23:00)

        # Parameters for ADX/DMI
        self.adx_period = 14
        self.adx_threshold = 20
        self.plus_di_threshold = 20
        self.minus_di_threshold = 20
        self.adx_lookback = self.adx_period + 1 # for TR

    def _atr(self, highs, lows, closes, period=14):
        if len(closes) < period + 1:
            return None
        trs = []
        for i in range(1, len(closes)):
            tr = max(highs[i]-lows[i], abs(highs[i]-closes[i-1]), abs(lows[i]-closes[i-1]))
            trs.append(tr)
        return sum(trs[-period:]) / period

    def _adx_dmi(self, highs, lows, closes, period=14):
        if len(highs) < period + 1:
            return None, None, None # ADX, +DI, -DI

        plus_dm = []
        minus_dm = []
        for i in range(1, len(highs)):
            up_move = highs[i] - highs[i-1]
            down_move = lows[i-1] - lows[i]

            plus_dm_val = 0.0
            minus_dm_val = 0.0

            if up_move > down_move and up_move > 0:
                plus_dm_val = up_move
            elif down_move > up_move and down_move > 0:
                minus_dm_val = down_move
            
            plus_dm.append(plus_dm_val)
            minus_dm.append(minus_dm_val)

        tr_values = []
        for i in range(1, len(closes)):
            tr_values.append(max(highs[i] - lows[i], abs(highs[i] - closes[i-1]), abs(lows[i] - closes[i-1])))
        
        # Calculate smoothed TR, +DM, -DM
        # Initial smoothing is a simple sum over 'period' bars
        # Subsequent smoothing uses Wilder's method: current_val - (current_val/period) + previous_smoothed_val
        
        if len(tr_values) < period:
            return None, None, None

        # Initial 'period' sum
        smoothed_tr = [sum(tr_values[:period])]
        smoothed_plus_dm = [sum(plus_dm[:period])]
        smoothed_minus_dm = [sum(minus_dm[:period])]

        for i in range(period, len(tr_values)):
            smoothed_tr.append(smoothed_tr[-1] - (smoothed_tr[-1] / period) + tr_values[i])
            smoothed_plus_dm.append(smoothed_plus_dm[-1] - (smoothed_plus_dm[-1] / period) + plus_dm[i])
            smoothed_minus_dm.append(smoothed_minus_dm[-1] - (smoothed_minus_dm[-1] / period) + minus_dm[i])
        
        if not smoothed_tr or smoothed_tr[-1] == 0:
            return None, None, None

        # Calculate DI+ and DI-
        plus_di = (smoothed_plus_dm[-1] / smoothed_tr[-1]) * 100
        minus_di = (smoothed_minus_dm[-1] / smoothed_tr[-1]) * 100

        # Calculate DX
        dx_val = 0.0
        if (plus_di + minus_di) != 0:
            dx_val = (abs(plus_di - minus_di) / (plus_di + minus_di)) * 100
        
        # Calculate ADX (smoothed DX)
        dx_list = []
        for i in range(len(smoothed_tr)): # Use smoothed_tr as a proxy for the length of calculated DI values
            if (smoothed_plus_dm[i] + smoothed_minus_dm[i]) != 0:
                current_plus_di = (smoothed_plus_dm[i] / smoothed_tr[i]) * 100
                current_minus_di = (smoothed_minus_dm[i] / smoothed_tr[i]) * 100
                dx_list.append((abs(current_plus_di - current_minus_di) / (current_plus_di + current_minus_di)) * 100)
            else:
                dx_list.append(0.0) # Or handle as appropriate

        if len(dx_list) < period:
            return None, plus_di, minus_di # Not enough data for ADX yet

        # Initial ADX smoothing
        adx = [sum(dx_list[-period:]) / period] if len(dx_list) >= period else []
        
        if not adx:
            return None, plus_di, minus_di

        # Remaining ADX smoothing
        for i in range(len(dx_list) - period, len(dx_list) -1):
            adx_val = adx[-1] - (adx[-1] / period) + dx_list[i]
            adx.append(adx_val)

        return adx[-1], plus_di, minus_di

    def on_bar(self, ctx: BarContext) -> Optional[Signal]:
        if ctx.bar_index < self.METADATA["warmup_bars"] + self.adx_lookback + self.adx_period:
            return None

        current_hour = ctx.bar.timestamp.hour
        
        # Exit logic for existing position
        if ctx.has_position():
            if current_hour == self.exit_hour:
                return ctx.signal("flat", confidence=0.7, metadata={
                    "reason": "session_handoff_exit",
                    "entry_hour": ctx.position["metadata"].get("entry_hour"),
                    "exit_hour": current_hour,
                    "bar_index_held": ctx.bar_index - ctx.position["metadata"].get("entry_bar_index")
                })
            return None # Hold position if not exit hour

        # Cooldown check
        if ctx.bar_index - self.last_trade_bar_index <= self.cooldown_bars:
            return None

        # Entry window: late US session
        if self.entry_hour_start <= current_hour <= self.entry_hour_end:
            # Check if it's the last bar for potential entry in this window
            # We want to measure the session impulse and enter on its close
            if current_hour == self.entry_hour_end:
                # Need at least self.session_impulse_period bars to calculate impulse
                if len(ctx.closes(self.session_impulse_period + 1)) < self.session_impulse_period + 1:
                    return None
                
                # Calculate session directional impulse (e.g., net change over the session)
                session_open = ctx.opens(self.session_impulse_period + 1)[0]
                session_close = ctx.bar.close
                
                price_change = session_close - session_open
                
                # ADX/DMI for trend confirmation
                adx, plus_di, minus_di = self._adx_dmi(
                    ctx.highs(self.adx_lookback + self.adx_period),
                    ctx.lows(self.adx_lookback + self.adx_period),
                    ctx.closes(self.adx_lookback + self.adx_period),
                    self.adx_period
                )

                if adx is None or plus_di is None or minus_di is None:
                    return None

                signal_direction = None
                reason = "no_signal"
                confidence = 0.0

                # Long condition: positive impulse + confirmed uptrend by DMI
                if price_change > 0 and plus_di > minus_di and adx > self.adx_threshold:
                    signal_direction = "long"
                    reason = "late_us_session_long_impulse_adx_confirm"
                    confidence = min(0.7 + (adx - self.adx_threshold) * 0.01, 0.9) # Scale confidence with ADX strength
                # Short condition: negative impulse + confirmed downtrend by DMI
                elif price_change < 0 and minus_di > plus_di and adx > self.adx_threshold:
                    signal_direction = "short"
                    reason = "late_us_session_short_impulse_adx_confirm"
                    confidence = min(0.7 + (adx - self.adx_threshold) * 0.01, 0.9) # Scale confidence with ADX strength

                if signal_direction:
                    self.last_trade_bar_index = ctx.bar_index
                    return ctx.signal(
                        signal_direction,
                        confidence=confidence,
                        metadata={
                            "reason": reason,
                            "entry_hour": current_hour,
                            "entry_bar_index": ctx.bar_index,
                            "session_open": session_open,
                            "session_close": session_close,
                            "price_change": price_change,
                            "adx": adx,
                            "plus_di": plus_di,
                            "minus_di": minus_di
                        }
                    )
        return None

    def on_position_close(self, ctx: BarContext, position: Dict[str, Any]) -> None:
        # Optional: adaptive logic based on position close.
        # For this strategy, we just log and reset cooldown if needed.
        pass