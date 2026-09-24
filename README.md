# OWAR 2026 Live Results

The public results site keeps the existing OWAR visual design and is served on Byte-Me port **6543**. It imports only a single RaceTec RDF export from a private shared folder.

## What it does

- Uses no AI, website scraping, public FTP/SFTP, reverse proxy, tunnel endpoint, router forwarding, Cloudflare, or public timing URL.
- Reads the RaceTec export folder in Docker as **read-only**.
- Checks it every 5 seconds and imports only after the same file contents have been observed twice (a full stable interval).
- Parses RaceTec RDF events/heats, riders, bibs, positions, and times.
- Displays every imported event/heat in the existing public results interface.
- Stores every import in SQLite, including a historical copy of each rider result.
- Keeps the token-protected Admin page for start/stop, file status, last successful import, errors, event visibility, and application version. None of this data is in the public API or public page.

## Byte-Me setup

1. On Byte-Me, create a private folder that the secure Algarve-to-Bristol share can write to, for example `/srv/racetec-export`. Configure RaceTec to replace one RDF export in that folder, for example `results.rdf`. The share must be private; do not expose a port or create an external endpoint.
2. Copy `.env.example` to `.env`, then set a long random `ADMIN_TOKEN`, the real host folder as `RACE_EXPORT_DIR`, and the RaceTec filename as `RACE_EXPORT_FILENAME`.
3. Start or update the service:

   ```bash
   docker compose up -d --build
   ```

4. Open `http://byte-me:6543/` for the public site. Open `http://byte-me:6543/admin/` and enter the admin token for controls and diagnostics.

Docker mounts the configured host folder at `/race-export:ro`; the service cannot write to the RaceTec share. Its persistent SQLite database is a separate Docker volume.

## Stable replacement behaviour

Use RaceTec's local/shared-folder export routine to write the same configured RDF filename each time. The service waits for two identical snapshots five seconds apart before parsing. If the file is missing, malformed, or still changing, the public site continues showing the last successful results and the Admin page reports the error.

