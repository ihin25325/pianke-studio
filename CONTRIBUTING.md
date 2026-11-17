# Contributing

1. Use Windows 10/11 x64 and Python 3.11+ (3.12 recommended).
2. Run `SETUP.bat`. Optional model/font environments are documented in README.
3. Run `RUN_TEST.bat` before proposing changes. Tests write isolated data under
   `work/`; never submit the test output or your real product catalogue.
4. Add/update tests for changes and make a focused commit. Follow `AGENTS.md`.
5. Explain which checks passed, failed or were skipped, especially when optional
   models, credentials, private fixtures or network access are unavailable.

Before a PR, run `.venv\Scripts\python.exe -B scripts\release_check.py` and check
the diff for private data. Do not use `git add .` without inspecting the file list.
No paid API or real account is necessary for core tests.

The source is MIT; retain notices for dependencies and external assets. Do not
contribute copyrighted product footage, scraped logos, real credentials or
customer spreadsheets as test fixtures.
