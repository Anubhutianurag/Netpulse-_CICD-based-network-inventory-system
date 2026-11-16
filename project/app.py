import os, re, csv, io, sqlite3, ipaddress
from datetime import datetime, timezone
from flask import Flask, render_template, request, redirect, jsonify, Response

app = Flask(__name__)
app.config["MAX_CONTENT_LENGTH"] = 256 * 1024
DB = os.environ.get("DB_PATH", "/data/netpulse.db")
STATUSES = ("Online", "Offline", "Maintenance")
APP_VERSION = "v1.2.0"
STARTED = datetime.now(timezone.utc)
BANNER = "Welcome to NetPulse"

def get_db():
    conn = sqlite3.connect(DB)
    conn.row_factory = sqlite3.Row
    return conn

def init_db():
    os.makedirs(os.path.dirname(DB), exist_ok=True)
    with get_db() as c:
        c.execute("""CREATE TABLE IF NOT EXISTS devices (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            name TEXT NOT NULL, ip TEXT NOT NULL UNIQUE,
            dtype TEXT, location TEXT, status TEXT NOT NULL DEFAULT 'Online')""")
        c.execute("""CREATE TABLE IF NOT EXISTS activity (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            ts TEXT NOT NULL, action TEXT NOT NULL, detail TEXT NOT NULL)""")
        c.execute("""CREATE TABLE IF NOT EXISTS snapshots (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            ts TEXT NOT NULL, online INTEGER NOT NULL, offline INTEGER NOT NULL,
            maintenance INTEGER NOT NULL)""")

init_db()

def log(c, action, detail):
    c.execute("INSERT INTO activity (ts, action, detail) VALUES (?,?,?)",
              (datetime.now(timezone.utc).isoformat(timespec="seconds"), action, detail))

SNAPSHOT_EVERY = 300
MAX_SNAPSHOTS = 500

def take_snapshot(c, periodic=False):
    counts = [c.execute("SELECT COUNT(*) FROM devices WHERE status=?", (s,)).fetchone()[0]
              for s in STATUSES]
    last = c.execute("SELECT ts, online, offline, maintenance FROM snapshots "
                     "ORDER BY id DESC LIMIT 1").fetchone()
    now = datetime.now(timezone.utc)
    if last:
        same = (last["online"], last["offline"], last["maintenance"]) == tuple(counts)
        age = (now - datetime.fromisoformat(last["ts"])).total_seconds()
        if periodic and age < SNAPSHOT_EVERY:
            return
        if not periodic and same:
            return
    c.execute("INSERT INTO snapshots (ts, online, offline, maintenance) VALUES (?,?,?,?)",
              (now.isoformat(timespec="seconds"), counts[0], counts[1], counts[2]))
    c.execute("DELETE FROM snapshots WHERE id NOT IN "
              "(SELECT id FROM snapshots ORDER BY id DESC LIMIT ?)", (MAX_SNAPSHOTS,))

def recent_trend(limit=40):
    with get_db() as c:
        rows = c.execute("SELECT ts, online, offline, maintenance FROM snapshots "
                         "ORDER BY id DESC LIMIT ?", (limit,)).fetchall()
    return [dict(r) for r in reversed(rows)]

def valid_ip(v):
    try:
        ipaddress.ip_address((v or "").strip())
        return True
    except ValueError:
        return False

def clean(f):
    status = f.get("status", "Online")
    if status not in STATUSES:
        status = "Online"
    return (f["name"].strip(), f["ip"].strip(), f.get("dtype", "").strip(),
            f.get("location", "").strip(), status)

def all_devices():
    with get_db() as c:
        rows = c.execute("SELECT * FROM devices ORDER BY id DESC").fetchall()
    return [dict(r) for r in rows]

def recent_activity(limit=10):
    with get_db() as c:
        rows = c.execute("SELECT ts, action, detail FROM activity ORDER BY id DESC LIMIT ?",
                         (limit,)).fetchall()
    return [dict(r) for r in rows]

def compute_stats(devices):
    total = len(devices)
    count = lambda s: sum(1 for d in devices if d["status"] == s)
    online = count("Online")
    types = {}
    for d in devices:
        t = d["dtype"] or "Other"
        types[t] = types.get(t, 0) + 1
    return {"total": total, "online": online, "offline": count("Offline"),
            "maintenance": count("Maintenance"),
            "health": round(online * 100 / total) if total else 0, "types": types}

