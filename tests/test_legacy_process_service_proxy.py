import flask_app.app as app_module


class FakeWorker:
    def __init__(self):
        self.calls = []

    def status(self):
        self.calls.append(("status",))
        return {"connected": True}

    def set_speed(self, value):
        self.calls.append(("set_speed", value))
        return {"speed": value}

    def move_to(self, position, wait=False):
        self.calls.append(("move_to", position, wait))
        return {"position": position}


class FakeRuntime:
    def __init__(self, worker):
        self.worker = worker
        self.reconciled = []

    def reconcile(self, config):
        self.reconciled.append(config)

    def get_for_rig(self, rig_id):
        assert rig_id == 1
        return self.worker


def test_legacy_mount_proxy_uses_rig1_process_runtime(monkeypatch):
    worker = FakeWorker()
    runtime = FakeRuntime(worker)
    config = {"rigs": [{"rig_id": 1}]}

    monkeypatch.setattr(
        app_module,
        "get_mount_worker_runtime",
        lambda **kwargs: runtime,
    )
    monkeypatch.setattr(
        app_module,
        "load_rig_configuration",
        lambda: config,
    )

    proxy = app_module._LegacyRig1ProcessServiceProxy("mount")

    assert proxy.status() == {"connected": True}
    assert proxy.set_speed(4) == {"speed": 4}
    assert worker.calls == [
        ("status",),
        ("set_speed", 4),
    ]
    assert runtime.reconciled == [config, config]


def test_legacy_focuser_proxy_uses_rig1_process_runtime(monkeypatch):
    worker = FakeWorker()
    runtime = FakeRuntime(worker)
    config = {"rigs": [{"rig_id": 1}]}

    monkeypatch.setattr(
        app_module,
        "get_focuser_worker_runtime",
        lambda **kwargs: runtime,
    )
    monkeypatch.setattr(
        app_module,
        "load_rig_configuration",
        lambda: config,
    )

    proxy = app_module._LegacyRig1ProcessServiceProxy("focuser")

    assert proxy.move_to(1234) == {"position": 1234}
    assert worker.calls == [
        ("move_to", 1234, False),
    ]
    assert runtime.reconciled == [config]


def test_legacy_proxy_fails_closed_without_rig1_worker(monkeypatch):
    runtime = FakeRuntime(None)

    monkeypatch.setattr(
        app_module,
        "get_mount_worker_runtime",
        lambda **kwargs: runtime,
    )
    monkeypatch.setattr(
        app_module,
        "load_rig_configuration",
        lambda: {"rigs": []},
    )

    proxy = app_module._LegacyRig1ProcessServiceProxy("mount")

    try:
        proxy.status()
    except RuntimeError as exc:
        assert str(exc) == "mount is not configured for rig 1"
    else:
        raise AssertionError("missing RIG 1 mount must fail closed")
