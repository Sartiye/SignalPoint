# SignalPoint design notes

## R2 storage preparation (10 November 2026)

R2 will replace only file storage. The API/website stays on App Engine and account,
catalog and build metadata stays in Firestore. Large transfers remain direct to
object storage. Desktop source now recognizes signed R2 S3 endpoints (default/EU)
without sending publisher credentials there. Version 0.1.1 is the R2-compatible
client; existing public v0.1.0 rejects R2 URLs and must be upgraded.
Uploads are limited to 5 GiB per file, including TPAC, checked before hashing and
again by the server. Total mod size may exceed 5 GiB across several files.
Interrupted R2 file uploads retry that file; completed files are reused.
The backend has private R2 storage, scoped signed transfers, bounded cleanup and
cloud-to-cloud migration verification. A real isolated R2 upload/install/update
test passed. Full operator details live in the private backend's
`docs/signalpoint-r2.md`.

Working notes from the design discussion. Last updated: 2026-11-10.
This document records the product direction. Local implementation was authorized
on 2026-11-10. Live cloud deployment remains a separate step.

## Product purpose

SignalPoint is a simple Python desktop mod manager and searchable marketplace
for Mount & Blade II: Bannerlord. Trusted publishers distribute mods they own
or are authorized to distribute. Public players browse, download, install, and
manage mods without an account.

The product prioritizes getting maintained mods installed and keeping them
current. It is not a historical release archive.

Guiding motto from the owner:

> If a mod is really old version dependent then the right place is ModDB.

Assume Bannerlord itself is up to date. SignalPoint tracks mod versions and
distributes their current published builds. Mods maintained only for old
Bannerlord versions belong on ModDB; an external link can direct players there
without hosting historical content.

## Public client and private backend

The SignalPoint repository will be public so players can inspect the desktop
client's behavior. The website/backend source remains in the private
Sartiye.MBCloudflare repository. This is the intended repository boundary; live
repository visibility has not been verified as part of these notes.

Public client code may contain public API addresses and protocol definitions.
Keep cloud credentials, signing keys, administrator credentials, private research
access, operational data, and local environment files out of the repository and
distributed application. Account-specific session credentials belong in local
runtime storage, not committed source or shared application configuration.

Backend authorization must remain effective against modified or independently
written clients. Enforce publisher approval, mod ownership, membership, quotas,
researcher scope, and publication rights on the server. Keeping server source
private does not itself establish that the deployed website is secure.

For inspectable application releases, plan to provide build instructions and
identify the source commit used to produce each distributed binary, along with
artifact checksums. A software license and binary-signing approach remain to be
chosen. Public source supports transparency; it is not a guarantee of safety for
the client, backend, or uploaded mods.

## Interface direction

- Simple, practical, and authentic, inspired by Steam and ModDB.
- Two primary areas: Installed and Browse.
- A large, prominent **Start Launcher** button at the top right. This starts the
  Bannerlord launcher, rather than directly entering a game session.
- Installed presents local mods, their declared mod versions, and their
  installation/update state, with update, repair, and uninstall actions.
- Browse provides search, compact mod listings, and straightforward mod details.
- Download/update progress is visible directly beneath the relevant mod card or
  row in Installed and Browse, as well as on its detail view.
- Publisher access and administration should not crowd the ordinary player flow.

Visual treatment: compact lists, readable system typography, flat dark navy
surfaces, thin separators, violet primary actions, and blue download progress.
Use the owner's provided SignalPoint tower artwork for the app/window/taskbar
icon, compact header badge, and admin-panel identity. The fuller artwork is also
preserved as a brand asset. Keep large content areas plain and readable.

The previous Browse freshness-feed suggestion is optional. The owner's fetching
requirement is specifically about getting the latest files for installed mods,
not importing Steam Workshop items or keeping search results fresh.

### Download interface

A good download interface is a core requirement. Keep progress attached to the
mod being installed rather than requiring navigation to a separate screen.

- Show a clear progress bar, percentage, transferred/total download size, speed,
  and an estimated time remaining when enough data is available.
- For updates, calculate transfer progress against the changed files being
  downloaded, not the full installed mod size.
- Distinguish queued, downloading, paused, verifying, installing, waiting for the
  game/launcher to close, ready, and failed states with readable text.
