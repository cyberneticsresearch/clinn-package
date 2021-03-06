# -*- coding: utf-8 -*-

# pylint: disable=too-many-lines

"""Build configuration for rtd."""
from __future__ import division, print_function, unicode_literals

import os
import re
from collections import namedtuple
from contextlib import contextmanager

import six

from .find import find_one
from .parser import ParseError, parse
from .validation import (
    ValidationError, validate_bool, validate_choice, validate_dict,
    validate_directory, validate_file, validate_list, validate_string,
    validate_value_exists)

__all__ = (
    'ALL',
    'load',
    'BuildConfigV1',
    'BuildConfigV2',
    'ConfigError',
    'ConfigOptionNotSupportedError',
    'InvalidConfig',
    'ProjectConfig',
)

ALL = 'all'
CONFIG_FILENAMES = ('readthedocs.yml', '.readthedocs.yml')

CONFIG_NOT_SUPPORTED = 'config-not-supported'
VERSION_INVALID = 'version-invalid'
BASE_INVALID = 'base-invalid'
BASE_NOT_A_DIR = 'base-not-a-directory'
CONFIG_SYNTAX_INVALID = 'config-syntax-invalid'
CONFIG_REQUIRED = 'config-required'
NAME_REQUIRED = 'name-required'
NAME_INVALID = 'name-invalid'
CONF_FILE_REQUIRED = 'conf-file-required'
PYTHON_INVALID = 'python-invalid'
SUBMODULES_INVALID = 'submodules-invalid'
INVALID_KEYS_COMBINATION = 'invalid-keys-combination'

DOCKER_DEFAULT_IMAGE = 'readthedocs/build'
DOCKER_DEFAULT_VERSION = '2.0'
# These map to coordisponding settings in the .org,
# so they haven't been renamed.
DOCKER_IMAGE = '{}:{}'.format(DOCKER_DEFAULT_IMAGE, DOCKER_DEFAULT_VERSION)
DOCKER_IMAGE_SETTINGS = {
    'readthedocs/build:1.0': {
        'python': {'supported_versions': [2, 2.7, 3, 3.4]},
    },
    'readthedocs/build:2.0': {
        'python': {'supported_versions': [2, 2.7, 3, 3.5]},
    },
    'readthedocs/build:3.0': {
        'python': {'supported_versions': [2, 2.7, 3, 3.3, 3.4, 3.5, 3.6]},
    },
    'readthedocs/build:stable': {
        'python': {'supported_versions': [2, 2.7, 3, 3.3, 3.4, 3.5, 3.6]},
    },
    'readthedocs/build:latest': {
        'python': {'supported_versions': [2, 2.7, 3, 3.3, 3.4, 3.5, 3.6]},
    },
}


class ConfigError(Exception):

    """Base error for the rtd configuration file."""

    def __init__(self, message, code):
        self.code = code
        super(ConfigError, self).__init__(message)


class ConfigOptionNotSupportedError(ConfigError):

    """Error for unsupported configuration options in a version."""

    def __init__(self, configuration):
        self.configuration = configuration
        template = (
            'The "{}" configuration option is not supported in this version'
        )
        super(ConfigOptionNotSupportedError, self).__init__(
            template.format(self.configuration),
            CONFIG_NOT_SUPPORTED
        )


class InvalidConfig(ConfigError):

    """Error for a specific key validation."""

    message_template = 'Invalid "{key}": {error}'

    def __init__(self, key, code, error_message, source_file=None,
                 source_position=None):
        self.key = key
        self.code = code
        self.source_file = source_file
        self.source_position = source_position
        message = self.message_template.format(
            key=key,
            code=code,
            error=error_message,
        )
        super(InvalidConfig, self).__init__(message, code=code)


