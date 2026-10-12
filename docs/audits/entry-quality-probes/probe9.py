import os, sys; sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from probe3 import *

# Scenario: Breakout above reference at 83521.15, retest holds, decision candle closes at 83529.47 ($8.32 above stop)
# Nearest structural target is at 83572.96 ($43.49 away)
# Shows that risk=8.32 produces R=5.23 and passes all planning checks without any minimum risk distance filter.
extra = [
    (83510, 83550, 83535),  # breakout candle
    (83515, 83540, 83528),  # retest candle
    (83520, 83545, 83529.47) # decision candle close at 83529.47
]
c, nb = scenario(extra, [30]*3, old_high=83572.96)
report(c, nb, "Trade 2 Synthetic Probe: tiny stop distance ($8.32) passes planning checks", only_swing=True)
