#!/usr/bin/env python3
"""
flink_screens.py
----------------
PyFlink streaming job: consumes the `scan.signals` Kafka topic produced by
stream_pipeline.py and continuously aggregates signal counts by type in a
5-second tumbling window — the streaming/scale-out complement to the batch
scanners. Run it while workers feed signals to see live screen throughput.

This is the "scale" path, not a single-laptop speedup: Flink's value is
distributing a *continuous* signal stream across a cluster. On one machine it
mainly demonstrates the streaming topology.

Prefer `kafka_signal_consumer.py` for normal use — same tumbling-window signal
count, pure Python, no JVM/JDK/connector-jar/reserved-keyword-SQL surface area.
Keep this file for when a real multi-node Flink deployment is worth its
operational cost. Note: the `apache-flink` Homebrew formula has no `brew
services` entry, so a standalone cluster started via start-cluster.sh is not
a persistent system service the way Cassandra/Kafka are — don't assume it
stays up unless something is actively supervising it.

Run (needs PyFlink + the Kafka SQL connector JAR + a JDK 17+ on PATH):
    pip install apache-flink
    mkdir -p ~/flink_jars && curl -sL -o ~/flink_jars/flink-sql-connector-kafka.jar \\
        https://repo1.maven.org/maven2/org/apache/flink/flink-sql-connector-kafka/5.0.0-2.2/flink-sql-connector-kafka-5.0.0-2.2.jar
    export JAVA_HOME=/opt/homebrew/opt/openjdk@17/libexec/openjdk.jdk/Contents/Home
    /opt/homebrew/Cellar/apache-flink/*/libexec/bin/start-cluster.sh   # dashboard :8081
    python3 flink_screens.py
    # (feed it:  python stream_pipeline.py consume  &  ... produce ...)
"""

import os
import sys

KAFKA_JAR = os.path.expanduser("~/flink_jars/flink-sql-connector-kafka.jar")


def main():
    try:
        from pyflink.table import EnvironmentSettings, TableEnvironment
    except ImportError:
        print("PyFlink not installed. `pip install apache-flink` (needs a JDK). "
              "Consider kafka_signal_consumer.py instead — same result, no JVM.",
              file=sys.stderr)
        sys.exit(1)

    t_env = TableEnvironment.create(EnvironmentSettings.in_streaming_mode())
    # Kafka SQL connector jar (not bundled with PyFlink) — required for the
    # 'connector'='kafka' table below; download once into ~/flink_jars/.
    t_env.get_config().set("pipeline.jars", f"file://{KAFKA_JAR}")

    # Source: the Kafka signals topic (JSON records from stream_pipeline workers)
    # NB: `close` is quoted — it's a reserved word in Flink SQL (Calcite) and an
    # unquoted `close DOUBLE` column fails DDL parsing with a SqlParseException.
    t_env.execute_sql("""
        CREATE TABLE signals (
            ticker STRING,
            signal STRING,
            rsi DOUBLE,
            `close` DOUBLE,
            ts AS PROCTIME()
        ) WITH (
            'connector' = 'kafka',
            'topic' = 'scan.signals',
            'properties.bootstrap.servers' = 'localhost:9092',
            'properties.group.id' = 'flink-screens',
            'scan.startup.mode' = 'earliest-offset',
            'format' = 'json',
            'json.ignore-parse-errors' = 'true'
        )
    """)

    # Sink: print aggregated counts to stdout / the Flink task logs
    t_env.execute_sql("""
        CREATE TABLE signal_counts (
            window_end TIMESTAMP(3),
            signal STRING,
            n BIGINT
        ) WITH ('connector' = 'print')
    """)

    # 5-second tumbling window count of each signal type — live screen throughput
    t_env.execute_sql("""
        INSERT INTO signal_counts
        SELECT
            TUMBLE_END(ts, INTERVAL '5' SECOND) AS window_end,
            signal,
            COUNT(*) AS n
        FROM signals
        GROUP BY TUMBLE(ts, INTERVAL '5' SECOND), signal
    """).wait()


if __name__ == "__main__":
    main()
