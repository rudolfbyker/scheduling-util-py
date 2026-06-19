# Scheduling utilities, written in Python

Python utilities for scheduling tasks in long-running processes, such as backups and health checks.

My use cases for these are specific enough to warrant writing my own utilities,
but not general enough that I would publish them on PyPI.
Wheels are available in the GitHub releases.

## Run a function a fixed number of times

```doctest
>>> from datetime import timedelta
>>> from scheduling_util import repeat
>>> events = []
>>> repeat(lambda: events.append("tick"), max_runs=3, sleep_duration=timedelta(0))
>>> events
['tick', 'tick', 'tick']

```

## Use a minimum-period rate limiter to limit the frequency of events

```doctest
>>> from datetime import timedelta
>>> from scheduling_util import RateLimiter
>>> limiter = RateLimiter(minimum_period=timedelta(seconds=30))
>>> limiter.ping_if_ready()
True
>>> limiter.is_ready()
False

```

## Use a rolling-window rate limiter to allow a burst of events

```doctest
>>> from datetime import timedelta
>>> from scheduling_util import RollingWindowRateLimiter
>>> limiter = RollingWindowRateLimiter(max_events=2, window=timedelta(minutes=1))
>>> limiter.ping_if_ready()
True
>>> limiter.ping_if_ready()
True
>>> limiter.ping_if_ready()
False

```

## Use the `schedule` Python function

You can schedule a Python function to run on a schedule in the current process by using the `schedule` function.

### Possible outcomes of `func`

Scheduled functions have four possible outcomes:

| Outcome            | `healthchecks.io`    | Slack                                | Wait before running again |
|--------------------|----------------------|--------------------------------------|---------------------------|
| Return `"success"` | Send success ping.   | Do nothing.                          | `--success-period`        |
| Return `"neutral"` | Do not ping.         | Do nothing.                          | `--neutral-period`        |
| Return `"failure"` | Send a failure ping. | Do nothing.                          | `--failure-period`        |
| Raise an exception | Send a failure ping. | Send message with exception details. | `--failure-period`        |

These outcomes are used internally by the [scheduler CLI](#use-the-scheduler-cli), too.

For type annotations, import `Outcome` from the package root:

```python
from scheduling_util import Outcome


def run_backup() -> Outcome:
    if upstream_export_is_not_ready():
        return "neutral"

    upload_backup()
    return "success"
```

### History

- The scheduler persists a `LastRunHistory` JSON file for each scheduled job.
- An attempt typically writes one `"started"` entry and one `"finished"` entry.
- `max_history_entries` limits raw history entries. 

## Use the scheduler CLI

### Overview of options

- The scheduler stores last-run state under `--cache-dir`.
- Use `--reset` when you want to ignore the cached state for a local test run.
- Add `--hc-*` options if you want the scheduler to contact `healthchecks.io`.
- The `--*-period` options control how long to wait after each outcome before running again.
- Use `--exit-codes-*` options if you need something other than the default exit code handling, 
  where `0` maps to "success" and everything else maps to "failure".

### Run Python code from the scheduler CLI

This will run in the same process.

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

### Run an external command from the scheduler CLI

This will run in a child process.

```shell
schedule \
  --name backup-job \
  --interval 1h \
  --success-period 1d \
  --neutral-period 30m \
  --failure-period 10m \
  --cache-dir ./.cache/scheduling-util \
  --heartbeat-path ./state/backup-heartbeat \
  subprocess \
  --exit-codes-success 0 \
  --exit-codes-neutral 75 \
  rsync -a ./source/ ./backup/
```

In this example, exit code `75` means "try again later" and follows
`--neutral-period` instead of counting as either a success or a failure.

### Invoke a Click Command from the scheduler CLI

This will run in the same process.

```shell
schedule \
  --name hello \
  --interval 1s \
  --success-period 10s \
  --cache-dir ./.cache/scheduling-util \
  --heartbeat-path ./state/hello-heartbeat \
  click-invoke \
  --command=scheduling_util:hello \
  --name=World
```
