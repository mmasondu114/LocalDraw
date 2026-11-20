What this change does:

Local Draw now opens in its own Windows desktop window. It embeds the existing studio UI, starts the Python service without a console or browser, and keeps saved projects and model paths in the existing checkout. The change also redesigns the home and work panes and moves 25 old entry points and notes into `archive/`.

Looks good. Ship.

Checked: the changed C# entry point and build script; Python startup, process lifetime and instance lock; home-page handlers and their callers; frontend layout and dialog rules; all moved launchers and their relative targets. Assumed load: one local user and one studio backend. This is a review of this change and its connected code, not an audit of every existing model or drawing workflow.

Validation:

- Windows x64 GUI executable header verified; no console subsystem.
- Final EXE creates a real WebView2 window, executes JavaScript and exits successfully in its self-test.
- Real frontend passes isolated WebView2 checks for home entry, 375px layout, prompt entry, retaining the draft when returning from home, the queue dialog, and large text at 1024px. JavaScript errors: zero. Five screenshots inspected, including desktop and narrow layouts.
- Six startup tests pass: service reuse, waiting for readiness, process failure, startup timeout, and two real-process console regressions. Both the EXE worker and backend report a zero console window handle while their log output remains available.
- All 25 archive destinations and hashes match the manifest. All `%~dp0` targets in moved scripts resolve to existing paths. No models or project data were moved or deleted.
- Reviewed and fixed during implementation: SDK extraction now happens after acquiring the desktop instance lock; canceled navigation no longer replaces the current UI with a connection error; process output is drained before its log is closed; old error messages now point to the EXE.

Not checked: complete EXE-to-backend integration and live model generation. This execution sandbox rejects local socket connections with WinError 10013. The isolated EXE service-reuse integration test therefore failed its readiness check; this is recorded as unverified, not counted as passing. No GPU workload was started.

Follow-up correction: the original detached console Python could still create a terminal through the venv redirector. Both launch stages now use pythonw, and the service uses CREATE_NO_WINDOW. The original desktop also lacked DPI opt-in; the final executable now verifies PerMonitorV2 at DPI 120. The real frontend renders to 3440×1440 pixels at devicePixelRatio 1.25; the expanded offline UI suite passes. The main window defaults to maximized, and repeated launch only restores a minimized window.

The 3440×1440 screenshot exposed an overlapping old/new CSS breakpoint: four panels were placed in three columns. The ultrawide breakpoint now has four columns, and acceptance checks that the preview stays on the same row and inside the viewport.
