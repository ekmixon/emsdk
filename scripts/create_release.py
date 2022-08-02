#!/usr/bin/env python3

import json
import os
import re
import subprocess
import sys
from collections import OrderedDict

script_dir = os.path.dirname(os.path.abspath(__file__))
root_dir = os.path.dirname(script_dir)
sys.path.append(root_dir)

import emsdk  # noqa


def version_to_list(version_string):
  return [int(part) for part in re.split('[.-]', version_string)[:3]]


def main(args):
  if subprocess.check_output(['git', 'status', '--porcelain'], cwd=root_dir).strip():
    print('tree is not clean')
    sys.exit(1)

  release_info = emsdk.load_releases_info()
  new_version = version_to_list(release_info['aliases']['latest'])
  new_version[-1] += 1
  branch_name = f"version_{'_'.join((str(part) for part in new_version))}"

  # Create a new git branch
  subprocess.check_call(['git', 'checkout', '-b', branch_name], cwd=root_dir)

  new_version = '.'.join(str(part) for part in new_version)
  new_hash = args[0] if args else emsdk.get_emscripten_releases_tot()
  print(f'Creating new release: {new_version} -> {new_hash}')
  release_info['releases'][new_version] = new_hash
  releases = list(release_info['releases'].items())
  releases.sort(key=lambda pair: version_to_list(pair[0]))

  release_info['releases'] = OrderedDict(reversed(releases))
  release_info['aliases']['latest'] = new_version

  with open(os.path.join(root_dir, 'emscripten-releases-tags.json'), 'w') as f:
    f.write(json.dumps(release_info, indent=2))
    f.write('\n')

  subprocess.check_call(os.path.join(script_dir, 'update_bazel_workspace.sh'), cwd=root_dir)

  # Create auto-generated changes to the new git branch
  subprocess.check_call(['git', 'add', '-u', '.'], cwd=root_dir)
  subprocess.check_call(['git', 'commit', '-m', new_version], cwd=root_dir)

  print(f'New relase created in branch: `{branch_name}`')

  return 0


if __name__ == '__main__':
  sys.exit(main(sys.argv[1:]))
