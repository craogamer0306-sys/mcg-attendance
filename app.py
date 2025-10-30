from flask import Flask, jsonify, request
from flask_sqlalchemy import SQLAlchemy
from flask_login import LoginManager, UserMixin
from werkzeug.security import generate_password_hash, check_password_hash
from datetime import datetime, date, time
from dotenv import load_dotenv
import os, requests

# --- Load environment variables ---
load_dotenv()

APP_NAME = os.getenv("APP_NAME", "MCG Attendance Portal")
app = Flask(__name__)
app.config["SECRET_KEY"] = os.getenv("SECRET_KEY", "dev-key")

# --- Database Setup ---
db_url = os.getenv("DATABASE_URL", None)
if not db_url:
    db_url = "sqlite:///mcg_local.db"
if db_url.startswith("postgres://"):
    db_url = db_url.replace("postgres://", "postgresql://", 1)
app.config["SQLALCHEMY_DATABASE_URI"] = db_url
app.config["SQLALCHEMY_TRACK_MODIFICATIONS"] = False

db = SQLAlchemy(app)
login_manager = LoginManager(app)
login_manager.login_view = "login"

# --- Models ---
class User(db.Model, UserMixin):
    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(120), nullable=False)
    email = db.Column(db.String(120), unique=True, nullable=False)
    password_hash = db.Column(db.String(255), nullable=False)
    role = db.Column(db.String(20), nullable=False, default="EMPLOYEE")
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
    check_in = db.Column(db.DateTime, nullable=True)
    status = db.Column(db.String(20), nullable=True)
    remarks = db.Column(db.String(255), default="")
    user = db.relationship('User', backref='attendances')


@login_manager.user_loader
def load_user(user_id):
    try:
        return db.session.get(User, int(user_id))
    except Exception:
        return None


# --- Helper Functions ---
def parse_office_time():
    hhmm = os.getenv("OFFICE_START_HHMM", "09:30")
    try:
        h, m = [int(x) for x in hhmm.split(":")]
    except Exception:
        h, m = 9, 30
    return time(h, m)


def compute_status(check_in_dt: datetime):
    if check_in_dt is None:
        return "Absent"
    grace = int(os.getenv("GRACE_MINUTES", "10"))
    start = parse_office_time()
    minutes = check_in_dt.hour * 60 + check_in_dt.minute
    start_mins = start.hour * 60 + start.minute
    if minutes <= start_mins + grace:
        return "Present"
    elif minutes > 12 * 60:
        return "Half Day"
    else:
        return "Late"


# --- Notion Sync ---
NOTION_TOKEN = os.getenv("NOTION_TOKEN")
NOTION_DB_ID = os.getenv("NOTION_DB_ID")
NOTION_HEADERS = {
    "Authorization": f"Bearer {NOTION_TOKEN}" if NOTION_TOKEN else "",
    "Notion-Version": "2022-06-28",
    "Content-Type": "application/json",
}


def notion_sync(emp_name: str, emp_id: str, when_dt, status: str):
    if not NOTION_TOKEN or not NOTION_DB_ID:
        app.logger.debug("Notion credentials missing, skipping sync.")
        return False
    try:
        date_part = when_dt.date().isoformat()
        time_part = when_dt.strftime("%H:%M:%S")
        status_caps = (status or "Present").upper()
        body = {
            "parent": {"database_id": NOTION_DB_ID},
            "properties": {
                "Employee Name": {"title": [{"text": {"content": emp_name}}]},
                "Employee ID": {"rich_text": [{"text": {"content": emp_id}}]},
                "Date": {"date": {"start": date_part}},
                "Time": {"rich_text": [{"text": {"content": time_part}}]},
                "Status": {"select": {"name": status_caps}},
            },
        }
        r = requests.post("https://api.notion.com/v1/pages", headers=NOTION_HEADERS, json=body, timeout=10)
        if r.status_code not in (200, 201):
            app.logger.warning("Notion sync failed %s %s", r.status_code, r.text)
            return False
        return True
    except Exception as e:
        app.logger.exception("Notion sync error: %s", e)
        return False


# --- Routes ---
@app.route("/")
def home():
    return "MCG Attendance Portal ✅ (All Employees Auto-Loaded)"


@app.route("/scan/<emp_id>")
def scan(emp_id):
    token = request.args.get("token")
    if token != os.getenv("ATTENDANCE_API_TOKEN", "mcg_secret_2025"):
        return jsonify({"error": "unauthorized"}), 401

    user = User.query.filter_by(employee_id=emp_id).first()
    if not user:
        return jsonify({"error": f"Employee {emp_id} not found"}), 404

    now = datetime.now()
    today = date.today()
    existing = Attendance.query.filter_by(user_id=user.id, date=today).first()
    if existing:
        return jsonify({"message": f"{user.name} already checked in today"}), 200

    status = compute_status(now)
    record = Attendance(user_id=user.id, date=today, check_in=now, status=status)
    db.session.add(record)
    db.session.commit()

    # Sync with Notion
    notion_sync(user.name, emp_id, now, status)

    return jsonify({
        "message": f"✅ {user.name} checked in",
        "status": status,
        "time": now.strftime("%H:%M:%S")
    }), 200


# --- Auto-create + seed DB for Render Free Plan ---
with app.app_context():
    db.create_all()
    if not User.query.first():
        # Default admin
        admin = User(
            name="Nirjhar Ghanti",
            email="nirjhar@mcg.local",
            employee_id="MCG-O-00_


