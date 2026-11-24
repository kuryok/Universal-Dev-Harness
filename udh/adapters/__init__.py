"""Contrato declarativo base para adaptadores de agente do UDH Core."""
from dataclasses import dataclass

from ..runtime import ContainerResult


@dataclass(frozen=True)
class AdapterCapabilities:
    name: str
    version: str
    stack_profiles: tuple[str, ...]
    supports_network_none: bool
    supports_uds_bridge: bool
    profiles: tuple[str, ...]
    supports_zero_tools_conformance: bool = True


@dataclass(frozen=True)
class AgentExecutionResult:
    adapter_name: str
    adapter_version: str
    profile: str
    ok: bool
    reason_code: str
    assistant_text: str
    stop_reason: str | None
    events_count: int
    config_integrity_ok: bool
    container_result: ContainerResult
    details: dict