class BuildConfigBase(object):

    """
    Config that handles the build of one particular documentation.

    You need to call ``validate`` before the config is ready to use. Also
    setting the ``output_base`` is required before using it for a build.
    """

    version = None

    def __init__(self, env_config, raw_config, source_file, source_position):
        self.env_config = env_config
        self.raw_config = raw_config
        self.source_file = source_file
        self.source_position = source_position
        self.base_path = os.path.dirname(self.source_file)
        self.defaults = self.env_config.get('defaults', {})

        self._config = {}

    def error(self, key, message, code):
        """Raise an error related to ``key``."""
        source = '{file} [{pos}]'.format(
            file=self.source_file,
            pos=self.source_position,
        )
        error_message = '{source}: {message}'.format(
            source=source,
            message=message,
        )
        raise InvalidConfig(
            key=key,
            code=code,
            error_message=error_message,
            source_file=self.source_file,
            source_position=self.source_position,
        )

    @contextmanager
    def catch_validation_error(self, key):
        """Catch a ``ValidationError`` and raises an ``InvalidConfig`` error."""
        try:
            yield
        except ValidationError as error:
            raise InvalidConfig(
                key=key,
                code=error.code,
                error_message=str(error),
                source_file=self.source_file,
                source_position=self.source_position,
            )

    def validate(self):
        raise NotImplementedError()

    @property
    def python_interpreter(self):
        ver = self.python_full_version
        return 'python{0}'.format(ver)

    @property
    def python_full_version(self):
        ver = self.python_version
        if ver in [2, 3]:
            # Get the highest version of the major series version if user only
            # gave us a version of '2', or '3'
            ver = max(
                v
                for v in self.get_valid_python_versions()
                if v < ver + 1
            )
        return ver

    def __getattr__(self, name):
        """Raise an error for unknown attributes."""
        raise ConfigOptionNotSupportedError(name)


