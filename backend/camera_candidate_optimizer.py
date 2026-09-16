"""Manufacturer-agnostic candidate qualification and selection."""
from dataclasses import dataclass, field
import statistics

@dataclass
class CandidateEvidence:
    candidate_id: str
    recipe: dict
    expected_trials: int
    durations_ms: list = field(default_factory=list)
    failures: list = field(default_factory=list)
    functional_ok: bool = False

    @property
    def reliable(self):
        return (self.functional_ok and not self.failures and self.expected_trials > 0
                and len(self.durations_ms) == self.expected_trials)

    @property
    def peak_ms(self):
        return max(self.durations_ms) if self.durations_ms else float("inf")

    @property
    def median_ms(self):
        return statistics.median(self.durations_ms) if self.durations_ms else float("inf")

    def compact(self):
        return {
            "candidate_id": self.candidate_id,
            "functional_ok": self.functional_ok,
            "reliable": self.reliable,
            "successful_trials": len(self.durations_ms),
            "expected_trials": self.expected_trials,
            "peak_ms": None if not self.durations_ms else round(self.peak_ms, 3),
            "median_ms": None if not self.durations_ms else round(self.median_ms, 3),
            "failure_count": len(self.failures),
        }

def select_best(evidence):
    proven = [item for item in evidence if item.reliable]
    if not proven:
        raise RuntimeError("No reliable candidate qualified")
    return min(proven, key=lambda item: (item.peak_ms, item.median_ms, item.candidate_id))

def compact_selection(action, evidence, selected):
    items = list(evidence)
    return {
        "action": action,
        "policy": "correctness_then_reliability_then_peak_then_median",
        "candidate_count": len(items),
        "qualified_count": sum(1 for item in items if item.reliable),
        "selected": selected.compact(),
    }
