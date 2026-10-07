"""Component #4 audit: setup-qualification replay (Step 5) regression tests.

Step 5 replays hand-validated frames through strict input validation. Two
validator gaps let inconsistent hand-built inputs through:

* datetimes nested inside tuples (touch/source timestamp sequences) were
  never checked against the frame ``as_of`` — a future timestamp buried in
  ``upper_touch_timestamps`` passed validation;
* ``swings``/``levels`` detection results were not required to share the
  context ``as_of``, despite the documented
  "context metrics must share their source window/as_of" guarantee.

Production frames are coherent by construction, so both repairs only make
hand-built incoherence fail loudly; coherent replays are byte-identical.
"""

from __future__ import annotations

from dataclasses import replace

import pytest
from test_setup_qualification import at, frame, frozen_range, result

from trading_assistant.setup_qualification import QualificationFrame


def test_future_timestamp_inside_a_tuple_is_rejected() -> None:
    corrupted = replace(frozen_range(), upper_touch_timestamps=(at(99),))
    with pytest.raises(ValueError, match="after frame as_of"):
        result([frame(6, active_range=corrupted)])


def test_stale_swings_detection_as_of_is_rejected() -> None:
    base = frame(6)
    stale = replace(base.patterns.structure.swings, as_of=at(0))
    source = replace(
        base.patterns, structure=replace(base.patterns.structure, swings=stale)
    )
    with pytest.raises(ValueError, match="share their source window/as_of"):
        result([QualificationFrame(source, ())])


def test_stale_levels_detection_as_of_is_rejected() -> None:
    base = frame(6)
    stale = replace(base.patterns.structure.levels, as_of=at(0))
    source = replace(
        base.patterns, structure=replace(base.patterns.structure, levels=stale)
    )
    with pytest.raises(ValueError, match="share their source window/as_of"):
        result([QualificationFrame(source, ())])


def test_coherent_hand_built_frames_still_replay() -> None:
    snapshot = result([frame(6, active_range=frozen_range())])
    assert snapshot.as_of == at(6)
    assert snapshot.status in ("evaluated", "incomplete")
