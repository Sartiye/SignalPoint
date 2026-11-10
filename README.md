# SignalPoint

A small Windows mod manager for Mount & Blade II: Bannerlord. Browse mods without
an account, install them directly into the game's Modules folder, and keep them
updated. Trusted publishers upload readable module folders through the desktop
app. Successful validated uploads publish directly.

The desktop source is [public on GitHub](https://github.com/Sartiye/SignalPoint)
under the [MIT license](LICENSE). The API and administration panel
live in the separate private MBCloudflare backend. This client contains no Google
Cloud credentials and does not import the backend package.

The project is ready for early public testing. Steam launch integration and the
installer have been tested locally; Epic and Xbox PC discovery are implemented,
but launching with those actual store installations still needs player testing.
Report reproducible client problems through [GitHub Issues](https://github.com/Sartiye/SignalPoint/issues).
Send credentials, account issues, and security reports privately to
[andach.elmaskaya@gmail.com](mailto:andach.elmaskaya@gmail.com).

The MIT license covers the SignalPoint client and its source. Bundled dependencies
retain their own licenses, included in the distribution's `licenses` folder.
Marketplace mods retain their publishers' licenses. Use of the hosted marketplace
is covered by the [Terms of Service](https://mb-cloudflare.ey.r.appspot.com/signalpoint/terms/)
and [Privacy Policy](https://mb-cloudflare.ey.r.appspot.com/signalpoint/privacy/).

## Download for Windows

Get the latest installer from the [SignalPoint website](https://mb-cloudflare.ey.r.appspot.com/signalpoint/)
or its [download link](https://mb-cloudflare.ey.r.appspot.com/signalpoint/download).
Installers and checksums are also published in [GitHub Releases](https://github.com/Sartiye/SignalPoint/releases).
The website lists current mods and their publisher artwork. Players need no account.
Downloads go directly from object storage; a [SHA-256 checksum](https://mb-cloudflare.ey.r.appspot.com/signalpoint/download/checksum)
is available for the current installer. Run a newer installer over an existing
installation to upgrade while keeping your settings and installed mods.

Version 0.1.1 supports Cloudflare R2 transfers. Install it before using the migrated
service; earlier installers do not recognize R2 links. Publisher checkout checks
the 5 GiB limit for each file, including TPAC files, before hashing or uploading.
A mod may contain multiple files whose combined size exceeds 5 GiB. Completed
file uploads are reused after interruption; an interrupted R2 file upload restarts
that file. Player downloads can resume with HTTP Range requests.

## Run from source

Install Python 3.13, then run from this checkout:

```powershell
.\scripts\setup.ps1
.\SignalPoint.cmd
```

The app connects automatically to the hosted SignalPoint service at
`https://mb-cloudflare.ey.r.appspot.com/signalpoint`. Players do not need to
configure a backend address. Settings includes the Bannerlord installation and
links to the public Privacy Policy and Terms of Service.

For development against a local backend, a specific installation, or separate local data:

```powershell
.\.venv\Scripts\python -m signalpoint.desktop --api-url http://127.0.0.1:8000/signalpoint --game 'D:\Games\Bannerlord' --data-dir .scratch\client
```

Steam, Epic, and Xbox PC / Microsoft Store discovery are implemented. Settings
also accepts a manually selected game folder. The launcher is found inside the
installation's bin directories; its working directory is used to resolve
`../../Modules`. Protected Xbox installations must expose a writable game folder;
the app does not change WindowsApps permissions. Actual Epic and Xbox runtime
behavior still needs testing with those store installations.

## Local publisher setup

These commands run **in the private MBCloudflare backend checkout**, using its
existing Python environment:

```powershell
.\.venv\Scripts\python -m pip install -c requirements.lock -e '.[dev]'
.\.venv\Scripts\python -m mb_cloudflare.signalpoint.cli create-admin admin
.\.venv\Scripts\python -m mb_cloudflare
```

The bootstrap command asks for a password and works only before the first account
exists. Open `http://127.0.0.1:8000/signalpoint/admin`, sign in, and create a listing
assigned to the publisher account. The administrator account can also publish.
New publisher accounts, membership/expiry, storage allowance, and suspension are
managed here. There is no per-upload approval step or payment processing.

In SignalPoint, choose **Publisher**, sign in, select the assigned listing, and
open **Mod page** to edit its description, choose or remove a thumbnail, then
**Save mod page**. These changes do not require a module upload or version change.
Thumbnail previews use a square crop. Players see the image filling the card's
left side, with details and actions beside it; image fetching is asynchronous
and cached locally.

In **Mod files**, drag in the module folder or use **Open Folder**. Each folder must have
SubModule.xml at its root. Add required companion module folders explicitly.
Choose **Scan & Review**, inspect versions, dependencies, destinations, and changed
files, then **Confirm Upload**. Archives are unsupported.

After publishing, Browse is public; the publisher login is not needed to install.
Initial testing should use a separate test installation. Existing manual or
Workshop modules are listed as unmanaged and are not silently overwritten.

## Updates and installation

- Bannerlord is assumed current. Mod versions come from SubModule.xml.
- Same-version content replacements still update through immutable build/file
  identities. Only whole changed files download; large changed TPACs download in
  full. Downloads can pause, resume, and retry, with progress beneath the mod card.
- Checks run at startup, on resume, every minute while open, and before launching.
  Updates stage while the game runs and apply after the game/launcher closes.
- Start Launcher completes updates. For Steam installs it opens the game's
  `steam://run/261550` link, letting Steam handle client startup and its configured
  launch options. It uses the Steam library's registered Bannerlord installation.
  For Epic and Xbox PC it checks/opens the current user's store client, waits up
  to 45 seconds, then opens the official launcher. Sign-in and client updates
  remain in the store app. Failed mod checks offer an explicit existing-install
  launch option using the same store-specific launch flow.
- File hashes and module metadata are verified. Folder swaps are journaled and
  recoverable. Repair can replace modified managed files; uninstall preserves
  unrelated and locally modified files.
- Native modules, including FastMode and NavalDLC, are protected.
- Research reports have a public API/read surface keyed to the exact build.
  The independent ILSpy/AI pipeline is a later project. Until it exists, the app
  correctly shows that no research report is available.

This is current mod distribution, not a release archive. Obsolete file content
can be removed after a grace period while hashes, metadata, and reports remain.

## Validate and build

```powershell
.\.venv\Scripts\python -m ruff check src tests
.\.venv\Scripts\python -m ruff format --check src tests
.\.venv\Scripts\python -m pytest -q
.\scripts\build.ps1
```

The Windows development build is `dist\SignalPoint\SignalPoint.exe`; distribute
the **entire SignalPoint folder**, including `_internal`. It bundles Python and
the desktop dependencies. `icon.ico` is also included for shortcuts and
installers; the executable embeds the same icon. `build-info.json` records the source commit and whether
the checkout was dirty, and the build command prints the executable's SHA-256.
Development builds are unsigned. A license and signed public-release process
remain to be selected.

### Windows installer

The installer packages the existing `dist\SignalPoint` folder; users do not
need a separate Python or Qt installation. On the build machine, install
[Inno Setup 7](https://jrsoftware.org/isdl.php), then run:

```powershell
.\scripts\build_installer.ps1
# Rebuild the app and installer together:
.\scripts\build_installer.ps1 -BuildApp
```

Output is `dist\installer\SignalPoint-Setup-0.1.0-x64.exe` (using the version in
`pyproject.toml`), with SHA-256 and a build manifest alongside it. A custom compiler
path can be supplied with `-Iscc`, or the `SIGNALPOINT_ISCC` environment variable.
The build checks the bundled EXE's checksum and runs its startup check before
compiling the installer.

Setup installs for the current user under `%LOCALAPPDATA%\Programs\SignalPoint`,
creates a Start menu shortcut, and offers an optional desktop shortcut. It uses
the SignalPoint icon. Rerunning a new installer upgrades the same installed app.
The uninstaller removes installed program files; local settings/cache in
`%LOCALAPPDATA%\SignalPoint` and game Modules are preserved.

To exercise installation, a same-version upgrade and uninstallation in a
disposable workspace directory:

```powershell
.\.venv\Scripts\python scripts\test_installer.py dist\installer\SignalPoint-Setup-0.1.0-x64.exe
```

This check refuses to run if SignalPoint already has an installed-program entry;
use a separate Windows account or Sandbox in that case. It creates no shortcuts
and does not open the game. Pass `--upgrade-installer PATH` to verify a newer
setup over an older one; the second setup must rediscover the original directory
without a `/DIR` override, keep one program entry, and preserve user files.
Same-version reinstall and a test-only 0.1.0 → 0.1.1 upgrade were verified.
The initial installer is unsigned; signing the app
and setup executable can be added for public releases.

The UI uses PySide6 Essentials, like MBServerLauncher. Packaging uses an isolated
Python/Windows PATH and avoids shadowing Windows ICU DLLs, following the existing
Albion and MBServerLauncher build patterns. Launched game processes also get a
clean DLL search environment so the app's bundled libraries do not leak into
Bannerlord.

The backend's integration tests exercise the real HTTP client and manager,
including direct trusted publishing, anonymous downloads, same-version updates,
permission enforcement, and content cleanup. The test suite also runs against
the isolated Firestore emulator. See its private `docs/signalpoint.md` for cloud
configuration and operator details.

[Design notes](DESIGN.md) document the product decisions and later research work.
