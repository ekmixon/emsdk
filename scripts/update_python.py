#!/usr/bin/env python3
# Copyright 2020 The Emscripten Authors.  All rights reserved.
# Emscripten is available under two separate licenses, the MIT license and the
# University of Illinois/NCSA Open Source License.  Both these licenses can be
# found in the LICENSE file.

"""Updates the python binaries that we cache store at
http://storage.google.com/webassembly.

We only supply binaries for windows and macOS, but we do it very different ways for those two OSes.

Windows recipe:
  1. Download the "embeddable zip file" version of python from python.org
  2. Remove .pth file to work around https://bugs.python.org/issue34841
  3. Download and install pywin32 in the `site-packages` directory
  4. Re-zip and upload to storage.google.com

macOS recipe:
  1. Clone cpython
  2. Use homebrew to install and configure openssl (for static linking!)
  3. Build cpython from source and use `make install` to create archive.
"""


import glob
import multiprocessing
import os
import platform
import urllib.request
import shutil
import subprocess
import sys
from subprocess import check_call

version = '3.9.2'
major_minor_version = '.'.join(version.split('.')[:2])  # e.g. '3.9.2' -> '3.9'
base = f'https://www.python.org/ftp/python/{version}/'
revision = '1'

pywin32_version = '227'
pywin32_base = f'https://github.com/mhammond/pywin32/releases/download/b{pywin32_version}/'


upload_base = 'gs://webassembly/emscripten-releases-builds/deps/'


def unzip_cmd():
    # Use 7-Zip if available (https://www.7-zip.org/)
    sevenzip = os.path.join(os.getenv('ProgramFiles'), '7-Zip', '7z.exe')
    return [sevenzip, 'x'] if os.path.isfile(sevenzip) else ['unzip', '-q']


def zip_cmd():
    # Use 7-Zip if available (https://www.7-zip.org/)
    sevenzip = os.path.join(os.getenv('ProgramFiles'), '7-Zip', '7z.exe')
    return [sevenzip, 'a', '-mx9'] if os.path.isfile(sevenzip) else ['zip', '-rq']


def make_python_patch(arch):
    if arch == 'amd64':
        pywin32_filename = (
            f'pywin32-{pywin32_version}.win-{arch}-py{major_minor_version}.exe'
        )

    else:
        pywin32_filename = (
            f'pywin32-{pywin32_version}.{arch}-py{major_minor_version}.exe'
        )

    filename = f'python-{version}-embed-{arch}.zip'
    out_filename = f'python-{version}-{revision}-embed-{arch}+pywin32.zip'
    if not os.path.exists(pywin32_filename):
        download_url = pywin32_base + pywin32_filename
        print(f'Downloading pywin32: {download_url}')
        urllib.request.urlretrieve(download_url, pywin32_filename)

    if not os.path.exists(filename):
        download_url = base + filename
        print(f'Downloading python: {download_url}')
        urllib.request.urlretrieve(download_url, filename)

    os.mkdir('python-embed')
    check_call(unzip_cmd() + [os.path.abspath(filename)], cwd='python-embed')
    os.remove(
        os.path.join(
            'python-embed',
            f"python{major_minor_version.replace('.', '')}._pth",
        )
    )


    os.mkdir('pywin32')
    rtn = subprocess.call(unzip_cmd() + [os.path.abspath(pywin32_filename)], cwd='pywin32')
    assert rtn in [0, 1]

    os.mkdir(os.path.join('python-embed', 'lib'))
    shutil.move(os.path.join('pywin32', 'PLATLIB'), os.path.join('python-embed', 'lib', 'site-packages'))

    check_call(zip_cmd() + [os.path.join('..', out_filename), '.'], cwd='python-embed')

    # cleanup if everything went fine
    shutil.rmtree('python-embed')
    shutil.rmtree('pywin32')

    upload_url = upload_base + out_filename
    print(f'Uploading: {upload_url}')
    cmd = ['gsutil', 'cp', '-n', out_filename, upload_url]
    print(' '.join(cmd))
    check_call(cmd)


def build_python():
    if sys.platform.startswith('darwin'):
        osname = 'macos'
        # Take some rather drastic steps to link openssl statically
        check_call(['brew', 'install', 'openssl', 'pkg-config'])
        if platform.machine() == 'x86_64':
            prefix = '/usr/local'
            min_macos_version = '10.11'
        elif platform.machine() == 'arm64':
            prefix = '/opt/homebrew'
            min_macos_version = '11.0'

        osname += f'-{platform.machine()}'

        try:
            os.remove(os.path.join(prefix, 'opt', 'openssl', 'lib', 'libssl.dylib'))
            os.remove(os.path.join(prefix, 'opt', 'openssl', 'lib', 'libcrypto.dylib'))
        except Exception:
            pass
        os.environ['PKG_CONFIG_PATH'] = os.path.join(prefix, 'opt', 'openssl', 'lib', 'pkgconfig')
    else:
        osname = 'linux'

    src_dir = 'cpython'
    if not os.path.exists(src_dir):
      check_call(['git', 'clone', 'https://github.com/python/cpython'])
    check_call(['git', 'checkout', f'v{version}'], cwd=src_dir)

    min_macos_version_line = f'-mmacosx-version-min={min_macos_version}'
    build_flags = f'{min_macos_version_line} -Werror=partial-availability'
    env = os.environ.copy()
    env['MACOSX_DEPLOYMENT_TARGET'] = min_macos_version
    check_call(
        [
            './configure',
            f'CFLAGS={build_flags}',
            f'CXXFLAGS={build_flags}',
            f'LDFLAGS={min_macos_version_line}',
        ],
        cwd=src_dir,
        env=env,
    )

    check_call(['make', '-j', str(multiprocessing.cpu_count())], cwd=src_dir, env=env)
    check_call(['make', 'install', 'DESTDIR=install'], cwd=src_dir, env=env)

    install_dir = os.path.join(src_dir, 'install')

    # Install requests module.  This is needed in particualr on macOS to ensure
    # SSL certificates are available (certifi in installed and used by requests).
    pybin = os.path.join(src_dir, 'install', 'usr', 'local', 'bin', 'python3')
    pip = os.path.join(src_dir, 'install', 'usr', 'local', 'bin', 'pip3')
    check_call([pybin, pip, 'install', 'requests'])

    dirname = f'python-{version}-{revision}'
    if os.path.isdir(dirname):
        print(f'Erasing old build directory {dirname}')
        shutil.rmtree(dirname)
    os.rename(os.path.join(install_dir, 'usr', 'local'), dirname)
    tarball = f'python-{version}-{revision}-{osname}.tar.gz'
    shutil.rmtree(
        os.path.join(dirname, 'lib', f'python{major_minor_version}', 'test')
    )

    shutil.rmtree(os.path.join(dirname, 'include'))
    for lib in glob.glob(os.path.join(dirname, 'lib', 'lib*.a')):
      os.remove(lib)
    check_call(['tar', 'zcvf', tarball, dirname])
    print(f'Uploading: {upload_base}{tarball}')
    check_call(['gsutil', 'cp', '-n', tarball, upload_base + tarball])


def main():
    if sys.platform.startswith('win'):
        for arch in ('amd64', 'win32'):
            make_python_patch(arch)
    else:
        build_python()
    return 0


if __name__ == '__main__':
  sys.exit(main())
