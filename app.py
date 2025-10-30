from flask import Flask, render_template, redirect, url_for, request, flash, jsonify
from flask_sqlalchemy import SQLAlchemy
from flask_login import LoginManager, login_user, login_required, logout_user, current_user, UserMixin
from werkzeug.security import generate_password_hash, check_password_hash
from datetime import datetime, date, time, timedelta
from dotenv import load_dotenv
import os, requests

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
    def set_password(self, pw): self.password_hash = generate_password_hash(pw)
    def check_password(self, pw): return check_password_hash(self.password_hash, pw)

class Attendance(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer, db.ForeignKey('user.id'), nullable=False)
    date = db.Column(db.Date, nullable=False, default=date.today)
    check_in = db.Column(db.DateTime)
    status = db.Column(db.String(20))
    user = db.relationship('User', backref='attendances')

@login_manager.user_loader
def load_user(uid):
    return db.session.get(User, int(uid))

# Helpers
def parse_office_time():
    h,m = map(int, os.getenv("OFFICE_START_HHMM","09:30").split(":"))
    return time(h,m)
def compute_status(dt):
    start=parse_office_time(); grace=int(os.getenv("GRACE_MINUTES","10"))
    mins=dt.hour*60+dt.minute; startm=start.hour*60+start.minute
    return "Present" if mins<=startm+grace else ("Half Day" if mins>720 else "Late")

NOTION_TOKEN=os.getenv("NOTION_TOKEN"); NOTION_DB_ID=os.getenv("NOTION_DB_ID")
def notion_sync(name,eid,dt,status):
    if not (NOTION_TOKEN and NOTION_DB_ID): return
    try:
        body={"parent":{"database_id":NOTION_DB_ID},
              "properties":{"Employee Name":{"title":[{"text":{"content":name}}]},
                            "Employee ID":{"rich_text":[{"text":{"content":eid}}]},
                            "Date":{"date":{"start":dt.date().isoformat()}},
                            "Time":{"rich_text":[{"text":{"content":dt.strftime('%H:%M:%S')}}]},
                            "Status":{"select":{"name":status}}}}
        requests.post("https://api.notion.com/v1/pages",
                      headers={"Authorization":f"Bearer {NOTION_TOKEN}",
                               "Notion-Version":"2022-06-28","Content-Type":"application/json"},
                      json=body,timeout=10)
    except Exception: pass

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
    today=date.today(); now=datetime.utcnow()+timedelta(hours=5,minutes=30)
    if Attendance.query.filter_by(user_id=current_user.id,date=today).first():
        flash("Already checked in today","info"); return redirect(url_for("dashboard"))
    status=compute_status(now)
    db.session.add(Attendance(user_id=current_user.id,date=today,check_in=now,status=status)); db.session.commit()
    notion_sync(current_user.name,current_user.employee_id,now,status)
    flash(f"Checked in at {now.strftime('%H:%M')} — {status}","success")
    return redirect(url_for("dashboard"))

@app.route("/logout")
@login_required
def logout():
    logout_user()
    flash("Logged out", "info")
    return redirect(url_for("login"))

# Auto-seed
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
        db.session.commit(); print("✅ seeded users")

if __name__=="__main__":
    app.run(host="0.0.0.0",port=int(os.getenv("PORT",5000)),debug=True)
