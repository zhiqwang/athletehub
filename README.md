# AthleteHub

COROS-first 运动数据中台 + AI 训练助手（MCP Server）

AthleteHub 面向马拉松和越野跑者，目标是把训练数据留在本地，并让 AI 可以基于统一 schema 做训练负荷、恢复、赛事和路线分析。

当前仓库已经不是空模板，而是一个可运行的第一版系统：

- 本地 SQLite 数据中台
- 完整训练科学 schema
- COROS 官方 Training Hub 导出导入器
- MCP server 与基础训练分析工具

## 当前数据源策略

首个真实打通的数据源是 COROS。

- 已实现：导入 COROS Training Hub 导出的 `.tcx`、`.zip`
- 已实现：活动摘要、lap、采样点、最佳配速片段、派生训练负荷
- 已实现：导入后自动重建 `load_daily`、`performance_snapshots`
- 暂未实现：COROS 私有合作 API 直连

这个取舍是刻意的。COROS 官方公开资料提供了数据批量导出能力，也提供了 API 合作申请入口，但没有公开可直接对接的 endpoint 文档。因此当前版本优先落地官方导出导入链路，让仓库先具备可用的数据接入能力。

## 训练科学模型

当前 schema 已覆盖这些核心域：

- 运动员与档案：`athletes`、`athlete_profiles`、`athlete_devices`、`athlete_gear`
- 数据源与同步：`data_sources`、`source_files`、`sync_runs`
- 活动与样本：`activities`、`activity_laps`、`activity_splits`、`activity_records`
- 活动分析：`activity_best_efforts`、`activity_training_effects`、`activity_zone_times`
- 健康与恢复：`health_daily`、`sleep_sessions`、`body_metrics`、`recovery_snapshots`
- 阈值与分区：`threshold_snapshots`、`zone_definitions`
- 训练负荷与表现：`load_daily`、`performance_snapshots`
- 训练计划：`workout_library`、`workout_steps`、`training_blocks`、`planned_sessions`
- 路线与赛事：`course_routes`、`course_route_points`、`race_events`、`race_strategy_snapshots`

其中 `load_daily` 已支持本地派生字段：

- `ctl`
- `atl`
- `tsb`
- `day_training_load`
- `base_fitness`
- `load_impact`
- `intensity_trend_pct`
- `training_status`

## 目录结构

```text
athletehub/
├── README.md
├── .python-version
├── pyproject.toml
├── athletehub/
│   ├── __init__.py
│   ├── config.py
│   ├── db/
│   │   ├── __init__.py
│   │   ├── analytics.py
│   │   ├── db.py
│   │   ├── migrate.py
│   │   └── schema.sql
│   ├── mcp/
│   │   ├── __init__.py
│   │   ├── health_tools.py
│   │   ├── query_tools.py
│   │   ├── race_tools.py
│   │   ├── server.py
│   │   ├── trail_tools.py
│   │   └── training_tools.py
│   ├── sync/
│   │   ├── __init__.py
│   │   └── coros_sync.py
│   └── utils/
│       ├── __init__.py
│       ├── elevation.py
│       ├── gpx.py
│       ├── metrics.py
│       ├── tcx.py
│       └── weather.py
├── data/
│   └── raw/
└── examples/
    ├── coros_sample.tcx
    ├── mcp.json
    └── queries.md
```

## 快速开始

```bash
git clone <your-repo>
cd athletehub
uv sync

# 初始化数据库
uv run athletehub-migrate

# 导入 COROS 导出文件
uv run athletehub-coros-sync examples/coros_sample.tcx

# 启动 MCP server
uv run athletehub-mcp
```

## 常用命令

```bash
# 安装或更新依赖
uv sync

# 包含开发依赖一起同步
uv sync --group dev

# 迁移数据库
uv run athletehub-migrate

# 导入单个 TCX
uv run athletehub-coros-sync ~/Downloads/coros-run.tcx

# 导入 Training Hub 导出 zip
uv run athletehub-coros-sync ~/Downloads/coros-export.zip

# 导入目录下全部导出
uv run athletehub-coros-sync ~/Downloads/coros-export-dir --account-label main

# 重建派生训练负荷
uv run athletehub-rebuild-loads
```

## MCP 工具

当前 server 暴露的工具包括：

- `training_summary`
- `recovery_status`
- `recent_activities`
- `activity_details`
- `upcoming_races`
- `race_strategy`
- `trail_difficulty`
- `trail_profile`
- `import_coros_export`

`examples/mcp.json` 可直接作为 Claude Code / 兼容客户端配置参考：

```json
{
  "mcpServers": {
    "athletehub": {
      "command": "uv",
      "args": ["run", "athletehub-mcp"],
      "cwd": "."
    }
  }
}
```

## 下一步优先级

当前更合理的后续工作顺序：

1. 补充 COROS 日常恢复/睡眠导入格式
2. 实现 FIT 解析支持
3. 把阈值、分区和课程计划连接到更多 MCP 分析工具
4. 再决定是否接入需要合作审批的官方 API
