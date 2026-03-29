"""
policy.py — Policy base class and concrete implementations.
"""
from __future__ import annotations
import logging
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import List
import yaml

logger = logging.getLogger(__name__)


@dataclass
class PatchInfo:
    filename: str
    size_bytes: int
    commit_hash: str = ""
    commit_message: str = ""


@dataclass
class FilterResult:
    accepted: List[PatchInfo] = field(default_factory=list)
    rejected: List[PatchInfo] = field(default_factory=list)
    accepted_bytes: int = 0
    rejected_bytes: int = 0
    reason: str = ""


class Policy(ABC):
    def __init__(self, config: dict):
        self.config = config
        self.group_name: str = config.get("group_name", "unknown")

    @abstractmethod
    def filter_patches(self, patches: List[PatchInfo], cycle_number: int = 0) -> FilterResult:
        pass

    @classmethod
    def from_yaml(cls, path: str) -> "Policy":
        with open(path) as f:
            cfg = yaml.safe_load(f)
        kind = cfg.get("policy", "unconstrained")
        if kind == "storage":
            return StoragePolicy(cfg)
        elif kind == "privacy":
            return PrivacyPolicy(cfg)
        else:
            return UnconstrainedPolicy(cfg)


class StoragePolicy(Policy):
    def __init__(self, config: dict):
        super().__init__(config)
        self.quota_bytes: int = config.get("initial_quota", 12 * 1024 * 1024)
        self.bandwidth_limit: int = config.get("bandwidth_limit", 5 * 1024 * 1024)
        self.carry_over_enabled: bool = config.get("carry_over_enabled", True)
        self.log_decisions: bool = config.get("log_filtering_decisions", True)
        self.accepted_this_cycle: int = 0

    @property
    def quota_remaining(self) -> int:
        return max(0, self.quota_bytes - self.accepted_this_cycle)

    def filter_patches(self, patches: List[PatchInfo], cycle_number: int = 0) -> FilterResult:
        result = FilterResult()
        sync_budget = min(self.bandwidth_limit, self.quota_remaining)
        accumulated = 0

        for patch in patches:
            if accumulated + patch.size_bytes <= sync_budget:
                result.accepted.append(patch)
                accumulated += patch.size_bytes
            else:
                result.rejected.append(patch)
                result.rejected_bytes += patch.size_bytes
                if self.log_decisions:
                    logger.info(
                        "[StoragePolicy cycle=%d] DROP %s (%.2f MB) — budget=%.2f MB",
                        cycle_number, patch.filename, patch.size_bytes / 1e6, sync_budget / 1e6,
                    )

        result.accepted_bytes = accumulated
        self.accepted_this_cycle += accumulated
        result.reason = (
            f"accepted {accumulated/1e6:.2f} MB / rejected {result.rejected_bytes/1e6:.2f} MB | "
            f"quota used {self.accepted_this_cycle/1e6:.2f}/{self.quota_bytes/1e6:.2f} MB"
        )
        return result

    def recalculate_quota(self, next_cycle_length_seconds: int) -> None:
        remaining = self.quota_remaining
        if self.carry_over_enabled and next_cycle_length_seconds > 0:
            new_quota = remaining // next_cycle_length_seconds
            logger.info(
                "[StoragePolicy] Carry-over: %.2f MB remaining → new quota %.2f MB",
                remaining / 1e6, new_quota / 1e6,
            )
            self.quota_bytes = new_quota
        else:
            self.quota_bytes = self.config.get("initial_quota", 12 * 1024 * 1024)
        self.accepted_this_cycle = 0


class PrivacyPolicy(Policy):
    def filter_patches(self, patches: List[PatchInfo], cycle_number: int = 0) -> FilterResult:
        return FilterResult(
            accepted=list(patches),
            accepted_bytes=sum(p.size_bytes for p in patches),
            reason="privacy filter: no-op stub — all patches accepted",
        )

    def apply_privacy_filter(self, patch_content: str) -> str:
        return patch_content  # no-op stub


class UnconstrainedPolicy(Policy):
    def filter_patches(self, patches: List[PatchInfo], cycle_number: int = 0) -> FilterResult:
        total = sum(p.size_bytes for p in patches)
        return FilterResult(
            accepted=list(patches),
            accepted_bytes=total,
            reason=f"unconstrained — accepted all {total/1e6:.2f} MB",
        )