- A completed download is not a completed installation. Keep verification and
  installation visible; show Ready only after the installation commits.
- Provide pause/resume during transfer and retry after failure. Cancellation
  must respect transaction boundaries and preserve the installed mod.
- Keep one underlying operation state synchronized across cards, details, and
  launcher preparation. Changing tabs must not interrupt a download.
- Keep the interface responsive while transfers, hashing, and file operations run.

A compact overall download summary is proposed for the bottom of the window,
showing aggregate speed and queued/active counts. It may expand to show the queue;
per-mod progress remains visible on the cards. The main interface stays focused
on Installed and Browse.

## Python client and installation

Python is required by the owner. PySide6 is the proposed desktop UI toolkit.
Distribute a packaged Windows application that includes its runtime and
dependencies; players should not need Python, Google CLI, or cloud credentials.

Platform scope: Windows, with Steam, Epic Games, and Xbox PC / Microsoft Store
installation discovery plus a manual installation-folder picker. Detect the
available launcher under the installation's bin directories rather than assuming
Steam's exact binary layout. Xbox console installation is outside a Windows
desktop app's scope. Protected or inaccessible installations must produce an
actionable message without changing WindowsApps permissions. Store discovery is
distinct from validating that a particular mod includes suitable platform DLLs.

Local engine-source verification: ModuleHelper's physical module prefix is
BasePath.Name + "Modules/" and the normal Windows BasePath is "../../".
Resolve Modules two levels above the launcher's directory and start the launcher
with that directory as its working directory. Validate Native/SubModule.xml
there before accepting an installation. Source references in the local decompiled
archive: TaleWorlds.Library/TaleWorlds.Library/BasePath.cs and
TaleWorlds.ModuleManager/TaleWorlds.ModuleManager/ModuleHelper.cs.

Install mod content under the selected Bannerlord installation's Modules folder.
Validate module structure and SubModule.xml. A catalog mod may contain multiple
modules, so catalog IDs, module IDs, and destination folders are separate facts.

The native modules BirthAndDeath, CustomBattle, FastMode, Multiplayer, Native,
NavalDLC, SandBox, SandBoxCore, and StoryMode are reserved. Exclude them from
marketplace uploads and managed replacement/removal. They remain available for
dependency discovery; do not show them as ordinary third-party mods to uninstall.

Recommended installation behavior:

- Inspect dependencies and conflicting module identities before changing files.
- Download into staging, verify content, and then install it.
- Keep a recoverable operation journal and a temporary backup during replacement.
- Avoid changing installed mod files while Bannerlord or its launcher is running.
  Updates can download into staging and wait until both are closed.
- Record managed files and installed build identities in local SQLite.
- Recognize existing manual and Workshop installations without silently adopting
  or replacing them.
- Restrict writes to approved module destinations and reject escaping paths or
  links. Root-folder replacements and publisher-provided install scripts are
  outside the proposed initial package format.
- Preserve unrelated files and identify local modifications before replacement
  or removal.

The official launcher can initially own activation and load order. Automatic mod
updates are the default: while SignalPoint is running, check for and install the
latest published mod builds. Bannerlord itself is assumed to be current; there is
no game-version target selection or separate update branch per Bannerlord version.

Start Launcher checks for updates, finishes required installations with visible
progress, ensures the matching store client is open, and opens the official
launcher once the managed installation is
consistent and current according to that check. Coordinate this operation with
the update queue so files are not replaced during launcher startup. This replaces
the earlier suggestion that players manually apply each pending update.

Before launching, use Native's steam.target/epic.target markers to identify the
required store; Xbox layouts and detected-install metadata cover Xbox PC or
manually selected installations. Steam installations use the game's registered
`steam://run/261550` link after updates, allowing Steam to start its own client and
launch the registered library copy with the player's Steam launch options. Do
not separately open Steam's library or start the launcher executable afterward.
For Epic/Xbox, reuse the current user's running client or activate it through its
registered Windows mechanism, showing progress and waiting up to 45 seconds.
Failure/timeout stops launch. Store authentication and client updates remain
user-controlled in the store UI.
GOG/standalone installs do not require an optional store client. Keep external
process DLL environments separate from the frozen desktop application's runtime.

