# Copyright 2019 Google LLC. All Rights Reserved.
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#     http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.
"""Utilities for retrieving paths for various types of artifacts."""

import os
import absl

from tfx.dsl.io import fileio
from tfx.types import artifact
from tfx.types import artifact_utils
from tfx.types import standard_artifacts
from tfx.utils import io_utils

_OLD_EVAL_MODEL_DIR = 'eval_model_dir'
_OLD_SERVING_MODEL_DIR = 'serving_model_dir'
EVAL_MODEL_DIR = 'Format-TFMA'
# LINT.IfChange
SERVING_MODEL_DIR = 'Format-Serving'
# LINT.ThenChange(Internal serving model dir)
STAMPED_MODEL_DIR = 'stamped_model'

"""Directory structure of exported model for estimator based trainer:

  |-- <ModelExportPath>
      |-- EVAL_MODEL_DIR  <- eval_model_dir, eval_model_path
          |-- saved_model.pb
          |-- ...
      |-- SERVING_MODEL_DIR  <- serving_model_dir, serving_model_path
          |-- saved_model.pb
          |-- ...

For generic trainer with Keras, there won't be eval model:
  |-- <ModelExportPath>
      |-- SERVING_MODEL_DIR  <- serving_model_dir, serving_model_path
          |-- saved_model.pb
          |-- ...

TODO(b/160795287): Deprecate estimator based executor.

Support for estimator-based executor and model export will be
deprecated soon. The following estimator working directory
structure is still supported for backwards compatibility:

Directory structure of exported model for estimator based trainer:
  |-- <ModelExportPath>
      |-- EVAL_MODEL_DIR  <- eval_model_dir
          |-- <timestamped model>  <- eval_model_path
              |-- saved_model.pb
              |-- ...
      |-- SERVING_MODEL_DIR  <- serving_model_dir
          |-- export
              |-- <exporter name>
                  |-- <timestamped model>  <- serving_model_path
                      |-- saved_model.pb
                      |-- ...
          |-- ...
"""


def is_old_model_artifact(model_artifact: artifact.Artifact) -> bool:
  """Check whether the model artifact is generated by old TFX version."""
  assert model_artifact.type == standard_artifacts.Model, ('Wrong artifact '
                                                           'type, only accept '
                                                           'Model.')
  return artifact_utils.is_artifact_version_older_than(
      model_artifact, artifact_utils._ARTIFACT_VERSION_FOR_MODEL_UPDATE)  # pylint: disable=protected-access


def eval_model_dir(output_uri: str, is_old_artifact: bool = False) -> str:
  """Returns directory for exported model for evaluation purpose."""
  if is_old_artifact:
    return os.path.join(output_uri, _OLD_EVAL_MODEL_DIR)
  return os.path.join(output_uri, EVAL_MODEL_DIR)


def eval_model_path(output_uri: str, is_old_artifact: bool = False) -> str:
  """Returns final path to exported model for evaluation purpose."""
  model_dir = eval_model_dir(output_uri, is_old_artifact)
  model_file = os.path.join(model_dir, 'saved_model.pb')
  if fileio.exists(model_file):
    return model_dir
  elif fileio.exists(model_dir):
    # TODO(b/160795287): Deprecate estimator based executor.
    absl.logging.warning('Support for estimator-based executor and model'
                         ' export will be deprecated soon. Please use'
                         ' export structure '
                         '<ModelExportPath>/eval_model_dir/saved_model.pb"')
    return io_utils.get_only_uri_in_dir(model_dir)
  else:
    # If eval model doesn't exist, use serving model for eval.
    return serving_model_path(output_uri, is_old_artifact)


def serving_model_dir(output_uri: str, is_old_artifact: bool = False) -> str:
  """Returns directory for exported model for serving purpose."""
  if is_old_artifact:
    return os.path.join(output_uri, _OLD_SERVING_MODEL_DIR)
  return os.path.join(output_uri, SERVING_MODEL_DIR)


def serving_model_path(output_uri: str, is_old_artifact: bool = False) -> str:
  """Returns path for exported serving model."""
  model_dir = serving_model_dir(output_uri, is_old_artifact)
  export_dir = os.path.join(model_dir, 'export')
  if fileio.exists(export_dir):
    # TODO(b/160795287): Deprecate estimator based executor.
    absl.logging.warning(
        'Support for estimator-based executor and model export'
        ' will be deprecated soon. Please use export structure '
        '<ModelExportPath>/serving_model_dir/saved_model.pb"')
    model_dir = io_utils.get_only_uri_in_dir(export_dir)
    return io_utils.get_only_uri_in_dir(model_dir)
  else:
    # If dir doesn't match estimator structure, use serving model root directly.
    return model_dir


def stamped_model_path(output_uri: str) -> str:
  """Returns path for the stamped model."""
  return os.path.join(output_uri, STAMPED_MODEL_DIR)


def warmup_file_path(saved_model_path: str) -> str:
  """Returns SavedModel Warmup file path.

  See https://www.tensorflow.org/tfx/serving/saved_model_warmup.
  This is a lexical operation, and does not guarantee the path is valid.

  Args:
    saved_model_path: A POSIX path to the TensorFlow SavedModel.

  Returns:
    A POSIX path to the SavedModel Warmup file.
  """
  return os.path.join(
      saved_model_path,
      'assets.extra',
      'tf_serving_warmup_requests')
