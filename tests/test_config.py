"""Regression tests for the fail-closed configuration (removed hardcoded credentials)."""
import importlib
import os
import unittest

import config


class TestRequiredVariables(unittest.TestCase):
    REQUIRED = ("TOKEN", "MONGO_URI", "MONGODB_URI", "REDIS_URL", "OWNER_ID")
    BASELINE = {
        "TOKEN": "123456789:TESTTOKENforunitundertest000000",
        "MONGO_URI": "mongodb://127.0.0.1:27017",
        "REDIS_URL": "redis://localhost:6379/0",
        "OWNER_ID": "1",
    }

    def setUp(self):
        self._saved = {k: os.environ.get(k) for k in self.REQUIRED}

    def tearDown(self):
        # Always hand the module a loadable environment back, otherwise a failing
        # test leaves config in a half-imported state for the next one.
        for key, value in self._saved.items():
            if value is None:
                os.environ.pop(key, None)
            else:
                os.environ[key] = value
        for key, value in self.BASELINE.items():
            os.environ.setdefault(key, value)
        importlib.reload(config)

    def _reload_without(self, *keys):
        for key in keys:
            os.environ.pop(key, None)
        return importlib.reload(config)

    def test_token_is_required(self):
        with self.assertRaises(RuntimeError) as ctx:
            self._reload_without("TOKEN")
        self.assertIn("TOKEN", str(ctx.exception))

    def test_redis_url_is_required(self):
        with self.assertRaises(RuntimeError) as ctx:
            self._reload_without("REDIS_URL")
        self.assertIn("REDIS_URL", str(ctx.exception))

    def test_owner_id_is_required(self):
        with self.assertRaises(RuntimeError) as ctx:
            self._reload_without("OWNER_ID")
        self.assertIn("OWNER_ID", str(ctx.exception))

    def test_mongo_uri_is_required(self):
        with self.assertRaises(RuntimeError) as ctx:
            self._reload_without("MONGO_URI", "MONGODB_URI")
        self.assertIn("MONGO_URI", str(ctx.exception))

    def test_mongodb_uri_is_accepted_as_alias(self):
        os.environ.pop("MONGO_URI", None)
        os.environ["MONGODB_URI"] = "mongodb://from-alias"
        try:
            self.assertEqual(importlib.reload(config).MONGO_URI, "mongodb://from-alias")
        finally:
            os.environ["MONGO_URI"] = self.BASELINE["MONGO_URI"]
            os.environ.pop("MONGODB_URI", None)

    def test_blank_value_counts_as_missing(self):
        os.environ["TOKEN"] = "   "
        with self.assertRaises(RuntimeError):
            importlib.reload(config)


class TestOptionalVariables(unittest.TestCase):
    def test_group_ids_default_to_zero_which_disables_them(self):
        for key in ("ADMIN_GROUP_ID", "OFFICIAL_GROUP_ID", "WORD_ADDITION_CHANNEL_ID"):
            os.environ.pop(key, None)
        reloaded = importlib.reload(config)
        self.assertEqual(reloaded.ADMIN_GROUP_ID, 0)
        self.assertEqual(reloaded.OFFICIAL_GROUP_ID, 0)
        self.assertEqual(reloaded.WORD_ADDITION_CHANNEL_ID, 0)

    def test_blank_group_id_does_not_raise(self):
        os.environ["ADMIN_GROUP_ID"] = ""
        try:
            reloaded = importlib.reload(config)
            self.assertEqual(reloaded.ADMIN_GROUP_ID, 0)
        finally:
            importlib.reload(config)

    def test_group_ids_parse_from_env(self):
        os.environ["ADMIN_GROUP_ID"] = "-1001234"
        try:
            self.assertEqual(importlib.reload(config).ADMIN_GROUP_ID, -1001234)
        finally:
            importlib.reload(config)

    def test_vip_list_accepts_comma_separated(self):
        os.environ["VIP"] = "1,2,3"
        try:
            self.assertEqual(importlib.reload(config).VIP, [1, 2, 3])
        finally:
            importlib.reload(config)

    def test_vip_list_accepts_json(self):
        os.environ["VIP_GROUP"] = "[4, 5]"
        try:
            self.assertEqual(importlib.reload(config).VIP_GROUP, [4, 5])
        finally:
            importlib.reload(config)

    def test_vip_list_defaults_to_empty(self):
        for key in ("VIP", "VIP_GROUP"):
            os.environ.pop(key, None)
        reloaded = importlib.reload(config)
        self.assertEqual(reloaded.VIP, [])
        self.assertEqual(reloaded.VIP_GROUP, [])

    def test_log_level_defaults_to_info(self):
        os.environ.pop("LOG_LEVEL", None)
        self.assertEqual(importlib.reload(config).LOG_LEVEL, "INFO")

    def test_log_level_is_uppercased(self):
        os.environ["LOG_LEVEL"] = "debug"
        try:
            self.assertEqual(importlib.reload(config).LOG_LEVEL, "DEBUG")
        finally:
            importlib.reload(config)


class TestNoHardcodedCredentials(unittest.TestCase):
    """The committed secrets must not come back as defaults."""

    FORBIDDEN = ("8533283359", "8515282600", "sumiloo", "cluster0.nb0umdm", "gQAAAAAAAbZ")

    def test_source_contains_no_credential_literals(self):
        import pathlib

        source = pathlib.Path(config.__file__).read_text()
        for fragment in self.FORBIDDEN:
            self.assertNotIn(fragment, source, f"config.py must not contain {fragment!r}")


if __name__ == "__main__":
    unittest.main()
