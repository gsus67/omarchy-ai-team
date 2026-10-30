import importlib.util
from pathlib import Path
import unittest

spec = importlib.util.spec_from_file_location("session_usage", Path(__file__).resolve().parents[1] / "tools/session_usage.py")
usage = importlib.util.module_from_spec(spec)
spec.loader.exec_module(usage)


class SessionUsageTests(unittest.TestCase):
    def response(self, used, minutes=300):
        return {"rateLimits": {"primary": {"usedPercent": used, "windowDurationMins": minutes}}}

    def test_handoff_starts_at_ten_percent_remaining(self):
        self.assertFalse(usage.five_hour_window(self.response(89.9))["handoffRequired"])
        value = usage.five_hour_window(self.response(90))
        self.assertTrue(value["handoffRequired"])
        self.assertEqual(value["remainingPercent"], 10)
        self.assertTrue(usage.five_hour_window(self.response(100))["handoffRequired"])

    def test_weekly_or_short_windows_cannot_trigger_five_hour_handoff(self):
        for minutes in (15, 10080, None):
            with self.assertRaises(ValueError):
                usage.five_hour_window(self.response(99, minutes))

    def test_native_codex_bucket_has_priority_over_unrelated_buckets(self):
        response = self.response(99)
        response["rateLimitsByLimitId"] = {"codex": {"secondary": {"usedPercent": 53, "windowDurationMins": 300}},
                                          "other": {"primary": {"usedPercent": 100, "windowDurationMins": 300}}}
        result = usage.five_hour_window(response)
        self.assertEqual(result["remainingPercent"], 47)
        self.assertFalse(result["handoffRequired"])

    def test_unknown_or_invalid_percent_is_not_treated_as_available_quota(self):
        for used in (None, "90", True, -1, 101, float("nan")):
            with self.assertRaises(ValueError):
                usage.five_hour_window(self.response(used))


if __name__ == "__main__":
    unittest.main()
