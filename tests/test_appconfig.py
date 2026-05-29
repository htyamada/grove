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

    def test_init_strips_whitespace_from_image_root_tables(self) -> None:
        ac = mock.Mock()
        ac.get.side_effect = lambda _project, _layer, key: {
            'image_root': [
                {'path': '  /srv/images  ', 'name': '  Images  '},
            ],
            'cache_dir': '',
        }[key]

        appconfig.init(ac)

        self.assertEqual(appconfig.image_roots, ['/srv/images'])
        self.assertEqual(appconfig.image_root_names, ['Images'])

    def test_init_strips_whitespace_from_image_root_strings(self) -> None:
        ac = mock.Mock()
        ac.get.side_effect = lambda _project, _layer, key: {
            'image_root': ['  /srv/images  '],
            'cache_dir': '',
        }[key]

        appconfig.init(ac)

        self.assertEqual(appconfig.image_roots, ['/srv/images'])
        self.assertEqual(appconfig.image_root_names, ['images'])


if __name__ == '__main__':
    unittest.main()
