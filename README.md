# SignalPoint

A small Windows mod manager for Mount & Blade II: Bannerlord. Browse mods without
an account, install them directly into the game's Modules folder, and keep them
updated. Trusted publishers upload readable module folders through the desktop
app. Successful validated uploads publish directly.

The desktop source is public for inspection. The API and administration panel
live in the separate private MBCloudflare backend. This client contains no Google
Cloud credentials and does not import the backend package.

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
drag in the module folder or use **Open Folder**. Each selected folder must have
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
- Start Launcher completes updates, makes sure the installation's Steam, Epic,
  or Xbox PC client is running for the current user, then opens the official
  launcher. A closed client opens automatically; an existing client is reused.
  Startup waits up to 45 seconds and shows progress. A missing client or startup
  timeout prevents the game launcher from starting. Sign-in and client updates
  remain in the store app. Failed mod checks offer an explicit existing-install
  launch option, which still performs the store-client check.
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