class BuildConfigV1(BuildConfigBase):

    """Version 1 of the configuration file."""

    BASE_INVALID_MESSAGE = 'Invalid value for base: {base}'
    BASE_NOT_A_DIR_MESSAGE = '"base" is not a directory: {base}'
    NAME_REQUIRED_MESSAGE = 'Missing key "name"'
    NAME_INVALID_MESSAGE = (
        'Invalid name "{name}". Valid values must match {name_re}'
    )
    CONF_FILE_REQUIRED_MESSAGE = 'Missing key "conf_file"'
    PYTHON_INVALID_MESSAGE = '"python" section must be a mapping.'
    PYTHON_EXTRA_REQUIREMENTS_INVALID_MESSAGE = (
        '"python.extra_requirements" section must be a list.'
    )

    PYTHON_SUPPORTED_VERSIONS = [2, 2.7, 3, 3.5]
    DOCKER_SUPPORTED_VERSIONS = ['1.0', '2.0', 'latest']

    version = '1'

    def get_valid_python_versions(self):
        """Get all valid python versions."""
        try:
            return self.env_config['python']['supported_versions']
        except (KeyError, TypeError):
            pass
        return self.PYTHON_SUPPORTED_VERSIONS

    def get_valid_formats(self):  # noqa
        """Get all valid documentation formats."""
        return (
            'htmlzip',
            'pdf',
            'epub',
        )

    def validate(self):
        """
        Validate and process ``raw_config`` and ``env_config`` attributes.

        It makes sure that:

        - ``base`` is a valid directory and defaults to the directory of the
          ``readthedocs.yml`` config file if not set
        """
        # Validate env_config.
        # TODO: this isn't used
        self._config['output_base'] = self.validate_output_base()

        # Validate the build environment first
        # Must happen before `validate_python`!
        self._config['build'] = self.validate_build()

        # Validate raw_config. Order matters.
        # TODO: this isn't used
        self._config['name'] = self.validate_name()
        # TODO: this isn't used
        self._config['base'] = self.validate_base()
        self._config['python'] = self.validate_python()
        self._config['formats'] = self.validate_formats()

        self._config['conda'] = self.validate_conda()
        self._config['requirements_file'] = self.validate_requirements_file()

    def validate_output_base(self):
        """Validates that ``output_base`` exists and set its absolute path."""
        assert 'output_base' in self.env_config, (
               '"output_base" required in "env_config"')
        base_path = os.path.dirname(self.source_file)
        output_base = os.path.abspath(
            os.path.join(
                self.env_config.get('output_base', base_path),
            )
        )
        return output_base

    def validate_name(self):
        """Validates that name exists."""
        name = self.raw_config.get('name', None)
        if not name:
            name = self.env_config.get('name', None)
        if not name:
            self.error('name', self.NAME_REQUIRED_MESSAGE, code=NAME_REQUIRED)
        name_re = r'^[-_.0-9a-zA-Z]+$'
        if not re.match(name_re, name):
            self.error(
                'name',
                self.NAME_INVALID_MESSAGE.format(
                    name=name,
                    name_re=name_re),
                code=NAME_INVALID)

        return name

    def validate_base(self):
        """Validates that path is a valid directory."""
        if 'base' in self.raw_config:
            base = self.raw_config['base']
        else:
            base = os.path.dirname(self.source_file)
        with self.catch_validation_error('base'):
            base_path = os.path.dirname(self.source_file)
            base = validate_directory(base, base_path)
        return base

    def validate_build(self):
        """
        Validate the build config settings.

        This is a bit complex,
        so here is the logic:

        * We take the default image & version if it's specific in the environment
        * Then update the _version_ from the users config
        * Then append the default _image_, since users can't change this
        * Then update the env_config with the settings for that specific image
           - This is currently used for a build image -> python version mapping

        This means we can use custom docker _images_,
        but can't change the supported _versions_ that users have defined.
        """
        # Defaults
        if 'build' in self.env_config:
            build = self.env_config['build'].copy()
        else:
            build = {'image': DOCKER_IMAGE}

        # User specified
        if 'build' in self.raw_config:
            _build = self.raw_config['build']
            if 'image' in _build:
                with self.catch_validation_error('build'):
                    build['image'] = validate_choice(
                        str(_build['image']),
                        self.DOCKER_SUPPORTED_VERSIONS,
                    )
            if ':' not in build['image']:
                # Prepend proper image name to user's image name
                build['image'] = '{}:{}'.format(
                    DOCKER_DEFAULT_IMAGE,
                    build['image']
                )
        # Update docker default settings from image name
        if build['image'] in DOCKER_IMAGE_SETTINGS:
            self.env_config.update(
                DOCKER_IMAGE_SETTINGS[build['image']]
            )
        # Update docker settings from user config
        if 'DOCKER_IMAGE_SETTINGS' in self.env_config and \
                build['image'] in self.env_config['DOCKER_IMAGE_SETTINGS']:
            self.env_config.update(
                self.env_config['DOCKER_IMAGE_SETTINGS'][build['image']]
            )

        # Allow to override specific project
        config_image = self.defaults.get('build_image')
        if config_image:
            build['image'] = config_image
        return build

    def validate_python(self):
        """Validates the ``python`` key, set default values it's necessary."""
        install_project = self.defaults.get('install_project', False)
        use_system_packages = self.defaults.get('use_system_packages', False)
        version = self.defaults.get('python_version', 2)
        python = {
            'use_system_site_packages': use_system_packages,
            'pip_install': False,
            'extra_requirements': [],
            'setup_py_install': install_project,
            'setup_py_path': os.path.join(
                os.path.dirname(self.source_file),
                'setup.py'),
            'version': version,
        }

        if 'python' in self.raw_config:
            raw_python = self.raw_config['python']
            if not isinstance(raw_python, dict):
                self.error(
                    'python',
                    self.PYTHON_INVALID_MESSAGE,
                    code=PYTHON_INVALID)

            # Validate use_system_site_packages.
            if 'use_system_site_packages' in raw_python:
                with self.catch_validation_error(
                        'python.use_system_site_packages'):
                    python['use_system_site_packages'] = validate_bool(
                        raw_python['use_system_site_packages'])

            # Validate pip_install.
            if 'pip_install' in raw_python:
                with self.catch_validation_error('python.pip_install'):
                    python['pip_install'] = validate_bool(
                        raw_python['pip_install'])

            # Validate extra_requirements.
            if 'extra_requirements' in raw_python:
                raw_extra_requirements = raw_python['extra_requirements']
                if not isinstance(raw_extra_requirements, list):
                    self.error(
                        'python.extra_requirements',
                        self.PYTHON_EXTRA_REQUIREMENTS_INVALID_MESSAGE,
                        code=PYTHON_INVALID)
                for extra_name in raw_extra_requirements:
                    with self.catch_validation_error(
                            'python.extra_requirements'):
                        python['extra_requirements'].append(
                            validate_string(extra_name)
                        )

            # Validate setup_py_install.
            if 'setup_py_install' in raw_python:
                with self.catch_validation_error('python.setup_py_install'):
                    python['setup_py_install'] = validate_bool(
                        raw_python['setup_py_install'])

            # Validate setup_py_path.
            if 'setup_py_path' in raw_python:
                with self.catch_validation_error('python.setup_py_path'):
                    base_path = os.path.dirname(self.source_file)
                    python['setup_py_path'] = validate_file(
                        raw_python['setup_py_path'], base_path)

            if 'version' in raw_python:
                with self.catch_validation_error('python.version'):
                    # Try to convert strings to an int first, to catch '2', then
                    # a float, to catch '2.7'
                    version = raw_python['version']
                    if isinstance(version, six.string_types):
                        try:
                            version = int(version)
                        except ValueError:
                            try:
                                version = float(version)
                            except ValueError:
                                pass
                    python['version'] = validate_choice(
                        version,
                        self.get_valid_python_versions(),
                    )

        return python

    def validate_conda(self):
        """Validates the ``conda`` key."""
        conda = {}

        if 'conda' in self.raw_config:
            raw_conda = self.raw_config['conda']
            if not isinstance(raw_conda, dict):
                self.error(
                    'conda',
                    self.PYTHON_INVALID_MESSAGE,
                    code=PYTHON_INVALID)

            if 'file' in raw_conda:
                with self.catch_validation_error('conda.file'):
                    base_path = os.path.dirname(self.source_file)
                    conda['file'] = validate_file(
                        raw_conda['file'], base_path)

            return conda
        return None

    def validate_requirements_file(self):
        """Validates that the requirements file exists."""
        if 'requirements_file' not in self.raw_config:
            requirements_file = self.defaults.get('requirements_file')
        else:
            requirements_file = self.raw_config['requirements_file']
        if not requirements_file:
            return None
        base_path = os.path.dirname(self.source_file)
        with self.catch_validation_error('requirements_file'):
            validate_file(requirements_file, base_path)
        return requirements_file

    def validate_formats(self):
        """Validates that formats contains only valid formats."""
        formats = self.raw_config.get('formats')
        if formats is None:
            return self.defaults.get('formats', [])
        if formats == ['none']:
            return []

        with self.catch_validation_error('format'):
            validate_list(formats)
            for format_ in formats:
                validate_choice(format_, self.get_valid_formats())

        return formats

    @property
    def name(self):
        """The project name."""
        return self._config['name']

    @property
    def base(self):
        """The base directory."""
        return self._config['base']

    @property
    def output_base(self):
        """The output base."""
        return self._config['output_base']

    @property
    def formats(self):
        """The documentation formats to be built."""
        return self._config['formats']

    @property
    def python(self):
        """Python related configuration."""
        return self._config.get('python', {})

    @property
    def python_version(self):
        """Python version."""
        return self._config['python']['version']

    @property
    def pip_install(self):
        """True if the project should be installed using pip."""
        return self._config['python']['pip_install']

    @property
    def install_project(self):
        """True if the project should be installed."""
        if self.pip_install:
            return True
        return self._config['python']['setup_py_install']

    @property
    def extra_requirements(self):
        """Extra requirements to be installed with pip."""
        if self.pip_install:
            return self._config['python']['extra_requirements']
        return []

    @property
    def use_system_site_packages(self):
        """True if the project should have access to the system packages."""
        return self._config['python']['use_system_site_packages']

    @property
    def use_conda(self):
        """True if the project use Conda."""
        return self._config.get('conda') is not None

    @property
    def conda_file(self):
        """The Conda environment file."""
        if self.use_conda:
            return self._config['conda'].get('file')
        return None

    @property
    def requirements_file(self):
        """The project requirements file."""
        return self._config['requirements_file']

    @property
    def build_image(self):
        """The docker image used by the builders."""
        return self._config['build']['image']