def build_alerts(devices):
    alerts = []
    for d in devices:
        if d["status"] == "Offline":
            sev, msg = "Critical", "Device unreachable"
        elif d["status"] == "Maintenance":
            sev, msg = "Warning", "Under maintenance"
        else:
            continue
        alerts.append({"id": d["id"], "severity": sev, "device": d["name"], "ip": d["ip"],
                       "location": d["location"], "message": msg})
    alerts.sort(key=lambda a: 0 if a["severity"] == "Critical" else 1)
    return alerts

def build_insights(devices):
    total = len(devices)
    if not total:
        return ["Add devices to see insights."]
    problems = [d for d in devices if d["status"] != "Online"]
    out = []
    if problems:
        out.append("%d of %d devices need attention (%d%%)." %
                   (len(problems), total, round(len(problems) * 100 / total)))
        by_loc = {}
        for d in problems:
            k = (d["location"] or "").strip() or "Unassigned"
            by_loc[k] = by_loc.get(k, 0) + 1
        loc, n = max(by_loc.items(), key=lambda kv: kv[1])
        out.append("%s has the most open issues: %d device(s) not online." % (loc, n))
    else:
        out.append("All %d devices are online." % total)
    h = compute_stats(devices)["health"]
    label = "excellent" if h >= 90 else ("acceptable" if h >= 70 else "low")
    out.append("Network health is %d%%, which is %s." % (h, label))
    return out

def read_commit():
    base = os.path.join(os.path.dirname(os.path.abspath(__file__)), ".git")
    try:
        head = open(os.path.join(base, "HEAD")).read().strip()
        if not head.startswith("ref:"):
            return head[:8]
        ref = head.split(" ", 1)[1]
        path = os.path.join(base, ref)
        if os.path.exists(path):
            return open(path).read().strip()[:8]
        for line in open(os.path.join(base, "packed-refs")):
            if line.strip().endswith(ref):
                return line.split()[0][:8]
    except OSError:
        pass
    return "dev"

def version_info():
    return {"app": APP_VERSION, "commit": read_commit(),
            "started": STARTED.isoformat(timespec="seconds")}

def system_status():
    db_ok = True
    try:
        with get_db() as c:
            c.execute("SELECT 1").fetchone()
    except sqlite3.Error:
        db_ok = False
    up = int((datetime.now(timezone.utc) - STARTED).total_seconds())
    return {"app": "OK", "database": "OK" if db_ok else "Error",
            "version": APP_VERSION, "commit": read_commit(), "uptime_seconds": up}

def build_state():
    with get_db() as c:
        take_snapshot(c, periodic=True)
    devices = all_devices()
    return {"devices": devices, "stats": compute_stats(devices),
            "alerts": build_alerts(devices), "activity": recent_activity(),
            "insights": build_insights(devices), "version": version_info(),
            "system": system_status(), "trend": recent_trend(), "banner": BANNER}

# ---------- NetPulse Assistant (rule-based) ----------
HELP = ("I can help with:\n"
        "- Devices: 'How many devices are offline?', 'Show offline devices in Jaipur'\n"
        "- Alerts: 'What needs attention?'\n"
        "- Changes: 'What changed recently?'\n"
        "- Actions: 'Mark CHN-FW-01 as maintenance' (I will ask you to confirm)\n"
        "- The project: 'What tech stack is used?', 'How does the pipeline work?', 'How do I roll back?'")
STACK = ("NetPulse is built with Flask (Python) and SQLite, served by Gunicorn behind Nginx. "
         "It runs in containers with Podman and is delivered by a GitLab CI/CD pipeline on a Linux VM.")
PIPELINE = ("Every push to GitLab starts a pipeline with three stages: test (pytest), build (container image) "
            "and deploy (podman-compose on the VM). A GitLab runner on the VM does the work. "
            "If a test fails, build and deploy never run.")
DEPLOY = ("After the tests and the build pass, the deploy stage restarts the app with podman-compose on the VM "
          "and checks the /health endpoint. If an earlier stage fails, the running version stays untouched.")
ROLLBACK = ("Use git revert on the bad commit and push, and the pipeline redeploys the fixed version. "
            "You can also restore a tagged stable version such as noc-ui-stable.")
TESTS = ("pytest runs automated tests in the test stage. A failing test stops the pipeline, "
         "so broken code is never deployed.")
TYPES = {"base station": "Base Station", "router": "Router", "switch": "Switch",
         "firewall": "Firewall", "server": "Server"}
STATUS_WORDS = {"Offline": ("offline", "down", "unreachable"),
                "Online": ("online", "healthy"),
                "Maintenance": ("maintenance",)}

def has(msg, *words):
    return any(re.search(r"\b" + re.escape(w) + r"\b", msg) for w in words)

