ARG BASE=quay.io/pypa/manylinux_2_34_x86_64@sha256:934419fa742ff922855f464203cc0ad5afe3317a8541712a00cf3dc7fb893fc8
FROM ubuntu:22.04@sha256:b8b6ee6aa931ecd9d0d952abc34dc0e5f7c6a30c6bb71b079fe399fde0329c02 AS llvm_mingw_runtime

FROM ${BASE}
ARG BUILDER_ID=manylinux-x86_64
RUN dnf install -y dnf-plugins-core epel-release \
    && dnf config-manager --set-enabled crb \
    && dnf install -y \
      gcc gcc-c++ clang llvm llvm-devel lld binutils \
      cmake ninja-build meson autoconf automake libtool gettext-devel pkgconf-pkg-config \
      make patch perl bison flex gperf zstd ruby rubygem-rake docbook-style-xsl libxslt ImageMagick inkscape cargo-c \
    && if [ "$(uname -m)" = x86_64 ]; then \
      dnf install -y \
        nasm yasm texinfo \
        mingw64-gcc mingw64-gcc-c++ mingw64-headers mingw64-crt mingw64-winpthreads-static; \
    fi \
    && dnf clean all
ARG RUST_VERSION=1.95.0
ARG RUSTUP_VERSION=1.28.2
ARG RUSTUP_X86_64_SHA256=20a06e644b0d9bd2fbdbfd52d42540bdde820ea7df86e92e533c073da0cdd43c
ARG RUSTUP_AARCH64_SHA256=e3853c5a252fca15252d07cb23a1bdd9377a8c6f3efa01531109281ae47f841c
ARG TARGETARCH
ENV RUSTUP_HOME=/opt/rustup CARGO_HOME=/opt/cargo PATH=/opt/cargo/bin:${PATH} RUSTUP_TOOLCHAIN=${RUST_VERSION}
RUN case "${TARGETARCH}" in \
      amd64) rust_arch=x86_64-unknown-linux-gnu; checksum="${RUSTUP_X86_64_SHA256}" ;; \
      arm64) rust_arch=aarch64-unknown-linux-gnu; checksum="${RUSTUP_AARCH64_SHA256}" ;; \
      *) echo "Unsupported Rust host architecture: ${TARGETARCH}" >&2; exit 1 ;; \
    esac && \
    curl -fL --retry 5 -o /tmp/rustup-init \
      "https://static.rust-lang.org/rustup/archive/${RUSTUP_VERSION}/${rust_arch}/rustup-init" && \
    echo "${checksum}  /tmp/rustup-init" | sha256sum -c - && \
    chmod 0755 /tmp/rustup-init && \
    /tmp/rustup-init -y --default-toolchain none --profile minimal && \
    rustup toolchain install "${RUST_VERSION}" --profile minimal && \
    rustup target add --toolchain "${RUST_VERSION}" \
      x86_64-pc-windows-gnu aarch64-pc-windows-gnullvm && \
    rustup default "${RUST_VERSION}" && \
    chmod -R a+rX "${RUSTUP_HOME}" "${CARGO_HOME}" && \
    rm -f /tmp/rustup-init
ENV PATH="/opt/python/cp312-cp312/bin:${PATH}" MUXTOOLS_BUILDER=1 MUXTOOLS_BUILDER_ID=${BUILDER_ID}
COPY builder/lock.toml builder/install-toolchains.py /opt/builder/
RUN python /opt/builder/install-toolchains.py
COPY --from=llvm_mingw_runtime /usr/lib/*-linux-gnu/libstdc++.so.6* /opt/llvm-mingw/host-libs/
ENV LD_LIBRARY_PATH="/opt/llvm-mingw/host-libs"
RUN /opt/llvm-mingw/bin/clang --version | grep -q 'clang version 22\.'
RUN rpm -qa --qf '%{NAME} %{VERSION}-%{RELEASE}.%{ARCH}\n' | sort > /opt/builder/packages.txt
WORKDIR /work
