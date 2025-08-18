## muxtools-binaries

A repo to manage windows and linux (maybe mac at some point) binaries and versions.

For windows existing sources for binaries will be used if applicable and for Linux there will be statically linked executables made in this repo.

## Dependencies

The linux builds here are done on **Ubuntu 22.04** and as such require **glibc 2.35+** (as far as I know).

Everything that released after 22.04 should also obviously run fine, such as:
- Debian>=12
- Fedora>=37
- Alma/Rocky/RHEL/CentOS-Stream >= **10**
- Any rolling distro

### Mkvtoolnix
This is currently a special case because I *really* don't feel like building it with all its dependencies. (Patches welcome however)

The linux release zip essentially just contains the official AppImage and "symlinks" for the respective cli tools.
AppImages require some form of libfuse2 installed on the system to work. See the [AppImage Wiki](https://github.com/AppImage/AppImageKit/wiki/FUSE).
