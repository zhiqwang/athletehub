PRAGMA foreign_keys = ON;

CREATE TABLE IF NOT EXISTS schema_metadata (
    key TEXT PRIMARY KEY,
    value TEXT NOT NULL,
    updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS athletes (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    external_id TEXT,
    name TEXT NOT NULL UNIQUE,
    birth_year INTEGER,
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS athlete_profiles (
    athlete_id INTEGER PRIMARY KEY REFERENCES athletes(id) ON DELETE CASCADE,
    sex TEXT,
    height_cm REAL,
    weight_kg REAL,
    max_hr_bpm REAL,
    resting_hr_bpm REAL,
    threshold_hr_bpm REAL,
    threshold_pace_s_per_km REAL,
    threshold_power_w REAL,
    vo2max REAL,
    timezone TEXT,
    units_preference TEXT NOT NULL DEFAULT 'metric',
    updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS athlete_devices (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    athlete_id INTEGER NOT NULL REFERENCES athletes(id) ON DELETE CASCADE,
    brand TEXT NOT NULL,
    model TEXT NOT NULL,
    serial_number TEXT,
    activated_at TEXT,
    retired_at TEXT,
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS athlete_gear (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    athlete_id INTEGER NOT NULL REFERENCES athletes(id) ON DELETE CASCADE,
    gear_type TEXT NOT NULL,
    name TEXT NOT NULL,
    brand TEXT,
    model TEXT,
    purchase_date TEXT,
    retired_at TEXT,
    accumulated_distance_m REAL NOT NULL DEFAULT 0,
    notes TEXT,
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS data_sources (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    source TEXT NOT NULL UNIQUE,
    account_label TEXT,
    sync_enabled INTEGER NOT NULL DEFAULT 1,
    sync_method TEXT NOT NULL DEFAULT 'manual_import',
    base_url TEXT,
    last_synced_at TEXT,
    notes TEXT,
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS source_files (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    source_id INTEGER NOT NULL REFERENCES data_sources(id),
    path TEXT NOT NULL,
    original_path TEXT,
    file_type TEXT NOT NULL,
    checksum TEXT NOT NULL UNIQUE,
    byte_size INTEGER,
    status TEXT NOT NULL DEFAULT 'imported',
    imported_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    notes TEXT
);

CREATE TABLE IF NOT EXISTS sync_runs (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    source_id INTEGER NOT NULL REFERENCES data_sources(id),
    started_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    finished_at TEXT,
    status TEXT NOT NULL,
    files_discovered INTEGER NOT NULL DEFAULT 0,
    activities_imported INTEGER NOT NULL DEFAULT 0,
    activities_skipped INTEGER NOT NULL DEFAULT 0,
    errors_count INTEGER NOT NULL DEFAULT 0,
    message TEXT
);

CREATE TABLE IF NOT EXISTS course_routes (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    athlete_id INTEGER REFERENCES athletes(id),
    source_id INTEGER REFERENCES data_sources(id),
    name TEXT NOT NULL,
    route_type TEXT,
    distance_m REAL,
    elevation_gain_m REAL,
    file_path TEXT,
    checksum TEXT,
    surface_notes TEXT,
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS course_route_points (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    route_id INTEGER NOT NULL REFERENCES course_routes(id) ON DELETE CASCADE,
    point_index INTEGER NOT NULL,
    latitude REAL,
    longitude REAL,
    altitude_m REAL,
    cumulative_distance_m REAL,
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    UNIQUE(route_id, point_index)
);

CREATE TABLE IF NOT EXISTS activities (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    athlete_id INTEGER NOT NULL REFERENCES athletes(id),
    source_id INTEGER REFERENCES data_sources(id),
    source_file_id INTEGER REFERENCES source_files(id),
    source_activity_id TEXT,
    sport TEXT NOT NULL,
    sport_subtype TEXT,
    workout_type TEXT,
    terrain_type TEXT,
    title TEXT,
    started_at TEXT NOT NULL,
    ended_at TEXT,
    timezone TEXT,
    distance_m REAL,
    moving_time_s INTEGER,
    elapsed_time_s INTEGER,
    elevation_gain_m REAL,
    elevation_loss_m REAL,
    average_speed_mps REAL,
    average_hr_bpm REAL,
    max_hr_bpm REAL,
    average_power_w REAL,
    normalized_power_w REAL,
    average_cadence_spm REAL,
    average_temperature_c REAL,
    calories_kcal REAL,
    training_load REAL,
    intensity_factor REAL,
    perceived_effort INTEGER,
    lap_count INTEGER,
    start_lat REAL,
    start_lon REAL,
    end_lat REAL,
    end_lon REAL,
    device_name TEXT,
    route_id INTEGER REFERENCES course_routes(id),
    load_source TEXT,
    checksum TEXT,
    notes TEXT,
    raw_payload TEXT,
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    UNIQUE (source_id, source_activity_id)
);

CREATE INDEX IF NOT EXISTS idx_activities_started_at ON activities(started_at);
CREATE INDEX IF NOT EXISTS idx_activities_sport ON activities(sport);
CREATE INDEX IF NOT EXISTS idx_activities_source_activity ON activities(source_id, source_activity_id);

CREATE TABLE IF NOT EXISTS activity_files (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    activity_id INTEGER NOT NULL REFERENCES activities(id) ON DELETE CASCADE,
    file_type TEXT NOT NULL,
    path TEXT NOT NULL,
    checksum TEXT,
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS activity_laps (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    activity_id INTEGER NOT NULL REFERENCES activities(id) ON DELETE CASCADE,
    lap_index INTEGER NOT NULL,
    started_at TEXT,
    total_time_s INTEGER,
    distance_m REAL,
    calories_kcal REAL,
    average_hr_bpm REAL,
    max_hr_bpm REAL,
    average_cadence_spm REAL,
    UNIQUE(activity_id, lap_index)
);

CREATE TABLE IF NOT EXISTS activity_splits (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    activity_id INTEGER NOT NULL REFERENCES activities(id) ON DELETE CASCADE,
    split_index INTEGER NOT NULL,
    distance_m REAL,
    elapsed_time_s INTEGER,
    moving_time_s INTEGER,
    avg_pace_s_per_km REAL,
    avg_hr_bpm REAL,
    elevation_gain_m REAL,
    UNIQUE(activity_id, split_index)
);

CREATE TABLE IF NOT EXISTS activity_records (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    activity_id INTEGER NOT NULL REFERENCES activities(id) ON DELETE CASCADE,
    sample_index INTEGER NOT NULL,
    recorded_at TEXT,
    elapsed_time_s INTEGER,
    distance_m REAL,
    latitude REAL,
    longitude REAL,
    altitude_m REAL,
    heart_rate_bpm REAL,
    cadence_spm REAL,
    speed_mps REAL,
    power_w REAL,
    temperature_c REAL,
    grade_pct REAL,
    UNIQUE(activity_id, sample_index)
);

CREATE INDEX IF NOT EXISTS idx_activity_records_activity_time
    ON activity_records(activity_id, sample_index);

CREATE TABLE IF NOT EXISTS activity_best_efforts (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    activity_id INTEGER NOT NULL REFERENCES activities(id) ON DELETE CASCADE,
    metric_type TEXT NOT NULL,
    distance_m REAL,
    duration_s INTEGER,
    value REAL,
    unit TEXT,
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS activity_training_effects (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    activity_id INTEGER NOT NULL UNIQUE REFERENCES activities(id) ON DELETE CASCADE,
    aerobic_effect REAL,
    anaerobic_effect REAL,
    training_focus TEXT,
    local_efficiency REAL,
    source TEXT,
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS activity_zone_times (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    activity_id INTEGER NOT NULL REFERENCES activities(id) ON DELETE CASCADE,
    zone_type TEXT NOT NULL,
    zone_number INTEGER NOT NULL,
    seconds INTEGER NOT NULL,
    ratio_pct REAL,
    UNIQUE(activity_id, zone_type, zone_number)
);

CREATE TABLE IF NOT EXISTS activity_fueling (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    activity_id INTEGER NOT NULL UNIQUE REFERENCES activities(id) ON DELETE CASCADE,
    carbs_g REAL,
    fluids_ml REAL,
    sodium_mg REAL,
    caffeine_mg REAL,
    notes TEXT,
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS activity_weather (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    activity_id INTEGER NOT NULL UNIQUE REFERENCES activities(id) ON DELETE CASCADE,
    source TEXT,
    temperature_c REAL,
    apparent_temperature_c REAL,
    humidity_pct REAL,
    dew_point_c REAL,
    wind_speed_kph REAL,
    wind_gust_kph REAL,
    precipitation_mm REAL,
    conditions TEXT,
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS health_daily (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    athlete_id INTEGER NOT NULL REFERENCES athletes(id),
    source_id INTEGER REFERENCES data_sources(id),
    day TEXT NOT NULL,
    resting_hr_bpm REAL,
    hrv_ms REAL,
    sleep_seconds INTEGER,
    stress_score REAL,
    body_battery REAL,
    training_readiness REAL,
    respiratory_rate_bpm REAL,
    spo2_pct REAL,
    skin_temperature_c REAL,
    weight_kg REAL,
    body_fat_pct REAL,
    vo2max REAL,
    fatigue_score REAL,
    soreness_score REAL,
    mood_score REAL,
    energy_score REAL,
    notes TEXT,
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    UNIQUE(athlete_id, source_id, day)
);

CREATE INDEX IF NOT EXISTS idx_health_daily_day ON health_daily(day);

CREATE TABLE IF NOT EXISTS sleep_sessions (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    athlete_id INTEGER NOT NULL REFERENCES athletes(id),
    source_id INTEGER REFERENCES data_sources(id),
    sleep_start TEXT NOT NULL,
    sleep_end TEXT NOT NULL,
    sleep_seconds INTEGER NOT NULL,
    deep_sleep_seconds INTEGER,
    light_sleep_seconds INTEGER,
    rem_sleep_seconds INTEGER,
    awake_seconds INTEGER,
    sleep_score REAL,
    respiratory_rate_bpm REAL,
    resting_hr_bpm REAL,
    notes TEXT,
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);

CREATE INDEX IF NOT EXISTS idx_sleep_sessions_start ON sleep_sessions(sleep_start);

CREATE TABLE IF NOT EXISTS body_metrics (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    athlete_id INTEGER NOT NULL REFERENCES athletes(id),
    source_id INTEGER REFERENCES data_sources(id),
    recorded_at TEXT NOT NULL,
    weight_kg REAL,
    body_fat_pct REAL,
    muscle_mass_kg REAL,
    skeletal_muscle_pct REAL,
    hydration_pct REAL,
    bmi REAL,
    notes TEXT,
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS threshold_snapshots (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    athlete_id INTEGER NOT NULL REFERENCES athletes(id),
    sport TEXT NOT NULL,
    effective_from TEXT NOT NULL,
    threshold_hr_bpm REAL,
    threshold_pace_s_per_km REAL,
    threshold_power_w REAL,
    max_hr_bpm REAL,
    source TEXT NOT NULL DEFAULT 'manual',
    notes TEXT,
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);

CREATE INDEX IF NOT EXISTS idx_threshold_snapshots_effective_from
    ON threshold_snapshots(athlete_id, sport, effective_from DESC);

CREATE TABLE IF NOT EXISTS zone_definitions (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    athlete_id INTEGER NOT NULL REFERENCES athletes(id),
    sport TEXT NOT NULL,
    zone_type TEXT NOT NULL,
    zone_number INTEGER NOT NULL,
    label TEXT,
    lower_bound REAL,
    upper_bound REAL,
    unit TEXT NOT NULL,
    source TEXT NOT NULL DEFAULT 'manual',
    effective_from TEXT,
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS load_daily (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    athlete_id INTEGER NOT NULL REFERENCES athletes(id),
    day TEXT NOT NULL,
    ctl REAL,
    atl REAL,
    tsb REAL,
    acute_load REAL,
    chronic_load REAL,
    fatigue_flag INTEGER NOT NULL DEFAULT 0,
    day_training_load REAL,
    seven_day_avg_load REAL,
    forty_two_day_avg_load REAL,
    base_fitness REAL,
    load_impact REAL,
    intensity_trend_pct REAL,
    monotony REAL,
    strain REAL,
    training_status TEXT,
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    UNIQUE(athlete_id, day)
);

CREATE INDEX IF NOT EXISTS idx_load_daily_day ON load_daily(day);

CREATE TABLE IF NOT EXISTS recovery_snapshots (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    athlete_id INTEGER NOT NULL REFERENCES athletes(id),
    day TEXT NOT NULL,
    source_id INTEGER REFERENCES data_sources(id),
    readiness_score REAL,
    sleep_score REAL,
    hrv_status TEXT,
    resting_hr_delta REAL,
    fatigue_score REAL,
    recommendation TEXT,
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS performance_snapshots (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    athlete_id INTEGER NOT NULL REFERENCES athletes(id),
    day TEXT NOT NULL,
    sport TEXT NOT NULL DEFAULT 'run',
    vo2max REAL,
    running_fitness_score REAL,
    marathon_level REAL,
    base_fitness REAL,
    load_impact REAL,
    intensity_trend_pct REAL,
    training_status TEXT,
    threshold_hr_bpm REAL,
    threshold_pace_s_per_km REAL,
    threshold_power_w REAL,
    predicted_5k_seconds INTEGER,
    predicted_10k_seconds INTEGER,
    predicted_half_marathon_seconds INTEGER,
    predicted_marathon_seconds INTEGER,
    source TEXT NOT NULL DEFAULT 'manual',
    notes TEXT,
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    UNIQUE(athlete_id, day, sport, source)
);

CREATE TABLE IF NOT EXISTS workout_library (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    athlete_id INTEGER REFERENCES athletes(id),
    source_id INTEGER REFERENCES data_sources(id),
    name TEXT NOT NULL,
    sport TEXT NOT NULL,
    description TEXT,
    estimated_load REAL,
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS workout_steps (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    workout_id INTEGER NOT NULL REFERENCES workout_library(id) ON DELETE CASCADE,
    step_index INTEGER NOT NULL,
    step_type TEXT NOT NULL,
    duration_value REAL,
    duration_unit TEXT,
    target_type TEXT,
    target_low REAL,
    target_high REAL,
    target_unit TEXT,
    notes TEXT,
    UNIQUE(workout_id, step_index)
);

CREATE TABLE IF NOT EXISTS training_blocks (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    athlete_id INTEGER NOT NULL REFERENCES athletes(id),
    name TEXT NOT NULL,
    phase TEXT NOT NULL,
    starts_on TEXT NOT NULL,
    ends_on TEXT NOT NULL,
    goal_race_id INTEGER,
    objective TEXT,
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS planned_sessions (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    athlete_id INTEGER NOT NULL REFERENCES athletes(id),
    training_block_id INTEGER REFERENCES training_blocks(id) ON DELETE SET NULL,
    workout_id INTEGER REFERENCES workout_library(id) ON DELETE SET NULL,
    scheduled_for TEXT NOT NULL,
    sport TEXT NOT NULL,
    title TEXT NOT NULL,
    target_duration_s INTEGER,
    target_distance_m REAL,
    target_load REAL,
    execution_status TEXT NOT NULL DEFAULT 'planned',
    notes TEXT,
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);

CREATE INDEX IF NOT EXISTS idx_planned_sessions_scheduled_for
    ON planned_sessions(scheduled_for);

CREATE TABLE IF NOT EXISTS planned_session_steps (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    planned_session_id INTEGER NOT NULL REFERENCES planned_sessions(id) ON DELETE CASCADE,
    step_index INTEGER NOT NULL,
    step_type TEXT NOT NULL,
    duration_value REAL,
    duration_unit TEXT,
    target_type TEXT,
    target_low REAL,
    target_high REAL,
    target_unit TEXT,
    notes TEXT,
    UNIQUE(planned_session_id, step_index)
);

CREATE TABLE IF NOT EXISTS race_events (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    athlete_id INTEGER REFERENCES athletes(id),
    route_id INTEGER REFERENCES course_routes(id),
    name TEXT NOT NULL,
    race_type TEXT NOT NULL,
    scheduled_for TEXT NOT NULL,
    priority TEXT,
    distance_km REAL,
    elevation_gain_m REAL,
    target_finish_seconds INTEGER,
    course_gpx_path TEXT,
    weather_notes TEXT,
    goal_notes TEXT,
    strategy_notes TEXT,
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);

CREATE INDEX IF NOT EXISTS idx_race_events_scheduled_for ON race_events(scheduled_for);

CREATE TABLE IF NOT EXISTS race_strategy_snapshots (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    race_event_id INTEGER NOT NULL REFERENCES race_events(id) ON DELETE CASCADE,
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    target_finish_seconds INTEGER,
    conservative_start_pace_s_per_km REAL,
    settled_pace_s_per_km REAL,
    finish_window_pace_s_per_km REAL,
    carbs_per_hour_g REAL,
    fluids_per_hour_ml REAL,
    notes TEXT
);

CREATE UNIQUE INDEX IF NOT EXISTS idx_athlete_devices_identity
    ON athlete_devices(athlete_id, brand, model, IFNULL(serial_number, ''));

CREATE UNIQUE INDEX IF NOT EXISTS idx_zone_definitions_identity
    ON zone_definitions(athlete_id, sport, zone_type, zone_number, IFNULL(effective_from, ''));

CREATE UNIQUE INDEX IF NOT EXISTS idx_recovery_snapshots_identity
    ON recovery_snapshots(athlete_id, day, IFNULL(source_id, 0));
