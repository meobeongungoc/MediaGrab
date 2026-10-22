"""Queue process lifecycle tests without operating any user window."""
import queue
import sys
import threading
import time
from types import SimpleNamespace
from mediagrab import gui


def item():
    return {'job': {'kind':'single'}, 'cancel':threading.Event(), 'process':None, 'state':'waiting'}


def test_queue_runs_worker_and_reports_success(monkeypatch):
    monkeypatch.setattr(gui, 'command', lambda mode: [sys.executable, '-X', 'utf8', '-c', 'import json,sys; json.load(sys.stdin); print("PROGRESS:100"); print("Đã tải", flush=True)'])
    app = SimpleNamespace(auth_lock=threading.Lock(), events=queue.Queue())
    task = item()
    gui.App.run_job(app, task)
    events = list(app.events.queue)
    assert any(e[0] == 'line' and e[2] == 'PROGRESS:100' for e in events)
    assert events[-1] == ('done', task, 'done')


def test_cancelled_waiting_job_does_not_launch(monkeypatch):
    monkeypatch.setattr(gui.subprocess, 'Popen', lambda *a, **k: (_ for _ in ()).throw(AssertionError('Must not start')))
    app = SimpleNamespace(auth_lock=threading.Lock(), events=queue.Queue())
    task = item()
    task['cancel'].set()
    gui.App.run_job(app, task)
    assert app.events.get()[2] == 'cancelled'


def test_cancel_running_worker_terminates_only_child(monkeypatch):
    monkeypatch.setattr(gui, 'command', lambda mode: [sys.executable, '-X', 'utf8', '-c', 'import sys,time; sys.stdin.read(); time.sleep(30)'])
    app = SimpleNamespace(auth_lock=threading.Lock(), events=queue.Queue())
    app.cancel = lambda task: gui.App.cancel(app, task)
    task = item()
    thread = threading.Thread(target=gui.App.run_job, args=(app,task))
    thread.start()
    for _ in range(40):
        if task['process'] is not None:
            break
        time.sleep(.05)
    assert task['process'] is not None
    gui.App.cancel(app, task)
    thread.join(timeout=5)
    assert not thread.is_alive()
    assert task['process'].poll() is not None
    assert list(app.events.queue)[-1][2] == 'cancelled'
