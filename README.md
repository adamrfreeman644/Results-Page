# OWAR 2026 Live Results

Mobile-first interactive race results demo for OWAR 2026, presented by Flow State PEV.

## Included

- Public live-results page
- Tournament stages and race tables
- Rider history pages
- Separate `/admin` demo controls
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

The automatic results-file import and server-side admin authentication are intentionally left for the backend stage.

## Live results service

The Docker service polls a read-only RDF file, stores only the public result fields in SQLite, and serves the public page and admin API. See `docker-compose.yml` for the Byte-Me mount and admin-token configuration.
