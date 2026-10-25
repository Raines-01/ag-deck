# Changelog

All notable changes to this project are documented here.
The format follows [Keep a Changelog](https://keepachangelog.com/en/1.1.0/)
and this project adheres to [Semantic Versioning](https://semver.org/).

## [1.0.0] — 2026-10-25

First public release, extracted from a personal tool and hardened for other
machines.

### Added

- Live dashboard for Antigravity Cascade sessions: overview, progress,
  per-turn timeline, step details on demand.
- Send instructions, stop a running turn, start a new session with a workspace
  picker; model selection.
- Streaming updates (SSE) with gzip and delta frames; mobile-aware payload
  compaction and lazy loading of command output / diffs / previews.
- Phone access with a 6-digit pairing flow, persistent token outside the
  repository, per-IP rate limiting and CSRF protection.
- Cross-platform discovery: Linux (`/proc`, `ss`), macOS (`ps`, `lsof`),
  Windows (PowerShell CIM, `netstat`).
- CLI: `serve`, `list`, `show`, `watch`, `send`, `new`, `models`, `doctor`,
  `net`, `config`.
- Onboarding screen when Antigravity is not detected, plus `/api/doctor`.
- `--demo` backend with sample data for screenshots and UI work.
- Config file in the per-user config directory; friendly port-conflict
  handling; `--version`, `--verbose`.
- Android WebView shell built without Gradle (`android/build.sh`).
- 31 unit tests and a GitHub Actions matrix (Linux/macOS/Windows).

### Fixed

- Sessions that finished their turn but still had a background task were shown
  as idle. The status now keeps "working" once the summary has been `RUNNING`
  for more than 8 seconds and labels the task ("后台任务运行中：...").
- File URIs are normalised without the host-specific `url2pathname`, so a
  POSIX workspace path renders identically on Windows (found by CI).
- New sessions could inherit a retired model (`gemini-4-argon-disabled`) and
  fail with `NOT_FOUND`; disabled models are now filtered out.
- Cancelling an already-finished session returned 500; it is now a no-op with
  a clear message.
- `100dvh` had no fallback for older Android WebViews.
