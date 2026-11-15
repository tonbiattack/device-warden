import unittest

from device_warden.apps import apk_label, package_details, user_packages, version_status


class PackageParsingTests(unittest.TestCase):
    def test_user_packages(self):
        output = ('package:com.example.app\npackage:com.example.other\n'
                  '# package:system.one\n\npackage:com.example.app\n')
        self.assertEqual(user_packages(output), ['com.example.app', 'com.example.other', 'com.example.app'])
        self.assertEqual(user_packages(''), [])

    def test_package_details(self):
        output = """Packages:
  Package [com.example.app] (4f1c):
    userId=10123
    versionCode=42 minSdk=24 targetSdk=34
    versionName=1.4.2
    firstInstallTime=2026-01-02 03:04:05
    lastUpdateTime=2026-02-03 04:05:06
    splits=[base]
"""
        details = package_details(output)
        self.assertEqual(details, {
            'version_name': '1.4.2', 'version_code': 42,
            'first_install_time': '2026-01-02 03:04:05',
            'last_update_time': '2026-02-03 04:05:06'})

    def test_missing_fields_do_not_fail(self):
        details = package_details('Package [com.example.app]\n versionCode=7\n')
        self.assertEqual(details['version_code'], 7)
        self.assertIsNone(details['version_name'])
        self.assertIsNone(details['last_update_time'])

    def test_lowercase_version_name_is_accepted(self):
        self.assertEqual(package_details('versionname=2.0')['version_name'], '2.0')

    def test_apk_label(self):
        output = "package: name='com.example.app' versionCode='42' versionName='1.4.2'\napplication-label:'Example'\n"
        self.assertEqual(apk_label(output), 'Example')
        self.assertIsNone(apk_label('nothing here'))

    def test_version_status_uses_version_code(self):
        self.assertEqual(version_status(10, 10), 'latest')
        self.assertEqual(version_status(9, 10), 'old')
        self.assertEqual(version_status(11, 10), 'newer')
        self.assertEqual(version_status(None, 10), 'unknown')
        self.assertEqual(version_status(10, None), 'unknown')
        # 1.10 is newer than 1.9 semantically even though it sorts earlier as text.
        self.assertEqual(version_status(110, 19), 'newer')


if __name__ == '__main__':
    unittest.main()
