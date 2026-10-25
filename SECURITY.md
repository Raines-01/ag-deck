# Security policy

AG Deck can send instructions to AI agents running on your machine, so a
compromised instance is equivalent to handing someone a shell. Please treat it
accordingly.

## Design

- **Loopback is trusted.** Requests from `127.0.0.1` / `::1` need no token, so
  the desktop experience has no login. If you expose the port through a proxy,
  make sure it preserves the client address rather than rewriting it to
  loopback.
- **Everything else needs a token.** Cookie, `X-AG-Token` header or `?t=` link.
  The token is generated once, stored in the per-user config directory with
  `0600`, and never written into the repository.
- **Pairing codes** are 6 digits, regenerated on every start, and rate limited
  to 6 failures per IP per 10 minutes.
- **CSRF**: writes require the `X-AGConsole: 1` custom header plus a
  `SameSite=Lax` cookie, which a cross-site form cannot produce.
- **Transport is plain HTTP.** Over Tailscale that is inside WireGuard; over an
  untrusted Wi-Fi, put TLS in front (`tailscale serve --bg 8848`) or use
  `--local-only`.

## Reporting

Please open a private security advisory on GitHub (or email the maintainer)
instead of a public issue. Include the version, platform and a minimal
reproduction. You can expect an initial response within a few days.

## Scope

In scope: authentication/authorization bypasses, token leakage, CSRF, path
traversal in the static handler, command injection.

Out of scope: the fact that a local user with access to your account can read
the token; denial of service by someone who already controls your machine;
vulnerabilities in Antigravity itself.
