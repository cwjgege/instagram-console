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
