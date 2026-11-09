import threading

import pytest

from app import hosxp as hosxp_module
from app.hosxp import Hosxp, HosxpError
from factories import settings


class FakeCursor:
    def __init__(self, conn):
        self.conn = conn

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False

    def execute(self, sql, params=None):
        if self.conn.broken:
            raise ValueError("PyMemoryView_FromBuffer(): info->buf must not be NULL")

    def fetchall(self):
        return [{"thread": threading.get_ident(), "conn": id(self.conn)}]


class FakeConn:
    def __init__(self):
        self.open, self.broken, self.closed = True, False, False

    def cursor(self):
        return FakeCursor(self)

    def ping(self, reconnect=False):
        pass

    def close(self):
        self.open, self.closed = False, True


@pytest.fixture
def made(monkeypatch):
    conns = []

    def connect(**kwargs):
        conns.append(FakeConn())
        return conns[-1]

    monkeypatch.setattr(hosxp_module.pymysql, "connect", connect)
    return conns


def test_each_thread_gets_its_own_connection(made):
    db = Hosxp(settings())
    seen, start = [], threading.Barrier(4)

    def work():
        start.wait()
        seen.append(db._select("SELECT 1")[0]["conn"])
        seen.append(db._select("SELECT 1")[0]["conn"])

    threads = [threading.Thread(target=work) for _ in range(4)]
    [t.start() for t in threads]
    [t.join() for t in threads]
    assert len(made) == 4 and len(set(seen)) == 4
    db.close()
    assert all(c.closed for c in made)


def test_broken_socket_reconnects_on_next_query(made):
    db = Hosxp(settings())
    db._select("SELECT 1")
    made[0].broken = True
    with pytest.raises(HosxpError):
        db._select("SELECT 1")
    assert made[0].closed
    assert db._select("SELECT 1")[0]["conn"] == id(made[1])
