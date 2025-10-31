# wsgi.py - import the Flask app from app.py for Gunicorn
from app import app

if __name__ == "__main__":
    # quick local run for debugging
    app.run(host="0.0.0.0", port=int(__import__('os').environ.get('PORT', 5000)))
