import os, sys; sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from probe3 import *
extra = [(83630, 83950, 83900), (83760, 83980, 83940), (83930, 84120, 84100)]
c, nb = scenario(extra, [30]*3, old_high=86500)
hist, frames = report(c, nb, "A2: unfiltered (all setups from the same breakout candle)", only_swing=False)
snap = hist[-2]; fr = frames[-2]
print([ (s.id[:8], next(b.reference.type for b in fr.patterns.breakouts if b.id==s.seed_event_id), str(next(b.reference.band_low for b in fr.patterns.breakouts if b.id==s.seed_event_id)), str(next(b.reference.band_high for b in fr.patterns.breakouts if b.id==s.seed_event_id))) for s in snap.setups])
