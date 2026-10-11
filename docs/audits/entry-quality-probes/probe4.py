import os, sys; sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from probe3 import *
# B: retest candle low is only $31 below breakout close, $150 ABOVE the broken level (within 0.2% retest tolerance)
extra = [(83630, 83950, 83900), (83869, 84010, 84000), (83990, 84100, 84080)]
c, nb = scenario(extra, [30]*3); report(c, nb, "B: 'retest' whose low stays $150 above the broken level (tolerance 0.2% = 167.4)")
# C: one very large breakout candle (+~1.9% body) then tiny pullback candle
extra = [(83630, 85200, 85150), (85090, 85300, 85250), (85200, 85400, 85350)]
c, nb = scenario(extra, [30]*3); report(c, nb, "C: very large breakout candle; retest = candle that never returns near the level")