If the update check or download fails, show the failure and offer retry. An
explicit option to launch the existing installation can be offered when it is
consistent; do not present an unsuccessful check as "up to date". Resolve any
partially applied installation before launch. Local file conflicts need visible
resolution rather than silently discarding player changes.

## Versions, replacements, and pending installs

Track the mods' own versions from their SubModule.xml Version fields. Bannerlord
itself is assumed to be up to date. Do not introduce Bannerlord-version targets,
compatibility selectors, or historical game-version branches in the upload or
player workflow. This supersedes the earlier interpretation of game-target-based
versioning.

Keep one current published build per catalog mod. Read and display declared module
versions during scanning and review; for a multi-module upload show each module's
version. Preserve publisher-authored metadata rather than rewriting SubModule.xml
or inventing another public release numbering scheme.

A replacement may keep exactly the same SubModule.xml Version. File hashes and
the internal build identity still distinguish it and trigger automatic updates.

An override means publishing replacement content even when the declared mod
version is unchanged. It still creates a new immutable build identity and queues
pending client updates. The server's published current-build pointer is the update
authority; a changed version string alone is not required to detect an update.

Replacement workflow:

1. The publisher reviews the scanned folders and confirms their upload.
2. Transfer the files and complete technical integrity/layout validation.
3. Publish it directly as the mod's current build. Trusted publishers do not need
   administrator review or approval for individual uploads.
4. Clients discover the replacement and automatically queue the update.
5. Download and verify changed files, then apply them when the game and launcher
   are closed. Pending is a temporary queue/waiting state, not a request for the
   player to approve every published replacement.

The existing published build remains current until its replacement finishes
validation and publication.
Internal immutable build IDs and manifest hashes identify exact content and
bind inspection reports to it. They are bookkeeping, not additional public
version numbers. Changing content invalidates its association with an earlier
inspection result.

Anonymous clients determine pending updates locally by comparing installed build
identities with current published metadata. Player accounts and a server-side
registry of individual installations are not required.

## Reliable fetching of installed-mod updates

This is a core product requirement, motivated by the owner's experience with
Steam Workshop fetching. It does not imply Steam Workshop scraping or mirroring.

Recommended behavior:

- Check current build metadata on app startup, explicit refresh, app resume, and
  before starting the launcher. Recheck periodically while SignalPoint is open.
- Automatically fetch and apply published mod replacements when safe.
  Opening SignalPoint should normally be sufficient to bring installed mods
  current; players should not need to update each mod manually.
- Batch current-build checks for installed mod identities.
- Keep the mutable current-build lookup fresh. Immutable file objects can be
  cached for longer, keyed by verified content identity.
- Distinguish "up to date" from "could not check" and show the last successful
  check time. An offline or failed check must not imply freshness.
- Pin an installation operation to one complete manifest. A publication during a
  download must not mix files from two builds.
- Resume interrupted transfers where practical, retry transient failures, and
  renew expired download authorization.
- Detect withdrawn or superseded builds. If an old download can no longer finish,
  offer a fresh plan for the current build and reuse verified matching content.
- Keep the installed-mod view usable offline.

Exact periodic-check cadence remains a design choice. The initial scope is
automatic updating while SignalPoint is running; an always-running Windows
service is not required. No live event service is required for the initial design.

## Changed-file updates

The owner selected whole changed files rather than binary or chunk patches.

Each build has a complete manifest containing relative paths, sizes, hashes, and
download content identities. Compare it with the managed local installation:

- Reuse unchanged files.
- Download new or changed files in full.
- Remove obsolete managed files after a successful replacement.
- Verify installed output and preserve recovery information until commit.

A small change to a large TPAC requires downloading that entire TPAC. There are
no version-to-version patch chains. Optional independent file compression must
not turn updates into mandatory whole-mod downloads.

Published manifests can reference unchanged content objects already stored for
that mod, avoiding repeated uploads and storage of identical files.

## Content retention: current distribution, not an archive

Superseded releases do not promise continued download or rollback access.
Large files and obsolete content may be deleted. Keep lightweight history:

- Internal build and manifest identities.
- File paths, sizes, and hashes.
- Publication, replacement, and withdrawal timestamps.
- Publisher identity, public research reports, and audit records.
- Small descriptive metadata or change notes useful for traceability.

