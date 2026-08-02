# Vector setup for ClassicalBot JSON logs

ClassicalBot writes one JSON object per stdout line. Vector must parse only
records carrying the application schema marker and leave every other Docker log
unchanged.

Example application line:

```json
{"schema":"classical_bot.log.v1","timestamp":"2026-08-02T15:20:00.123456+00:00","level":"info","service":"classical-bot","logger":"crawlers.base","event":"crawler_upload_completed","message":"Upload completed","crawler":"filharmonia_sk","inserted_count":12,"skipped_count":3}
```

## Vector transform

Back up the active Vector configuration. Substitute the existing Docker source
ID for `docker_logs`, then add this fail-open remap transform:

```yaml
transforms:
  parse_classical_bot_json:
    type: remap
    inputs:
      - docker_logs
    drop_on_error: false
    source: |-
      parsed, err = parse_json(.message)
      if err == null && is_object(parsed) {
        schema, schema_err = get(parsed, ["schema"])
        if schema_err == null && schema == "classical_bot.log.v1" {
          . = merge(., parsed)
        }
      }
```

This retains Docker fields such as `app`, `host`, `container_name`, and
`stream`. A structured record replaces `.message` and `.timestamp` with the
application values. Plain text, malformed JSON, third-party logs, and unrelated
containers pass through with their original Docker message and timestamp.

Point the existing VictoriaLogs sink at `parse_classical_bot_json` instead of
directly at `docker_logs`. Keep only the currently used low-cardinality fields
`app,host,stream` as stream fields. Do not add `event`, `logger`, `crawler`,
URLs, concert IDs, or error values to `_stream_fields`.

For an HTTP JSON-lines sink, the relevant shape is:

```yaml
sinks:
  vlogs:
    type: http
    inputs:
      - parse_classical_bot_json
    uri: http://VICTORIALOGS_HOST:9428/insert/jsonline?_stream_fields=app,host,stream&_msg_field=message&_time_field=timestamp
    compression: gzip
    encoding:
      codec: json
    framing:
      method: newline_delimited
    healthcheck:
      enabled: false
```

For an Elasticsearch-compatible sink, retain the existing endpoint and
credentials and use:

```yaml
sinks:
  vlogs:
    type: elasticsearch
    inputs:
      - parse_classical_bot_json
    endpoints:
      - http://VICTORIALOGS_HOST:9428/insert/elasticsearch/
    api_version: v8
    compression: gzip
    healthcheck:
      enabled: false
    query:
      _msg_field: message
      _time_field: timestamp
      _stream_fields: app,host,stream
```

Use the active source ID, sink ID, endpoint, tenant headers, authentication,
TLS, and other existing sink settings rather than copying placeholders over
them.

## Rollout and verification

1. Run `vector validate` against the updated configuration.
2. Reload or restart Vector and confirm existing plain-text logs still arrive.
3. Deploy ClassicalBot after Vector is healthy.
4. Confirm structured records:

   ```logsql
   _time:15m schema:="classical_bot.log.v1"
   ```

5. Confirm field filtering:

   ```logsql
   _time:15m schema:="classical_bot.log.v1" level:="error"
   ```

6. Inspect `_stream` on several records. It should remain based on
   `app`, `host`, and `stream`, with application fields available as ordinary
   searchable fields.
7. If parsing or ingestion regresses, restore the previous Vector configuration;
   the application JSON remains valid plain-text `_msg` content until the
   transform is restored.

References:

- [Vector remap transform](https://vector.dev/docs/reference/configuration/transforms/remap/)
- [Vector Docker logs source](https://vector.dev/docs/reference/configuration/sources/docker_logs/)
- [VictoriaLogs Vector integration](https://docs.victoriametrics.com/victorialogs/data-ingestion/vector/index.html)
