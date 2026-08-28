"""Camera-service adapter which fans IPC operations out across active rigs."""

from __future__ import annotations

from copy import copy
from concurrent.futures import Future, ThreadPoolExecutor
from dataclasses import dataclass, is_dataclass, replace
from datetime import datetime
from typing import Any, Callable, Iterable, Mapping

from backend.exposure_model import MaterializedExposure
from plugins.camera.base import CaptureResult
from scripts.camera_ipc_client import CameraIpcError, _sanitized_log_value
from services.camera_service import PreparedCapture


@dataclass(frozen=True)
class _PreparedRig:
    rig_id: int
    token_id: str


class FanoutCameraAdapter:
    """Present the camera-service interface for all currently active IPC rigs."""

    def __init__(
        self,
        ipc_client: Any,
        log_fn: Callable[[str], None] = print,
        atmos_enabled_by_rig: Mapping[int, bool] | None = None,
        atmos_intent_transformer: (
            Callable[[int, Any], tuple[Any, bool]] | None
        ) = None,
    ) -> None:
        self._ipc = ipc_client
        self._log = log_fn
        self._atmos_enabled_by_rig = atmos_enabled_by_rig or {}
        self._atmos_intent_transformer = atmos_intent_transformer
        # Keep the pool alive: creating one per operation adds avoidable latency at
        # precisely the point where the trigger is preparing or firing cameras.
        self._executor = ThreadPoolExecutor(max_workers=4)

    def close(self) -> None:
        self._executor.shutdown(wait=True)

    def initialize(
        self,
        *,
        aperture: str | None = None,
        iso: str | None = None,
        image_format: str = "RAW",
        white_balance: str = "Daylight",
    ) -> None:
        rig_ids = self._active_rig_ids()
        futures = self._submit_all(
            rig_ids,
            self._ipc.initialize,
            aperture=aperture,
            iso=iso,
            image_format=image_format,
            white_balance=white_balance,
        )
        self._collect("initialize", futures)

    def apply_phase_settings(
        self, aperture: str | None = None, iso: str | None = None
    ) -> None:
        rig_ids = self._active_rig_ids()
        futures = self._submit_all(
            rig_ids,
            self._ipc.apply_phase_settings,
            aperture=aperture,
            iso=iso,
        )
        self._collect("apply_phase_settings", futures)

    def prepare_capture(self, intent: Any) -> PreparedCapture:
        rig_ids = self._active_rig_ids()
        intents_by_rig = {
            rig_id: self._prepare_intent_for_rig(rig_id, intent) for rig_id in rig_ids
        }
        futures = {
            rig_id: self._executor.submit(
                self._ipc.prepare_capture, rig_id, intents_by_rig[rig_id]
            )
            for rig_id in rig_ids
        }
        results = self._collect("prepare_capture", futures)

        prepared_rigs: list[_PreparedRig] = []
        successful: list[dict[str, Any]] = []
        materialized: list[MaterializedExposure] = []
        for rig_id, result in results:
            token_id = result.get("token_id") if isinstance(result, dict) else None
            if not isinstance(token_id, str):
                self._log_failure(
                    "prepare_capture", rig_id, ValueError("missing prepared token_id")
                )
                continue
            prepared_rigs.append(_PreparedRig(rig_id, token_id))
            successful.append(result)
            plugin_name = result.get("plugin_name")
            exposures_s = result.get("exposures_s")
            request_id = result.get("request_id")
            iso_applied = result.get("iso_applied")
            corrections = result.get("corrections")
            warnings = result.get("warnings")
            if isinstance(plugin_name, str):
                materialized.append(
                    MaterializedExposure(
                        rig_id=rig_id,
                        plugin_name=plugin_name,
                        exposures_s=(
                            exposures_s
                            if isinstance(exposures_s, list)
                            and all(
                                isinstance(exposure, (int, float))
                                for exposure in exposures_s
                            )
                            else None
                        ),
                        iso_applied=(
                            iso_applied
                            if isinstance(iso_applied, str) and iso_applied
                            else None
                        ),
                        corrections=(
                            corrections
                            if isinstance(corrections, list)
                            and all(isinstance(item, str) for item in corrections)
                            else []
                        ),
                        warnings=(
                            warnings
                            if isinstance(warnings, list)
                            and all(isinstance(item, str) for item in warnings)
                            else []
                        ),
                        logical_request_id=(
                            request_id if isinstance(request_id, str) else None
                        ),
                    )
                )

        estimates = [
            result["estimated_total_s"]
            for result in successful
            if isinstance(result.get("estimated_total_s"), (int, float))
        ]
        planned_counts = [
            result["planned_count"]
            for result in successful
            if isinstance(result.get("planned_count"), int)
        ]
        representative = max(
            successful,
            key=lambda result: result.get("estimated_total_s") or 0.0,
            default={},
        )
        return PreparedCapture(
            token=tuple(prepared_rigs),
            estimated_total_s=max(estimates, default=None),
            exposures_s=representative.get("exposures_s"),
            planned_count=max(planned_counts, default=None),
            plugin_name="fanout",
            materialized=materialized,
        )

    def _prepare_intent_for_rig(self, rig_id: int, intent: Any) -> Any:
        copied_intent = replace(intent) if is_dataclass(intent) else copy(intent)
        if not self._atmos_enabled_by_rig.get(rig_id):
            return copied_intent

        original_origin = getattr(copied_intent, "origin", None)
        if self._atmos_intent_transformer is None:
            return copied_intent
        transformed_intent, added = self._atmos_intent_transformer(
            rig_id, copied_intent
        )

        origin = "atmos" if added else original_origin
        if is_dataclass(transformed_intent):
            return replace(transformed_intent, origin=origin)
        setattr(transformed_intent, "origin", origin)
        return transformed_intent

    def trigger_prepared(
        self, prepared: PreparedCapture, deadline: datetime | None = None
    ) -> CaptureResult:
        prepared_rigs = tuple(prepared.token)
        futures = {
            item.rig_id: self._executor.submit(
                self._ipc.trigger_prepared,
                item.rig_id,
                item.token_id,
                deadline=deadline,
            )
            for item in prepared_rigs
        }
        return self._capture_result(
            "trigger_prepared", self._collect("trigger_prepared", futures)
        )

    def shoot_speed_list(
        self,
        speeds: Iterable[str],
        photo_num_start: int = 0,
        deadline: datetime | None = None,
        slowest_override_seconds: float | None = None,
    ) -> CaptureResult:
        rig_ids = self._active_rig_ids()
        futures = self._submit_all(
            rig_ids,
            self._ipc.shoot_speed_list,
            list(speeds),
            photo_num_start=photo_num_start,
            deadline=deadline,
            slowest_override_seconds=slowest_override_seconds,
        )
        return self._capture_result(
            "shoot_speed_list", self._collect("shoot_speed_list", futures)
        )

    def _active_rig_ids(self) -> tuple[int, ...]:
        return tuple(self._ipc.list_active_camera_rigs()["rig_ids"])

    def _submit_all(
        self,
        rig_ids: Iterable[int],
        operation: Callable[..., Any],
        *args: Any,
        **kwargs: Any,
    ) -> dict[int, Future[Any]]:
        return {
            rig_id: self._executor.submit(operation, rig_id, *args, **kwargs)
            for rig_id in rig_ids
        }

    def _collect(
        self, operation: str, futures: dict[int, Future[Any]]
    ) -> list[tuple[int, Any]]:
        results = []
        for rig_id, future in futures.items():
            try:
                results.append((rig_id, future.result()))
            except Exception as exc:
                self._log_failure(operation, rig_id, exc)
        return results

    def _capture_result(
        self, operation: str, results: list[tuple[int, Any]]
    ) -> CaptureResult:
        frames = []
        planned = []
        for rig_id, result in results:
            if not isinstance(result, dict):
                self._log_failure(operation, rig_id, ValueError("invalid capture result"))
                continue
            if isinstance(result.get("frames"), int):
                frames.append(result["frames"])
            if isinstance(result.get("planned"), int):
                planned.append(result["planned"])
        return CaptureResult(
            frames=max(frames, default=0),
            planned=max(planned, default=0),
            detail="fanout",
        )

    def _log_failure(self, operation: str, rig_id: int, exc: Exception) -> None:
        if isinstance(exc, CameraIpcError) and exc.logged:
            return
        code = exc.code if isinstance(exc, CameraIpcError) else type(exc).__name__
        self._log(
            f"CAMERA_IPC_ERROR code={_sanitized_log_value(code)} "
            f"operation={_sanitized_log_value(operation)} "
            f"rig_id={_sanitized_log_value(rig_id)} "
            f"message={_sanitized_log_value(exc)}"
        )


__all__ = ["FanoutCameraAdapter"]
