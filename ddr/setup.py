#!/usr/bin/env python

import codecs
from distutils.core import setup
import os
import re

here = os.path.abspath(os.path.dirname(__file__))

def read(*parts):
    # intentionally *not* adding an encoding option to open
    return codecs.open(os.path.join(here, *parts), 'r').read()

def find_version(*file_paths):
    version_file = read(*file_paths)
    version_match = re.search(r"^VERSION = ['\"]([^'\"]*)['\"]",
                              version_file, re.M)
    if version_match:
        return version_match.group(1)
    raise RuntimeError("Unable to find version string.")

long_description = read('README.rst')

setup(
    url = 'https://github.com/densho/ddr-cmdln/',
    download_url = 'https://github.com/densho/ddr-cmdln.git',
    name = 'ddr-cmdln',
    description = 'ddr-cmdln',
    long_description = long_description,
    version = find_version('DDR', '__init__.py'),
    #license = 'TBD',
    author = 'Geoffrey Jost',
    author_email = 'geoffrey.jost@densho.org',
    platforms = 'Linux',
    classifiers = [  # https://pypi.python.org/pypi?:action=list_classifiers
        'Development Status :: 4 - Beta',
        'Natural Language :: English',
        'Environment :: Console',
        'Intended Audience :: Other Audience',
        #'License :: OSI Approved :: TBD',
        'Natural Language :: English',
        'Operating System :: POSIX :: Linux',
        'Programming Language :: Python',
        'Programming Language :: Python :: 2',
        'Topic :: Other/Nonlisted Topic',
        'Topic :: Sociology :: History',
    ],
    install_requires = [
        'nose'
    ],
    packages = [
        'DDR',
        'DDR.converters'
    ],
    include_package_data = True,
    package_dir = {
        'DDR': 'DDR'
    },
    package_data = {'DDR': [
        '*.tpl',
        'models/*',
        'templates/*',
    ]},
    scripts = [
        'bin/ddr',
        'bin/ddr-checkencoding',
        'bin/ddr-export',
        'bin/ddr-import',
        'bin/ddrfilter',
        'bin/ddrindex',
        'bin/ddrmassupdate',
        'bin/ddrpubcopy',
        'bin/ddrdensho255fix',
    ],
)
