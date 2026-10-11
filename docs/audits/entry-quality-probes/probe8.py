import os, sys; sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from probe3 import *
# F: how long does QUALIFIED/PLANNABLE persist after the held retest? (far target; steady climb; strong volume)
extra = [(83630, 83950, 83900), (83760, 83980, 83940)]
px = 83940
for k in range(12):
    nxt = px + 60
    extra.append((px - 10, nxt + 20, nxt)); px = nxt
c, nb = scenario(extra, [30]*len(extra), old_high=90000)
report(c, nb, "F: persistence", only_swing=True)
