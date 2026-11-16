# NetPulse: Network Device Inventory & Health Board

A Flask web app for tracking network devices and their health, delivered through a fully automated **GitLab CI/CD pipeline** (test → build → deploy) on a Linux VM using **Podman**.

![Python](https://img.shields.io/badge/Python-3.11-blue)
![Flask](https://img.shields.io/badge/Flask-3.0-black)
![Podman](https://img.shields.io/badge/Podman-Compose-purple)
![CI/CD](https://img.shields.io/badge/GitLab-CI%2FCD-orange)
![Tests](https://img.shields.io/badge/pytest-28%20tests-green)

> The app is the demo. The main focus of this project is the **automation pipeline** around it.

---

## Table of Contents
- [Problem & Goal](#problem--goal)
- [Features](#features)
- [Architecture](#architecture)
- [Tech Stack](#tech-stack)
- [CI/CD Pipeline](#cicd-pipeline)
- [Project Structure](#project-structure)
- [Run Locally](#run-locally)
- [Testing](#testing)
- [API Endpoints](#api-endpoints)
- [Security](#security)
- [Rollback](#rollback)
- [Future Scope](#future-scope)
- [Author](#author)

---

## Problem & Goal

**Problem:** Network teams often track devices (routers, switches, firewalls, base stations, servers) in scattered spreadsheets. Outages are noticed late, and manual deployments cause configuration drift and human error.

**Goal:** One inventory and health dashboard, delivered through a pipeline where every code push is automatically tested, built, deployed and verified, with no manual deployment steps.

---

## Features

- **Device inventory:** add, edit, delete, search, sort and filter devices
- **CSV import / export:** bulk-load devices, export data for Excel or Power BI
- **Network health score:** online devices ÷ total devices
- **Clickable status cards:** Online / Offline / Maintenance
- **Alerts panel:** Offline = Critical, Maintenance = Warning
- **Activity log:** audit trail of every change
- **Availability trend:** snapshots on every status change and every 5 minutes
- **Auto-insights:** e.g. which location has the most open issues
- **NetPulse Assistant:** rule-based chat that answers questions ("How many devices are offline?") and asks for **confirmation** before changing a device status
- **UX extras:** Ctrl+K command palette, 10-second auto-refresh, dark/light theme
- **Release footer:** shows app version and build commit SHA
- **Monitoring endpoints:** `/health` and Prometheus-style `/metrics`

<!-- Add a dashboard screenshot here -->
<!-- ![Dashboard](docs/dashboard.png) -->

---

## Architecture

```
Browser ──► Nginx (:8080) ──► Gunicorn ──► Flask app ──► SQLite (named volume)
            └──────────── containers run via Podman Compose on a Linux VM ────────────┘
```

- **Nginx** is the reverse proxy and the only exposed port (`8080`)
- **Gunicorn** runs the Flask app
- **SQLite** data lives on a named volume, so it survives redeployments
- The **GitLab runner** runs on the same VM that hosts the app

---

## Tech Stack

| Layer | Technology |
|---|---|
| Backend | Python 3.11, Flask 3.0, Gunicorn |
| Database | SQLite (named persistent volume) |
| Web server | Nginx (reverse proxy) |
| Containers | Podman + podman-compose |
| CI/CD | GitLab CI with a self-hosted runner |
| Testing | pytest (28 tests) |

---

## CI/CD Pipeline

The pipeline is defined in [`.gitlab-ci.yml`](.gitlab-ci.yml) with three sequential stages. All jobs run on a self-hosted runner on the Linux VM.

```
 git push ──► [ TEST ] ──► [ BUILD ] ──► [ DEPLOY (main only) ] ──► health check
```

| Stage | What it does |
|---|---|
| **Test** | Creates a fresh Python virtual environment, installs `requirements.txt`, runs `pytest -q` (28 tests) |
| **Build** | `podman build`, tags the image with the **commit SHA** and `latest`, then lists images to confirm |
| **Deploy** | Runs on the **`main` branch only**. `podman-compose down`, then `up -d --build`, then **polls `/health` up to 8 times (3 s apart)**. A final health check fails the job if the app isn't responding. Prints running containers with `podman ps` |

**Key behaviors**
- If tests fail, **build and deploy never run**, so broken code can't reach the live app
- Feature branches are tested and built, but **never deployed**
- A failed earlier stage leaves the **running deployment untouched**
- Every image is traceable to its exact commit via the SHA tag

<!-- Add a green pipeline screenshot here -->
<!-- ![Pipeline](docs/pipeline.png) -->

---

## Project Structure

```
.
├── app.py               # Flask app (routes, DB, assistant, metrics)
├── test_app.py          # 28 pytest tests
├── templates/
│   └── index.html       # Dashboard UI
├── Dockerfile           # python:3.11-slim + Gunicorn
├── docker-compose.yml   # app + nginx services, named volume
├── nginx.conf           # reverse proxy config
├── .gitlab-ci.yml       # CI/CD pipeline
├── requirements.txt     # flask, gunicorn, pytest
└── README.md
```

---

## Run Locally

**With Podman Compose (same as the pipeline):**
```bash
podman-compose up -d --build
```
Open **http://localhost:8080**

**With Docker Compose:**
```bash
docker compose up -d --build
```

**Without containers (development):**
```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
export DB_PATH=./data/netpulse.db
python -m flask --app app run --port 5000
```

---

## Testing

```bash
pytest -q
```

The 28 tests cover:
- Health, stats and state APIs
- Adding devices and rejecting invalid IPs
- Critical alerts for offline devices
- Activity logging
- CSV import (valid, invalid, duplicate rows)
