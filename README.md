django-urlographer
==================

A URL mapper for the django web framework.

[![Build Status](https://travis-ci.org/ConsumerAffairs/django-urlographer.png?branch=master)](https://travis-ci.org/ConsumerAffairs/django-urlographer)
[![Coverage Status](https://coveralls.io/repos/ConsumerAffairs/django-urlographer/badge.png)](https://coveralls.io/r/ConsumerAffairs/django-urlographer)


Features
--

* supplements the django url resolution
* database + cache driven
* automatic caching and cache invalidation on save
* permanent and temporary redirects
* map url to arbitrary status code
* url canonicalization
    * lowercase
    * ascii
    * eliminate relative paths
    * extra slashes


To run tests locally
--

```
tox
```

To run tests for a specific environment:
```
tox -e django19
```


To debug tests locally
--

* Add `ipdb` to `deps` in `[base]`, example:

```[base]
deps =
    mox
    nose
    ipdb
```

Run this command:
```
tox -e django19 -- -s
```

To run tests locally
--

```
fab test
```

To run coverage locally AND report it
--

```
fab test_coverage
coverage report -m
```

To install in current virtualenv
--
Make sure your virtualenv is loaded and:
```
python setup.py install
```