class BuildConfigV2(BuildConfigBase):

    """Version 2 of the configuration file."""

    version = '2'
    valid_formats = ['htmlzip', 'pdf', 'epub']
    valid_build_images = ['1.0', '2.0', '3.0', 'stable', 'latest']
    default_build_image = 'latest'
    valid_install_options = ['pip', 'setup.py']
    valid_sphinx_builders = {
        'html': 'sphinx',
        'htmldir': 'sphinx_htmldir',
        'singlehtml': 'sphinx_singlehtml',
    }

    def validate(self):
        """
        Validates and process ``raw_config`` and ``env_config``.

        Sphinx is the default doc type to be built. We don't merge some values
        from the database (like formats or python.version) to allow us set
        default values.
        """
        self._config['formats'] = self.validate_formats()
        self._config['conda'] = self.validate_conda()
        # This should be called before validate_python
        self._config['build'] = self.validate_build()
        self._config['python'] = self.validate_python()
        # Call this before validate sphinx and mkdocs
        self.validate_doc_types()
        self._config['mkdocs'] = self.validate_mkdocs()
        self._config['sphinx'] = self.validate_sphinx()
        self._config['submodules'] = self.validate_submodules()

    def validate_formats(self):
        """
        Validates that formats contains only valid formats.

        The ``ALL`` keyword can be used to indicate that all formats are used.
        We ignore the default values here.
        """
        formats = self.raw_config.get('formats', [])
        if formats == ALL:
            return self.valid_formats
        with self.catch_validation_error('formats'):
            validate_list(formats)
            for format_ in formats:
                validate_choice(format_, self.valid_formats)
        return formats

    def validate_conda(self):
        """Validates the conda key."""
        raw_conda = self.raw_config.get('conda')
        if raw_conda is None:
            return None

        with self.catch_validation_error('conda'):
            validate_dict(raw_conda)

        conda = {}
        with self.catch_validation_error('conda.environment'):
            environment = validate_value_exists('environment', raw_conda)
            conda['environment'] = validate_file(environment, self.base_path)
        return conda

    def validate_build(self):
        """
        Validates the build object.

        It prioritizes the value from the default image if exists.
        """
        raw_build = self.raw_config.get('build', {})
        with self.catch_validation_error('build'):
            validate_dict(raw_build)
        build = {}
        with self.catch_validation_error('build.image'):
            image = raw_build.get('image', self.default_build_image)
            build['image'] = '{}:{}'.format(
                DOCKER_DEFAULT_IMAGE,
                validate_choice(
                    image,
                    self.valid_build_images,
                ),
            )

        # Allow to override specific project
        config_image = self.defaults.get('build_image')
        if config_image:
            build['image'] = config_image
        return build

    def validate_python(self):
        """
        Validates the python key.

        validate_build should be called before this, since it initialize the
        build.image attribute.

        Fall back to the defaults of:
        - ``requirements``
        - ``install`` (only for setup.py method)
        - ``system_packages``

        .. note::
           - ``version`` can be a string or number type.
           - ``extra_requirements`` needs to be used with ``install: 'pip'``.
        """
        raw_python = self.raw_config.get('python', {})
        with self.catch_validation_error('python'):
            validate_dict(raw_python)

        python = {}
        with self.catch_validation_error('python.version'):
            version = raw_python.get('version', 3)
            if isinstance(version, six.string_types):
                try:
                    version = int(version)
                except ValueError:
                    try:
                        version = float(version)
                    except ValueError:
                        pass
            python['version'] = validate_choice(
                version,
                self.get_valid_python_versions(),
            )

        with self.catch_validation_error('python.requirements'):
            requirements = self.defaults.get('requirements_file')
            requirements = raw_python.get('requirements', requirements)
            if requirements != '' and requirements is not None:
                requirements = validate_file(requirements, self.base_path)
            python['requirements'] = requirements

        with self.catch_validation_error('python.install'):
            install = (
                'setup.py' if self.defaults.get('install_project') else None
            )
            install = raw_python.get('install', install)
            if install is not None:
                validate_choice(install, self.valid_install_options)
            python['install_with_setup'] = install == 'setup.py'
            python['install_with_pip'] = install == 'pip'

        with self.catch_validation_error('python.extra_requirements'):
            extra_requirements = raw_python.get('extra_requirements', [])
            extra_requirements = validate_list(extra_requirements)
            if extra_requirements and not python['install_with_pip']:
                self.error(
                    'python.extra_requirements',
                    'You need to install your project with pip '
                    'to use extra_requirements',
                    code=PYTHON_INVALID,
                )
            python['extra_requirements'] = [
                validate_string(extra) for extra in extra_requirements
            ]

        with self.catch_validation_error('python.system_packages'):
            system_packages = self.defaults.get(
                'use_system_packages',
                False,
            )
            system_packages = raw_python.get(
                'system_packages',
                system_packages,
            )
            python['use_system_site_packages'] = validate_bool(system_packages)

        return python

    def get_valid_python_versions(self):
        """
        Get the valid python versions for the current docker image.

        This should be called after ``validate_build()``.
        """
        build_image = self.build.image
        if build_image not in DOCKER_IMAGE_SETTINGS:
            build_image = '{}:{}'.format(
                DOCKER_DEFAULT_IMAGE,
                self.default_build_image,
            )
        python = DOCKER_IMAGE_SETTINGS[build_image]['python']
        return python['supported_versions']

    def validate_doc_types(self):
        """
        Validates that the user only have one type of documentation.

        This should be called before validating ``sphinx`` or ``mkdocs`` to
        avoid innecessary validations.
        """
        with self.catch_validation_error('.'):
            if 'sphinx' in self.raw_config and 'mkdocs' in self.raw_config:
                self.error(
                    '.',
                    'You can not have the ``sphinx`` and ``mkdocs`` '
                    'keys at the same time',
                    code=INVALID_KEYS_COMBINATION,
                )

    def validate_mkdocs(self):
        """
        Validates the mkdocs key.

        It makes sure we are using an existing configuration file.
        """
        raw_mkdocs = self.raw_config.get('mkdocs')
        if raw_mkdocs is None:
            return None

        with self.catch_validation_error('mkdocs'):
            validate_dict(raw_mkdocs)

        mkdocs = {}
        with self.catch_validation_error('mkdocs.configuration'):
            configuration = raw_mkdocs.get('configuration')
            if configuration is not None:
                configuration = validate_file(configuration, self.base_path)
            mkdocs['configuration'] = configuration

        with self.catch_validation_error('mkdocs.fail_on_warning'):
            fail_on_warning = raw_mkdocs.get('fail_on_warning', False)
            mkdocs['fail_on_warning'] = validate_bool(fail_on_warning)

        return mkdocs

    def validate_sphinx(self):
        """
        Validates the sphinx key.

        It makes sure we are using an existing configuration file.

        .. note::
           It should be called after ``validate_mkdocs``. That way
           we can default to sphinx if ``mkdocs`` is not given.
        """
        raw_sphinx = self.raw_config.get('sphinx')
        if raw_sphinx is None:
            if self.mkdocs is None:
                raw_sphinx = {}
            else:
                return None

        with self.catch_validation_error('sphinx'):
            validate_dict(raw_sphinx)

        sphinx = {}
        with self.catch_validation_error('sphinx.builder'):
            builder = validate_choice(
                raw_sphinx.get('builder', 'html'),
                self.valid_sphinx_builders.keys(),
            )
            sphinx['builder'] = self.valid_sphinx_builders[builder]

        with self.catch_validation_error('sphinx.configuration'):
            configuration = self.defaults.get('sphinx_configuration')
            # The default value can be empty
            if not configuration:
                configuration = None
            configuration = raw_sphinx.get('configuration', configuration)
            if configuration is not None:
                configuration = validate_file(configuration, self.base_path)
            sphinx['configuration'] = configuration

        with self.catch_validation_error('sphinx.fail_on_warning'):
            fail_on_warning = raw_sphinx.get('fail_on_warning', False)
            sphinx['fail_on_warning'] = validate_bool(fail_on_warning)

        return sphinx

    def validate_submodules(self):
        """
        Validates the submodules key.

        - We can use the ``ALL`` keyword in include or exlude.
        - We can't exlude and include submodules at the same time.
        """
        raw_submodules = self.raw_config.get('submodules', {})
        with self.catch_validation_error('submodules'):
            validate_dict(raw_submodules)

        submodules = {}
        with self.catch_validation_error('submodules.include'):
            include = raw_submodules.get('include', [])
            if include != ALL:
                include = [
                    validate_string(submodule)
                    for submodule in validate_list(include)
                ]
            submodules['include'] = include

        with self.catch_validation_error('submodules.exclude'):
            exclude = raw_submodules.get('exclude', [])
            if exclude != ALL:
                exclude = [
                    validate_string(submodule)
                    for submodule in validate_list(exclude)
                ]
            submodules['exclude'] = exclude

        with self.catch_validation_error('submodules'):
            if submodules['exclude'] and submodules['include']:
                self.error(
                    'submodules',
                    'You can not exclude and include submodules '
                    'at the same time',
                    code=SUBMODULES_INVALID,
                )

        with self.catch_validation_error('submodules.recursive'):
            recursive = raw_submodules.get('recursive', False)
            submodules['recursive'] = validate_bool(recursive)

        return submodules

    @property
    def formats(self):
        return self._config['formats']

    @property
    def conda(self):
        Conda = namedtuple('Conda', ['environment'])  # noqa
        if self._config['conda']:
            return Conda(**self._config['conda'])
        return None

    @property
    def build(self):
        Build = namedtuple('Build', ['image'])  # noqa
        return Build(**self._config['build'])

    @property
    def python(self):
        Python = namedtuple(  # noqa
            'Python',
            [
                'version',
                'requirements',
                'install_with_pip',
                'install_with_setup',
                'extra_requirements',
                'use_system_site_packages',
            ],
        )
        return Python(**self._config['python'])

    @property
    def sphinx(self):
        Sphinx = namedtuple(  # noqa
            'Sphinx',
            ['builder', 'configuration', 'fail_on_warning'],
        )
        if self._config['sphinx']:
            return Sphinx(**self._config['sphinx'])
        return None

    @property
    def mkdocs(self):
        Mkdocs = namedtuple(  # noqa
            'Mkdocs',
            ['configuration', 'fail_on_warning'],
        )
        if self._config['mkdocs']:
            return Mkdocs(**self._config['mkdocs'])
        return None

    @property
    def doctype(self):
        if self.mkdocs:
            return 'mkdocs'
        return self.sphinx.builder

    @property
    def submodules(self):
        Submodules = namedtuple(  # noqa
            'Submodules',
            ['include', 'exclude', 'recursive'],
        )
        return Submodules(**self._config['submodules'])


