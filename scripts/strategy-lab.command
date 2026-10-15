#!/bin/sh
# Dedicated standalone research. No operating-bot configuration or credentials.
set -eu
LAB_SOURCE=$(CDPATH= cd -- "$(dirname -- "$0")/.." && pwd -P)
cd "$LAB_SOURCE"
LAB_PYTHON="$LAB_SOURCE/.strategy-lab-runtime/bin/python"
if [ ! -x "$LAB_PYTHON" ]; then
    printf '%s\n' 'First run: python3 -m venv .strategy-lab-runtime' '.strategy-lab-runtime/bin/pip install -e ".[dev]"'
    exit 1
fi
if [ -L "$LAB_SOURCE/.strategy-lab-runtime" ] || [ -L "$LAB_SOURCE/.strategy-lab" ]; then
    printf '%s\n' 'Refusing redirected lab runtime or storage.'
    exit 1
fi
if [ "$#" -eq 0 ]; then
    set -- overnight --minutes 480
fi
# A clean environment prevents application settings, .env and private values
# entering the child. Source, runtime and lab paths are fixed to this checkout.
if [ "$(uname -s)" = Darwin ] && [ "$1" = overnight ]; then
    exec /usr/bin/caffeinate -i /usr/bin/env -i PATH=/usr/bin:/bin PYTHONPATH="$LAB_SOURCE/src" "$LAB_PYTHON" -m trading_assistant.strategy_lab --root "$LAB_SOURCE/.strategy-lab" "$@"
fi
exec /usr/bin/env -i PATH=/usr/bin:/bin PYTHONPATH="$LAB_SOURCE/src" "$LAB_PYTHON" -m trading_assistant.strategy_lab --root "$LAB_SOURCE/.strategy-lab" "$@"
