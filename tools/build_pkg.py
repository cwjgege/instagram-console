"""Package a signed macOS app at its fixed Applications path."""
import plistlib
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
APP_NAME = 'Instagram 控制台.app'
BUNDLE_ID = 'local.igconsole.desktop'


def build_pkg(app):
    app = Path(app).resolve()
    info = plistlib.loads((app / 'Contents/Info.plist').read_bytes())
    if app.name != APP_NAME or info.get('CFBundleIdentifier') != BUNDLE_ID:
        raise ValueError('Expected the canonical Instagram Console application bundle.')
    version = info['CFBundleShortVersionString']
    output = app.parent / f'Instagram-Console-{version}-AppleSilicon.pkg'
    subprocess.run(['/usr/bin/codesign', '--verify', '--deep', '--strict', app], check=True)
    with tempfile.TemporaryDirectory() as temporary:
        root = Path(temporary) / 'payload'
        root.mkdir()
        subprocess.run(['/usr/bin/ditto', app, root / APP_NAME], check=True)
        components = Path(temporary) / 'components.plist'
        components.write_bytes(plistlib.dumps([{
            'RootRelativeBundlePath': APP_NAME,
            'BundleIsRelocatable': False,
            'BundleIsVersionChecked': True,
            'BundleHasStrictIdentifier': True,
            'BundleOverwriteAction': 'upgrade',
        }]))
        subprocess.run([
            '/usr/bin/pkgbuild', '--root', root, '--component-plist', components,
            '--scripts', ROOT / 'tools/installer', '--identifier', BUNDLE_ID + '.installer',
            '--version', version, '--install-location', '/Applications', output,
        ], check=True)
    return output


if __name__ == '__main__':
    print(build_pkg(sys.argv[1] if len(sys.argv) > 1 else ROOT / 'dist' / APP_NAME))
