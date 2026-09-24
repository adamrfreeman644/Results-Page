# OWAR 2026 Live Results

Mobile-first live race results for OWAR 2026, presented by Flow State PEV.

## Included

- Public live-results page
- Tournament stages and race tables
- Rider history pages
- Separate token-protected `/admin` controls for RaceTec sources, visibility, event status, and live-polling stop/start
- Responsive, high-contrast outdoor design
- Links to OW Algarve and Flow State PEV

## Structure

The static website is in `dist/`.

- `dist/index.html` — public results
- `dist/admin/index.html` — admin demo
- `dist/rider/index.html` — rider details
- `dist/app.js` — demo data and interactions
- `dist/styles.css` — responsive styling

Current demo: https://owar-2026-live-results.adam-ow.chatgpt.site/

RaceTec public-meeting URLs are imported automatically. The service discovers the meeting's event/stage list and refreshes published standings without changing timing files.

## Live results service

The Docker service exposes the public page and admin API on Byte-Me port `6543`. RaceTec sometimes blocks plain HTTP clients, so the image includes Chromium as a browser-only fallback for its public pages. See `docker-compose.yml` for configuration.

Before the first start, copy `.env.example` to `.env` and set `ADMIN_TOKEN`. The `.env` file remains on Byte-Me and is deliberately not tracked by Git, so later application updates do not overwrite your token.

If the AD53 Shared App Updater is installed on Byte-Me, the Admin page uses its Results Page entry at `http://host.docker.internal:8093/apps/results-page` for checked, backed-up updates and rollback.