def find_status(msg):
    for s, words in STATUS_WORDS.items():
        if has(msg, *words):
            return s
    return None

def find_type(msg):
    for key, val in TYPES.items():
        if re.search(r"\b" + key + r"(es|s)?\b", msg):
            return val
    return None

def find_location(msg, devices):
    locs = sorted({(d["location"] or "").strip() for d in devices if (d["location"] or "").strip()},
                  key=len, reverse=True)
    for loc in locs:
        if loc.lower() in msg:
            return loc
    return None

def find_device(msg, devices):
    for d in sorted(devices, key=lambda x: len(x["name"]), reverse=True):
        if d["name"].lower() in msg:
            return d
    for d in devices:
        if re.search(r"(?<![\d.])" + re.escape(d["ip"]) + r"(?![\d.])", msg):
            return d
    return None

def filt(devices, status, loc, dtype):
    out = devices
    if status:
        out = [d for d in out if d["status"] == status]
    if loc:
        out = [d for d in out if (d["location"] or "").strip().lower() == loc.lower()]
    if dtype:
        out = [d for d in out if d["dtype"] == dtype]
    return out

def describe(status, loc, dtype, n=2):
    s = ((status + " ") if status else "") + ((dtype + " ") if dtype else "") + ("device" if n == 1 else "devices")
    if loc:
        s += " in " + loc
    return s

def dev_view(d):
    return {"id": d["id"], "name": d["name"], "ip": d["ip"], "status": d["status"],
            "location": d["location"], "dtype": d["dtype"]}

def list_reply(title, devs, limit=10):
    reply = title if devs else "No matching devices."
    if len(devs) > limit:
        reply += " Showing %d of %d." % (limit, len(devs))
    return {"reply": reply, "devices": [dev_view(d) for d in devs[:limit]]}

def assistant_reply(message):
    msg = (message or "").lower().strip()
    devices = all_devices()
    if not msg:
        return {"reply": HELP}
    status = find_status(msg)
    dev = find_device(msg, devices)
    loc = find_location(msg, devices)
    dtype = find_type(msg)

    if has(msg, "mark", "set", "change", "put", "move", "update") and status:
        if not dev:
            return {"reply": "Which device? Use its exact name, for example BLR-CORE-RTR-01."}
        if dev["status"] == status:
            return {"reply": "%s is already %s." % (dev["name"], status)}
        return {"reply": "Change %s from %s to %s? Please confirm." % (dev["name"], dev["status"], status),
                "pending": {"device_id": dev["id"], "status": status}}
    if has(msg, "help", "what can you do", "commands"):
        return {"reply": HELP}
    if has(msg, "tech stack", "stack", "technology", "technologies", "built with", "built using"):
        return {"reply": STACK}
    if has(msg, "rollback", "roll back", "revert", "undo"):
        return {"reply": ROLLBACK}
    if has(msg, "version", "build", "commit", "release"):
        v = version_info()
        return {"reply": "NetPulse %s, build %s, running since %s UTC. Deployed by the GitLab CI/CD pipeline."
                         % (v["app"], v["commit"], v["started"].replace("T", " ").replace("+00:00", ""))}
    if has(msg, "deploy", "deployed", "deployment"):
        return {"reply": DEPLOY}
    if has(msg, "pipeline", "ci/cd", "cicd", "gitlab", "runner"):
        return {"reply": PIPELINE}
    if has(msg, "test", "tests", "testing", "pytest"):
        return {"reply": TESTS}
    if has(msg, "attention", "problem", "problems", "issue", "issues", "alert", "alerts",
           "critical", "incident", "incidents"):
        plist = [d for d in filt(devices, None, loc, dtype) if d["status"] != "Online"]
        return list_reply("%d device(s) need attention:" % len(plist), plist)
    if has(msg, "health"):
        st = compute_stats(devices)
        return {"reply": "Network health is %d%% (%d of %d devices are online)."
                         % (st["health"], st["online"], st["total"])}
    if has(msg, "recent", "recently", "history", "activity", "changed", "audit"):
        acts = recent_activity(5)
        if not acts:
            return {"reply": "No changes recorded yet."}
        lines = ["- %s %s: %s" % (a["ts"][11:16], a["action"], a["detail"]) for a in acts]
        return {"reply": "Latest changes (UTC):\n" + "\n".join(lines)}
    if has(msg, "summary", "overview", "report", "insights"):
        st = compute_stats(devices)
        head = "%d devices: %d online, %d offline, %d in maintenance." % (
            st["total"], st["online"], st["offline"], st["maintenance"])
        return {"reply": head + "\n" + "\n".join(build_insights(devices))}
    if dev:
        return {"reply": "%s (%s) is %s. Type: %s, location: %s." % (
                    dev["name"], dev["ip"], dev["status"], dev["dtype"] or "Other", dev["location"] or "-"),
                "devices": [dev_view(dev)]}
    if has(msg, "how many", "count", "number", "total"):
        out = filt(devices, status, loc, dtype)
        if not (status or loc or dtype):
            st = compute_stats(devices)
            return {"reply": "%d devices in total: %d online, %d offline, %d in maintenance." % (
                        st["total"], st["online"], st["offline"], st["maintenance"])}
        return {"reply": "%d %s." % (len(out), describe(status, loc, dtype, len(out)))}
    if status or loc or dtype:
        out = filt(devices, status, loc, dtype)
        return list_reply("%d %s:" % (len(out), describe(status, loc, dtype, len(out))), out)
    return {"reply": "Sorry, I did not understand that.\n" + HELP}

