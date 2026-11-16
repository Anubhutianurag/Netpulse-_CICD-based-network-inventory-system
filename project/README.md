# NetPulse: Network Device Inventory & Health Board

Flask app with an automated CI/CD pipeline on GitLab.

## Features
- Device inventory with add, edit, delete, search, sort and filters
- Network health score and clickable status cards
- Alerts panel (Offline = Critical, Maintenance = Warning)
- Activity log (audit trail)
- Command palette (Ctrl+K) and 10-second auto-refresh
- Release footer showing app version and build

## Tech stack
- Flask + SQLite
- Gunicorn + Nginx
- Podman Compose
- GitLab CI/CD (test, build, deploy)

## Run
    podman-compose up -d --build

## Test
    pytest -q
