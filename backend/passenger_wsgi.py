"""Entry point for MochaHost cPanel "Setup Python App" (Phusion Passenger).

Passenger speaks WSGI, FastAPI is ASGI, so a2wsgi adapts it.
In cPanel set: Application startup file = passenger_wsgi.py, Entry point = application.
"""
import os
import sys

sys.path.insert(0, os.path.dirname(__file__))

from a2wsgi import ASGIMiddleware  # noqa: E402

from app.main import app  # noqa: E402

application = ASGIMiddleware(app)
