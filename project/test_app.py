import os, uuid
os.environ["DB_PATH"] = "/tmp/test_netpulse.db"
from app import app

def uid():
    return uuid.uuid4().hex[:8]

def rand_ip():
    b = uuid.uuid4().bytes
    return "10.%d.%d.%d" % (b[0], b[1], b[2])

def add_device(c, name, status="Online", dtype="Switch", location="Chennai"):
    c.post("/add", data={"name": name, "ip": rand_ip(), "dtype": dtype,
                         "location": location, "status": status})

def ask(c, message):
    return c.post("/api/assistant", json={"message": message}).json

def test_health():
    r = app.test_client().get("/health")
    assert r.status_code == 200 and r.json["status"] == "ok"

def test_home():
    assert app.test_client().get("/").status_code == 200

def test_stats_api():
    r = app.test_client().get("/api/stats")
    assert r.status_code == 200
    for key in ("total", "online", "offline", "maintenance", "health"):
        assert key in r.json

def test_add_device():
    name = "R-" + uid()
    c = app.test_client()
    c.post("/add", data={"name": name, "ip": rand_ip(), "dtype": "Router",
                         "location": "Bengaluru", "status": "Online"})
    assert name in c.get("/").get_data(as_text=True)

def test_invalid_ip_rejected():
    name = "BAD-" + uid()
    c = app.test_client()
    c.post("/add", data={"name": name, "ip": "not-an-ip", "dtype": "Router",
                         "location": "X", "status": "Online"})
    assert name not in c.get("/").get_data(as_text=True)

def test_state_api():
    r = app.test_client().get("/api/state")
    assert r.status_code == 200
    for key in ("devices", "stats", "alerts", "activity", "version"):
        assert key in r.json

def test_activity_logged_on_add():
    name = "ACT-" + uid()
    c = app.test_client()
    add_device(c, name)
    acts = c.get("/api/state").json["activity"]
    assert any(name in a["detail"] for a in acts)

def test_offline_device_raises_critical_alert():
    name = "OFF-" + uid()
    c = app.test_client()
    add_device(c, name, status="Offline", location="Jaipur")
    alerts = c.get("/api/state").json["alerts"]
    assert any(a["device"] == name and a["severity"] == "Critical" for a in alerts)

def test_version_info():
    v = app.test_client().get("/api/state").json["version"]
    assert v["app"] and v["commit"] and v["started"]

def test_insights_in_state():
    ins = app.test_client().get("/api/state").json["insights"]
    assert isinstance(ins, list) and len(ins) >= 1

def test_assistant_counts_offline_devices():
    c = app.test_client()
    add_device(c, "CNT-" + uid(), status="Offline")
    reply = ask(c, "How many devices are offline?")["reply"]
    assert reply[0].isdigit() and "Offline" in reply

def test_assistant_finds_device_by_name():
    name = "AST-" + uid()
    c = app.test_client()
    add_device(c, name, status="Offline")
    reply = ask(c, "is " + name + " down?")["reply"]
    assert name in reply and "Offline" in reply

def test_assistant_asks_before_changing_then_applies():
    name = "CFM-" + uid()
    c = app.test_client()
    add_device(c, name, status="Online")
    r = ask(c, "mark " + name + " as maintenance")
    assert "pending" in r
    done = c.post("/api/assistant", json={"confirm": r["pending"]}).json
    assert done.get("changed") is True
    devs = c.get("/api/state").json["devices"]
    assert [d["status"] for d in devs if d["name"] == name] == ["Maintenance"]

def test_assistant_answers_project_question():
    assert "Flask" in ask(app.test_client(), "What tech stack is used?")["reply"]

def test_assistant_rejects_bad_confirmations():
    c = app.test_client()
    assert c.post("/api/assistant", json={"confirm": {"device_id": 999999, "status": "Online"}}).status_code == 404
    assert c.post("/api/assistant", json={"confirm": {"device_id": "x", "status": "Online"}}).status_code == 400
    assert c.post("/api/assistant", json={"confirm": {"device_id": 1, "status": "Nope"}}).status_code == 400

def test_export_devices_csv():
    r = app.test_client().get("/export/devices.csv")
    assert r.status_code == 200 and r.mimetype == "text/csv"
    assert r.get_data(as_text=True).startswith("id,name,ip,type,location,status")

def test_download_button_on_devices_page():
    assert "/export/devices.csv" in app.test_client().get("/").get_data(as_text=True)

def test_metrics_endpoint_prometheus_format():
    r = app.test_client().get("/metrics")
    body = r.get_data(as_text=True)
    assert r.status_code == 200 and r.content_type.startswith("text/plain")
    assert "# TYPE netpulse_devices gauge" in body
    assert 'netpulse_devices{status="online"}' in body
    assert "netpulse_devices_total " in body
    assert "netpulse_network_health_percent " in body

def test_system_status_in_state():
    s = app.test_client().get("/api/state").json["system"]
    assert s["app"] == "OK" and s["database"] == "OK"
    assert s["version"] and s["uptime_seconds"] >= 0

def test_system_health_panel_on_dashboard():
    assert "System health" in app.test_client().get("/").get_data(as_text=True)

import io

def post_csv(c, text, filename="t.csv"):
    return c.post("/import", data={"file": (io.BytesIO(text.encode("utf-8")), filename)},
                  content_type="multipart/form-data")

def test_import_adds_valid_rows_and_skips_bad_ones():
    c = app.test_client()
    ip = rand_ip()
    text = ("name,ip,type,location,status\n"
            "IMP-%s,%s,Router,Pune,Offline\n"
            "BAD-%s,999.1.1.1,Router,Pune,Online\n"
            "DUP-%s,%s,Router,Pune,Online\n") % (uid(), ip, uid(), uid(), ip)
    j = post_csv(c, text).json
    assert (j["added"], j["invalid"], j["duplicates"]) == (1, 1, 1)

def test_import_is_logged_in_activity():
    c = app.test_client()
    text = "name,ip\nLOG-%s,%s\n" % (uid(), rand_ip())
    post_csv(c, text, filename="logcheck.csv")
    acts = c.get("/api/state").json["activity"]
    assert any(a["action"] == "Import" and "logcheck.csv" in a["detail"] for a in acts)

def test_import_requires_name_and_ip_columns():
    r = post_csv(app.test_client(), "foo,bar\n1,2\n")
    assert r.status_code == 400 and "name, ip" in r.json["error"]

def test_import_without_file_is_rejected():
    assert app.test_client().post("/import", data={}).status_code == 400

def test_import_button_on_devices_page():
    assert "Import CSV" in app.test_client().get("/").get_data(as_text=True)

def test_trend_in_state():
    t = app.test_client().get("/api/state").json["trend"]
    assert isinstance(t, list) and len(t) >= 1
    for key in ("ts", "online", "offline", "maintenance"):
        assert key in t[-1]

def test_snapshot_recorded_when_status_changes():
    c = app.test_client()
    name = "TRD-" + uid()
    add_device(c, name, status="Online")
    before = c.get("/api/state").json["trend"][-1]
    dev = [d for d in c.get("/api/state").json["devices"] if d["name"] == name][0]
    c.post("/edit/%d" % dev["id"], data={"name": name, "ip": dev["ip"], "dtype": dev["dtype"],
                                         "location": dev["location"], "status": "Offline"})
    after = c.get("/api/state").json["trend"][-1]
    assert after["offline"] == before["offline"] + 1
    assert after["online"] == before["online"] - 1

def test_trend_panel_on_dashboard():
    assert "Availability trend" in app.test_client().get("/").get_data(as_text=True)
