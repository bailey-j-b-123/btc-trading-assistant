import os, sys; sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from probe1 import *

def scenario(extra, vols_after, old_high=84400):
    path = []
    path += ramp(83000, old_high, 6)    # old high X (the structural target above)
    path += ramp(old_high, 82500, 8)
    path += ramp(82500, 83200, 8)    # peak A
    path += ramp(83200, 82900, 4)
    path += ramp(82900, 83700, 8)    # peak B = reference swing high
    path += ramp(83700, 83300, 4)
    path += ramp(83300, 83640, 4)
    nb = len(path)
    path += extra
    vols = {nb+i: v for i, v in enumerate(vols_after)}
    return build(path, vols), nb

def report(c, nb, label, only_swing=True):
    bounds = [x.timestamp + H for x in c]
    snaps = analyze_patterns_at(c, exchange="binance", symbol="BTC/USDT", timeframe="1h", as_of_boundaries=bounds)
    frames = [QualificationFrame(s, ()) for s in snaps]
    hist = enumerate_qualifications(frames, as_of=bounds[-1])
    print("=====", label)
    for snap, fr in zip(hist, frames):
        i = (snap.as_of - EPOCH)//H - 1
        if i < nb: continue
        cand = c[i]
        print(f"candle#{i-nb} close {snap.as_of:%H:%M}  O{cand.open} H{cand.high} L{cand.low} C{cand.close} V{cand.volume}")
        for st in snap.setups:
            ref = next((b.reference for b in fr.patterns.breakouts if b.id == st.seed_event_id), None)
            if only_swing and ref is not None and ref.type != "swing_high": continue
            line = f"   {st.state.value:9s} term={st.terminal_reason} failed={st.failed_rules} pending={[p for p in st.pending_rules if p not in ('higher_timeframe:not_requested','classical_pattern')]}"
            if st.state.value == "QUALIFIED":
                plan = plan_trade(snapshot=snap, frame=fr, setup_id=st.id)
                line += f"\n      PLAN {plan.state.value} entry={plan.entry.value} stop={plan.stop.value} risk={plan.risk_per_unit} targets={[(str(t.level.value), str(t.r_multiple)) for t in plan.targets]} reasons={plan.reasons}"
            print(line)
    return hist, frames

if __name__ == "__main__":
    # A: ordinary: breakout, shallow retest, continuation
    extra = [(83630, 83950, 83900), (83760, 83980, 83940), (83930, 84120, 84100), (84050, 84300, 84250), (84200, 84390, 84300)]
    c, nb = scenario(extra, [30]*5)
    report(c, nb, "A: breakout -> shallow held retest -> continuation")
