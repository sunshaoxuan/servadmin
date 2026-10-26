# Sliding calendar-month browser authentication: local candidate, not released

The user's latest requirement supersedes the previous fixed 30-day absolute /
seven-day refresh-idle proposal. Neither old limit is part of this candidate.

## Scope and identity

Task: bounded, revocable authentication per private browser to reduce repeated logins.
Execution hostname was checked as OHR0067; workspace is C:\workspace\server-admin-app.
Base commit: c0692a8. Initial tracked working tree was clean. Existing untracked
`.test-output/` and `sites/` were retained. No AGENTS.md was found in the repository
or its workspace/root ancestors; no .agents or .codex directory exists here.

Repository identity matches the requested application: README.md:107 explicitly
names https://ccnode.briconbric.com/server-desk/; deploy/nginx-server-desk.conf maps
`/server-desk/` to `127.0.0.1:8090/`; README names `/opt/server-desk` and the
server-desk systemd service. These are repository mapping evidence, not a fresh
inspection of the running production checkout.

IMPORTANT: scripts/server_desk_git_sync.sh fetches origin/main, backs up the DB,
stops the service, fast-forwards, installs dependencies, tests, and restarts.
README says its timer runs every five minutes. No push, commit, deployment,
production environment change, real device enrollment, or real long-lived token
generation was performed during this task.

## Cause and feasibility

Existing SessionCodec signs a stateless cookie for 12 hours. Login already sets
Max-Age=43200, HttpOnly, SameSite=Lax; Secure depends on OPS_COOKIE_SECURE. Thus
the existing cookie already persists past an ordinary tab close. Session loss
can follow expiration, changed signing secret, deleted cookies, a different
origin/path, or a browser context that does not share storage. Production cookie
metadata and signing-secret stability were not inspected, so a unique production
root cause has NOT been established.

The available in-app browser inventory is associated with a codexSessionId.
This suggests thread-specific routing, but does not prove cookie partitioning.
Controlled local observations used only fake credentials:

- Two tabs in the current in-app context shared the authenticated browser state.
- A separate Edge profile showed the login form for the same localhost origin.
- An independent TestClient cookie jar could not restore authentication.

No cross-thread cookie extraction, copying, profile modification, or new Codex
thread was performed. A browser-only persistent cookie cannot safely identify
the same physical machine across arbitrary isolated contexts. This candidate
solves expiry in a persistent shared profile; it does not claim to solve actual
Codex cross-thread isolation. Use one trusted persistent Edge profile if the
workflow permits it. Automatic identity across isolated profiles requires a
separately designed trusted client integration (for example an OS-bound identity
with explicit enrollment), beyond this repository's minimal scope.