Deletion must protect content referenced by any current published build, active
draft/research job, or in-progress transfer within a bounded grace period. An
unchanged object shared with the replacement must not be deleted merely because
the older build was retired.

After that grace period, remove unreferenced content. Historical decompilation
workspaces and other bulky research artifacts can follow a separate short
retention policy. Reports can remain without retaining full decompiled trees.

The grace period and local backup lifetime remain to be chosen. Temporary install
recovery is distinct from a permanent release archive. A retained hash cannot
reconstruct deleted content, so downloadable rollback disappears after cleanup.

Before implementation, review Cloud Storage soft delete, object versioning, and
lifecycle configuration so intended deletion and actual retained storage agree.

## Backend integration

Use the existing private shared backend repository, `Sartiye.MBCloudflare`.

Local inspection confirmed Python 3.13, FastAPI/Uvicorn, Firestore and Cloud
Storage dependencies, application URL mounts, and App Engine configuration.
This inspection did not verify a live deployment or modify cloud resources.

Proposed SignalPoint namespace: `/signalpoint` on the existing backend origin,
with the web panel at `/signalpoint/admin`. Backend code belongs in the shared
backend repository; the desktop app belongs in this repository.

Use scoped Firestore collections and dedicated SignalPoint storage resources.
Cloud files transfer directly between clients and Cloud Storage; the backend
handles catalog metadata, authorization, publication, and inspection integration.
Use revocable account/session access and temporary transfer authorization rather
than embedding cloud credentials in clients.

A separate App Engine service/identity can be considered if runtime isolation is
needed. The existing URL namespace alone does not provide resource isolation.

## Publishers and the web panel

Initially, publisher access is trust-based and administrator-controlled. Later,
membership may be paid externally and recorded manually. Payment processing,
checkout, and automatic billing are outside scope. Public downloads remain
account-free.

Keep publisher approval separate from membership status. Proposed account fields
include active/suspended status, approval, assigned mods, trusted/complimentary/
externally-paid basis, optional expiry, and storage allowance.

Proposed panel areas:

- Publishers: accounts, approval, assigned mods, membership, limits, suspension.
- Mods: listings, ownership/collaborators, visibility.
- Builds: upload status, current publication, replacement, withdrawal, reports.
- Activity: permission changes, uploads, publication, storage, and audit events.

All admitted publishers are trusted initially. Administrators control publisher
access, assigned mods, membership, and withdrawal, but do not approve individual
uploads. Completed valid uploads publish directly. Later unsafe-publisher
categories or restrictions can be added if needed; they are not a current
publication gate. Membership expiry stops new uploading/publishing but does not
itself withdraw published content. Suspension and withdrawal remain distinct.

A signed-in Publisher mode in the Python client is the proposed upload interface:
drag and drop a readable module folder or choose **Open Folder**, review detected
metadata and content, enter details, and upload changed files. The web panel
handles administration, publication visibility, and public research records.

### Select, scan, and review upload

The owner requires a checkout-style review page after choosing the upload source.
Use a clear **Review Upload** step before any content is uploaded. This is a
folder-content confirmation flow; membership payment processing remains outside
scope.

Uploads accept ordinary readable module folders only. Provide a folder drop area
and **Open Folder**. ZIPs and other archive inputs are unsupported; there is no
archive extraction step.

The selected folder must itself be a module root with SubModule.xml directly
inside it, matching the filename case-insensitively. Read that metadata and scan
the module's actual files. Show the resulting installation destination directly
under Modules. Ask the publisher to select the module folder if they choose an
outer wrapper, a whole Modules directory, or an unrelated folder.

If a listing includes several modules, they can be added as explicit module
folders to the same review. Do not silently discover and bundle arbitrary nested
modules. Duplicate module IDs or conflicting destination folders must be resolved
before upload.

Read metadata without executing included code. Show scan/hash progress and keep
the interface responsive. Confine scanning to the selected module roots and
reject links that escape them. If SubModule.xml is missing or invalid, explain
the problem and let the publisher choose a different folder.

The review page should show:

- The assigned marketplace listing being created or updated.
- Detected module names, IDs, folder names, and declared module versions.
- Module dependencies and whether each is included or externally required.
- Declared mod versions read from SubModule.xml, without a Bannerlord target
  selector. The product assumes the game is up to date.
