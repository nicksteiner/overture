# overture in a container: OPERA DSWx, overture and openSEPPO in one pixi environment.
#
#   docker build -t overture .
#   docker run --rm -u $(id -u):$(id -g) -v /data:/data overture run \
#       --h5 20260930=/data/<granule>.h5 --out /data/run --anc-cache /data/ancillary_cache
#   docker run --rm -u $(id -u):$(id -g) -v $HOME/.netrc:/tmp/.netrc:ro -v /data:/data overture search \
#       --product GCOV --collection '%' --track 173 --frame 8 --direction A --https --download /data/granules
FROM ghcr.io/prefix-dev/pixi:0.63.2

RUN apt-get update && apt-get install -y --no-install-recommends git ca-certificates \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /opt/overture
COPY pixi.toml pixi.lock pyproject.toml README.md LICENSE ./
COPY src ./src
COPY tests ./tests
RUN pixi install --locked && rm -rf /root/.cache/rattler /root/.cache/uv \
    && pixi shell-hook -s bash > /opt/overture/activate.sh \
    && echo 'exec "$@"' >> /opt/overture/activate.sh \
    && chmod -R a+rX /opt/overture

# a home any user id can write to (Earthdata token cache, matplotlib cache)
ENV HOME=/tmp
WORKDIR /data
ENTRYPOINT ["/bin/bash", "/opt/overture/activate.sh", "overture"]
CMD ["--help"]
