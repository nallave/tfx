# Copyright 2020 Google LLC. All Rights Reserved.
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
"""TFX template taxi model.

A DNN keras model which uses features defined in features.py and network
parameters defined in constants.py.
"""

from absl import logging
import tensorflow as tf
import tensorflow_transform as tft

from tfx.experimental.templates.taxi.models import features
from tfx.experimental.templates.taxi.models.keras_model import constants
from tfx_bsl.public import tfxio


def _get_tf_examples_serving_signature(model, tf_transform_output):
  """Returns a serving signature that accepts `tensorflow.Example`."""

  # We need to track the layers in the model in order to save it.
  # TODO(b/162357359): Revise once the bug is resolved.
  model.tft_layer_inference = tf_transform_output.transform_features_layer()

  @tf.function(input_signature=[
      tf.TensorSpec(shape=[None], dtype=tf.string, name='examples')
  ])
  def serve_tf_examples_fn(serialized_tf_example):
    """Returns the output to be used in the serving signature."""
    raw_feature_spec = tf_transform_output.raw_feature_spec()
    # Remove label feature since these will not be present at serving time.
    raw_feature_spec.pop(features.LABEL_KEY)
    raw_features = tf.io.parse_example(serialized_tf_example, raw_feature_spec)
    transformed_features = model.tft_layer_inference(raw_features)
    logging.info('serve_transformed_features = %s', transformed_features)

    outputs = model(transformed_features)
    # TODO(b/154085620): Convert the predicted labels from the model using a
    # reverse-lookup (opposite of transform.py).
    return {'outputs': outputs}

  return serve_tf_examples_fn


def _get_transform_features_signature(model, tf_transform_output):
  """Returns a serving signature that applies tf.Transform to features."""

  # We need to track the layers in the model in order to save it.
  # TODO(b/162357359): Revise once the bug is resolved.
  model.tft_layer_eval = tf_transform_output.transform_features_layer()

  @tf.function(input_signature=[
      tf.TensorSpec(shape=[None], dtype=tf.string, name='examples')
  ])
  def transform_features_fn(serialized_tf_example):
    """Returns the transformed_features to be fed as input to evaluator."""
    raw_feature_spec = tf_transform_output.raw_feature_spec()
    raw_features = tf.io.parse_example(serialized_tf_example, raw_feature_spec)
    transformed_features = model.tft_layer_eval(raw_features)
    logging.info('eval_transformed_features = %s', transformed_features)
    return transformed_features

  return transform_features_fn


def _input_fn(file_pattern, data_accessor, tf_transform_output, batch_size=200):
  """Generates features and label for tuning/training.

  Args:
    file_pattern: List of paths or patterns of input tfrecord files.
    data_accessor: DataAccessor for converting input to RecordBatch.
    tf_transform_output: A TFTransformOutput.
    batch_size: representing the number of consecutive elements of returned
      dataset to combine in a single batch

  Returns:
    A dataset that contains (features, indices) tuple where features is a
      dictionary of Tensors, and indices is a single Tensor of label indices.
  """
  return data_accessor.tf_dataset_factory(
      file_pattern,
      tfxio.TensorFlowDatasetOptions(
          batch_size=batch_size,
          label_key=features.transformed_name(features.LABEL_KEY)),
      tf_transform_output.transformed_metadata.schema).repeat()


