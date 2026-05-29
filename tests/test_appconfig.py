import sys
import unittest
from pathlib import Path
from unittest import mock


ROOT = Path(__file__).resolve().parents[1]
LIB = ROOT / 'lib'
if str(LIB) not in sys.path:
    sys.path.insert(0, str(LIB))

from imhandler import appconfig


class ImhandlerAppConfigTests(unittest.TestCase):
    def test_init_variant_loads_imhandler_conf(self) -> None:
        fake_appconfig = object()
        with mock.patch.object(appconfig, 'AppConfig', return_value=fake_appconfig) as appconfig_cls:
            with mock.patch.object(appconfig, 'init') as init_mock:
                appconfig.init_variant('hty7')

        expected_conf = ROOT / 'etc' / 'imhandler.conf'
        appconfig_cls.assert_called_once_with(str(expected_conf), 'hty7')
        init_mock.assert_called_once_with(fake_appconfig)


if __name__ == '__main__':
    unittest.main()