- Proposed destinations under Modules, including all modules in the package.
- File count, installed size, upload size, and included DLLs.
- New/changed/removed files compared with the current published build, and whether
  the upload replaces that build, including a same-mod-version override.
- Missing/malformed metadata, conflicts, and other blocking validation errors.
- Editable listing details and short change notes.

Primary action: **Confirm Upload**. Provide Back/Cancel to revise the selection.
The final manifest must match the reviewed content; changed source files require
a rescan or a stable local snapshot. After transfer and technical validation,
upload completion publishes directly. An unfinished upload remains private and
does not replace the currently published build.

The backend independently validates authorization and the submitted package
manifest. Client-side scanning is a useful interface, not a permission boundary.

## First acceptance test: Warlords

The owner's first real end-to-end test is uploading the Warlords mod and then
installing it through SignalPoint. Use the publisher's prepared readable module
folder(s); do not assume marketplace names, module IDs, or folder layout from the
project name.

Acceptance flow:

1. Sign in with a trusted publisher account and drag in the Warlords module
   folder, or select it using Open Folder. Add any other intended module folders
   explicitly.
2. Read each root SubModule.xml and present the review page with the correct
   module roots, mod versions, dependencies, destinations, and content sizes.
   Confirm that archive inputs and unrelated/wrapper folders are rejected with
   clear guidance.
3. Confirm and upload; the validated build publishes without an admin approval
   step. The later independent research pipeline is not a prerequisite for this
   first test, and its absence must not be represented as a completed inspection.
4. Browse and install the published mod as a player without signing in.
5. Detect the selected Bannerlord installation, show per-mod download and install
   progress, and place the files directly under the intended Modules folders.
6. Verify installed file hashes and that the official launcher recognizes the
   modules. Use the prominent Start Launcher button.
7. Confirm that an existing unmanaged installation is recognized as a conflict
   rather than silently overwritten, and that the publisher's source is intact.

Follow-up acceptance: publish a replacement, including one retaining the same mod
version, automatically fetch only changed files, apply it when safe, then verify
repair/uninstall without removing unrelated content. Launch and recognition
checks are distinct from validating the mod's complete in-game behavior.

## Later project: independent researcher client

DLL/mod inspection is a separate future project running on an independent
computer or server, with a small authenticated researcher client integration.
It is not part of the ordinary player's desktop workflow.

Intended pipeline:

1. Claim an inspection job for an exact uploaded/published manifest.
2. Download and install/reconstruct the mod into an isolated research workspace.
3. Use ILSpy or a similar tool to extract managed code.
4. Have AI analyze the extracted code and relevant mod content.
5. Submit a report and PASS/FAIL verdict.
6. Make the report and verdict publicly available to players for that exact mod
   version/build. Reports inform players and are not a publication prerequisite.

Initial "install" means constructing the package in the research workspace;
executing the mod or launching Bannerlord is a possible additional stage, not an
implicit part of static inspection.

Store exact hashes, researcher identity, timestamps, pipeline/model/rule versions,
coverage, errors, findings, and the verdict. Incomplete or interrupted work must
not become PASS. Native, unreadable, or otherwise uninspected content must be
represented in coverage rather than silently treated as checked.

Researchers use scoped, revocable credentials to fetch assigned content and
submit results, without publication permission. Run inspection separately from
the web process with restricted access and resource limits.

Reports are player-facing and tied to exact hashes as well as the declared mod
version. A same-version replacement needs its own report association. Preserve
historical reports even after obsolete binaries are deleted. Show a clear
"No report yet" state until research results exist. PASS/FAIL describes the
pipeline assessment, not a guarantee of harmless code or an automatic publishing
gate. Admins retain the ability to withdraw a mod based on findings.

Prepare only the integration contract in SignalPoint: jobs, researcher access,
report submission, build associations, and public player-facing presentation. The external
pipeline is to be designed and implemented later.

## Initial implementation and local verification

The first implementation includes the Python/PySide6 Essentials desktop app,
folder scanning and uploader review, trusted direct publishing, the private
MBCloudflare API/admin panel, changed-file updates, repair/uninstall, per-mod
progress, launcher startup, and scoped public research-report retrieval.
The external research pipeline itself remains later work.