def _build_keras_model(hidden_units, learning_rate):
  """Creates a DNN Keras model for classifying taxi data.

  Args:
    hidden_units: [int], the layer sizes of the DNN (input layer first).
    learning_rate: [float], learning rate of the Adam optimizer.

  Returns:
    A keras Model.
  """
  deep_input = {
      colname: tf.keras.layers.Input(name=colname, shape=(1,), dtype=tf.float32)
      for colname in features.transformed_names(features.DENSE_FLOAT_FEATURE_KEYS)
  }
  wide_vocab_input = {
      colname: tf.keras.layers.Input(name=colname, shape=(1,), dtype='int32')
      for colname in features.transformed_names(features.VOCAB_FEATURE_KEYS)
  }
  wide_bucket_input = {
      colname: tf.keras.layers.Input(name=colname, shape=(1,), dtype='int32')
      for colname in features.transformed_names(features.BUCKET_FEATURE_KEYS)
  }
  wide_categorical_input = {
      colname: tf.keras.layers.Input(name=colname, shape=(1,), dtype='int32')
      for colname in features.transformed_names(features.CATEGORICAL_FEATURE_KEYS)
  }
  input_layers = {
      **deep_input,
      **wide_vocab_input,
      **wide_bucket_input,
      **wide_categorical_input,
  }

  deep = tf.keras.layers.concatenate(
      [tf.keras.layers.Normalization()(layer) for layer in deep_input.values()]
  )
  for numnodes in (hidden_units or [100, 70, 50, 25]):
    deep = tf.keras.layers.Dense(numnodes)(deep)

  wide_layers = []
  for key in features.transformed_names(features.VOCAB_FEATURE_KEYS):
    wide_layers.append(
        tf.keras.layers.CategoryEncoding(num_tokens=features.VOCAB_SIZE + features.OOV_SIZE)(
            input_layers[key]
        )
    )
  for key, num_tokens in zip(
      features.transformed_names(features.BUCKET_FEATURE_KEYS),
      features.BUCKET_FEATURE_BUCKET_COUNT,
  ):
    wide_layers.append(
        tf.keras.layers.CategoryEncoding(num_tokens=num_tokens)(
                input_layers[key]
        )
    )
  for key, num_tokens in zip(
      features.transformed_names(features.CATEGORICAL_FEATURE_KEYS),
      features.CATEGORICAL_FEATURE_MAX_VALUES,
  ):
    wide_layers.append(
        tf.keras.layers.CategoryEncoding(num_tokens=num_tokens)(
            input_layers[key]
        )
    )
  wide = tf.keras.layers.concatenate(wide_layers)

  output = tf.keras.layers.Dense(1, activation='sigmoid')(
      tf.keras.layers.concatenate([deep, wide])
  )
  output = tf.keras.layers.Reshape((1,))(output)

  model = tf.keras.Model(input_layers, output)
  model.compile(
      loss='binary_crossentropy',
      optimizer=tf.keras.optimizers.Adam(learning_rate=learning_rate),
      metrics=[tf.keras.metrics.BinaryAccuracy()],
  )
  model.summary(print_fn=logging.info)
  return model


# TFX Trainer will call this function.
def run_fn(fn_args):
  """Train the model based on given args.

  Args:
    fn_args: Holds args used to train the model as name/value pairs.
  """

  tf_transform_output = tft.TFTransformOutput(fn_args.transform_output)

  train_dataset = _input_fn(fn_args.train_files, fn_args.data_accessor,
                            tf_transform_output, constants.TRAIN_BATCH_SIZE)
  eval_dataset = _input_fn(fn_args.eval_files, fn_args.data_accessor,
                           tf_transform_output, constants.EVAL_BATCH_SIZE)

  mirrored_strategy = tf.distribute.MirroredStrategy()
  with mirrored_strategy.scope():
    model = _build_keras_model(
        hidden_units=constants.HIDDEN_UNITS,
        learning_rate=constants.LEARNING_RATE)

  # Write logs to path
  tensorboard_callback = tf.keras.callbacks.TensorBoard(
      log_dir=fn_args.model_run_dir, update_freq='epoch')

  model.fit(
      train_dataset,
      steps_per_epoch=fn_args.train_steps,
      validation_data=eval_dataset,
      validation_steps=fn_args.eval_steps,
      callbacks=[tensorboard_callback])

  signatures = {
      'serving_default':
          _get_tf_examples_serving_signature(model, tf_transform_output),
      'transform_features':
          _get_transform_features_signature(model, tf_transform_output),
  }
  model.save(fn_args.serving_model_dir, save_format='tf', signatures=signatures)