class ProjectConfig(list):

    """Wrapper for multiple build configs."""

    def validate(self):
        """Validates each configuration build."""
        for build in self:
            build.validate()


def load(path, env_config):
    """
    Load a project configuration and the top-most build config for a given path.

    That is usually the root of the project, but will look deeper. According to
    the version of the configuration a build object would be load and validated,
    ``BuildConfigV1`` is the default.
    """
    filename = find_one(path, CONFIG_FILENAMES)

    if not filename:
        files = '{}'.format(', '.join(map(repr, CONFIG_FILENAMES[:-1])))
        if files:
            files += ' or '
        files += '{!r}'.format(CONFIG_FILENAMES[-1])
        raise ConfigError(
            'No files {} found'.format(files),
            code=CONFIG_REQUIRED,
        )
    build_configs = []
    with open(filename, 'r') as configuration_file:
        try:
            configs = parse(configuration_file.read())
        except ParseError as error:
            raise ConfigError(
                'Parse error in {filename}: {message}'.format(
                    filename=filename,
                    message=str(error),
                ),
                code=CONFIG_SYNTAX_INVALID,
            )
        for i, config in enumerate(configs):
            allow_v2 = env_config.get('allow_v2')
            if allow_v2:
                version = config.get('version', 1)
            else:
                version = 1
            build_config = get_configuration_class(version)(
                env_config,
                config,
                source_file=filename,
                source_position=i,
            )
            build_configs.append(build_config)

    project_config = ProjectConfig(build_configs)
    project_config.validate()
    return project_config


def get_configuration_class(version):
    """
    Get the appropriate config class for ``version``.

    :type version: str or int
    """
    configurations_class = {
        1: BuildConfigV1,
        2: BuildConfigV2,
    }
    try:
        version = int(version)
        return configurations_class[version]
    except (KeyError, ValueError):
        raise ConfigError(
            'Invalid version of the configuration file',
            code=VERSION_INVALID,
        )
