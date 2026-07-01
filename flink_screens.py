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

Run (needs PyFlink + the Kafka SQL connector JAR):
    pip install apache-flink
    # download flink-sql-connector-kafka-*.jar into ./ (matching your Flink 2.x)
    /opt/homebrew/opt/apache-flink/libexec/bin/start-cluster.sh     # dashboard :8081
    flink run -py flink_screens.py
    # (feed it:  python stream_pipeline.py consume  &  ... produce ...)
"""

import sys


def main():
    try:
        from pyflink.table import EnvironmentSettings, TableEnvironment
    except ImportError:
        print("PyFlink not installed. `pip install apache-flink` (needs a JDK).",
              file=sys.stderr)
        sys.exit(1)

    t_env = TableEnvironment.create(EnvironmentSettings.in_streaming_mode())

    # Source: the Kafka signals topic (JSON records from stream_pipeline workers)
    t_env.execute_sql("""
        CREATE TABLE signals (
            ticker STRING,
            signal STRING,
            rsi DOUBLE,
            close DOUBLE,
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
