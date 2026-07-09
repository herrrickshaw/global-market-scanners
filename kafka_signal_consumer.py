#!/usr/bin/env python3
"""
kafka_signal_consumer.py
-------------------------
Pure-Python alternative to flink_screens.py — same job (aggregate scan.signals
by signal type in a tumbling window), no PyFlink/JVM/Kafka-SQL-connector-jar
required. See kafka_cdc_consumer.py's docstring for why this repo now prefers
the plain-Kafka-consumer approach over PyFlink for these single-node,
low-volume aggregations.

Run:
    python3 kafka_signal_consumer.py                # 5s tumbling window
    python3 kafka_signal_consumer.py --window 10
"""
from __future__ import annotations

import argparse
import json
import os
import time
from collections import Counter

from confluent_kafka import Consumer

KAFKA_BOOTSTRAP = os.environ.get("KAFKA_BOOTSTRAP", "localhost:9092")
TOPIC = "scan.signals"


def run(window_seconds: int = 5, idle_exit_after: float | None = None):
    consumer = Consumer({
        "bootstrap.servers": KAFKA_BOOTSTRAP,
        "group.id": "kafka-signal-consumer",
        "auto.offset.reset": "earliest",
    })
    consumer.subscribe([TOPIC])

    window_start = time.time()
    counts: Counter = Counter()
    n_seen = 0
    last_msg_at = time.time()
    print(f"[kafka-signals] consuming topic={TOPIC} bootstrap={KAFKA_BOOTSTRAP} window={window_seconds}s", flush=True)

    try:
        while True:
            msg = consumer.poll(timeout=1.0)
            now = time.time()

            if msg is not None and not msg.error():
                try:
                    rec = json.loads(msg.value())
                    counts[rec.get("signal", "UNKNOWN")] += 1
                    n_seen += 1
                    last_msg_at = now
                except Exception:
                    pass

            if now - window_start >= window_seconds:
                _flush(counts, window_start)
                counts = Counter()
                window_start = now

            if idle_exit_after and (now - last_msg_at) > idle_exit_after and n_seen > 0:
                _flush(counts, window_start)
                print(f"[kafka-signals] idle for {idle_exit_after}s, exiting (smoke-test mode)", flush=True)
                return
    except KeyboardInterrupt:
        _flush(counts, window_start)
    finally:
        consumer.close()


def _flush(counts: Counter, window_start: float):
    if not counts:
        return
    window_end = time.strftime("%Y-%m-%dT%H:%M:%S", time.localtime(window_start))
    for signal, n in counts.most_common():
        print(f"[kafka-signals] window_end={window_end} signal={signal} n={n}", flush=True)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--window", type=int, default=5)
    ap.add_argument("--idle-exit-after", type=float, default=None)
    args = ap.parse_args()
    run(window_seconds=args.window, idle_exit_after=args.idle_exit_after)


if __name__ == "__main__":
    main()
