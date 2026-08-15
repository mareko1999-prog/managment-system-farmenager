import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import db


def test_connection_lock_can_be_acquired_recursively():
    assert db._connection_lock.acquire(timeout=0.1)
    try:
        assert db._connection_lock.acquire(timeout=0.1)
        db._connection_lock.release()
    finally:
        db._connection_lock.release()
