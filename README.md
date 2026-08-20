# Conjure Crosshair

Conjure Crosshair is a Windows system-tray utility that displays a customizable, click-through crosshair above other applications. It supports multiple monitors, saved placement, custom PNG images, color selection, and global keyboard or mouse-button toggles.

## Features

- Always-on-top, click-through overlay
- Exact centering based on each monitor's available resolution and origin
- Built-in crosshairs plus custom PNG import and removal
- Color selection and saved position
- Keyboard, numpad, and extra mouse-button hotkeys
- Default toggle hotkey: `F8`
- Single-instance behavior: a second launch exits and turns on the existing crosshair
- Starts with the crosshair on every fresh launch
- Optional Windows startup shortcut through the installer

## Use The Application

The application runs in the system tray. Its menu provides:

- `Select Crosshair` - choose a built-in or imported crosshair
- `Select Color` - choose the overlay color
- `Select Monitor` - choose which monitor receives the crosshair
- `Set Position` - edit the saved X/Y position
- `Toggle: On/Off` - show or hide the crosshair
- `Set Hotkey: <Hotkey>` - capture a keyboard, numpad, or extra mouse button
- `Update` - check for a newer GitHub Release and launch its installer
- `Exit` - close the application

The hotkey dialog listens for a physical key or mouse button, shows the captured input, and provides `Reassign` and `Save` actions. Mouse movement is ignored.

## Run From Source

Windows and Python 3.11 or newer are required. Global input hooks may require running with the same permissions as the application being controlled.

```powershell
py -3 -m venv .venv
.venv\Scripts\python.exe -m pip install PyQt6==6.11.0 pystray==0.19.5 Pillow==12.3.0 keyboard==0.13.5 mouse==0.7.1
.venv\Scripts\python.exe main.py
```

The source-mode configuration and log are written beside the Python files. Do not commit personal configuration or log files.

## Build A Release

Run `package.bat` from a Windows Command Prompt or PowerShell:

```powershell
.\package.bat
```

The script creates `.venv` when needed, installs the pinned dependencies, builds the application for packaging, and creates:

```text
release\Conjure Crosshair.exe
```

This is the installer and the only file intended for distribution. Python 3.11 or newer, the Python Launcher (`py`), and Inno Setup 6 are required to create it. The installed application targets the architecture of the Python installation used to build it; use 64-bit Python for current 64-bit Windows systems.

## Publish A GitHub Release

Releases use semantic-version tags. Push the `main` branch first, then create and push a tag:

```powershell
git add .
git commit -m "Describe the change"
git push origin main
git tag -a v1.0.0 -m "Release v1.0.0"
git push origin v1.0.0
```

Pushing a tag matching `v*.*.*` starts `.github/workflows/release.yml` on a Windows runner. The workflow builds the application internally, wraps it in the Inno Setup installer using the tag version, and publishes one downloadable file to a GitHub Release: `Conjure Crosshair.exe`. Use a new tag for each release, such as `v1.1.0` or `v1.1.1`; tags are immutable release identifiers and should not be reused.

The workflow also stamps the release tag into `version.py`, so the installed application compares its own version with the latest published release correctly.

Normal branch pushes do not publish a release. The workflow uses GitHub's temporary Actions token, so no personal access token or repository secret is required. Repository Actions must have permission to write releases.

## Install Or Distribute

For normal distribution, share the GitHub Release asset `Conjure Crosshair.exe`. This is the installer. It creates Start Menu and desktop shortcuts and offers an optional Windows startup task.

The PyInstaller application binary is an internal build input and is not published as a separate release download.

### Updating An Installation

To update an installed copy, download and run the installer from the newest GitHub Release. Inno Setup recognizes the existing installation through the stable application ID, reuses the existing install directory, and replaces the application files with the newer version. It closes the running application during the update and launches the new version afterward.

The installer can be cancelled before making changes. User settings, imported crosshairs, and logs remain in `%LOCALAPPDATA%\Conjure Crosshair` and are not removed during an update. The published EXE is the installer, so the `Update` menu action downloads and launches it for managed updates.

## User Data And Logs

Frozen or installed builds store settings, logs, and user-added crosshairs in:

```text
%LOCALAPPDATA%\Conjure Crosshair
```

The startup log is `conjure_crosshair.log`. It records detected monitor resolutions, monitor origins, calculated centers, and crosshair placement coordinates.

Source-mode runs store the same files in the project directory because the source configuration directory is the directory containing `main.py`.

Deleting the data directory resets the application to its defaults and removes imported crosshairs and logs.

## Project Layout

- `main.py` - application startup, tray menu, dialogs, hotkeys, and instance signaling
- `crosshair_window.py` - transparent overlay window and monitor positioning
- `config_manager.py` - configuration defaults and persistence
- `assets/` - built-in crosshair images
- `icon.ico` - application, dialog, executable, and installer icon
- `build.spec` - PyInstaller one-file configuration
- `installer.iss` - Inno Setup configuration
- `package.bat` - repeatable Windows release build
- `version.py` - current application version used by the updater
- `.github/workflows/release.yml` - tag-triggered GitHub Release automation

Generated folders such as `.venv`, `build`, `dist`, and `__pycache__` are local artifacts and should not be distributed with the source. User-specific `config.json` and `conjure_crosshair.log` files should also remain local.

## Troubleshooting

- **The hotkey does not work:** choose another key or mouse button, and run Conjure Crosshair with the permissions required by the target application.
- **The overlay is not visible:** use `Toggle: On/Off`, verify the selected monitor, and check `conjure_crosshair.log` for detected monitor geometry.
- **A second launch does not show the crosshair:** fully close the existing process and relaunch the current executable. Single-instance signaling is supported on Windows.
- **The installer is not created:** install Inno Setup 6, then run `package.bat` again.
- **Packaging fails:** confirm that `py` is available, use 64-bit Python for 64-bit Windows, and ensure dependencies can be downloaded.
