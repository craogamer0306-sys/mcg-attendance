from flask import Flask, render_template, redirect, url_for, request, flash, jsonify
from flask_sqlalchemy import SQLAlchemy
from flask_login import LoginManager, login_user, login_required, logout_user, current_user, UserMixin
from werkzeug.security import generate_password_hash, check_password_hash
from datetime import datetime, date, time, timedelta
from dotenv import load_dotenv
import os, requests
from functools import wraps

load_dotenv()
APP_NAME = os.getenv("APP_NAME", "MCG Attendance Portal")
app = Flask(__name__, template_folder="templates")
app.config["SECRET_KEY"] = os.getenv("SECRET_KEY", "dev-key")

# Database
db_url = os.getenv("DATABASE_URL", "sqlite:///mcg_local.db")
if db_url.startswith("postgres://"):
    db_url = db_url.replace("postgres://", "postgresql://", 1)
app.config["SQLALCHEMY_DATABASE_URI"] = db_url
app.config["SQLALCHEMY_TRACK_MODIFICATIONS"] = False
db = SQLAlchemy(app)

login_manager = LoginManager(app)
login_manager.login_view = "login"

# ------------------ MODELS ------------------
class User(db.Model, UserMixin):
    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(120), nullable=False)
    email = db.Column(db.String(120), unique=True, nullable=False)
    password_hash = db.Column(db.String(255), nullable=False)
    role = db.Column(db.String(20), default="EMPLOYEE")
    employee_id = db.Column(db.String(40), unique=True, nullable=False)
    active = db.Column(db.Boolean, default=True)

    def set_password(self, pw): self.password_hash = generate_password_hash(pw)
    def check_password(self, pw): return check_password_hash(self.password_hash, pw)

class Attendance(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer, db.ForeignKey('user.id'), nullable=False)
    date = db.Column(db.Date, nullable=False, default=date.today)
    check_in = db.Column(db.DateTime)
    status = db.Column(db.String(20))
    user = db.relationship('User', backref='attendances')

class Task(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer, db.ForeignKey('user.id'), nullable=False)
    date = db.Column(db.Date, default=date.today)
    title = db.Column(db.String(255))
    description = db.Column(db.Text)
    user = db.relationship('User', backref='tasks')

@login_manager.user_loader
def load_user(uid):
    return db.session.get(User, int(uid))

# ------------------ HELPERS ------------------
def parse_office_time():
    h,m = map(int, os.getenv("OFFICE_START_HHMM","09:30").split(":"))
    return time(h,m)

def compute_status(dt):
    start=parse_office_time(); grace=int(os.getenv("GRACE_MINUTES","10"))
    mins=dt.hour*60+dt.minute; startm=start.hour*60+start.minute
    return "Present" if mins<=startm+grace else ("Half Day" if mins>720 else "Late")

# ------------------ NOTION ------------------
NOTION_TOKEN = os.getenv("NOTION_TOKEN")
NOTION_DB_ID = os.getenv("NOTION_DB_ID")
NOTION_TASK_DB_ID = os.getenv("NOTION_TASK_DB_ID")
HEADERS = {
    "Authorization": f"Bearer {NOTION_TOKEN}" if NOTION_TOKEN else "",
    "Notion-Version": "2022-06-28",
    "Content-Type": "application/json",
}

def notion_sync_attendance(user, when_dt, status):
    if not (NOTION_TOKEN and NOTION_DB_ID): return
    try:
        body = {
            "parent": {"database_id": NOTION_DB_ID},
            "properties": {
                "Employee Name": {"title": [{"text": {"content": user.name}}]},
                "Employee ID": {"rich_text": [{"text": {"content": user.employee_id}}]},
                "Date": {"date": {"start": when_dt.date().isoformat()}},
                "Time": {"rich_text": [{"text": {"content": when_dt.strftime("%H:%M:%S")}}]},
                "Status": {"select": {"name": status}},
            },
        }
        requests.post("https://api.notion.com/v1/pages", headers=HEADERS, json=body, timeout=10)
    except Exception as e:
        app.logger.error(f"Notion attendance sync failed: {e}")

