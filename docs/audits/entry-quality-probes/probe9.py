"""Probe 9: Executable synthetic test demonstrating Trade 2 tiny stop planning pass."""

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from test_tiny_stop_planning import test_tiny_stop_distance_accepted_by_planner

if __name__ == "__main__":
    test_tiny_stop_distance_accepted_by_planner()
