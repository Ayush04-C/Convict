# feat(auth): add demo login and JWT-protected API access

Base: `main`

Head: `feat/ayush-authentication`

## Problem and behavior

The foundation had authentication placeholders returning 501. This change implements
the authentication stage so a configured demo user can log in, receive a one-hour
access token, and access protected API routes.

An unauthenticated run request now returns **401**. A valid bearer token reaches
the existing run placeholder and returns **501**, making the boundary between
completed authentication and unfinished analysis explicit.

## Changes

- Implement `POST /auth/login` and protected `GET /auth/me`.
- Verify Argon2id password hashes and issue HS256 JWTs containing subject, issue
  time, expiry, issuer and audience.
- Require and validate token claims, signature, algorithm, configured user,
  timestamps and a maximum one-hour lifetime.
- Apply the shared authentication dependency to run creation, status, events,
  and report routes.
- Return generic 401 responses with a bearer challenge, sanitized validation
  errors, request IDs and non-cacheable authentication responses.
- Require valid auth configuration at API startup without requiring provider keys.
- Add a private setup command that initializes missing `.env` credentials through
  hidden password prompts and an atomic owner-only write. Preserve existing
  authentication values, provider settings and comments.
- Document setup, manual verification, credential rotation and stage boundaries
  in `docs/authentication.md`; update README, Makefile and environment guidance.

## Validation

- **111 tests passed locally**, including the original foundation checks.
- Ruff, Python compilation and the offline demo passed.
- A temporary real HTTP server passed health, unauthorized access, incorrect
  login, successful login, authenticated identity and authenticated stub checks.
- Manual Postman verification confirmed login **200** and `/auth/me` **200** with
  the configured username.
- Tests cover malformed requests, missing/invalid claims, expired and tampered
  tokens, wrong signatures/algorithms, all protected routes, safe errors, startup
  validation, setup preservation, interruption and failed atomic writes.
- Tests use isolated credentials; no provider credits or developer `.env` required.

GitHub Actions must pass on the proposed branch before merging. Local test results
do not imply a successful hosted CI run.

## Scope and limits

This is single-account authentication for the local hackathon application.
Registration, refresh tokens, login rate limiting and server-side token revocation
are not implemented. Changing the password does not invalidate already issued
tokens; rotating the signing secret and restarting invalidates them.

Ledger-backed run ownership, persistent orchestration, live extraction/providers,
and functional run/report endpoints belong to subsequent stages. No Daksh
feature files are changed. Local secret files and the unrelated working-copy edit
to `docs/foundation-pr.md` are excluded from this PR.
