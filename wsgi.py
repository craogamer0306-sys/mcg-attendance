# wsgi.py - robust entrypoint for Gunicorn (safe fallback)
import importlib, sys

def get_app():
    try:
        m = importlib.import_module("app")
    except Exception as e:
        raise RuntimeError(f"Failed to import module 'app': {e}")

    # 1) If module has top-level 'app'
    if hasattr(m, "app"):
        return getattr(m, "app")

    # 2) If module provides create_app factory
    if hasattr(m, "create_app"):
        try:
            return m.create_app()
        except Exception as e:
            raise RuntimeError(f"app.create_app() failed: {e}")

    # 3) alternative name 'application'
    if hasattr(m, "application"):
        return getattr(m, "application")

    raise RuntimeError("No WSGI app found in 'app' module. Expected 'app' or 'create_app()'.")
    
app = get_app()

if __name__ == "__main__":
    import os
    port = int(os.environ.get("PORT", 5000))
    app.run(host="0.0.0.0", port=port, debug=True)
