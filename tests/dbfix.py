"""A real database built from the sample data once per test run, and cheap per-test copies of it."""
import os
import shutil
import sqlite3
import tempfile

from wellspring import loader

SAMPLE = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "data", "sample-data"))
_BASE = None


def have_data():
    return os.path.isdir(SAMPLE)


def base_db():
    global _BASE
    if _BASE is None:
        _BASE = os.path.join(tempfile.mkdtemp(), "base.db")
        _, problems = loader.load([SAMPLE], _BASE)
        assert not problems, problems
    return _BASE


def copy_db():
    path = os.path.join(tempfile.mkdtemp(), "copy.db")
    shutil.copy(base_db(), path)
    return path


def connect(path):
    con = sqlite3.connect(path)
    con.row_factory = sqlite3.Row
    return con
