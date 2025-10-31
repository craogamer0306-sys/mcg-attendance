from flask import Flask, render_template, redirect, url_for, request, flash, jsonify, current_app
from flask_sqlalchemy import SQLAlchemy
from flask_login import LoginManager, login_user, login_required, logout_user, current_user, UserMixin
from werkzeug.security import generate_password_hash, check_password_hash
from datetime import datetime, date, time, timedelta
from dotenv import load_dotenv
import os, requests, math

load_dotenv()

APP_NAME = os.getenv("APP_NAME", "MCG Attendance Portal")
app = Flask(__name__, template_folder="templates")
app.config["SECRET_KEY"] = os.getenv("SECRET_KEY", "dev-key")

# Database config
db_url = os.getenv("DATABASE_URL", "sqlite:///mcg_local.db")
if db_url.startswith("postgres://"):
    db_url = db_url.replace("postgres://", "postgresql://", 1)
app.config["SQLALCHEMY_DATABASE_URI"] = db_url
app.config["SQLALCHEMY_TRACK_MODIFICATIONS"] = False
db = SQLAlchemy(app)

# Login manager
login_manager = LoginManager(app)
login_manager.login_view = "login"

# Models
class User(db.Model, UserMixin):
    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(120), nullable=False)
    email = db.Column(db.String(120), unique=True, nullable=False)
    password_hash = db.Column(db.String(255), nullable=False)
    role = db.Column(db.String(20), default="EMPLOYEE")
    employee_id = db.Column(db.String(40), unique=True, nullable=False)
    active = db.Column(db.Boolean, default=True)

    def set_password(self, pw):
        self.password_hash = generate_password_hash(pw)

    def check_password(self, pw):
        return check_password_hash(self.password_hash, pw)


class Attendance(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer, db.ForeignKey('user.id'), nullable=False)
    date = db.Column(db.Date, nullable=False, default=date.today)
    check_in = db.Column(db.DateTime)
    status = db.Column(db.String(20))
    office_name = db.Column(db.String(50))
    inside = db.Column(db.Boolean, default=False)
    user = db.relationship('User', backref='attendances')


@login_manager.user_loader
def load_user(uid):
    try:
        return db.session.get(User, int(uid))
    except Exception:
        return None

# Helpers
def parse_office_time():
    h, m = map(int, os.getenv("OFFICE_START_HHMM", "09:30").split(":"))
    return time(h, m)

def compute_status(dt):
    start = parse_office_time()
    grace = int(os.getenv("GRACE_MINUTES", "10"))
    mins = dt.hour * 60 + dt.minute
    startm = start.hour * 60 + start.minute
    return "Present" if mins <= startm + grace else ("Half Day" if mins > 720 else "Late")

# Offices (edit lat/lon/radius as needed)
OFFICES = [
    {"name": "Champadanga", "lat": 22.8394628, "lon": 87.9730338, "radius": 200},
    {"name": "Baruipara", "lat": 22.764719, "lon": 88.243307, "radius": 200},
]

def haversine(lat1, lon1, lat2, lon2):
    R = 6371000.0
    phi1, phi2 = math.radians(lat1), math.radians(lat2)
    dphi = math.radians(lat2 - lat1)
    dlambda = math.radians(lon2 - lon1)
    a = math.sin(dphi/2)**2 + math.cos(phi1)*math.cos(phi2)*math.sin(dlambda/2)**2
    return 2 * R * math.atan2(math.sqrt(a), math.sqrt(1 - a))

# Notion config (store actual tokens in Render env vars, not in .env in repo)
NOTION_TOKEN = os.getenv("NOTION_TOKEN")
NOTION_DB_ID = os.getenv("NOTION_DB_ID")
NOTION_HEADERS = {
    "Authorization": f"Bearer {NOTION_TOKEN}" if NOTION_TOKEN else "",
    "Notion-Version": "2022-06-28",
    "Content-Type": "application/json",
}