# ---------- CSV export (for Power BI / Excel) ----------
def csv_cell(v):
    s = "" if v is None else str(v)
    if s[:1] in ("=", "+", "-", "@"):
        s = "'" + s
    return s

def csv_response(filename, header, rows):
    buf = io.StringIO()
    w = csv.writer(buf)
    w.writerow(header)
    for r in rows:
        w.writerow([csv_cell(x) for x in r])
    return Response(buf.getvalue(), mimetype="text/csv",
                    headers={"Content-Disposition": "attachment; filename=" + filename})

@app.route("/")
def index():
    return render_template("index.html", state=build_state())

@app.route("/add", methods=["POST"])
def add():
    name, ip, dtype, loc, status = clean(request.form)
    if name and valid_ip(ip):
        try:
            with get_db() as c:
                c.execute("INSERT INTO devices (name, ip, dtype, location, status) VALUES (?,?,?,?,?)",
                          (name, ip, dtype, loc, status))
                log(c, "Added", "%s (%s)" % (name, ip))
                take_snapshot(c)
        except sqlite3.IntegrityError:
            pass
    return redirect("/")

@app.route("/edit/<int:did>", methods=["POST"])
def edit(did):
    name, ip, dtype, loc, status = clean(request.form)
    if name and valid_ip(ip):
        try:
            with get_db() as c:
                old = c.execute("SELECT status FROM devices WHERE id=?", (did,)).fetchone()
                if old:
                    c.execute("UPDATE devices SET name=?, ip=?, dtype=?, location=?, status=? WHERE id=?",
                              (name, ip, dtype, loc, status, did))
                    if old["status"] != status:
                        log(c, "Status changed", "%s: %s -> %s" % (name, old["status"], status))
                    else:
                        log(c, "Edited", name)
                    take_snapshot(c)
        except sqlite3.IntegrityError:
            pass
    return redirect("/")

@app.route("/delete/<int:did>", methods=["POST"])
def delete(did):
    with get_db() as c:
        row = c.execute("SELECT name, ip FROM devices WHERE id=?", (did,)).fetchone()
        if row:
            c.execute("DELETE FROM devices WHERE id=?", (did,))
            log(c, "Deleted", "%s (%s)" % (row["name"], row["ip"]))
            take_snapshot(c)
    return redirect("/")

MAX_IMPORT_ROWS = 500

def import_devices(text, filename="upload.csv"):
    reader = csv.DictReader(io.StringIO(text))
    if not reader.fieldnames:
        return None, "The file is empty."
    cols = {(f or "").strip().lower(): f for f in reader.fieldnames}
    name_col = cols.get("name")
    ip_col = cols.get("ip") or cols.get("ip_address") or cols.get("ip address")
    if not name_col or not ip_col:
        return None, "The CSV needs at least the columns: name, ip (optional: type, location, status)."
    type_col = cols.get("type") or cols.get("dtype")
    loc_col = cols.get("location")
    status_col = cols.get("status")
    added = dup = bad = rows = 0
    problems = []
    with get_db() as c:
        for row in reader:
            rows += 1
            if rows > MAX_IMPORT_ROWS:
                problems.append("Stopped after %d rows (limit)." % MAX_IMPORT_ROWS)
                break
            name = (row.get(name_col) or "").strip()
            ip = (row.get(ip_col) or "").strip()
            dtype = (row.get(type_col) or "").strip() if type_col else ""
            loc = (row.get(loc_col) or "").strip() if loc_col else ""
            status = (row.get(status_col) or "").strip().capitalize() if status_col else "Online"
            if status not in STATUSES:
                status = "Online"
            if not name or not valid_ip(ip):
                bad += 1
                if len(problems) < 5:
                    problems.append("Row %d: invalid name or IP (%s)." % (rows + 1, ip or "empty"))
                continue
            try:
                c.execute("INSERT INTO devices (name, ip, dtype, location, status) VALUES (?,?,?,?,?)",
                          (name[:80], ip, dtype[:40], loc[:60], status))
                added += 1
            except sqlite3.IntegrityError:
                dup += 1
        if added or dup or bad:
            log(c, "Import", "%s: %d added, %d duplicate, %d invalid" % (filename[:60], added, dup, bad))
            take_snapshot(c)
    return {"added": added, "duplicates": dup, "invalid": bad, "problems": problems}, None

