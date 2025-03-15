# Postgres Login from ENV

Run the following command to generate the correct configuration file (included in `.gitignore` to prevent pushing to public repo).

```sh
envsubst < postgresql-sink.json.template > postgresql-sink.json
```