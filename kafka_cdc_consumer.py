#!/usr/bin/env python3
"""
kafka_cdc_consumer.py
----------------------
Pure-Python alternative to flink_cdc_consumer.py for the mandatory real-time
consumer on Kafka topic `ohlc.cdc`.

Why this exists: PyFlink proved fragile in this environment specifically
because of things unrelated to the actual logic —
  - the `apache-flink` Homebrew formula has NO `brew services` entry (unlike
    cassandra/kafka), so a Flink cluster only runs as long as something keeps
    `bin/start-cluster.sh`'s JobManager/TaskManager processes alive; they are
    not a persistent system service and were killed by an external SIGTERM
    within an hour of starting them, whereas Cassandra/Kafka (both proper
    `brew services`) kept running throughout.
  - needed a manually-downloaded Kafka SQL connector jar, an explicit
    JAVA_HOME, and -pyexec/-pyclientexec flags to make the client's Python
    driver resolvable at all.
  - a reserved-SQL-keyword bug (`close`) in the sibling flink_screens.py job
    that only surfaces at DDL-parse time.

None of that machinery is actually needed for what this job does: count CDC
mutation events per ticker in a tumbling window. That's ~40 lines of plain
confluent_kafka + a dict. This is the file market_store.py's CDC comment
should point at as the default/mandatory consumer; flink_cdc_consumer.py
stays available for when a real multi-node Flink deployment is worth its
operational overhead (e.g. joins across multiple high-volume topics).

Run:
    python3 kafka_cdc_consumer.py                  # runs forever, Ctrl-C to stop
    python3 kafka_cdc_consumer.py --window 30       # window size in seconds

Persistent background service: see kafka_cdc_consumer.plist (install like
com.umashankar.hiddengems.plist — `launchctl bootstrap gui/$(id -u) <plist>`).
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import time
from collections import defaultdict

from confluent_kafka import Consumer

KAFKA_BOOTSTRAP = os.environ.get("KAFKA_BOOTSTRAP", "localhost:9092")
TOPIC = "ohlc.cdc"


def parse_cdc(raw: bytes):
    """(ticker, n_bars) or None for a malformed record — never raises."""
    try:
        rec = json.loads(raw)
        return rec["ticker"], int(rec.get("n_bars", 0))
    except Exception:
        return None


def run(window_seconds: int = 30, max_messages: int | None = None, idle_exit_after: float | None = None):
    consumer = Consumer({
        "bootstrap.servers": KAFKA_BOOTSTRAP,
        "group.id": "kafka-cdc-consumer",
        "auto.offset.reset": "earliest",
    })
    consumer.subscribe([TOPIC])

    window_start = time.time()
    counts: dict[str, int] = defaultdict(int)
    n_seen = 0
    last_msg_at = time.time()
    print(f"[kafka-cdc] consuming topic={TOPIC} bootstrap={KAFKA_BOOTSTRAP} window={window_seconds}s", flush=True)

    try:
        while True:
            msg = consumer.poll(timeout=1.0)
            now = time.time()

            if msg is not None and not msg.error():
                parsed = parse_cdc(msg.value())
                if parsed:
                    ticker, n_bars = parsed
                    counts[ticker] += n_bars
                    n_seen += 1
                    last_msg_at = now
                    if max_messages and n_seen >= max_messages:
                        _flush(counts, window_start)
                        return

            if now - window_start >= window_seconds:
                _flush(counts, window_start)
                counts = defaultdict(int)
                window_start = now

            if idle_exit_after and (now - last_msg_at) > idle_exit_after and n_seen > 0:
                _flush(counts, window_start)
                print(f"[kafka-cdc] idle for {idle_exit_after}s, exiting (smoke-test mode)", flush=True)
                return
    except KeyboardInterrupt:
        _flush(counts, window_start)
    finally:
        consumer.close()


def _flush(counts: dict, window_start: float):
    if not counts:
        return
    for ticker, n in sorted(counts.items()):
        print(f"[kafka-cdc] ticker={ticker} bars_mutated_in_window={n}", flush=True)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--window", type=int, default=30, help="tumbling window size in seconds")
    ap.add_argument("--max-messages", type=int, default=None, help="exit after N messages (smoke test)")
    ap.add_argument("--idle-exit-after", type=float, default=None,
                    help="exit after this many idle seconds once at least one message has been seen (smoke test)")
    args = ap.parse_args()
    run(window_seconds=args.window, max_messages=args.max_messages, idle_exit_after=args.idle_exit_after)


if __name__ == "__main__":
    main()
