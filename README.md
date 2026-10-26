# Markdown CRM

A local CRM backed by readable Markdown files. Browse boards, edit records, track reply status and preserve surrounding notes and unknown record fields. Python standard library only; no account, cloud backend, telemetry or external font service.

Extracted from Exlumina's internal tool. This public package contains synthetic examples only, without company contacts, fundraising history or service credentials.

## Try the synthetic workspace

Requires Python3.10+ and a current browser. Node18+ is needed only for frontend logic checks.

```sh
python3 init_demo.py ./local-data
python3 server.py --data-dir ./local-data --port 8765
```

Open http://127.0.0.1:8765 . Stop with Ctrl-C. The initializer refuses an existing destination. Keep one server/writer per data directory. Do not expose this unauthenticated personal service to a network, reverse proxy or public server; it binds to127.0.0.1 and rejects unexpected Host/Origin headers. No installer or background service is needed.

## Data and recovery

The directory contains boards.md, records/*.md and Changes.md. Each board/record document has exactly one `## Structured record` section followed by fenced JSON. Other Markdown prose is preserved. Record IDs stay stable when names/filenames change. Data can be read in an ordinary editor.

SHA256 revisions reject stale UI edits; writes replace one record atomically under a process-local lock. External editors do not participate in that lock. Back up data normally before changes and avoid simultaneous external writes. This is not a multiuser database or a backup system. Symlinks in data paths and ambiguous IDs are refused.

Investment-rating/funding fields can be ignored for ordinary relationship boards. Boards and stages come from data. Synthetic sample events do not represent real people or businesses.

## Verify

```sh
python3 -m unittest -v test_server.py
node test_ui_logic.js
```

Tests use temporary synthetic files and ephemeral loopback ports, covering revision conflicts, preservation, paths, invalid data, request origin checks and UI helper behavior. They do not establish physical drag/accessibility acceptance in every browser. This extraction does not change the internal tool or live records.

## License and support

Original source is MIT. The adapted palette and bundled Geist font retain their licenses and notices in THIRD_PARTY_NOTICES.md, VERCEL_CHATBOT_LICENSE.txt and GEIST_LICENSE.txt. Company names in provenance are attribution, not endorsement. Report bugs with synthetic reproductions; never attach contact databases, credentials or private correspondence.
