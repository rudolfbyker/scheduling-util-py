# Scheduling utilities, written in Python

Python utilities for scheduling tasks in long-running processes, such as backups and health checks.

My use cases for these are specific enough to warrant writing my own utilities,
but not general enough that I would publish them on PyPI.
Wheels are available in the GitHub releases.

## Examples

Run a function a fixed number of times:

```python
>>> from datetime import timedelta
>>> from scheduling_util import repeat
>>> events = []
>>> repeat(lambda: events.append("tick"), max_runs=3, sleep_duration=timedelta(0))
>>> events
['tick', 'tick', 'tick']

```

Use a rate limiter to decide whether a notification or retry should run:

```python
>>> from datetime import timedelta
>>> from scheduling_util import RateLimiter
>>> limiter = RateLimiter(minimum_period=timedelta(seconds=30))
>>> limiter.ping_if_ready()
True
>>> limiter.is_ready()
False

```

Run Python code from the scheduler CLI:

```shell
schedule \
  --name local-maintenance \
  --interval 1h \
  --max-runs 1 \
  --cache-dir ./.cache/scheduling-util \
  --reset \
  py-exec \
  --code "from pathlib import Path; Path('last-run.txt').write_text('ok')"
```

Run an external command instead:

```shell
schedule \
  --name backup-job \
  --interval 1h \
  --success-period 1d \
  --failure-period 10m \
  --cache-dir ./.cache/scheduling-util \
  --heartbeat-file ./state/backup-heartbeat \
  subprocess rsync -a ./source/ ./backup/
```

The scheduler stores last-run state under `--cache-dir`; use `--reset` when you
want to ignore that state for a local test run. Add Healthchecks.io options such
as `--hc-ping-key`, `--hc-manage-key`, `--hc-timeout`, and `--hc-grace` only when
you want the scheduler to contact Healthchecks.io.
