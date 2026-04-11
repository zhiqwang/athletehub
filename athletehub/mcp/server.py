from __future__ import annotations

from dataclasses import asdict

from athletehub.db.migrate import migrate
from athletehub.mcp.health_tools import recovery_status as recovery_status_tool
from athletehub.mcp.query_tools import activity_details as activity_details_tool
from athletehub.mcp.query_tools import recent_activities as recent_activities_tool
from athletehub.mcp.race_tools import race_strategy as race_strategy_tool
from athletehub.mcp.race_tools import upcoming_races as upcoming_races_tool
from athletehub.mcp.trail_tools import trail_difficulty as trail_difficulty_tool
from athletehub.mcp.trail_tools import trail_profile as trail_profile_tool
from athletehub.mcp.training_tools import summarize_training_load
from athletehub.sync.coros_sync import sync_coros

try:
    from mcp.server.fastmcp import FastMCP
except ImportError as exc:  # pragma: no cover
    FastMCP = None
    IMPORT_ERROR = exc
else:
    IMPORT_ERROR = None


def build_server():
    if FastMCP is None:
        raise RuntimeError(
            "The `mcp` package is not installed. Run `uv sync` first."
        ) from IMPORT_ERROR

    server = FastMCP("athletehub")

    @server.tool()
    def training_summary(days: int = 7) -> dict:
        """Aggregate recent training volume, load, and sport mix."""

        return summarize_training_load(days)

    @server.tool()
    def recovery_status(days: int = 7) -> dict:
        """Estimate recovery based on recent health and load metrics."""

        return recovery_status_tool(days)

    @server.tool()
    def recent_activities(limit: int = 10, days: int = 30) -> list[dict]:
        """List recent activities in the selected date window."""

        return recent_activities_tool(limit=limit, days=days)

    @server.tool()
    def activity_details(activity_id: int) -> dict:
        """Fetch a single activity with split and file metadata."""

        return activity_details_tool(activity_id)

    @server.tool()
    def upcoming_races(limit: int = 5) -> list[dict]:
        """List upcoming races stored in the local database."""

        return upcoming_races_tool(limit=limit)

    @server.tool()
    def race_strategy(
        distance_km: float = 42.195,
        target_finish_minutes: float = 240.0,
        elevation_gain_m: float = 0.0,
        expected_temperature_c: float | None = None,
    ) -> dict:
        """Generate a pacing and fueling strategy for a race goal."""

        return race_strategy_tool(
            distance_km=distance_km,
            target_finish_minutes=target_finish_minutes,
            elevation_gain_m=elevation_gain_m,
            expected_temperature_c=expected_temperature_c,
        )

    @server.tool()
    def trail_difficulty(
        distance_km: float,
        elevation_gain_m: float,
        technicality: int = 3,
        max_altitude_m: float | None = None,
    ) -> dict:
        """Estimate route difficulty for a trail race or long run."""

        return trail_difficulty_tool(
            distance_km=distance_km,
            elevation_gain_m=elevation_gain_m,
            technicality=technicality,
            max_altitude_m=max_altitude_m,
        )

    @server.tool()
    def trail_profile(days: int = 90) -> dict:
        """Summarize recent trail-specific volume and climbing."""

        return trail_profile_tool(days=days)

    @server.tool()
    def import_coros_export(path: str, account_label: str | None = None) -> dict:
        """Import a COROS Training Hub TCX export file, ZIP, or directory."""

        return asdict(sync_coros(path, account_label=account_label))

    return server


def main() -> None:
    migrate()
    server = build_server()
    server.run()


if __name__ == "__main__":
    main()
