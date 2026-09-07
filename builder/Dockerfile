ARG BASE=quay.io/pypa/manylinux_2_34_x86_64@sha256:934419fa742ff922855f464203cc0ad5afe3317a8541712a00cf3dc7fb893fc8
FROM ${BASE}
RUN dnf install -y \
      gcc gcc-c++ clang llvm llvm-devel lld binutils \
      cmake ninja-build meson autoconf automake libtool gettext-devel pkgconf-pkg-config \
      nasm yasm make patch perl bison flex texinfo zstd \
      mingw64-gcc mingw64-gcc-c++ mingw64-headers mingw64-crt \
      mingw64-winpthreads-static \
    && dnf clean all
ENV PATH="/opt/python/cp312-cp312/bin:${PATH}" MUXTOOLS_BUILDER=1
COPY builder/lock.toml /opt/builder/lock.toml
RUN rpm -qa --qf '%{NAME} %{VERSION}-%{RELEASE}.%{ARCH}\n' | sort > /opt/builder/packages.txt
WORKDIR /work