def notion_sync(emp_name: str, emp_id: str, when_dt, status: str, office_name=None, inside=False):
    if not NOTION_TOKEN or not NOTION_DB_ID:
        current_app.logger.debug("Notion credentials missing, skipping sync.")
        return False
    try:
        date_part = when_dt.date().isoformat()
        time_part = when_dt.strftime("%H:%M:%S")
        body = {
            "parent": {"database_id": NOTION_DB_ID},
            "properties": {
                "Employee Name": {"title": [{"text": {"content": emp_name}}]},
                "Employee ID": {"rich_text": [{"text": {"content": emp_id}}]},
                "Date": {"date": {"start": date_part}},
                "Time": {"rich_text": [{"text": {"content": time_part}}]},
                "Status": {"select": {"name": (status or "Present").title()}},
                "Office Name": {"rich_text": [{"text": {"content": office_name or ""}}]},
                "Inside Office": {"checkbox": bool(inside)},
            },
        }
        r = requests.post("https://api.notion.com/v1/pages", headers=NOTION_HEADERS, json=body, timeout=10)
        if r.status_code not in (200, 201):
            current_app.logger.warning("Notion sync failed %s %s", r.status_code, r.text)
            return False
        return True
    except Exception as e:
        current_app.logger.exception("Notion sync error: %s", e)
        return False

def notion_task_sync(emp_name: str, emp_id: str, when_dt, title: str, description: str,
                     category: str = "", output_result: str = "", status: str = "", notes: str = ""):
    if not NOTION_TOKEN or not NOTION_DB_ID:
        current_app.logger.debug("Notion task sync skipped - creds missing")
        return False
    try:
        body = {
            "parent": {"database_id": NOTION_DB_ID},
            "properties": {
                "Task Title": {"title": [{"text": {"content": title or "(no title)"}}]},
                "Employee": {"rich_text": [{"text": {"content": emp_name}}]},
                "Date": {"date": {"start": when_dt.date().isoformat()}},
                "Task Description": {"rich_text": [{"text": {"content": description or ""}}]},
                "Category": {"rich_text": [{"text": {"content": category or ""}}]},
                "Output Result": {"rich_text": [{"text": {"content": output_result or ""}}]},
                "Status": {"rich_text": [{"text": {"content": status or ""}}]},
                "Notes": {"rich_text": [{"text": {"content": notes or ""}}]},
            }
        }
        r = requests.post("https://api.notion.com/v1/pages", headers=NOTION_HEADERS, json=body, timeout=10)
        if r.status_code not in (200, 201):
            current_app.logger.warning("Notion task sync failed %s %s", r.status_code, r.text)
            return False
        return True
    except Exception as e:
        current_app.logger.exception("Notion task sync error: %s", e)
        return False

# Routes
@app.route("/")
def root():
    return redirect(url_for("dashboard") if current_user.is_authenticated else url_for("login"))

@app.route("/login", methods=["GET","POST"])
def login():
    if request.method=="POST":
        eid=request.form["employee_id"].strip(); pw=request.form["password"]
        user=User.query.filter((User.employee_id==eid)|(User.email==eid)).first()
        if user and user.check_password(pw):
            login_user(user); flash("Welcome "+user.name,"success"); return redirect(url_for("dashboard"))
        flash("Invalid ID or password","danger")
    return render_template("login.html", app_name=APP_NAME)

@app.route("/dashboard")
@login_required
def dashboard():
    today=date.today()
    att=Attendance.query.filter_by(user_id=current_user.id,date=today).first()
    recent=Attendance.query.filter_by(user_id=current_user.id).order_by(Attendance.date.desc()).limit(7)
    return render_template("dashboard.html",user=current_user,todays_att=att,recent=recent,offices=OFFICES,app_name=APP_NAME)

@app.route("/checkin_geo", methods=["POST"])
@login_required
def checkin_geo():
    data = request.get_json(force=True)
    lat, lon = data.get("lat"), data.get("lon")
    if lat is None or lon is None:
        return jsonify({"error": "Missing coordinates"}), 400
    now = datetime.utcnow() + timedelta(hours=5, minutes=30)
    today = date.today()
    existing = Attendance.query.filter_by(user_id=current_user.id, date=today).first()
    if existing:
        return jsonify({"message": "Already checked in today"}), 200
    best_office, best_distance, inside = None, 1e9, False
    for o in OFFICES:
        dist = haversine(lat, lon, o["lat"], o["lon"])
        if dist < best_distance:
            best_office, best_distance = o["name"], dist
            inside = dist <= o["radius"]
    status = compute_status(now)
    record = Attendance(user_id=current_user.id, date=today, check_in=now, status=status, office_name=best_office, inside=inside)
    db.session.add(record); db.session.commit()
    notion_sync(current_user.name, current_user.employee_id, now, status, office_name=best_office, inside=inside)
    return jsonify({"status": status, "office_name": best_office, "distance_m": round(best_distance, 2), "inside": inside})

