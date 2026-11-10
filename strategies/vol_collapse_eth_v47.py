from domains.strategy_contract import Strategy, BarContext, Signal
from typing import Optional, Dict, Any
import math

class EthVolCoilBreakout(Strategy):
    METADATA = {
        "name": "EthVolCoilBreakout",
        "domain": "eth_usdc",
        "declared_sl_bps": 220.0,
        "declared_tp_bps": 480.0,
        "declared_hold_seconds": 18000,
        "warmup_bars": 200,
        "required_features": [],
    }

    def initialize(self, ctx: BarContext) -> None:
        self.vol_period = 168
        self.channel_period = 12
        self.cooldown_bars = 10
        self.state = "IDLE"
        self.spike_bar = -1
        self.coil_armed_bar = -1
        self.last_exit_bar = -999

    def _atr(self, highs, lows, closes, period=14):
        if len(closes) < period + 1:
            return None
        trs = []
        for i in range(1, len(closes)):
            tr = max(highs[i] - lows[i], abs(highs[i] - closes[i - 1]), abs(lows[i] - closes[i - 1]))
            trs.append(tr)
        return sum(trs[-period:]) / period

    def on_position_close(self, ctx: BarContext, position: Dict[str, Any]) -> None:
        self.last_exit_bar = ctx.bar_index
        self.state = "IDLE"

    def on_bar(self, ctx: BarContext) -> Optional[Signal]:
        closes = ctx.closes(self.vol_period + 20)
        highs = ctx.highs(self.vol_period + 20)
        lows = ctx.lows(self.vol_period + 20)

        if len(closes) < self.vol_period + 14:
            return None

        # Calculate 14-period True Range rolling history to evaluate Realized Volatility
        atr_window = 14
        tr_list = []
        for i in range(1, len(closes)):
            tr = max(highs[i] - lows[i], abs(highs[i] - closes[i - 1]), abs(lows[i] - closes[i - 1]))
            tr_list.append(tr)

        if len(tr_list) < self.vol_period:
            return None

        recent_trs = tr_list[-self.vol_period:]
        current_atr = sum(recent_trs[-atr_window:]) / atr_window
        sorted_trs = sorted(recent_trs)
        median_atr = sorted_trs[len(sorted_trs) // 2]

        if median_atr <= 0:
            return None

        vol_ratio = current_atr / median_atr
        current_bar = ctx.bar_index

        # Position management: Structural time-decay / ATR-based trailing exit
        if ctx.has_position():
            pos = ctx.position
            entry_price = pos.get("entry_price", ctx.bar.close)
            direction = pos.get("direction")
            bars_held = current_bar - self.coil_armed_bar

            # Structural volatility exit: if volatility re-explodes negatively against thesis
            if direction == "long" and ctx.bar.close < (entry_price - 1.5 * current_atr):
                return ctx.signal("flat", confidence=0.7, metadata={"reason": "struct_vol_stop_long", "atr": current_atr})
            elif direction == "short" and ctx.bar.close > (entry_price + 1.5 * current_atr):
                return ctx.signal("flat", confidence=0.7, metadata={"reason": "struct_vol_stop_short", "atr": current_atr})
            return None

        # Cooldown guard
        if current_bar - self.last_exit_bar < self.cooldown_bars:
            return None

        # State Machine: IDLE -> SPIKED -> COILED -> TRIGGER
        if self.state == "IDLE":
            if vol_ratio >= 1.85:
                self.state = "SPIKED"
                self.spike_bar = current_bar
        elif self.state == "SPIKED":
            if current_bar - self.spike_bar > 48:
                self.state = "IDLE"
            elif vol_ratio <= 1.05:
                self.state = "COILED"
                self.coil_armed_bar = current_bar
        elif self.state == "COILED":
            if current_bar - self.coil_armed_bar > 24:
                self.state = "IDLE"
            else:
                # 12-bar Donchian channel breakout
                chan_highs = highs[-(self.channel_period + 1):-1]
                chan_lows = lows[-(self.channel_period + 1):-1]
                highest_h = max(chan_highs)
                lowest_l = min(chan_lows)

                price = ctx.bar.close
                # Volatility-based structural stop calculation in bps
                stop_bps = max(140.0, min(300.0, (1.6 * current_atr / price) * 10000.0))
                tp_bps = stop_bps * 2.2

                if price > highest_h:
                    self.state = "IDLE"
                    return ctx.signal(
                        "long",
                        confidence=0.8,
                        stop_loss_bps=stop_bps,
                        take_profit_bps=tp_bps,
                        horizon_seconds=18000,
                        metadata={
                            "reason": "coil_breakout_long",
                            "breakout_level": highest_h,
                            "vol_ratio": round(vol_ratio, 3),
                            "atr": round(current_atr, 2),
                        },
                    )
                elif price < lowest_l:
                    self.state = "IDLE"
                    return ctx.signal(
                        "short",
                        confidence=0.8,
                        stop_loss_bps=stop_bps,
                        take_profit_bps=tp_bps,
                        horizon_seconds=18000,
                        metadata={
                            "reason": "coil_breakout_short",
                            "breakout_level": lowest_l,
                            "vol_ratio": round(vol_ratio, 3),
                            "atr": round(current_atr, 2),
                        },
                    )

        return None