# Desktop UI

The app is a Chinese-language macOS utility for configuring and running USB iPhones.
A user should be able to identify the affected device, understand its state, and find the next action without reading a technical log.

Use macOS native controls and PingFang SC. Keep a restrained blue accent for primary actions and selection. Shared presentation lives in `src/ui_theme.py`.

- Main workspace: compact fleet summary, device table, selected-device detail, persistent feedback.
- Table states stay short. Full reasons wrap in the detail area. Color always accompanies a text label.
- Device controls and batch controls are separate. No-selection, offline, stopping, scanning, queued-start, partial search failure, and invalid-signature states have explicit feedback.
- Scan-time start requests wait for detection. Stop cancels waiting requests.
- Device forms scroll within their tab. Save remains visible in the footer. Running sessions lock editing and explain why.
- On narrower windows, the table omits the system column; system information remains in the selected-device details.
- Keyboard: Command+Return starts, Escape stops, Command+S saves device settings, Control+Tab changes settings tabs. Forms support Page Up/Down when focused.
- Closing a device window hides it. Closing the workspace stops and releases all device sessions.

Palette: ink `#182329`, secondary text `#54636E`, blue `#185ADB`, green `#176948`, amber `#825B12`, red `#A52D38`. Native control backgrounds follow the macOS theme. Status text remains understandable without color.

- Setup distinguishes USB pairing, installed controller inventory, signature validity, and a verified control session. Missing Settings developer entries alone never imply failure.
- Install failure feedback identifies the current attempt and next action. Verification of an existing signed build has its own persistent button; it does not request a new signature.
- Reconnection limits explicitly describe a five-minute window. Empty transport errors still display an actionable reason.

- Transport settings are per device and separate from browsing preferences. USB remains the default; local Wi-Fi and externally managed WDA explain their distinct startup and signing requirements.
- Remote endpoint fields appear only for remote mode. Verification runs off the UI thread; old settings remain until verification and binding succeed.
- Network fingerprint checks prevent accidental endpoint changes; they are not authentication. Unverified wireless support is visibly identified as experimental.

- Wireless onboarding leads with LAN discovery and import/export. Device details are collapsed by default and auto-filled after selection. Export uses a strict connection-field whitelist; import verifies the original fingerprint before saving, reports partial failures, and preserves existing browsing settings. A fingerprint-derived identifier is explicitly distinguished from hardware UDID.
