# IDN Training Knowledge Synchronization Engine

Step 1 foundation for a future long-running system that discovers IDN training products and synchronizes validated knowledge. This version does **not** crawl at scale, log in, publish data, or run an autonomous pipeline.

## Architecture

The future flow is `Supervisor → Discovery → Crawler → Extractor → Research → Resolver → Validator → Publisher → Verifier`. In Step 1, only configuration, SQLite state/checkpoints, rotating logging, runtime lifecycle, persistent Playwright browser, and health checks are active.

## Windows setup (PowerShell)

Python 3.11 or newer and Git are required. Google Chrome is optional because Playwright installs its own compatible Chromium.

```powershell
python -m venv .venv
Set-ExecutionPolicy -Scope Process -ExecutionPolicy Bypass
.\.venv\Scripts\Activate.ps1
python -m pip install --upgrade pip
pip install -r requirements.txt
playwright install chromium
Copy-Item .env.example .env
```

Run the checks:

```powershell
python main.py health
python main.py db-test
python main.py browser-test
python main.py run
```

`browser-test` uses the persistent profile in `runtime/chrome-profile` and saves a screenshot under `runtime/screenshots`. The default is visible (`HEADLESS=false`); set it to `true` in `.env` for unattended/headless use. The bundled Chromium is recommended for version compatibility. A later step can add an explicit installed-Chrome channel if required.

## Main folders

```text
app/core/       config, database, logging, runtime, health
app/browser/    persistent browser manager and session scaffold
app/discovery/  future pipeline stages (also crawler, extractor, research,
                resolver, validator, publisher, verifier)
data/           SQLite database and future extracted data
runtime/        lock, browser profile, screenshots, artifacts
logs/           rotating application log
prompts/        future prompts
tests/          automated tests
```

Runtime data, browser cookies, databases, logs, `.env`, and virtual environments are ignored by Git. Never commit usernames, passwords, cookies, tokens, or other credentials; provide secrets only through environment variables or an appropriate secret store.

## Current scope

The `run` command initializes the runtime, database, logging, and a lightweight health check, then exits cleanly. Autonomous crawling and publishing remain intentionally disabled; Step 2 only adds bounded reconnaissance.

## Gate 0 reconnaissance (Step 2)

Build the deterministic IDN site model with HTTP-first fetching and browser fallback:

```powershell
python main.py idn-learn
python main.py idn-learn --limit 5
python main.py idn-report
```

`--limit` limits only diverse landing-page samples; the full directory catalog is always parsed. Raw HTML snapshots and generated JSON models are local debug/runtime artifacts and are ignored by Git. The crawler respects robots.txt, uses bounded retries and a conservative delay, stays on IDN sources, does not follow social/WhatsApp/external links, and never accesses `kb.idn.id` in this gate.
