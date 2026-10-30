# The action's test (ci.yml, docker-build-action): a layer whose content differs
# each time it is really built, so an equal value means it came from the cache.
FROM docker.io/library/busybox:1.37
RUN cat /proc/sys/kernel/random/uuid > /built
