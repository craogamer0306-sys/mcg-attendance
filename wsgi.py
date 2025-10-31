# wsgi.py - robust entrypoint for Gunicorn
# tries: module 'app' with top-level 'app', then create_app(), then 'application'
import importlib, sys

def get_app():
    try:
        m = importlib.import_module("app")
    except Exception as e:
        raise RuntimeError(f"Failed to import module 'app': {e}")

    # 1) common case: top-level Flask app named "app"
    if hasattr(m, "app"):
        return getattr(m, "app")

    # 2) factory case: create_app()
    if hasattr(m, "create_app"):
        try:
            return m.create_app()
        except Exception as e:
            raise RuntimeError(f"app.create_app() failed: {e}")

    # 3) alternative name
    if hasattr(m, "application"):
        return getattr(m, "application")

    raise RuntimeError("No WSGI app found in 'app' module. Expected 'app' or 'create_app()'.")

# expose 'app' for gunicorn wsgi:app
app = get_app()

if __name__ == '__main__':
    # local debug run
    import os
    port = int(os.environ.get("PORT", 5000))
    app.run(host="0.0.0.0", port=port, debug=True)
