"""HTTP-Middleware-Stack für die BauLV-FastAPI-App.

Einzelne Middleware-Komponenten leben in eigenen Modulen
(``security_headers.py``, ggf. mehr) und werden in
``app.main:create_app`` an die FastAPI-Instanz angehängt.
"""
