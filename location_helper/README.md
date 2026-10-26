# Fixed location helper

This helper is GPL-3.0-or-later. It is a separate per-device executable that
communicates with the GUI over JSON lines on stdin/stdout. Its JSON protocol accepts
`set` (UDID, latitude, longitude) and `clear` (UDID). It also exposes
`--prepare-wireless UDID` to enable and read back trusted Wi-Fi pairing over USB,
and `--check-wireless UDID` to check the Network developer-location channel
without setting or clearing a position. Both verify the selected physical UDID.
No trajectories or social
network actions are implemented. This license does not grant rights to unrelated
third-party components; their notices are supplied in `licenses/location`.

Rebuild on Apple Silicon macOS using Python 3.14:

```
python3 -m venv location-venv
location-venv/bin/pip install -r requirements.lock
location-venv/bin/python build_location.py
```

In the repository, use `tools/build_location.py`; the release's corresponding-source
archive includes a standalone copy. Put the built `ig-location-helper` directory
in the application's `location-backend` resource directory. The source archive
includes exact dependency sdists, hashes, requirements, and build scripts. Install
from those sdists with pip's `--find-links dependencies` if needed. PyInstaller
itself has its upstream bootloader distribution exception.

Supports the iOS 16 legacy developer location service and iOS 17.4+ userspace
CoreDevice tunnels. iOS 17.0–17.3 is deliberately unsupported in this first version.
Newer iOS versions require real-device validation. No admin elevation, remoted
suspension, or shared native-tunnel fallback is used. USB trust, Developer Mode,
and a prepared developer disk image/Xcode are prerequisites. Wi-Fi requires an
already paired, network-visible device. A generic WDA URL is insufficient.

`applied` means the developer service accepted the coordinate, not GPS readback.
`clear_sent` means the restore request was sent (DVT has no response). The process
keeps the location service alive until clear, stdin EOF, or connection loss. EOF
attempts clear. Crashes or lost devices cannot guarantee restoration; reconnect
and clear, or restart the phone, and confirm in Maps.
