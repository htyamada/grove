from django.test import override_settings

from .. import config
from .base import DocumentViewTestCase


class LimitTests(DocumentViewTestCase):
    def test_returns_builtin_default(self):
        self.assertEqual(
            config.limit('DOCUMENT_VIEWER_MAX_PDF_PREVIEW_PAGES'),
            config._LIMIT_DEFAULTS['DOCUMENT_VIEWER_MAX_PDF_PREVIEW_PAGES'],
        )

    def test_host_setting_overrides_default(self):
        with override_settings(DOCUMENT_VIEWER_MAX_PDF_PREVIEW_PAGES=7):
            self.assertEqual(config.limit('DOCUMENT_VIEWER_MAX_PDF_PREVIEW_PAGES'), 7)

    def test_host_setting_without_builtin_default_is_returned_not_raised(self):
        # Regression: the guard used to evaluate _LIMIT_DEFAULTS[name]
        # eagerly as getattr's default, so a name a host had configured but
        # that had no built-in default raised KeyError instead of returning
        # the configured value.
        with override_settings(DOCUMENT_VIEWER_FUTURE_LIMIT=42):
            self.assertEqual(config.limit('DOCUMENT_VIEWER_FUTURE_LIMIT'), 42)

    def test_unknown_and_unconfigured_name_raises(self):
        with self.assertRaises(KeyError):
            config.limit('DOCUMENT_VIEWER_NO_SUCH_LIMIT')
