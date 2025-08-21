# Install dependencies
sudo apt-get install -y build-essential git autoconf automake libtool libtool-bin gettext pkg-config wget g++-mingw-w64-x86-64

# Download libfdk from msys2 repos
wget "https://repo.msys2.org/mingw/mingw64/$LIBFDK_MSYS_FILE"
sudo tar xvf "$LIBFDK_MSYS_FILE" -C /usr/x86_64-w64-mingw32 --strip-component=1
sudo find /usr/x86_64-w64-mingw32 -type f \( -name "libfdk-aac.dll.a" -o -name "libfdk-aac-2.dll" \) -delete

# Build fdkaac
git clone https://github.com/nu774/fdkaac.git --branch "$TAG_FDKAAC" --single-branch
cd fdkaac
autoreconf -fiv
PKG_CONFIG_LIBDIR=/usr/x86_64-w64-mingw32/lib/pkgconfig ./configure --prefix=$BUILD_DIR_WIN64 --host=x86_64-w64-mingw32
make clean
make -j $(nproc) install
