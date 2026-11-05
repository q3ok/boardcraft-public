"""Local writer admission avoids competing SQLite busy-handler queues."""
from concurrent.futures import ThreadPoolExecutor
import threading
from boardcraft.server.storage import Store


def test_same_process_store_instances_admit_one_writer_connection(tmp_path):
    stores = [Store(tmp_path), Store(tmp_path)]
    active, peak = [0], [0]
    guard = threading.Lock()
    originals = [store.connect for store in stores]
    for store, original in zip(stores, originals):
        def connect(open_connection=original):
            with guard:
                active[0] += 1
                peak[0] = max(peak[0], active[0])
            try:
                threading.Event().wait(.01)
                return open_connection()
            finally:
                with guard:
                    active[0] -= 1
        store.connect = connect
    def write(index):
        with stores[index % 2].transaction(True) as db:
            db.execute('SELECT 1')
    with ThreadPoolExecutor(8) as pool:
        list(pool.map(write, range(8)))
    assert peak[0] == 1
    with stores[0].transaction(True):
        with stores[1].transaction() as db:
            assert db.execute('SELECT 1').fetchone() == (1,)
