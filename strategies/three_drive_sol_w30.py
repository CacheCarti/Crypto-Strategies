from domains.strategy_contract import Strategy, BarContext, Signal
from typing import Optional, Dict, Any
import math
import collections

class ExhaustionFader(Strategy):
    METADATA = {
        "name": "Exhaustion Fader",
        "domain": "sol_usdc",
        "declared_sl_bps": 200.0,
        "declared_tp_bps": 400.0,
        "declared_hold_seconds": 3600, # 1-hour bars, aiming for multi-bar holds
        "warmup_bars": 60, # Need enough bars for 3 drives and momentum checks
        "required_features": ["fear_greed_index"],
    }

    def initialize(self, ctx: BarContext) -> None:
        self.drive_period = 15 # Period to look for price pushes
        self.momentum_period = 5 # Shorter period for momentum comparison
        self.min_drive_strength_pct = 0.5 # Minimum percentage gain/loss for a drive
        self.cooldown_bars = 5 # Cooldown after a trade
        self.last_signal_bar = -self.cooldown_bars

    def _sma(self, values, period):
        if len(values) < period:
            return None
        return sum(values[-period:]) / period

    def _stddev(self, values, period):
        if len(values) < period:
            return None
        slice_v = values[-period:]
        mean = sum(slice_v) / period
        variance = sum((x - mean) ** 2 for x in slice_v) / period
        return math.sqrt(variance)

    def _zscore(self, values, period):
        if len(values) < period:
            return None
        mean = self._sma(values, period)
        std = self._stddev(values, period)
        if std is None or std == 0:
            return 0.0
        return (values[-1] - mean) / std

    def on_bar(self, ctx: BarContext) -> Optional[Signal]:
        closes = ctx.closes(self.drive_period + self.momentum_period + 1)
        highs = ctx.highs(self.drive_period + self.momentum_period + 1)
        lows = ctx.lows(self.drive_period + self.momentum_period + 1)

        if len(closes) < self.METADATA["warmup_bars"]:
            return None

        if ctx.bar_index < self.last_signal_bar + self.cooldown_bars:
            return None

        current_price = ctx.bar.close

        # Market context filters
        fear_greed = ctx.features.get("fear_greed_index", 50)
        regime = ctx.market.get("regime", "NORMAL")

        # Only trade in 'NORMAL' or 'HIGH_VOL' regimes
        if regime not in ["NORMAL", "HIGH_VOL"]:
            return None
        
        # Avoid trading during extreme fear/greed
        if fear_greed < 25 or fear_greed > 75:
             return None

        # --- Exhaustion Logic ---
        # Look for 3 drives in the same direction with weakening momentum
        
        # Check for bullish exhaustion (looking to short)
        if not ctx.has_position():
            bullish_drives = self._detect_drives(closes, highs, lows, "long")
            if bullish_drives and len(bullish_drives) >= 3:
                # Drives are (start_bar_index, end_bar_index, price_change_pct, momentum_zscore)
                # Check for weakening momentum: last drive momentum < previous drive momentum
                # And the last drive should show signs of weakness (e.g., lower z-score)
                last_drive = bullish_drives[-1]
                second_last_drive = bullish_drives[-2]
                
                # Check for overall exhaustion: last drive's momentum is weaker than the second last
                if last_drive[3] < second_last_drive[3] * 0.8: # Last drive momentum significantly weaker
                    # Check if the current bar is failing to push higher after the last drive
                    # i.e., current price is below the high of the last drive's peak, or z-score is negative
                    z_score_current = self._zscore(closes, self.momentum_period)
                    
                    if z_score_current < -0.5: # Current price is weakening significantly
                        entry_price = ctx.bar.close
                        midpoint_of_last_drive = (highs[last_drive[1]] + lows[last_drive[0]]) / 2
                        target_price = entry_price - (entry_price - midpoint_of_last_drive) * 0.5 # Aim for halfway retracement
                        
                        # Calculate custom SL/TP based on the drive structure
                        custom_sl_bps = 100.0 # tight stop above the last drive's high
                        custom_tp_bps = abs((target_price - entry_price) / entry_price * 10000)
                        
                        if custom_tp_bps < 100: # Ensure minimum TP
                            custom_tp_bps = 100.0

                        self.last_signal_bar = ctx.bar_index
                        return ctx.signal(
                            "short",
                            confidence=0.75,
                            stop_loss_bps=custom_sl_bps,
                            take_profit_bps=custom_tp_bps,
                            horizon_seconds=self.METADATA["declared_hold_seconds"],
                            metadata={
                                "reason": "bullish_exhaustion_fade",
                                "last_drive_momentum_z": last_drive[3],
                                "second_last_drive_momentum_z": second_last_drive[3],
                                "current_z_score": z_score_current,
                                "entry_price": entry_price,
                                "target_midpoint": midpoint_of_last_drive,
                                "regime": regime,
                                "fear_greed": fear_greed
                            },
                        )

        # Check for bearish exhaustion (looking to long)
        if not ctx.has_position():
            bearish_drives = self._detect_drives(closes, highs, lows, "short")
            if bearish_drives and len(bearish_drives) >= 3:
                last_drive = bearish_drives[-1]
                second_last_drive = bearish_drives[-2]

                if last_drive[3] < second_last_drive[3] * 0.8: # Last drive momentum significantly weaker
                    z_score_current = self._zscore(closes, self.momentum_period)
                    
                    if z_score_current > 0.5: # Current price is strengthening significantly
                        entry_price = ctx.bar.close
                        midpoint_of_last_drive = (highs[last_drive[0]] + lows[last_drive[1]]) / 2
                        target_price = entry_price + (midpoint_of_last_drive - entry_price) * 0.5 # Aim for halfway retracement

                        custom_sl_bps = 100.0 # tight stop below the last drive's low
                        custom_tp_bps = abs((target_price - entry_price) / entry_price * 10000)
                        
                        if custom_tp_bps < 100: # Ensure minimum TP
                            custom_tp_bps = 100.0

                        self.last_signal_bar = ctx.bar_index
                        return ctx.signal(
                            "long",
                            confidence=0.75,
                            stop_loss_bps=custom_sl_bps,
                            take_profit_bps=custom_tp_bps,
                            horizon_seconds=self.METADATA["declared_hold_seconds"],
                            metadata={
                                "reason": "bearish_exhaustion_fade",
                                "last_drive_momentum_z": last_drive[3],
                                "second_last_drive_momentum_z": second_last_drive[3],
                                "current_z_score": z_score_current,
                                "entry_price": entry_price,
                                "target_midpoint": midpoint_of_last_drive,
                                "regime": regime,
                                "fear_greed": fear_greed
                            },
                        )

        return None

    def _detect_drives(self, closes, highs, lows, direction):
        drives = []
        
        # Iterate backwards from the most recent bar to find drives
        for i in range(len(closes) - self.drive_period, len(closes) - self.momentum_period - 1):
            drive_start_index = i
            drive_end_index = i + self.drive_period - 1 # Last bar of this potential drive

            if drive_end_index >= len(closes):
                continue

            drive_slice_closes = closes[drive_start_index : drive_end_index + 1]
            drive_slice_highs = highs[drive_start_index : drive_end_index + 1]
            drive_slice_lows = lows[drive_start_index : drive_end_index + 1]

            if direction == "long":
                # A long drive: price increases significantly
                price_change = drive_slice_closes[-1] - drive_slice_closes[0]
                price_change_pct = price_change / drive_slice_closes[0] * 100
                
                if price_change_pct > self.min_drive_strength_pct:
                    # Calculate momentum for this drive using a shorter period z-score
                    momentum_zscore = self._zscore(drive_slice_closes, self.momentum_period)
                    if momentum_zscore is not None:
                        drives.append((drive_start_index, drive_end_index, price_change_pct, momentum_zscore))
            elif direction == "short":
                # A short drive: price decreases significantly
                price_change = drive_slice_closes[0] - drive_slice_closes[-1]
                price_change_pct = price_change / drive_slice_closes[0] * 100
                
                if price_change_pct > self.min_drive_strength_pct:
                    # Calculate momentum for this drive using a shorter period z-score
                    momentum_zscore = self._zscore(drive_slice_closes, self.momentum_period)
                    if momentum_zscore is not None:
                        drives.append((drive_start_index, drive_end_index, price_change_pct, momentum_zscore))
        
        # Filter for consecutive drives in the same direction and sort by end index
        drives_filtered = []
        if drives:
            # Sort by end_index to process chronologically
            drives.sort(key=lambda x: x[1])
            
            current_sequence = []
            for i in range(len(drives)):
                if not current_sequence:
                    current_sequence.append(drives[i])
                else:
                    # Check if the current drive starts roughly after the previous one ends
                    # and there's no significant price reversal in between
                    prev_drive_end_bar = current_sequence[-1][1]
                    current_drive_start_bar = drives[i][0]

                    # A simple check: current drive must start after or very close to previous drive end
                    # And there should be no significant overlap or gap that indicates a reversal
                    if current_drive_start_bar >= prev_drive_end_bar:
                        current_sequence.append(drives[i])
                    else:
                        if len(current_sequence) >= 3:
                            drives_filtered.append(current_sequence)
                        current_sequence = [drives[i]]
            
            if len(current_sequence) >= 3:
                drives_filtered.append(current_sequence)
        
        # Return the longest sequence of valid drives, or the most recent one if multiple
        if drives_filtered:
            # We want the *most recent* sequence of 3+ drives
            # Find the sequence with the latest end bar
            latest_sequence = None
            latest_end_bar_index = -1
            for seq in drives_filtered:
                if seq[-1][1] > latest_end_bar_index:
                    latest_end_bar_index = seq[-1][1]
                    latest_sequence = seq
            
            if latest_sequence and len(latest_sequence) >= 3:
                return latest_sequence
        
        return None

    def on_position_close(self, ctx: BarContext, position: Dict[str, Any]) -> None:
        # Log the outcome for analysis
        pass