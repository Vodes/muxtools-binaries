# Install dependencies
sudo apt-get install -y build-essential git autoconf automake libtool libtool-bin gettext pkg-config

# Build libogg
git clone https://github.com/xiph/ogg.git --branch "$TAG_OGG" --single-branch
cd ogg
./autogen.sh
./configure --prefix=$BUILD_DIR --disable-shared --enable-static
make clean
make -j $(nproc) install

# Build libopus
cd ..
git clone https://github.com/xiph/opus.git --branch "$TAG_OPUS" --single-branch
cd opus
./autogen.sh
./configure --prefix=$BUILD_DIR --disable-shared --enable-static
make clean
make -j $(nproc) install

# Build opusfile
cd ..
git clone https://github.com/xiph/opusfile.git --branch "$TAG_OPUSFILE" --single-branch
cd opusfile
./autogen.sh
./configure --prefix=$BUILD_DIR --disable-shared --enable-static --disable-maintainer-mode --disable-http
make clean
make -j $(nproc) install

# Build libopusenc
cd ..
git clone https://github.com/xiph/libopusenc.git --branch "$TAG_LIBOPUSENC" --single-branch
cd libopusenc
./autogen.sh
./configure --prefix=$BUILD_DIR --disable-shared --enable-static --disable-maintainer-mode
make clean
make -j $(nproc) install

# Build flac
cd ..
git clone https://github.com/xiph/flac.git --branch "$TAG_FLAC" --single-branch
cd flac
./autogen.sh
./configure --prefix=$BUILD_DIR --disable-shared --enable-static --disable-oggtest --disable-cpplibs --disable-doxygen-docs --with-ogg="$BUILD_DIR"
make clean
make -j $(nproc) install

# Build opus-tools
cd ..
git clone https://github.com/xiph/opus-tools.git --branch "$TAG_OPUSTOOLS" --single-branch
cd opus-tools
./autogen.sh
./configure --prefix=$BUILD_DIR --disable-shared --enable-static --disable-maintainer-mode
make clean
make -j $(nproc) install