@app.route("/checkin", methods=["POST"])
@login_required
def checkin():
    today=date.today(); now=datetime.utcnow()+timedelta(hours=5,minutes=30)
    if Attendance.query.filter_by(user_id=current_user.id,date=today).first():
        flash("Already checked in today","info"); return redirect(url_for("dashboard"))
    status=compute_status(now)
    record=Attendance(user_id=current_user.id,date=today,check_in=now,status=status,office_name="Manual",inside=False)
    db.session.add(record); db.session.commit()
    notion_sync(current_user.name,current_user.employee_id,now,status,office_name="Manual",inside=False)
    flash(f"Checked in at {now.strftime('%H:%M')} — {status}","success")
    return redirect(url_for("dashboard"))

@app.route("/submit-task", methods=["GET","POST"])
@login_required
def submit_task():
    if request.method == "POST":
        title = request.form.get("title", "").strip()
        description = request.form.get("description", "").strip()
        category = request.form.get("category", "").strip()
        output_result = request.form.get("output_result", "").strip()
        status_val = request.form.get("status", "").strip()
        notes = request.form.get("notes", "").strip()
        if not title and not description:
            flash("Please enter a title or description for the task.", "warning")
            return redirect(url_for("submit_task"))
        when_dt = datetime.utcnow() + timedelta(hours=5, minutes=30)
        ok = notion_task_sync(current_user.name, current_user.employee_id, when_dt, title, description, category, output_result, status_val, notes)
        if ok:
            flash("Task submitted and synced to Notion ✅", "success")
        else:
            flash("Task submitted locally but Notion sync failed (check server logs).", "warning")
        return redirect(url_for("dashboard"))
    return render_template("submit_task.html", app_name=APP_NAME)

@app.route("/change-password", methods=["GET", "POST"])
@login_required
def change_password():
    if request.method == "POST":
        old = request.form.get("old_password", "")
        new = request.form.get("new_password", "")
        confirm = request.form.get("confirm_password", "")
        if not current_user.check_password(old):
            flash("Old password incorrect", "danger"); return redirect(url_for("change_password"))
        if new != confirm:
            flash("New passwords do not match", "danger"); return redirect(url_for("change_password"))
        current_user.set_password(new); db.session.commit()
        flash("Password changed successfully", "success"); return redirect(url_for("dashboard"))
    return render_template("change_password.html", app_name=APP_NAME)

@app.route("/logout")
@login_required
def logout():
    logout_user(); flash("Logged out", "info"); return redirect(url_for("login"))

# Auto-seed users
with app.app_context():
    db.create_all()
    if not User.query.first():
        admin=User(name="Nirjhar Ghanti",email="nirjhar@mcg.local",employee_id="MCG-O-0002",role="ADMIN")
        admin.set_password("mcg12345"); db.session.add(admin)
        for n,i in [("Chinmay Kumar Ghanti","MCG-O-0001"),("Megha Ghanti","MCG-O-0003"),("Jhumki Ghosh","MCG-O-0004"),
                    ("Tarun – Operations Head","MCG-E-0001"),("Diptanu – Admin & Query","MCG-E-0002"),
                    ("Sardhya – Operational Support","MCG-E-0003"),("Sourasis – Field Sales","MCG-E-0004")]:
            u=User(name=n,email=f"{i.lower()}@mcg.local",employee_id=i,role="EMPLOYEE")
            u.set_password("mcg12345"); db.session.add(u)
        db.session.commit()

if __name__=="__main__":
    app.run(host="0.0.0.0",port=int(os.getenv("PORT",5000)),debug=True)
