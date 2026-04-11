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
from athletehub.mcp.trail_tools import (
    trail_technical_section_detector as trail_technical_section_detector_tool,
)
from athletehub.mcp.trail_tools import trail_climb_efficiency as trail_climb_efficiency_tool
from athletehub.mcp.trail_tools import trail_downhill_risk as trail_downhill_risk_tool
from athletehub.mcp.trail_tools import trail_hiking_ratio as trail_hiking_ratio_tool
from athletehub.mcp.trail_tools import trail_cutoff_risk as trail_cutoff_risk_tool
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
    def trail_technical_section_detector(
        gpx_path: str | None = None,
        activity_id: int | None = None,
        grade_threshold: float = 15.0,
        min_section_length_m: float = 50.0,
    ) -> dict:
        """Detect technical sections (steep slopes, rocky terrain) from a GPX file or activity records."""

        return trail_technical_section_detector_tool(
            gpx_path=gpx_path,
            activity_id=activity_id,
            grade_threshold=grade_threshold,
            min_section_length_m=min_section_length_m,
        )

    @server.tool()
    def trail_climb_efficiency(
        activity_id: int,
        climb_grade_threshold: float = 5.0,
    ) -> dict:
        """Analyse climbing efficiency: VAM, HR zones, cadence patterns."""

        return trail_climb_efficiency_tool(
            activity_id=activity_id,
            climb_grade_threshold=climb_grade_threshold,
        )

    @server.tool()
    def trail_downhill_risk(
        activity_id: int,
        descent_grade_threshold: float = -5.0,
    ) -> dict:
        """Score downhill risk based on cadence, HR drift, speed variability, and slope."""

        return trail_downhill_risk_tool(
            activity_id=activity_id,
            descent_grade_threshold=descent_grade_threshold,
        )

    @server.tool()
    def trail_hiking_ratio(
        race_distance_km: float,
        race_elevation_gain_m: float,
        days: int = 180,
        hiking_pace_threshold_min_per_km: float = 9.0,
    ) -> dict:
        """Predict run/hike ratio for a race based on training history."""

        return trail_hiking_ratio_tool(
            race_distance_km=race_distance_km,
            race_elevation_gain_m=race_elevation_gain_m,
            days=days,
            hiking_pace_threshold_min_per_km=hiking_pace_threshold_min_per_km,
        )

    @server.tool()
    def trail_cutoff_risk(
        race_distance_km: float,
        race_elevation_gain_m: float,
        cutoff_time_minutes: float,
        intermediate_cutoffs: list[dict] | None = None,
        days: int = 180,
        expected_temperature_c: float | None = None,
    ) -> dict:
        """Predict whether the athlete risks missing race cutoffs."""

        return trail_cutoff_risk_tool(
            race_distance_km=race_distance_km,
            race_elevation_gain_m=race_elevation_gain_m,
            cutoff_time_minutes=cutoff_time_minutes,
            intermediate_cutoffs=intermediate_cutoffs,
            days=days,
            expected_temperature_c=expected_temperature_c,
        )

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