The local acceptance run uploaded and anonymously installed the actual Warlords
and Armory source module folders together: 650 files, 3,699,150,183 bytes. All
installed output was verified against the manifest. This used an isolated test
game tree; the real installed game was not changed or launched. Recognition was
checked through module discovery, not a live in-game session.

Client/core tests cover folder-only scanning, native-module protection, store
discovery fixtures, resumable downloads, update/repair/uninstall, interrupted
transaction recovery, launcher directory selection, and desktop publishing and
progress states. Backend integration tests exercise the actual client against
SQLite and the isolated Firestore emulator.

Packaging borrows the existing Albion and MBServerLauncher build safeguards:
clean build PATH, Windows ICU handling, and a clean DLL environment for launched
game processes. Builds include owner-provided brand assets and an application
icon, third-party notices, source/dependency metadata, and an executable checksum.
The builder runs an automatic offscreen startup check before declaring success.

Production uses the existing MBCloudflare default App Engine service at
`https://mb-cloudflare.ey.r.appspot.com/signalpoint`, Firestore's scoped
SignalPoint collections, and a private `mb-cloudflare-signalpoint` bucket in
europe-west3. Downloads use a dedicated keyless signing identity with bucket
read access; the shared runtime can sign through that identity. Files transfer
directly through Cloud Storage, without passing through App Engine.

The desktop defaults to this hosted address and ignores legacy saved development
addresses. There is no backend URL setting for players; `--api-url` remains a
development-only command-line override. Cached catalogs track their source, and
existing install receipts retain their original source protection.

Public Privacy and Terms pages describe actual account, logging, storage,
publishing, and cleanup behavior. Operator/contact details match the other
MBCloudflare applications. They are linked from Settings, publishing, and the
website. Actual Epic/Xbox game execution remains to be validated on those
installations.

## Remaining decisions

Windows installer packaging uses Inno Setup 7 around the existing PyInstaller
folder. Default installation is per user at LocalAppData/Programs/SignalPoint,
with Start menu and optional desktop shortcuts using icon.ico. A stable
Sartiye.SignalPoint AppId makes later installers update the same installation.
Installer version comes from pyproject.toml. The initial development installer
is unsigned; code signing remains a release decision.

The installer bundles the Python/Qt runtime and third-party notices. Uninstall
removes its tracked application files, while preserving local settings/cache and
the game's installed modules. Verification installs to an isolated workspace,
checks every payload hash and startup, exercises an upgrade, then uninstalls.
The verifier refuses to replace an existing registered SignalPoint installation.

Publisher descriptions and thumbnails are edited in the desktop's Mod page tab.
The administrator creates/assigns a listing using only ID, name and publisher;
no description field is required in the panel. Metadata changes keep the current
module build/version unchanged. Marketplace cards place a full-height cover image
on the left, description/version information in the middle, and actions on the
right, with transfer progress below. Images load asynchronously with bounded
concurrency and SHA-256-verified local caching.

Small thumbnail uploads (4 MiB maximum) are decoded and normalized by the API
into metadata-free 384-square PNGs. Stored images and player image downloads use
the private Cloud Storage bucket and signed URLs. Actual module transfers remain
direct to/from Cloud Storage. Superseded image objects become eligible for
operator cleanup after 24 hours, and deleting a mod/account also purges thumbnails.

- Periodic update-check cadence and presentation of offline/error launch options.
- Download grace periods, failed-draft expiry, research artifact cleanup, and
  temporary local recovery retention.
- Precise dependency-conflict behavior and real-installation validation of Epic
  and Xbox PC layouts beyond simulated discovery fixtures.

## Design references

- Local backend host: `Sartiye.MBCloudflare/src/mb_cloudflare/app.py`.
- Local deployment configuration: `Sartiye.MBCloudflare/app.yaml` and
  `app.knightflare.yaml`.
- [TaleWorlds module layout](https://moddocs.bannerlord.com/asset-management/quickguide_create_a_mod/).
- [Cloud Storage objects](https://docs.cloud.google.com/storage/docs/objects).
- [Cloud Storage signed URLs](https://docs.cloud.google.com/storage/docs/access-control/signed-urls).
- [ILSpy](https://github.com/icsharpcode/ILSpy) and
  [ILSpy command-line tool](https://github.com/icsharpcode/ILSpy/blob/master/ICSharpCode.ILSpyCmd/README.md).