def notion_sync_task(user, title, desc, when_dt):
    if not (NOTION_TOKEN and NOTION_TASK_DB_ID): return
    try:
        body = {
            "parent": {"database_id": NOTION_TASK_DB_ID},
            "properties": {
                "Employee Name": {"title": [{"text": {"content": user.name}}]},
                "Employee ID": {"rich_text": [{"text": {"content": user.employee_id}}]},
                "Date": {"date": {"start": when_dt.date().isoformat()}},
                "Task Title": {"rich_text": [{"text": {"content": title}}]},
                "Task Description": {"rich_text": [{"text": {"content": desc}}]},
            },
        }
        requests.post("https://api.notion.com/v1/pages", headers=HEADERS, json=body, timeout=10)
    except Exception as e:
        app.logger.error(f"Notion task sync failed: {e}")

# ------------------ ROUTES ------------------
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
    return render_template("dashboard.html",user=current_user,todays_att=att,recent=recent,app_name=APP_NAME)

@app.route("/checkin", methods=["POST"])
@login_required
def checkin():
    today=date.today()
    now=datetime.utcnow()+timedelta(hours=5,minutes=30)
    if Attendance.query.filter_by(user_id=current_user.id,date=today).first():
        flash("Already checked in today","info"); return redirect(url_for("dashboard"))
    status=compute_status(now)
    rec=Attendance(user_id=current_user.id,date=today,check_in=now,status=status)
    db.session.add(rec); db.session.commit()
    notion_sync_attendance(current_user,now,status)
    flash(f"Checked in at {now.strftime('%H:%M')} — {status}","success")
    return redirect(url_for("dashboard"))

@app.route("/change-password", methods=["GET","POST"])
@login_required
def change_password():
    if request.method=="POST":
        old=request.form.get("old_password"); new=request.form.get("new_password"); confirm=request.form.get("confirm_password")
        if not current_user.check_password(old):
            flash("Old password incorrect","danger"); return redirect(url_for("change_password"))
        if new!=confirm:
            flash("Passwords do not match","danger"); return redirect(url_for("change_password"))
        current_user.set_password(new); db.session.commit()
        flash("Password updated successfully","success"); return redirect(url_for("dashboard"))
    return render_template("change_password.html",app_name=APP_NAME)

@app.route("/submit-task", methods=["GET","POST"])
@login_required
def submit_task():
    if request.method=="POST":
        title=request.form.get("title","").strip(); desc=request.form.get("description","").strip()
        if not title and not desc:
            flash("Please enter title or description","danger"); return redirect(url_for("submit_task"))
        t=Task(user_id=current_user.id,date=date.today(),title=title,description=desc)
        db.session.add(t); db.session.commit()
        now=datetime.utcnow()+timedelta(hours=5,minutes=30)
        notion_sync_task(current_user,title,desc,now)
        flash("Task submitted successfully","success")
        return redirect(url_for("dashboard"))
    return render_template("submit_task.html",app_name=APP_NAME)

@app.route("/logout")
@login_required
def logout():
    logout_user(); flash("Logged out","info"); return redirect(url_for("login"))

# ------------------ AUTO-SEED ------------------
with app.app_context():
    db.create_all()
    if not User.query.first():
        admin=User(name="Nirjhar Ghanti",email="nirjhar@mcg.local",
                   employee_id="MCG-O-0002",role="ADMIN")
        admin.set_password("mcg12345"); db.session.add(admin)
        for n,i in [("Chinmay Kumar Ghanti","MCG-O-0001"),
                    ("Megha Ghanti","MCG-O-0003"),
                    ("Jhumki Ghosh","MCG-O-0004"),
                    ("Tarun – Operations Head","MCG-E-0001"),
                    ("Diptanu – Admin & Query","MCG-E-0002"),
                    ("Sardhya – Operational Support","MCG-E-0003"),
                    ("Sourasis – Field Sales","MCG-E-0004")]:
            u=User(name=n,email=f"{i.lower()}@mcg.local",employee_id=i,role="EMPLOYEE")
            u.set_password("mcg12345"); db.session.add(u)
        db.session.commit()

if __name__=="__main__":
    app.run(host="0.0.0.0",port=int(os.getenv("PORT",5000)),debug=True)
