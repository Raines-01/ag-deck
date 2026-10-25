# Contributing

Thanks for taking a look. This is a small project; the bar is "does not break
the dashboard on someone else's machine".

## Development setup

```bash
python3 -m venv .venv
. .venv/bin/activate
pip install -e .
python -m agdeck serve --demo      # UI with sample data, no Antigravity needed
```

Run the checks before sending a patch:

```bash
python -m unittest discover -s tests -v
python -m compileall -q agdeck
node --check agdeck/static/app.js
```

If you touched the Android shell:

```bash
./android/build.sh
adb install -r android/dist/AG-Deck.apk
```

## Guidelines

- **Zero runtime dependencies.** The server must run on a stock Python 3.9+
  install. New third-party packages need a very good reason.
- **Three platforms.** Discovery and address detection must keep working on
  Linux, macOS and Windows. Add a unit test for pure logic; keep OS calls in
  `platform_util.py`.
- **Small payloads.** The UI is used over Tailscale relays; do not send large
  strings in list/detail responses. Heavy content goes through
  `/api/sessions/<id>/steps/<index>`.
- **No personal data.** Screenshots come from `--demo`. Do not commit tokens,
  logs, keystores or real project names.
- **Front end stays dependency-free.** Plain DOM, no build step.

## Commit messages

Short imperative subject, then a body explaining *why* when it is not obvious.
`Fix mobile detail not scrolling to the latest` is good; `update app.js` is not.

## Reporting a bug

`agdeck doctor` output (it contains no secrets) plus the OS, Python version and
Antigravity version is the fastest way to get a fix.
