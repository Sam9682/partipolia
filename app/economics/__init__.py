"""Partipolia economic simulation engine.

This package is intentionally independent from the web presentation layer and
from the database: it must not import FastAPI, SQLAlchemy or any ``app.api`` /
``app.web`` module. It computes deterministic, ``Decimal``-based estimates from an
immutable model snapshot.
"""
