import json
import logging

from trading_assistant.logging_config import JsonFormatter


def test_json_formatter_emits_structured_log_fields():
    record = logging.LogRecord(
        name="trading_assistant.test",
        level=logging.INFO,
        pathname=__file__,
        lineno=10,
        msg="configuration loaded",
        args=(),
        exc_info=None,
    )
    record.fields = {"symbol": "ETH/USDT"}

    output = json.loads(JsonFormatter().format(record))

    assert output["level"] == "INFO"
    assert output["logger"] == "trading_assistant.test"
    assert output["message"] == "configuration loaded"
    assert output["fields"] == {"symbol": "ETH/USDT"}
    assert output["timestamp"].endswith("Z")
