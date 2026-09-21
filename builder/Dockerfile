ARG BASE=quay.io/pypa/manylinux_2_34_x86_64@sha256:934419fa742ff922855f464203cc0ad5afe3317a8541712a00cf3dc7fb893fc8
FROM ubuntu:22.04@sha256:b8b6ee6aa931ecd9d0d952abc34dc0e5f7c6a30c6bb71b079fe399fde0329c02 AS llvm_mingw_runtime

FROM ${BASE}
ARG BUILDER_ID=manylinux-x86_64
RUN dnf install -y \
      gcc gcc-c++ clang llvm llvm-devel lld binutils \
      cmake ninja-build meson autoconf automake libtool gettext-devel pkgconf-pkg-config \
      make patch perl bison flex zstd ruby rubygem-rake docbook-style-xsl libxslt ImageMagick inkscape \
    && if [ "$(uname -m)" = x86_64 ]; then \
      dnf install -y \
        nasm yasm gperf texinfo \
        mingw64-gcc mingw64-gcc-c++ mingw64-headers mingw64-crt mingw64-winpthreads-static; \
    fi \
    && dnf clean all
ENV PATH="/opt/python/cp312-cp312/bin:${PATH}" MUXTOOLS_BUILDER=1 MUXTOOLS_BUILDER_ID=${BUILDER_ID}
COPY builder/lock.toml builder/install-toolchains.py /opt/builder/
RUN python /opt/builder/install-toolchains.py
COPY --from=llvm_mingw_runtime /usr/lib/*-linux-gnu/libstdc++.so.6* /opt/llvm-mingw/host-libs/
ENV LD_LIBRARY_PATH="/opt/llvm-mingw/host-libs"
RUN /opt/llvm-mingw/bin/clang --version | grep -q 'clang version 22\.'
RUN rpm -qa --qf '%{NAME} %{VERSION}-%{RELEASE}.%{ARCH}\n' | sort > /opt/builder/packages.txt
WORKDIR /work
