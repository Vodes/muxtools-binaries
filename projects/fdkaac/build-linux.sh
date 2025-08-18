# Install dependencies
sudo apt-get install -y build-essential git autoconf automake libtool libtool-bin gettext pkg-config

# Build libfdk
git clone https://github.com/mstorsjo/fdk-aac.git --branch "$TAG_LIBFDK" --single-branch
cd fdk-aac
./autogen.sh
./configure --prefix=$BUILD_DIR --disable-shared --enable-static
make clean
make -j $(nproc) install

# Build fdkaac
cd ..
git clone https://github.com/nu774/fdkaac.git --branch "$TAG_FDKAAC" --single-branch
cd fdkaac
autoreconf -fiv
./configure --prefix=$BUILD_DIR # Seems to be static by default?
make clean
make -j $(nproc) install
