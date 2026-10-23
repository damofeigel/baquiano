FROM postgres:16-alpine

# Files in /docker-entrypoint-initdb.d/ run automatically, in file-name order,
# the first time the container starts with an empty data volume:
#   001_initial_schema.sql -> [002_seed_data.sql] -> 003_entity_resolution.sql
# Later migrations don't run on an existing volume: apply them with psql, or
# recreate the database with `docker compose down -v` (deletes the data).
COPY src/db/migrations/*.sql /docker-entrypoint-initdb.d/

# The seed is example data for development (several of its actors are made
# up). Only load it when asked, so it never mixes with real crawled actors:
#   DB_LOAD_SEED=true docker compose up -d --build
ARG LOAD_SEED=false
COPY src/db/seeds/ /opt/seeds/
RUN if [ "$LOAD_SEED" = "true" ]; then cp /opt/seeds/*.sql /docker-entrypoint-initdb.d/; fi
