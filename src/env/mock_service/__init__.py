"""A single configurable mock microservice used for service_a/b/c in
docker-compose. Behavior (health, metrics, logs, restart, disk surface) is
driven by SERVICE_NAME and SERVICE_ROLE env vars so one image serves every
instance.
"""
