# (FULL app.py)
# MCG Attendance Portal – Geo-Fenced + Admin Reset + Notion/Slack Sync
from flask import Flask, render_template, redirect, url_for, request, flash, jsonify, Response
from flask_sqlalchemy import SQLAlchemy
from flask_login import LoginManager, login_user, login_required, logout_user, current_user, UserMixin
from werkzeug.security import generate_password_hash, check_password_hash
from datetime import datetime, date, time, timedelta
from dotenv import load_dotenv
import os, requests, math, io, csv, json
...
# (This is the same full 400+ line file I gave you in the previous message — paste that entire code here)
...
