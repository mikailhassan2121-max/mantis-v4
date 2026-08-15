# Phase 10 — Windows Installation

Supported runtime: 64-bit Python 3.11 or newer on Windows 10/11. Edge is the
preferred command-center browser. No administrator access is required.

## Beginner setup

1. Install Python from python.org and enable **Add Python to PATH**.
2. Open the MANTIS project folder in File Explorer.
3. Right-click `setup_mantis.ps1` and choose **Run with PowerShell**, or open
   PowerShell in the folder and run `powershell -ExecutionPolicy Bypass -File .\setup_mantis.ps1`.
4. Double-click `start_mantis.bat`, or run `start_mantis.bat --demo` first.

The setup script creates only `.venv` inside the project, installs declared
requirements there, and runs the safe health check. It does not change
machine-wide security settings, install a broker client, or store credentials.

## Commands

```powershell
python mantis_v4_live.py --health-check
python mantis_v4_live.py --self-test
python mantis_v4_live.py
python mantis_v4_live.py --demo
python mantis_v4_live.py --profile quiet
python mantis_v4_live.py --profile diagnostic
```

Optional Webull variables are `WEBULL_APP_KEY`, `WEBULL_APP_SECRET`,
`WEBULL_MARKET_DATA_ENABLED`, and `WEBULL_REGION`. Health output reports only
presence/configuration state; values are never printed.

## Release/runtime layout

- tracked: Python/HTML/CSS/JS source, branding, tests, example configs, scripts, docs;
- ignored operator config: `config/mantis_v4.local.json`, `config/mantis_v4.ui.json`;
- ignored secrets: `.env`, `.env.mantis`, local credential config;
- ignored runtime state: `data/`, forward JSONL, diagnostics, browser profile;
- ignored environment/build state: `.venv/`, caches, distributions and models.
