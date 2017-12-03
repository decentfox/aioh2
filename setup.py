#!/usr/bin/env python

try:
    from setuptools import setup
except ImportError:
    from distutils.core import setup

with open('README.rst') as readme_file:
    readme = readme_file.read()

with open('HISTORY.rst') as history_file:
    history = history_file.read()

requirements = [
    'h2 >=3, <4',
    'priority==1.3.0',
]

test_requirements = [
]

setup(
    name='aioh2',
    version='0.2.1',
    description="HTTP/2 implementation with hyper-h2 on Python 3 asyncio.",
    long_description=readme + '\n\n' + history,
    author="Fantix King",
    author_email='fantix.king@gmail.com',
    url='https://github.com/decentfox/aioh2',
    packages=[
        'aioh2',
    ],
    package_dir={
        'aioh2': 'aioh2',
    },
    include_package_data=True,
    install_requires=requirements,
    license="BSD",
    zip_safe=False,
    keywords='aioh2',
    classifiers=[
        'Development Status :: 2 - Pre-Alpha',
        'Intended Audience :: Developers',
        'License :: OSI Approved :: BSD License',
        'Natural Language :: English',
        'Programming Language :: Python :: 3',
        'Programming Language :: Python :: 3.4',
        'Programming Language :: Python :: 3.5',
        'Programming Language :: Python :: 3.6',
    ],
    test_suite='tests',
    tests_require=test_requirements
)
