import io
import json
import logging
import logging.config
import unittest

from app.logging_config import JsonFormatter, logging_config


class LoggingTests(unittest.TestCase):
    def test_multiline_chained_exception_is_one_json_record(self):
        output = io.StringIO()
        handler = logging.StreamHandler(output)
        handler.setFormatter(JsonFormatter("app"))
        logger = logging.Logger("test.exception")
        logger.addHandler(handler)
        try:
            try:
                raise ValueError('first line\n"second" — õ')
            except ValueError as cause:
                raise RuntimeError("outer failure") from cause
        except RuntimeError:
            logger.exception("Failed %s\nwith context", "operation")
        lines = output.getvalue().splitlines()
        self.assertEqual(len(lines), 1)
        event = json.loads(lines[0])
        self.assertEqual(event["severity"], "ERROR")
        self.assertEqual(event["service"], "app")
        self.assertEqual(event["logger"], "test.exception")
        self.assertEqual(event["message"], "Failed operation\nwith context")
        self.assertIn("ValueError", event["exception"])
        self.assertIn("RuntimeError", event["exception"])
        self.assertIn("direct cause", event["exception"])
        self.assertIn('"second" — õ', event["exception"])

    def test_uvicorn_and_library_events_use_same_json_handler_once(self):
        logging.config.dictConfig(logging_config("mcp"))
        output = io.StringIO()
        logging.getLogger().handlers[0].setStream(output)
        for name in ("uvicorn.error", "mcp.server", "httpx", "app.passi_auth"):
            logging.getLogger(name).warning("test event")
        events = [json.loads(line) for line in output.getvalue().splitlines()]
        self.assertEqual(len(events), 4)
        self.assertTrue(all(event["service"] == "mcp" for event in events))
        self.assertTrue(all(event["severity"] == "WARNING" for event in events))
        # Avoid a dangling handler stream in later tests or interpreter shutdown.
        logging.getLogger().handlers.clear()


if __name__ == "__main__":
    unittest.main()
