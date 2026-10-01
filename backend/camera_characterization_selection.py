"""Pure candidate-selection helpers for camera characterization."""
from __future__ import annotations


def choose_common_bracket_command(candidates, excluded, sizes):
    """A single exact command must pass every discovered size; no mixing."""
    required = {str(n) for n in sizes}
    eligible = {key: trials for key, trials in candidates.items()
                if key not in excluded and required and set(trials) == required}
    if not eligible:
        return None
    return min(
        eligible,
        key=lambda key: (
            # Synchronization criterion: minimize the worst measured time from
            # preparation start to the first camera file notification.
            max(
                item["spec"].get(
                    "peak_prepare_to_first_file_ms",
                    item["spec"]["peak_capture_ms"],
                )
                for item in eligible[key].values()
            ),
            max(item["spec"]["peak_capture_ms"] for item in eligible[key].values()),
            sum(item["spec"]["peak_capture_ms"] for item in eligible[key].values()),
            key,
        ),
    )


def _capture_validation_state(expected, observed, error):
    """Classify one capture from automatic USB evidence only.

    ``confirmed`` means exactly the expected number of FILE_ADDED events arrived
    and the trigger path returned without error. ``runtime_error`` means all
    expected files were accounted for but the command itself failed.
    ``incomplete`` means the automatic count is wrong and the capture is rejected
    without asking the operator.
    """
    if observed == expected:
        return "confirmed" if error is None else "runtime_error"
    return "incomplete"


def _select_common_bracket_calibration_candidate(entries):
    """Choose one reliable primitive shared by calibration bracket sizes.

    A calibration primitive must be acceptable across *all* selected sizes.
    Selection therefore minimizes the aggregate complete operational duration
    across those sizes first.  Using only the worst absolute duration lets the
    largest bracket dominate the decision and can select a primitive that is
    catastrophically slower on the smaller calibration bracket because of
    measurement noise on the largest one.
    """
    reliable = [entry for entry in entries if entry["evidence"].reliable]
    if not reliable:
        return None
    return min(
        reliable,
        key=lambda entry: (
            entry["calibration_total_capture_ms"],
            entry["calibration_worst_capture_ms"],
            entry["calibration_total_prepare_to_first_file_ms"],
            entry["command_id"],
        ),
    )


def _select_bracket_candidate(entries):
    """Choose one reliable capture recipe for one bracket size only.

    Selection is based on complete operational duration, not on FILE_ADDED
    timing. FILE_ADDED is USB evidence that a frame exists; it is not an
    authoritative physical exposure-start timestamp.

    Among reliable exact-N/N candidates:
      1. lowest worst complete capture duration;
      2. lowest median complete capture duration;
      3. lowest prepare-to-first-file value only as a final tie-breaker;
      4. deterministic command id.

    Different bracket sizes may deliberately select different trigger
    primitives when that gives the shortest safe operational path.
    """
    reliable = [entry for entry in entries if entry["evidence"].reliable]
    if not reliable:
        return None
    return min(
        reliable,
        key=lambda entry: (
            entry["spec"]["peak_capture_ms"],
            entry["evidence"].median_ms,
            entry["spec"]["peak_prepare_to_first_file_ms"],
            entry["command_id"],
        ),
    )
