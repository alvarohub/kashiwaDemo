# sensor: pivitals
# description: The node's own chip temperature / CPU / RAM (stand-in sensor
#   until real hardware is wired). Written by loggers/sensor_pivitals.py.
# location: the kami node itself (this node)
# units: temp=degC, cpu=%, ram=%
# logger: loggers/sensor_pivitals.py, interval=60s
# queryable: yes — `python3 loggers/sensor_pivitals.py` writes a fresh line

# (No samples yet — the logger appends one line a minute once the node runs.)
