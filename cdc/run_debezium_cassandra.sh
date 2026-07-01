#!/usr/bin/env bash
# Launch the Debezium Cassandra-5 CDC connector as a standalone agent.
# Streams Cassandra commit-log mutations -> Kafka. Requires JDK 17+ and the
# connector plugin (set DBZ_HOME to its extracted dir).
set -euo pipefail
export JAVA_HOME="${JAVA_HOME:-/opt/homebrew/opt/openjdk@21}"
DBZ_HOME="${DBZ_HOME:-/tmp/dbz-cassandra5/debezium-connector-cassandra-5}"
CONF="$(dirname "$0")/cassandra-cdc.properties"
mkdir -p /tmp/debezium/relocation /tmp/debezium/offsets
# Cassandra's commit-log reader needs the JDK internal add-opens/exports flags.
JVMFLAGS=$(grep -hE '^--add-(opens|exports)' /opt/homebrew/etc/cassandra/jvm17-server.options | tr '\n' ' ')
exec "$JAVA_HOME/bin/java" $JVMFLAGS -Dcassandra.storagedir=/opt/homebrew/var/lib/cassandra \
  -cp "$DBZ_HOME/*" \
  io.debezium.connector.cassandra.CassandraConnectorTask "$CONF"
