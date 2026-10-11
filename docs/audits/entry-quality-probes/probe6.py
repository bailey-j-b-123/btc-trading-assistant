import os, sys; sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from probe3 import *
# E: candle#1 touches the band but closes only just above it (observed, NOT held); candle#2 never returns to the band and rockets: labelled 'held'
extra = [(83630, 83950, 83900), (83700, 83900, 83740), (83950, 84330, 84300), (84250, 84400, 84350)]
c, nb = scenario(extra, [30]*4, old_high=86500)
hist, frames = report(c, nb, "E: observed touch, then a later candle far from the band closes above it", only_swing=True)
for snap, fr in zip(hist, frames):
    i = (snap.as_of - EPOCH)//H - 1
    if i < nb: continue
    print(f"retests@candle#{i-nb}:", [(r.state, f"cand#{(r.candle.timestamp-EPOCH)//H-nb}", "dist_from_band", str(r.distance_from_band), r.breakout.reference.type) for r in fr.patterns.retests if r.breakout.reference.type=='swing_high'])
