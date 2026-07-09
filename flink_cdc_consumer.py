#!/usr/bin/env python3
"""
flink_cdc_consumer.py
----------------------
PyFlink real-time consumer for the market-data CDC stream (topic `ohlc.cdc`,
published by market_store.py on every Cassandra write).

Prefer `kafka_cdc_consumer.py` for normal use — same tumbling-window ticker
mutation count, pure Python (confluent_kafka only), no JVM/JDK/connector-jar
required. Keep this file for when a real multi-node Flink deployment is worth
its operational cost (e.g. joins across multiple high-volume topics, exactly-
once state across a cluster). Note: the `apache-flink` Homebrew formula has no
`brew services` entry, so a standalone cluster started via start-cluster.sh is
not a persistent system service the way Cassandra/Kafka are — it will not
survive the way those two do, so don't assume it stays up unattended.

Run against a local standalone cluster:
    export JAVA_HOME=/opt/homebrew/opt/openjdk@17/libexec/openjdk.jdk/Contents/Home
    mkdir -p ~/flink_jars && curl -sL -o ~/flink_jars/flink-sql-connector-kafka.jar \\
        https://repo1.maven.org/maven2/org/apache/flink/flink-sql-connector-kafka/5.0.0-2.2/flink-sql-connector-kafka-5.0.0-2.2.jar
    /opt/homebrew/Cellar/apache-flink/*/libexec/bin/start-cluster.sh
    flink run -pyexec "$(which python3)" -pyclientexec "$(which python3)" \\
        -py flink_cdc_consumer.py --jarfile ~/flink_jars/flink-sql-connector-kafka.jar

Or locally (embedded mini-cluster, no `flink run` needed) for a smoke test:
    python3 flink_cdc_consumer.py
"""
from __future__ import annotations

import argparse
import json
import os

from pyflink.common import SimpleStringSchema, WatermarkStrategy
from pyflink.common.typeinfo import Types
from pyflink.datastream import StreamExecutionEnvironment
from pyflink.datastream.connectors.kafka import KafkaSource, KafkaOffsetsInitializer
from pyflink.datastream.window import TumblingProcessingTimeWindows
from pyflink.common.time import Time

KAFKA_BOOTSTRAP = os.environ.get("KAFKA_BOOTSTRAP", "localhost:9092")
TOPIC = "ohlc.cdc"
KAFKA_JAR = os.path.expanduser("~/flink_jars/flink-sql-connector-kafka.jar")


def parse_cdc(raw: str):
    """(ticker, n_bars) or None for a malformed record — never raises."""
    try:
        rec = json.loads(raw)
        return rec["ticker"], int(rec.get("n_bars", 0))
    except Exception:
        return None


def build_pipeline(env: StreamExecutionEnvironment, run_seconds: int | None = None):
    source = (
        KafkaSource.builder()
        .set_bootstrap_servers(KAFKA_BOOTSTRAP)
        .set_topics(TOPIC)
        .set_group_id("flink-cdc-consumer")
        .set_starting_offsets(KafkaOffsetsInitializer.earliest())
        .set_value_only_deserializer(SimpleStringSchema())
        .build()
    )

    stream = env.from_source(source, WatermarkStrategy.no_watermarks(), "ohlc.cdc")

    parsed = (
        stream.map(parse_cdc, output_type=Types.TUPLE([Types.STRING(), Types.LONG()]))
        .filter(lambda kv: kv is not None)
    )

    counted = (
        parsed.key_by(lambda kv: kv[0])
        .window(TumblingProcessingTimeWindows.of(Time.seconds(30)))
        .reduce(lambda a, b: (a[0], a[1] + b[1]))
    )

    counted.map(
        lambda kv: f"[flink-cdc] ticker={kv[0]} bars_mutated_in_window={kv[1]}",
        output_type=Types.STRING(),
    ).print()

    return stream


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--local", action="store_true", help="run an embedded mini-cluster (smoke test)")
    ap.add_argument("--timeout", type=int, default=0, help="seconds before cancelling (0 = run forever)")
    args = ap.parse_args()

    env = StreamExecutionEnvironment.get_execution_environment()
    env.add_jars(f"file://{KAFKA_JAR}")
    env.set_parallelism(1)

    build_pipeline(env)

    print(f"[flink-cdc] starting job — topic={TOPIC} bootstrap={KAFKA_BOOTSTRAP}", flush=True)
    env.execute("ohlc-cdc-consumer")


if __name__ == "__main__":
    main()
