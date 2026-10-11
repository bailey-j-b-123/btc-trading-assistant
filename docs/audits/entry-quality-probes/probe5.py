import os, sys; sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from probe3 import *
# D: held retest on candle #1 with LOW volume (relative volume < 1) -> WATCH; later strong-volume bars -> QUALIFIED/PLANNABLE at a higher price
extra = [(83630, 83950, 83900), (83760, 83980, 83940), (83930, 84120, 84100), (84050, 84300, 84250), (84200, 84390, 84300)]
vols = [30, 2, 30, 30, 30]
c, nb = scenario(extra, vols, old_high=86500); report(c, nb, "D: held retest on a low-volume candle; target far above (86.5k)")
