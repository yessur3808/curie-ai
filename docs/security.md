# Security maintenance

Curie's CI treats dependency, source-code, and credential scans as release
gates. A finding may be suppressed only when it is documented here, scoped to
one advisory, and backed by a concrete compensating control.

## Patched dependencies (2026-10-25)

- GitPython is pinned to 3.2.1 in both runtime and CI requirements. This fixes
  [CVE-2026-114145](https://github.com/gitpython-developers/GitPython/security/advisories/GHSA-83vg-56qc-22m7),
  where a malicious submodule path could write into `.git` before validation.
- Black is pinned to 26.10.1 in runtime requirements, pre-commit, and CI,
  following its [security release](https://github.com/psf/black/releases/tag/26.10.1)
  for CVE-2026-114134. Curie invokes the installed CLI rather than the affected
  bundled action; both pins are nevertheless updated, without an audit exception.

## Dependency exceptions

### PYSEC-2026-2447 / CVE-2025-69872

- **Package:** `diskcache==5.6.3`, pulled transitively by
  `llama-cpp-python`.
- **Risk:** DiskCache's default pickle deserialization can execute code when an
  attacker can write a malicious entry into the cache directory and Curie then
  reads that entry.
- **Upstream status:** 5.6.3 is the latest published release and no patched
  version exists as of 2026-09-20.
- **Curie exposure:** Curie does not import or configure `diskcache), and does
  not enable a llama.cpp disk cache. The dependency is present only as part of
  the optional local-model runtime.
- **Compensating controls:** Run Curie as its dedicated unprivileged service
  account; do not grant other users or containers write access to its runtime
  or model directories; do not configure a shared or externally writable
  DiskCache directory.
- **CI handling:** Only `PYSEC-2026-2447` is ignored. All other dependency
  advisories still fail the audit.
- **Removal condition:** Remove the exception as soon as DiskCache publishes a
  fixed release or llama-cpp-python removes the vulnerable dependency. Review
  this exception with every dependency update and at least monthly.

## Secret-scan baseline

`.secrets.baseline` contains hashes for reviewed, synthetic values used by
security tests and onboarding examples. It contains no plaintext credentials.
New findings remain fatal. Do not refresh the baseline wholesale: inspect each
new finding, remove real credentials, and add only demonstrably synthetic test
fixtures.
