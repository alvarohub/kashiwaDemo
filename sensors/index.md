# SENSOR REPORT — the place reads itself

# description: Cross-sensor dashboard — maintained summary so the kami does

# NOT re-analyze raw logs on every question. Updated periodically (script

# today; the kami or a Level-0 analyst agent later). This is what the node

# _notices_ about itself.

# updated: never (no data yet)

# writer: by hand for now; later: a summary script or analyst agent

## Sensor inventory (what this node has)

| sensor     | file                 | what it measures                                  | logging params               |
| ---------- | -------------------- | ------------------------------------------------- | ---------------------------- |
| pivitals   | sensor_pivitals.md   | node CPU/RAM/chip temp                            | cron, 60 s, keeps 60 lines   |
| solarpower | sensor_solarpower.md | panel V, battery %, charging (DUMMY until INA219) | cron, 300 s, keeps 120 lines |

## Current state

(No sensor data yet. Once loggers write files here, summaries appear.)

## Trends

## Anomalies / things worth mentioning

## Notes from the kami
