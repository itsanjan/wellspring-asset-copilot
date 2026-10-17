"""In-process adapter: the real Copilot over a fresh copy of the database, called like the HTTP API would be."""
import os
import shutil
import sqlite3
import tempfile

from wellspring.agent import Copilot

from .harness import to_reply

SOURCE = os.environ.get("WELLSPRING_DB", "var/wellspring.db")
_state = {}


def reset(client=None, source=None):
    """Start from a clean database (no feedback, no conversations). Tests pass `client` to script the model
    and `source` to point at a database they built."""
    path = os.path.join(tempfile.mkdtemp(), "e2e.db")
    shutil.copy(source or SOURCE, path)
    con = sqlite3.connect(path)
    names = {n: i for i, n in con.execute("SELECT user_id, name FROM users")}
    con.close()
    _state.update(db=path, copilot=Copilot(path, client=client), names=names, conversations={})


def ask(user, message, session):
    if "copilot" not in _state:
        reset()
    state = _state
    answer = state["copilot"].ask(state["names"][user], message, state["conversations"].get(session))
    state["conversations"][session] = answer["conversation_id"]
    return to_reply(answer)
