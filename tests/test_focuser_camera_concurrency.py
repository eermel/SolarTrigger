from __future__ import annotations

import threading
from concurrent.futures import ThreadPoolExecutor

from backend.camera_worker import CameraWorker
from backend.focuser_worker import FocuserWorker


class DummyFocuserService:
    def __init__(self) -> None:
        self.move_started = threading.Event()
        self.release_move = threading.Event()
        self.close_calls = 0

    def move_to(self, _position, **_kwargs) -> str:
        self.move_started.set()

        # Le focuseur reste volontairement occupé jusqu'à ce que le test
        # le libère explicitement. Aucun timing système n'est utilisé pour
        # représenter l'occupation du périphérique.
        self.release_move.wait()

        return "focuser-finished"

    def close(self) -> None:
        self.close_calls += 1


class DummyCameraService:
    def __init__(self) -> None:
        self.close_calls = 0

    def shoot_speed_list(self, _speeds, **_kwargs) -> str:
        return "camera-finished"

    def close(self) -> None:
        self.close_calls += 1


def _worker_threads(device_kind: str, rig_id: int) -> list[threading.Thread]:
    name = f"{device_kind}-worker-r{rig_id}"
    return [
        thread
        for thread in threading.enumerate()
        if thread.name == name
    ]


def test_camera_shoots_run_while_focuser_is_busy_on_same_rig() -> None:
    rig_id = 601

    focuser_service = DummyFocuserService()
    camera_service = DummyCameraService()

    focuser_worker = FocuserWorker(
        rig_id=rig_id,
        service_factory=lambda: focuser_service,
    )
    camera_worker = CameraWorker(
        rig_id=rig_id,
        service_factory=lambda: camera_service,
    )

    focuser_worker.start()
    camera_worker.start()

    try:
        assert len(_worker_threads("focuser", rig_id)) == 1
        assert len(_worker_threads("camera", rig_id)) == 1

        with ThreadPoolExecutor(max_workers=4) as callers:
            focuser_future = callers.submit(
                focuser_worker.move_to,
                123,
            )

            assert focuser_service.move_started.wait(timeout=1.0)

            # Le focuseur est maintenant explicitement bloqué.
            assert not focuser_future.done()

            camera_futures = [
                callers.submit(camera_worker.test_photo, ["1/100"])
                for _ in range(3)
            ]

            # Les commandes caméra doivent terminer alors que le focuseur
            # est toujours occupé.
            assert [
                future.result(timeout=1.0)
                for future in camera_futures
            ] == [
                "camera-finished",
                "camera-finished",
                "camera-finished",
            ]

            assert not focuser_future.done()

            # On libère seulement maintenant le focuseur.
            focuser_service.release_move.set()

            assert (
                focuser_future.result(timeout=1.0)
                == "focuser-finished"
            )

    finally:
        # Garantit qu'un échec d'assertion ne laisse jamais le worker focuseur
        # bloqué dans DummyFocuserService.move_to().
        focuser_service.release_move.set()

        camera_worker.stop(timeout=1.0)
        focuser_worker.shutdown(timeout=1.0)

    assert not camera_worker.running
    assert not focuser_worker.running

    assert not _worker_threads("camera", rig_id)
    assert not _worker_threads("focuser", rig_id)

    assert camera_service.close_calls == 1
    assert focuser_service.close_calls == 1
