from fastapi import FastAPI
from pydantic import BaseModel, Field

app = FastAPI(title="AutoCare Maintenance Service", version="0.1.0")


class MaintenanceRequest(BaseModel):
    mileage: int = Field(ge=0)
    vehicle_age_years: int = Field(ge=0)
    service_history: list[str] = Field(default_factory=list)


def determine_recommendation(request: MaintenanceRequest) -> tuple[str, str]:
    """Return an intentionally simple, deterministic recommendation."""
    if request.mileage >= 100_000 or request.vehicle_age_years >= 10:
        return "high", "Book a comprehensive inspection soon."
    if (
        request.mileage >= 50_000
        or request.vehicle_age_years >= 5
        or (request.mileage >= 15_000 and not request.service_history)
    ):
        return "medium", "Schedule a routine maintenance check."
    return "low", "Continue the regular manufacturer service schedule."


@app.get("/health")
def health() -> dict[str, str]:
    return {"service": "autocare-maintenance-service", "status": "ok"}


@app.post("/maintenance-analysis")
def analyze_maintenance(request: MaintenanceRequest) -> dict[str, str]:
    risk_level, recommendation = determine_recommendation(request)
    return {"riskLevel": risk_level, "recommendation": recommendation}
