from typing import List, Literal
from pydantic import BaseModel, Field


class Anomaly(BaseModel):
    type: Literal[
        "wash_trading",
        "latency_arbitrage",
        "copy_trading",
        "size_anomaly",
        "off_hours",
        "other",
    ] = Field(..., description="Categoría de la anomalía detectada")
    severity: Literal["low", "medium", "high", "critical"] = Field(
        ..., description="Severidad de la anomalía"
    )
    description: str = Field(..., description="Explicación concisa de la anomalía")
    evidence: str = Field(
        ..., description="Cita textual del log que sustenta la anomalía"
    )


class AuditVerdict(BaseModel):
    is_fraudulent: bool = Field(..., description="¿Se detectó fraude?")
    confidence: float = Field(..., ge=0.0, le=1.0)
    anomalies: List[Anomaly] = Field(default_factory=list)
    summary: str = Field(..., description="Resumen ejecutivo de la auditoría")
    recommended_action: Literal["approve", "review", "reject"]
