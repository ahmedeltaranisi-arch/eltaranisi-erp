"""The D1 write path: a whole document must cost one round-trip, not 36.

`db.transaction()` on D1 yields a DeferredConnection: writes are buffered and
flushed as one atomic `batch()` on the first read or at commit. These tests pin
that contract with a fake Cloudflare connection that counts requests, because
the real thing is only reachable from inside the deployment.
"""

import os
import sqlite3
import sys
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)
os.environ.setdefault("ERP_DB", "/tmp/deferred-test.db")

import db  # noqa: E402


class FakeCursor:
    def __init__(self, cur):
        self._cur = cur
        self.lastrowid = cur.lastrowid or 0
        self.rowcount = cur.rowcount

    def fetchone(self):
        return self._cur.fetchone()

    def fetchall(self):
        return self._cur.fetchall()

    def fetchmany(self, size=1):
        return self._cur.fetchmany(size)

    def __iter__(self):
        return iter(self._cur.fetchall())


class FakeD1:
    """Just enough of d1.D1Connection to prove the batching."""

    def __init__(self):
        self.sqlite = sqlite3.connect(":memory:")
        self.sqlite.row_factory = sqlite3.Row
        self.sqlite.execute("CREATE TABLE invoices (id INTEGER PRIMARY KEY, code TEXT)")
        self.sqlite.execute("CREATE TABLE lines (id INTEGER PRIMARY KEY, invoice_id INT, amount INT)")
        self.requests = 0          # round-trips that reached "the network"
        self.batches = []

    def execute(self, sql, params=()):
        self.requests += 1
        return FakeCursor(self.sqlite.execute(sql, tuple(params)))

    def batch(self, statements):
        self.requests += 1
        self.batches.append(list(statements))
        out = [FakeCursor(self.sqlite.execute(s, tuple(p))) for s, p in statements]
        return out

    def executemany(self, sql, seq):
        self.requests += 1
        return FakeCursor(self.sqlite.executemany(sql, [tuple(p) for p in seq]))

    def executescript(self, script):
        self.requests += 1
        self.sqlite.executescript(script)

    def commit(self):
        self.sqlite.commit()

    def rollback(self):
        self.sqlite.rollback()

    def close(self):
        pass


class DeferredWriteTest(unittest.TestCase):
    def setUp(self):
        self.fake = FakeD1()
        self._backend = db.BACKEND
        self._connect = db.connect
        db.BACKEND = "d1"
        db.connect = lambda *a, **k: self.fake

    def tearDown(self):
        db.BACKEND = self._backend
        db.connect = self._connect

    def test_a_document_save_is_one_round_trip(self):
        with db.transaction() as conn:
            cur = conn.execute("INSERT INTO invoices (code) VALUES (?)", ("INV-1",))
            invoice_id = cur.lastrowid            # forces exactly one flush
            for n in range(20):
                conn.execute("INSERT INTO lines (invoice_id, amount) VALUES (?, ?)",
                             (invoice_id, n))
        # the header insert has to land before its id is known, so the save
        # costs 2 requests (header, then the 20 lines) instead of 21
        self.assertEqual(len(self.fake.batches), 2)
        self.assertEqual(self.fake.requests, 2,
                         f"document save made {self.fake.requests} requests")
        self.assertEqual(len(self.fake.batches[1]), 20)
        rows = self.fake.sqlite.execute("SELECT COUNT(*) FROM lines").fetchone()[0]
        self.assertEqual(rows, 20)

    def test_reads_see_buffered_writes_in_order(self):
        with db.transaction() as conn:
            conn.execute("INSERT INTO invoices (code) VALUES (?)", ("INV-2",))
            row = conn.execute("SELECT COUNT(*) AS n FROM invoices").fetchone()
            self.assertEqual(row["n"], 1)
        self.assertEqual(self.fake.sqlite.execute("SELECT COUNT(*) FROM invoices").fetchone()[0], 1)

    def test_identical_reads_in_one_request_hit_the_database_once(self):
        with db.transaction() as conn:
            for _ in range(5):
                self.assertEqual(conn.execute("SELECT COUNT(*) AS n FROM invoices").fetchone()["n"], 0)
        self.assertEqual(self.fake.requests, 1)

    def test_a_failing_unit_sends_nothing(self):
        class Boom(Exception):
            pass

        with self.assertRaises(Boom):
            with db.transaction() as conn:
                conn.execute("INSERT INTO invoices (code) VALUES (?)", ("INV-3",))
                raise Boom()
        self.assertEqual(self.fake.requests, 0, "an unwritten buffer must not reach D1")
        self.assertEqual(self.fake.sqlite.execute("SELECT COUNT(*) FROM invoices").fetchone()[0], 0)

    def test_executemany_is_batched_too(self):
        with db.transaction() as conn:
            conn.executemany("INSERT INTO lines (invoice_id, amount) VALUES (?, ?)",
                             [(1, 5), (1, 6), (1, 7)])
        self.assertEqual(len(self.fake.batches[0]), 3)
        self.assertEqual(self.fake.sqlite.execute("SELECT COUNT(*) FROM lines").fetchone()[0], 3)

    def test_batched_write_results_report_lastrowid(self):
        with db.transaction() as conn:
            cur = conn.execute("INSERT INTO invoices (code) VALUES (?)", ("INV-4",))
            self.assertGreaterEqual(cur.lastrowid, 1)
            cur2 = conn.execute("INSERT INTO invoices (code) VALUES (?)", ("INV-5",))
            self.assertEqual(cur2.lastrowid, cur.lastrowid + 1)


if __name__ == "__main__":
    unittest.main(verbosity=2)