@app.route("/import", methods=["POST"])
def import_csv():
    f = request.files.get("file")
    if not f or not f.filename:
        return jsonify(error="Choose a CSV file first."), 400
    try:
        text = f.read().decode("utf-8-sig")
    except UnicodeDecodeError:
        return jsonify(error="The file must be a UTF-8 CSV."), 400
    summary, err = import_devices(text, f.filename)
    if err:
        return jsonify(error=err), 400
    return jsonify(summary)

@app.errorhandler(413)
def too_large(e):
    return jsonify(error="The file is too large (limit 256 KB)."), 413

@app.route("/api/assistant", methods=["POST"])
def api_assistant():
    data = request.get_json(silent=True) or {}
    conf = data.get("confirm")
    if isinstance(conf, dict):
        try:
            did = int(conf.get("device_id"))
        except (TypeError, ValueError):
            return jsonify(reply="That confirmation was not valid."), 400
        status = conf.get("status")
        if status not in STATUSES:
            return jsonify(reply="That status is not valid."), 400
        with get_db() as c:
            row = c.execute("SELECT name, status FROM devices WHERE id=?", (did,)).fetchone()
            if not row:
                return jsonify(reply="That device no longer exists."), 404
            c.execute("UPDATE devices SET status=? WHERE id=?", (status, did))
            log(c, "Assistant", "%s: %s -> %s" % (row["name"], row["status"], status))
            take_snapshot(c)
        return jsonify(reply="Done. %s is now %s." % (row["name"], status), changed=True)
    return jsonify(assistant_reply(str(data.get("message", ""))[:300]))

@app.route("/export/devices.csv")
def export_devices():
    rows = [(d["id"], d["name"], d["ip"], d["dtype"], d["location"], d["status"]) for d in all_devices()]
    return csv_response("netpulse_devices.csv", ["id", "name", "ip", "type", "location", "status"], rows)

@app.route("/export/activity.csv")
def export_activity():
    with get_db() as c:
        rs = c.execute("SELECT ts, action, detail FROM activity ORDER BY id DESC").fetchall()
    return csv_response("netpulse_activity.csv", ["timestamp_utc", "action", "detail"],
                        [(r["ts"], r["action"], r["detail"]) for r in rs])

@app.route("/api/state")
def api_state():
    return jsonify(build_state())

@app.route("/api/stats")
def api_stats():
    return jsonify(compute_stats(all_devices()))

@app.route("/metrics")
def metrics():
    st = compute_stats(all_devices())
    up = (datetime.now(timezone.utc) - STARTED).total_seconds()
    lines = [
        "# HELP netpulse_devices Number of devices by status.",
        "# TYPE netpulse_devices gauge",
        'netpulse_devices{status="online"} %d' % st["online"],
        'netpulse_devices{status="offline"} %d' % st["offline"],
        'netpulse_devices{status="maintenance"} %d' % st["maintenance"],
        "# HELP netpulse_devices_total Total number of devices.",
        "# TYPE netpulse_devices_total gauge",
        "netpulse_devices_total %d" % st["total"],
        "# HELP netpulse_network_health_percent Percentage of devices that are online.",
        "# TYPE netpulse_network_health_percent gauge",
        "netpulse_network_health_percent %d" % st["health"],
        "# HELP netpulse_uptime_seconds Seconds since the app started.",
        "# TYPE netpulse_uptime_seconds gauge",
        "netpulse_uptime_seconds %d" % up,
        "# HELP netpulse_app_info Application version information.",
        "# TYPE netpulse_app_info gauge",
        'netpulse_app_info{version="%s",commit="%s"} 1' % (APP_VERSION, read_commit()),
    ]
    return Response("\n".join(lines) + "\n", content_type="text/plain; version=0.0.4; charset=utf-8")

@app.route("/health")
def health():
    return jsonify(status="ok")
