# Curie’s browser-based X research

This is a separate, opt-in research routine, not the API posting service. It has
**no paid X API fallback**. X warns that scripted website automation may lead to
account suspension. Enable only after the owner explicitly accepts that risk.

The server uses its **installed Firefox**, through Selenium/geckodriver, with a
persistent profile dedicated to each owner. Login and scheduled research reuse
that same profile. Firefox loads ordinary website resources, cookies, local
storage, IndexedDB and service workers. There is no endpoint allowlist or request
rewriting: X's own internal requests are necessary for its website and do not use
Curie's paid developer API credentials. The collector reads visible DOM content;
it never constructs or replays private API calls.

This replaces the temporary Playwright contexts and restrictive network filter.
It does not guarantee X will accept a login or remove an account/IP restriction.

## Behaviour

- Plans 2–6 stable, pseudo-random sessions per local calendar day, between 09:00
  and 22:00 in the owner’s profile timezone. Restarting does not reroll the day.
- Visits the home feed and three rotating searches per session. Two sessions
  cover AI news, AI setups/technology, world news, Hong Kong and Egypt.
- Reads at most 60 DOM-visible posts, two viewports per page, within 180 seconds.
  The entire research/delivery cycle is limited to 300 seconds, within a
  600-second Rust scheduler lease. Resource pressure may defer a session.
- Skips missed slots after one hour; never catches up with a burst. Failed and
  interrupted attempts consume the slot. Thus 2–6 is a planned range, not a
  guarantee during downtime, rate limits, or authentication problems.
- Filters stale/future posts, irrelevant posts, duplicate links/text, and repeated
  stories. Rotating searches prevent the home feed from being the only source.
- Checks up to three leads against free public Google News RSS. Related reports
  require fresh headlines from at least two distinct, recognised news domains.
  This is **headline correlation, not claim-level verification or proof of
  independent sourcing**. Summaries say so; X claims are not silently promoted
  to facts. Publisher selection is intentionally conservative and may miss
  specialist/primary-source releases without mainstream coverage.
- Stores owner-scoped research separately from personal memory. Findings have
  source links, observed timestamps, a 24-hour expiry, and heuristic confidence
  (0.25 uncorroborated / 0.60 related reporting—not a calibrated probability).
  Retains at most 200 findings and bounded deduplication state.
- Sends at most two concise, source-linked Telegram digests per local day, only
  when useful source-linked discoveries exist. Ambiguous sends are not replayed.
  Existing proactive opt-in, snooze, quiet hours, topic exclusions, channel
  preferences and global message budgets still apply.
  Research context is added only to relevant news/update questions, not all chats.
- Does not post, like, reply, repost, send DMs, or follow accounts. The owner’s
  permission to follow is not used indiscriminately; this initial routine is
  read-only. Existing separately configured API autoposting is unchanged.

## Authentication and activation

API OAuth credentials **do not** authenticate the website. Normal desktop Chrome
cookies are never imported. Enrol a fresh browser session for Curie’s account:

1. Install the optional browser runtime on the server if missing:

   ```sh
   .venv/bin/python -m pip install -r requirements-browser.txt
   # Install Firefox and geckodriver using the server OS package manager.
   # Ubuntu Firefox Snap already includes /snap/bin/geckodriver.
   ```

2. On the **server’s graphical desktop**, from the repository with its private
   environment loaded, run:

   ```sh
   .venv/bin/python -m scripts.x_browser_login --owner-id OWNER_ID --handle CURIE_HANDLE
   ```

3. Sign in manually in the new browser and complete any verification yourself.
   Return to the terminal and press Enter only once Curie’s home feed is visible.
   A mismatched account is rejected. No password is read by the script.
4. Firefox retains its session in its private profile directory. These are normal
   browser files, **not encrypted by Curie's vault**. The directory and its root
   are restricted to the service user (0700); the adjacent lock is 0600. Keep
   server storage encrypted as appropriate and exclude profiles from backups
   shared with others. Never export profiles to source control, chat or logs.
   The encrypted `x_browser` vault record holds only handle, enrollment time and
   profile format. The existing vault key remains in private configuration.
5. Set only the verified owner’s profile fields:
   `x_research_browser_risk_accepted=true`, `x_research_handle=CURIE_HANDLE`,
   `x_research_enabled=true`. These are operator configuration, not model tools.
6. Restart the application to load new code. Existing proactive background
   startup registers the browser worker with the Rust task engine. If no browser
   session exists, it waits with `server_browser_login_required`, without API calls.

The default profile root is `.curie-browser/x` (gitignored), or
`~/snap/firefox/common/curie-x-profiles` when Snap geckodriver is installed. The
latter is accessible inside Firefox's Snap confinement. Set
`CURIE_X_BROWSER_PROFILE_ROOT` to override it; use the same absolute root and
service user for login and background research. Optional `CURIE_X_FIREFOX_BINARY`
and `CURIE_X_GECKODRIVER` select installed binaries. No driver is downloaded at
runtime. Keep Snap profiles outside `/tmp` and hidden home directories.

An OS file lock serializes login and research per owner. Curie never deletes
browser locks or kills another browser to take over a profile. Startup failures
and cancellation close the browser before releasing the lease. Existing vault
cookie exports require a fresh enrollment; they are not silently imported.
An enrollment marker checks profile availability, while every research session
also verifies the actual signed-in account.

The setup never opens a public remote-debugging port, copies a browser profile,
disables browser security, or bypasses a login challenge. It requires a private
owner identity and an available Rust task engine. A Linux server without a
graphical session needs a separately approved private login interface; do not
expose CDP/VNC on a public address as a workaround.

## Controls and failure recovery

- `/xresearch status`: configuration, authentication gate and next planned slot.
- `/xresearch latest`: fresh source-linked discoveries.
- `/xresearch pause`: stop future scans/digests without changing other news work.
- `/xresearch resume`: resume an already risk-approved configuration with a saved
  login. Rechecks the actual account on the next scan.

Authentication challenges, a wrong account, or 403/429 responses pause the worker
until a new login or explicit resume. A browser/network timeout consumes its slot
and can try at the next planned slot. No stealth, CAPTCHA solving, session theft,
private API replay, proxy rotation, or paid fallback is implemented.

## Validation

`tests/test_x_browser_research.py` tests schedule limits and timezones, missed slots,
duplicate stories, source quality/freshness, owner isolation, pause/restart paths,
wrong accounts, metadata privacy, digest idempotency and
the native scheduler lease (when installed). Existing awareness and research tests
must also pass. `tests/test_x_browser_profile.py` covers profile locking,
permissions, isolation, cancellation and enrollment. Its optional real-Firefox
test checks cookie/local-storage/service-worker persistence across restarts,
DOM extraction and response-status observation, using only a local fixture:

```sh
CURIE_RUN_BROWSER_TESTS=1 .venv/bin/python -m pytest -q -m integration tests/test_x_browser_profile.py
```

For Snap, pass `--basetemp` pointing to a fresh directory under
`~/snap/firefox/common/` so fixture profiles are accessible. These offline tests do not establish that X’s current DOM works
with a newly authenticated account: complete a bounded live session after login
and check `last_completed_at`, `last_post_count`, and actual digest delivery before
calling end-to-end deployment verified.

References: [X automation rules](https://help.x.com/en/rules-and-policies/x-automation),
[Firefox profile handling](https://firefox-source-docs.mozilla.org/testing/geckodriver/Profiles.html),
[Selenium Firefox support](https://www.selenium.dev/documentation/webdriver/browsers/firefox/).
