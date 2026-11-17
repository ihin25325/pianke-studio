# Security

This is a **single-user local Windows application**, not a multi-user web server.
Keep it bound to 127.0.0.1; do not expose it through port forwarding, a public
tunnel or an untrusted reverse proxy. There is no user-login or tenant isolation.

- Secrets are optional and encrypted with Windows DPAPI for the current account.
- Do not commit `data/`, `configs/task_*.json`, `.env`, logs, cookies, original
  business tables, sources, outputs or local model/runtime folders.
- Never paste a real secret into a GitHub issue. Redact screenshots and logs.
- If a secret was published, revoke/rotate it first; deleting a file from the
  latest revision does not remove it from Git history.
- Do not publicly report reproducible vulnerabilities before a private contact
  is available. Use GitHub private vulnerability reporting if enabled; otherwise
  ask the maintainer for a private reporting channel without exploit details.

Network parsers and dependencies can change. All security checks are best-effort;
they are not a guarantee that arbitrary untrusted media is safe. Keep Windows,
Python, FFmpeg and yt-dlp updated and use authorized public media only.
