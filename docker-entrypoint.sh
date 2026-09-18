#!/bin/sh
set -e

# Apply pending migrations before starting. Set RUN_MIGRATIONS=0 when running
# several replicas and migrate once from a separate job instead.
if [ "$RUN_MIGRATIONS" = "1" ]; then
  echo "Applying database migrations..."
  flask db upgrade
fi

exec "$@"
