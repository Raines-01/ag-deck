"""AG Deck — a local web console for Antigravity Cascade sessions.

AG Deck lets you use Antigravity running on your computer from your phone or
another machine — over your LAN or a Tailscale tailnet. It talks to the
`language_server` process that ships with Antigravity, reads its Cascade
trajectories over the local Connect(JSON) RPC endpoint and renders them as a
live dashboard for desktop and mobile browsers.

This project is unofficial and is not affiliated with Google.
"""

__version__ = "1.0.0"
APP_NAME = "AG Deck"
USER_AGENT = f"AGDeck/{__version__}"