References: [Electron sessions](https://electronjs.org/docs/latest/api/session),
[OWASP sessions](https://cheatsheetseries.owasp.org/cheatsheets/Session_Management_Cheat_Sheet.html),
[OWASP authentication](https://cheatsheetseries.owasp.org/cheatsheets/Authentication_Cheat_Sheet.html).

## Implemented local candidate

- Feature gate OPS_DEVICE_AUTH_ENABLED defaults to 0. Disabled `/api/me` retains
  the existing response format; ordinary password logins retain 12-hour cookies.
- Explicit opt-in after password verification creates an independently labeled
  browser credential. Maximum 10 currently active enrollments per user.
- Each successful remembered-browser password login, valid token restoration,
  and authenticated API access extends the device deadline to one calendar month
  from the authentication time, in UTC, preserving hour/minute/second. A missing
  day is clamped to month-end (Jan 31 -> Feb 28/29). No absolute lifetime ceiling
  and no independent seven-day idle rule remain. Continuous valid activity can
  keep the device authenticated indefinitely. Exactly at expiry the token fails;
  an expired token is never revived by token-based access.
- Every authenticated API access commits its exact deadline and last-used time
  without time-based throttling. SQLite serializes validation/renewal with
  rotation/revocation; max(existing deadline, new month) prevents clock rollback
  or request ordering from shortening validity. Static files, the unauthenticated
  index/health endpoint, rejected credentials, and replay do not renew. A valid
  API authentication can renew even if business logic subsequently returns an
  error; denied step-up checks and incorrect reauthentication passwords do not
  extend it. Background authenticated dashboard polling counts as activity.
- A short session still lasts 12 hours. Expiry is transparently restored by the
  browser with its valid saved credential, including during normal API use,
  without password entry. Same-page parallel API failures share one restore.
  Every authenticated request checks device revocation, the sliding deadline,
  current refresh secret and current password hash/salt version. A successful
  password login with the current saved credential renews/rotates the same active
  device rather than creating duplicate enrollments. Explicit new enrollment
  after password verification remains possible; it never revives a revoked row.
- A selector plus 32 cryptographically random bytes forms the refresh credential;
  only SHA-256 of its random secret is stored. Short-session secrets are also
  stored only as SHA-256. No bearer secret is returned in JSON or audit detail.
- Both managed cookie types are HttpOnly, SameSite=Strict, host-only; Secure
  follows OPS_COOKIE_SECURE. The persistent cookie has absolute Expires and
  Max-Age matching the committed sliding deadline. Its name includes an opaque
  token-generation hash prefix and exact deadline, so delayed older responses
  cannot overwrite either a rotated secret or a later expiry. On the next
  request, current valid secrets take precedence and obsolete cookies presented
  by the browser are deleted. No token is read by JavaScript or copied across
  profiles. OPS_DEVICE_COOKIE_PATH defaults to `/`; creation and deletion use
  the same path. Production proposal scopes it to `/server-desk/`.
- Refresh is explicit POST and atomically rotates inside SQLite BEGIN IMMEDIATE.
  One simultaneous refresh wins; a duplicate previous token within 30 seconds
  returns 409 without revoking or clearing cookies. The page may need a reload.
  Reuse of the immediately previous token after 30 seconds revokes the device
  family. Older/unknown tokens fail closed, without a family-revocation claim.
  Lost refresh responses can require a password login; no recoverable plaintext
  refresh-token copy is stored to mask transport loss.
- Remembered logins use managed sessions, never mint a legacy session. No fallback
  to a legacy cookie occurs when a presented managed short session is invalid.
  Device revocation cannot retroactively revoke unrelated preexisting stateless
  legacy cookies; legacy compatibility is retained deliberately.
- Restored sessions can read inventory/dashboard/audit, check TCP connectivity,
  save traffic readings, sync configured provider usage, and edit ordinary asset
  metadata (name, region, notes, tags, starred state) without repeated passwords.
- High-sensitivity operations retain a password check within 15 minutes:
  credential/connection-secret display, device revocation, asset creation/deletion,
  SSH target/authentication/key/options or credential changes, provider login or
  connector changes, monitoring/retirement security configuration changes, and
  inspect/quality-check (which remotely execute fixed SSH collection scripts).
  Metadata-only edits on the existing PUT endpoint compare submitted fields
  against stored values to avoid an unnecessary step-up. Logout needs no step-up.
  Reauthentication rotates the short session. Old legacy password sessions retain
  existing behavior; no existing protection was relaxed for them.
- Device list and per-user revocation are available in Settings. Logout revokes
  the matching browser credential even after its short session expires, then
  clears cookies; logout transport errors remain visible. Displayed secrets in
  JS caches are cleared when returning to login. Revocation affects subsequent
  authentication checks; it cannot undo already-started actions or delivered data.
- When enabled, unsafe requests with Sec-Fetch-Site cross-site or same-site are
  rejected (including sibling subdomain origins). No CORS policy is relaxed.
  API responses use Cache-Control: no-store.
- Migration 9 only adds remembered_devices/device_sessions tables and indexes.
  No existing schema/data is removed and no dependency is added.

Bearer-cookie theft or a compromised browser/OS can expose the ordinary read/write
permissions above; continued use can keep renewing access until revocation or a
password-version change. An unattended tab's polling also keeps it alive. IP and
UA are not used as identity. This is a remembered
browser, not hardware attestation. Existing password login has no repository-level
rate limiting; this change does not claim to provide one. Before production,
review existing proxy login/reauthentication throttling and secret/log handling.

## Verification and evidence

The existing Windows Store Python cannot launch under the default sandbox.
Reviewed execution of the existing `.venv\Scripts\python.exe` succeeded; no
new environment, container, image, dependency installation, or production access
was needed. Initial pytest collection failed on a preexisting inaccessible
default temp directory; all subsequent runs used new task-specific directories
under `.test-output/` and retained other tasks' output.

Current sliding candidate checks:

- `.venv\Scripts\python.exe -m pytest -q --tb=short --basetemp=.test-output/device-auth-sliding-final-20261026`
  => **95 cases executed: 94 passed, one test assertion failed, 49.75s**.
  The failed assertion selected an intentional old-cookie deletion header rather
  than the new renewal header. Production code did not change. After correcting
  only that selector, the affected test was rerun with
  `tests/test_device_auth.py::test_every_authenticated_access_renews_server_and_browser_not_static`
  and `--basetemp=.test-output/device-auth-sliding-cookie-assertion-20261026`
  => **1 passed in 6.03s**. Together these are passing evidence for all 95 cases;
  do not describe this as one full command reporting 95 passed.
- `node --test tests/test_frontend.cjs` => **6 passed, 193ms**. The new concurrent
  restore test initially raced the harness's initial /me and was fixed by awaiting
  that initialization; the production implementation did not need a repair.
- `node --check app/static/app.js` and `git diff --check` => success.
- Earlier fixed-lifetime candidate had 82 passed in 32.25s; that is historical
  evidence, not verification of the new sliding behavior. The first sliding-only
  run had 25 passed and one wrong expected status for a nonexistent provider-sync
  fixture (existing code returns 502, without invoking a remote connector).
- Current actual browser UI: new calendar-month wording, fake opt-in login,
  labeled current-device list, short-session expiry during an active page followed
  by refresh-list restoring automatically without any password prompt, deadline
  moving from Nov 26 12:38:52 to Nov 26 12:39:13 in the browser's display timezone,
  and device revocation still opening a password prompt. Cancellation did not
  revoke. Prior same-context/different-profile evidence remains valid because no
  client isolation or cross-profile credential transfer was added.
- Screenshots: `.test-output/device-auth-sliding-login.jpg` and
  `.test-output/device-auth-sliding-restored.jpg`.
  The preview fixture bound only to 127.0.0.1:18090, used an independent fake DB
  and predictable fake secrets, and disabled mesh/provider background jobs.
  It is not a production configuration. Preview is stopped after acceptance.

Tests cover calendar-month end/leap-year/year-rollover boundaries, sequential
renewals over five months past both old limits, renewal on each successive API
access, no static/health renewal, password login reuse, malformed/revoked/replayed
tokens not extending expiry, stale responses across both token rotation and a
month boundary, concurrent renewal, clock ordering, metadata/security step-up
separation, plus expiry boundaries, hash-only storage and cookie flags, rotation,
two simultaneous refreshes, refresh/revoke concurrency, independent-device
revocation, user ownership, logout after expiry, invalid tokens, password-version
invalidation, feature kill switch, old-login compatibility, 15-minute freshness,
device cap, additive migration idempotence, cookie-path clearing, CSRF metadata,
and independent cookie-jar isolation. All are local fake-data tests; none proves
production deployment or actual cross-Codex-thread sharing.

Gate-efficiency review: there is no evidence of shared-environment contention or
a mandatory redundant gate requiring repository changes. Full pytest grew from
82 cases / 32.25s to 95 / 49.75s; PBKDF2 password setup and the additional test
cases are part of that work, but no profiler was run to assign exact per-cause
cost. The existing full run was followed rather than duplicated. After a
test-only assertion correction, 94 valid case results were reused and only one
affected case reran (6.03s), avoiding another approximately 49.75s full run (about
43.7s less measured test-command time, not a benchmark guarantee). Frontend
initialization-fixture repair reran only the 193ms Node suite. Required auth,
concurrency, legacy compatibility and actual UI checks were retained; no gate
was removed or weakened, and no unrelated gate refactor was introduced.

## Production authorization and preflight history

At 2026-10-26 05:27:02 UTC, the user explicitly approved the sliding month Token
scheme, the risk of unlimited renewal under continued use, migration, activation
and service restart. No repeat approval of that same scheme is required.

OHR0067 release preflight found origin/main at
4b08008db48f1f9e4fa849620e8c806c04d41895 and the remote development branch at
29705df3a804b12ca69a8a20f714b67146987302. The local candidate remains based on
c0692a8. The development history contains unrelated existing changes beyond main;
do not push that whole branch to main without verifying actual live deployment
identity and selecting only the approved change.

Existing project Secure configuration was used through the normal Server-Desk
login/inventory interface, without printing secret values. Live inventory
identified CCNODE-MAIN at ccnode.briconbric.com:22, root, SSH key authentication.
Its configured Windows, macOS-local and Linux key paths are not existing files on
OHR0067. No SSH connection or production shell command executed.

A proposed fallback to a same-named key already present in the project Secure
directory was rejected by automatic approval review: a matching filename does not
verify private-key identity or authorization for root production access. The
rejected patch/action was not applied or executed, and no workaround was used.
At that point the release was blocked on a verified existing SSH credential path
or explicit credential authorization, followed by live preflight checks. No
production push, migration, activation or restart had happened. This was not a production failure
requiring rollback: production was not changed by the attempted release.

The user subsequently confirmed the exact existing project key for this specific
CCNODE root connection at 2026-10-26 05:36:50 UTC. Strict hostname trust initially
failed because the domain retains an old RSA record. No record was overwritten.
A credential-free handshake to the explicitly approved IP matched its existing
ED25519 known_hosts fingerprint exactly. Strict RejectPolicy then connected via
that IP. At 05:40:05 UTC, live production was confirmed on clean main at
4b08008db48f1f9e4fa849620e8c806c04d41895, with application and auto-sync timer active.

The release candidate is ported only from the auth commit onto that actual main
baseline in an isolated detached worktree. Auth Python code and device tests are
unchanged from the locally accepted candidate (git content comparison). The UI
keeps production's existing general error formatter and CSS baseline; only auth
retry/step-up changes and the JS asset version are added. Production had no Node
suite: two relevant auth tests are added (both pass in 158ms), without importing
four unrelated provider-save tests or their unreleased feature changes. The
formal Linux deployment gate still runs the complete applicable Python suite.

## Approved production procedure

The scheme below is approved. Retain its original permission and risk boundaries.

1. Confirm the goal limitation: remembered browser in a shared profile, not an
   assertion of physical-device trust or guaranteed isolated-thread SSO. Approve
   rolling one-calendar-month UTC deadline with no absolute/7-day cap, transparent
   12-hour short-session restoration, and the ordinary read/write versus protected
   operation boundary above. Specifically confirm that repeated use (including
   polling or stolen credentials) can perpetuate access until explicit revocation
   or password change. Retain the 15-minute password step-up for high sensitivity.
2. Recheck live checkout, proxy path, environment variable presence (never print
   values), actual HTTPS/Secure cookie behavior and login throttling. Secure must
   remain enabled. Check no other deployment/test task holds the environment.
3. Preserve current release identity and env backup and make the existing encrypted
   consistent SQLite backup. Coordinate the origin/main auto-deploy timer; push
   itself may trigger release. Any timer change is a separate concrete production
   action and must be included in the confirmed release procedure.
4. Deploy the reviewed exact candidate with migration 9. Keep existing APP_SECRET,
   credential encryption keys, permissions, proxy TLS and origins unchanged.
   Set OPS_DEVICE_AUTH_ENABLED=1 and OPS_DEVICE_COOKIE_PATH=/server-desk/ in the
   protected production environment; retain OPS_COOKIE_SECURE=1. Restart service
   in the agreed window. No standalone permanent token is pre-generated/configured;
   real credentials would be minted only after explicit user opt-in login.
5. Verify health, ordinary login, opt-in cookie flags/path, restore, password gate,
   individual revoke and logout using an approved production enrollment, then
   revoke the acceptance enrollment. Do not log Set-Cookie or bearer values.

Risk: expanded persistence makes theft of a private browser more useful; active
use can perpetuate ordinary read/write access without a fixed lifetime ceiling.
An inactive credential expires one calendar month after its last successful
authentication. Service restart can
interrupt in-flight requests. The git-sync script stops the service and does not
automatically roll back on test/start failure; no zero-interruption claim is made.

## Rollback proposal (not executed)

Emergency disable with OPS_DEVICE_AUTH_ENABLED=0 and restart; managed sessions
then fail closed and users sign in with their password. Ordinary legacy sessions
remain compatible. To prevent old remembered credentials becoming valid after a
future re-enable, mark all remembered_devices revoked and delete device_sessions
inside one SQLite transaction before re-enabling. This deliberate global revoke
is an authentication production change requiring approval.

For code rollback, coordinate/hold the auto-sync timer so it cannot redeploy the
candidate, restore the recorded previous release via a reviewed rollback commit
or approved deployment procedure, restore prior env, restart and verify health
and password login. Do not force-push or reset other work. Leave the additive
tables/schema_migrations row 9 in place; the old app ignores them. Never restore
the whole DB merely to remove two tables, as that loses later business writes.
Browsers retain unusable managed cookies until explicitly cleared or expired;
the old app ignores them, and new password login can proceed. The old stateless
session limitations remain after rollback.

## Separate read-only source audit: Check configuration

This audit did not change inspection code or execute a button/remote command.
Frontend calls POST /api/servers/{id}/inspect. The endpoint invokes
run_and_store_inspection -> run_server_inspection. In the current candidate these
are app/main.py:2076, :2040, :1518 (baseline c0692a8:1885, :1849, :1444).

The shell/Python payloads collect hostname/OS/kernel/CPU/memory/disks, package
inventory, interface/routes/DNS, sysctl values, running and failed services,
listeners, time sync, sshd effective policy and firewall status. Commands such
as systemctl list-units/show/is-active, sshd -T, ufw status, nft list ruleset are
queries; no apt install, config edit, firewall write or service restart exists in
these payloads. They also perform outward IP lookups to api.ipify.org and
api64.ipify.org, HTTPS HEAD requests to Cloudflare/Google/Microsoft, and pings to
1.1.1.1/8.8.8.8. Thus this is active observation, not a zero-network action.

Password SSH uses Paramiko. If python3 is available, run_paramiko_python_script
(current :1468; baseline :1394) uploads a uniquely named
`/tmp/server-desk-inspect-<uuid>.py`, chmods it to 0700, runs python3, and attempts
rm -f cleanup in finally. No Python installation is attempted. Without python3,
it runs the shell payload. Key SSH and local paths run the shell payload directly.
Temp files could remain if upload/chmod/cleanup fails. SSH login itself can be
recorded by the target OS. Existing Paramiko AutoAddPolicy accepts unknown host
keys; this audit neither changes nor endorses that preexisting policy.

The application writes the collected hostname, report/status/apps/services and
last_config_check_at into its own DB and appends an inspect audit event. Therefore
the precise conclusion is: no intended system configuration/install/restart
mutation; remote temp-file writes in one path, external probes, and application
DB writes. This source audit does not prove the running deployed code is identical.
