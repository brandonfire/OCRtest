#Modified OCR based on keras_ocr
#Chengbin Hu
import numpy as np
import typing
import string
import tensorflow as tf
import cv2
from tensorflow import keras
from keras_ocr import detection, recognition, tools
DEFAULT_BUILD_PARAMS = {
    'height': 31,
    'width': 200,
    'color': False,
    'filters': (64, 128, 256, 256, 512, 512, 512),
    'rnn_units': (128, 128),
    'dropout': 0.25,
    'rnn_steps_to_discard': 2,
    'pool_size': 2,
    'stn': True,
}
DEFAULT_ALPHABET = string.digits + string.ascii_lowercase

PRETRAINED_WEIGHTS = {
    'kurapan': {
        'alphabet': DEFAULT_ALPHABET,
        'build_params': DEFAULT_BUILD_PARAMS,
        'weights': {
            'notop': {
                'url': 'https://www.mediafire.com/file/n9yfn5wueu82rgf/crnn_kurapan_notop.h5/file',
                'filename': 'crnn_kurapan_notop.h5',
                'sha256': '027fd2cced3cbea0c4f5894bb8e9e85bac04f11daf96b8fdcf1e4ee95dcf51b9'
            },
            'top': {
                'url': 'https://www.mediafire.com/file/pkj2p29b1f6fpil/crnn_kurapan.h5/file',
                'filename': 'crnn_kurapan.h5',
                'sha256': 'a7d8086ac8f5c3d6a0a828f7d6fbabcaf815415dd125c32533013f85603be46d'
            }
        }
    }
}
def swish(x, beta=1):
    return x * keras.backend.sigmoid(beta * x)


keras.utils.get_custom_objects().update({'swish': keras.layers.Activation(swish)})


def _repeat(x, num_repeats):
    ones = tf.ones((1, num_repeats), dtype='int32')
    x = tf.reshape(x, shape=(-1, 1))
    x = tf.matmul(x, ones)
    return tf.reshape(x, [-1])


def _meshgrid(height, width):
    x_linspace = tf.linspace(-1., 1., width)
    y_linspace = tf.linspace(-1., 1., height)
    x_coordinates, y_coordinates = tf.meshgrid(x_linspace, y_linspace)
    x_coordinates = tf.reshape(x_coordinates, shape=(1, -1))
    y_coordinates = tf.reshape(y_coordinates, shape=(1, -1))
    ones = tf.ones_like(x_coordinates)
    indices_grid = tf.concat([x_coordinates, y_coordinates, ones], 0)
    return indices_grid


# pylint: disable=too-many-statements
def _transform(inputs):
    locnet_x, locnet_y = inputs
    output_size = locnet_x.shape[1:]
    batch_size = tf.shape(locnet_x)[0]
    height = tf.shape(locnet_x)[1]
    width = tf.shape(locnet_x)[2]
    num_channels = tf.shape(locnet_x)[3]

    locnet_y = tf.reshape(locnet_y, shape=(batch_size, 2, 3))

    locnet_y = tf.reshape(locnet_y, (-1, 2, 3))
    locnet_y = tf.cast(locnet_y, 'float32')

    output_height = output_size[0]
    output_width = output_size[1]
    indices_grid = _meshgrid(output_height, output_width)
    indices_grid = tf.expand_dims(indices_grid, 0)
    indices_grid = tf.reshape(indices_grid, [-1])  # flatten?
    indices_grid = tf.tile(indices_grid, tf.stack([batch_size]))
    indices_grid = tf.reshape(indices_grid, tf.stack([batch_size, 3, -1]))

    transformed_grid = tf.matmul(locnet_y, indices_grid)
    x_s = tf.slice(transformed_grid, [0, 0, 0], [-1, 1, -1])
    y_s = tf.slice(transformed_grid, [0, 1, 0], [-1, 1, -1])
    x = tf.reshape(x_s, [-1])
    y = tf.reshape(y_s, [-1])

    # Interpolate
    height_float = tf.cast(height, dtype='float32')
    width_float = tf.cast(width, dtype='float32')

    output_height = output_size[0]
    output_width = output_size[1]

    x = tf.cast(x, dtype='float32')
    y = tf.cast(y, dtype='float32')
    x = .5 * (x + 1.0) * width_float
    y = .5 * (y + 1.0) * height_float

    x0 = tf.cast(tf.floor(x), 'int32')
    x1 = x0 + 1
    y0 = tf.cast(tf.floor(y), 'int32')
    y1 = y0 + 1

    max_y = tf.cast(height - 1, dtype='int32')
    max_x = tf.cast(width - 1, dtype='int32')
    zero = tf.zeros([], dtype='int32')

    x0 = tf.clip_by_value(x0, zero, max_x)
    x1 = tf.clip_by_value(x1, zero, max_x)
    y0 = tf.clip_by_value(y0, zero, max_y)
    y1 = tf.clip_by_value(y1, zero, max_y)

    flat_image_dimensions = width * height
    pixels_batch = tf.range(batch_size) * flat_image_dimensions
    flat_output_dimensions = output_height * output_width
    base = _repeat(pixels_batch, flat_output_dimensions)
    base_y0 = base + y0 * width
    base_y1 = base + y1 * width
    indices_a = base_y0 + x0
    indices_b = base_y1 + x0
    indices_c = base_y0 + x1
    indices_d = base_y1 + x1

    flat_image = tf.reshape(locnet_x, shape=(-1, num_channels))
    flat_image = tf.cast(flat_image, dtype='float32')
    pixel_values_a = tf.gather(flat_image, indices_a)
    pixel_values_b = tf.gather(flat_image, indices_b)
    pixel_values_c = tf.gather(flat_image, indices_c)
    pixel_values_d = tf.gather(flat_image, indices_d)

    x0 = tf.cast(x0, 'float32')
    x1 = tf.cast(x1, 'float32')
    y0 = tf.cast(y0, 'float32')
    y1 = tf.cast(y1, 'float32')

    area_a = tf.expand_dims(((x1 - x) * (y1 - y)), 1)
    area_b = tf.expand_dims(((x1 - x) * (y - y0)), 1)
    area_c = tf.expand_dims(((x - x0) * (y1 - y)), 1)
    area_d = tf.expand_dims(((x - x0) * (y - y0)), 1)
    transformed_image = tf.add_n([
        area_a * pixel_values_a, area_b * pixel_values_b, area_c * pixel_values_c,
        area_d * pixel_values_d
    ])
    # Finished interpolation

    transformed_image = tf.reshape(transformed_image,
                                   shape=(batch_size, output_height, output_width, num_channels))
    return transformed_image


def CTCDecoder():
    def decoder(y_pred):
        input_shape = tf.keras.backend.shape(y_pred)
        input_length = tf.ones(shape=input_shape[0]) * tf.keras.backend.cast(
            input_shape[1], 'float32')
        unpadded = tf.keras.backend.ctc_decode(y_pred, input_length)[0][0]
        unpadded_shape = tf.keras.backend.shape(unpadded)
        padded = tf.pad(unpadded,
                        paddings=[[0, 0], [0, input_shape[1] - unpadded_shape[1]]],
                        constant_values=-1)
        return padded

    return tf.keras.layers.Lambda(decoder, name='decode')


def build_model(alphabet,
                height,
                width,
                color,
                filters,
                rnn_units,
                dropout,
                rnn_steps_to_discard,
                pool_size,
                stn=True):
    """Build a Keras CRNN model for character recognition.

    Args:
        height: The height of cropped images
        width: The width of cropped images
        color: Whether the inputs should be in color (RGB)
        filters: The number of filters to use for each of the 7 convolutional layers
        rnn_units: The number of units for each of the RNN layers
        dropout: The dropout to use for the final layer
        rnn_steps_to_discard: The number of initial RNN steps to discard
        pool_size: The size of the pooling steps
        stn: Whether to add a Spatial Transformer layer
    """
    assert len(filters) == 7, '7 CNN filters must be provided.'
    assert len(rnn_units) == 2, '2 RNN filters must be provided.'
    inputs = keras.layers.Input((height, width, 3 if color else 1))
    x = keras.layers.Permute((2, 1, 3))(inputs)
    x = keras.layers.Lambda(lambda x: x[:, :, ::-1])(x)
    x = keras.layers.Conv2D(filters[0], (3, 3), activation='relu', padding='same', name='conv_1')(x)
    x = keras.layers.Conv2D(filters[1], (3, 3), activation='relu', padding='same', name='conv_2')(x)
    x = keras.layers.Conv2D(filters[2], (3, 3), activation='relu', padding='same', name='conv_3')(x)
    x = keras.layers.BatchNormalization(name='bn_3')(x)
    x = keras.layers.MaxPooling2D(pool_size=(pool_size, pool_size), name='maxpool_3')(x)
    x = keras.layers.Conv2D(filters[3], (3, 3), activation='relu', padding='same', name='conv_4')(x)
    x = keras.layers.Conv2D(filters[4], (3, 3), activation='relu', padding='same', name='conv_5')(x)
    x = keras.layers.BatchNormalization(name='bn_5')(x)
    x = keras.layers.MaxPooling2D(pool_size=(pool_size, pool_size), name='maxpool_5')(x)
    x = keras.layers.Conv2D(filters[5], (3, 3), activation='relu', padding='same', name='conv_6')(x)
    x = keras.layers.Conv2D(filters[6], (3, 3), activation='relu', padding='same', name='conv_7')(x)
    x = keras.layers.BatchNormalization(name='bn_7')(x)
    if stn:
        # pylint: disable=pointless-string-statement
        """Spatial Transformer Layer
        Implements a spatial transformer layer as described in [1]_.
        Borrowed from [2]_:
        downsample_fator : float
            A value of 1 will keep the orignal size of the image.
            Values larger than 1 will down sample the image. Values below 1 will
            upsample the image.
            example image: height= 100, width = 200
            downsample_factor = 2
            output image will then be 50, 100
        References
        ----------
        .. [1]  Spatial Transformer Networks
                Max Jaderberg, Karen Simonyan, Andrew Zisserman, Koray Kavukcuoglu
                Submitted on 5 Jun 2015
        .. [2]  https://github.com/skaae/transformer_network/blob/master/transformerlayer.py
        .. [3]  https://github.com/EderSantana/seya/blob/keras1/seya/layers/attention.py
        """
        stn_input_output_shape = (width // pool_size**2, height // pool_size**2, filters[6])
        stn_input_layer = keras.layers.Input(shape=stn_input_output_shape)
        locnet_y = keras.layers.Conv2D(16, (5, 5), padding='same',
                                       activation='relu')(stn_input_layer)
        locnet_y = keras.layers.Conv2D(32, (5, 5), padding='same', activation='relu')(locnet_y)
        locnet_y = keras.layers.Flatten()(locnet_y)
        locnet_y = keras.layers.Dense(64, activation='relu')(locnet_y)
        locnet_y = keras.layers.Dense(6,
                                      weights=[
                                          np.zeros((64, 6), dtype='float32'),
                                          np.float32([[1, 0, 0], [0, 1, 0]]).flatten()
                                      ])(locnet_y)
        localization_net = keras.models.Model(inputs=stn_input_layer, outputs=locnet_y)
        x = keras.layers.Lambda(_transform,
                                output_shape=stn_input_output_shape)([x, localization_net(x)])
    x = keras.layers.Reshape(target_shape=(width // pool_size**2,
                                           (height // pool_size**2) * filters[-1]),
                             name='reshape')(x)

    x = keras.layers.Dense(rnn_units[0], activation='relu', name='fc_9')(x)

    rnn_1_forward = keras.layers.LSTM(rnn_units[0],
                                      kernel_initializer="he_normal",
                                      return_sequences=True,
                                      name='lstm_10')(x)
    rnn_1_back = keras.layers.LSTM(rnn_units[0],
                                   kernel_initializer="he_normal",
                                   go_backwards=True,
                                   return_sequences=True,
                                   name='lstm_10_back')(x)
    rnn_1_add = keras.layers.Add()([rnn_1_forward, rnn_1_back])
    rnn_2_forward = keras.layers.LSTM(rnn_units[1],
                                      kernel_initializer="he_normal",
                                      return_sequences=True,
                                      name='lstm_11')(rnn_1_add)
    rnn_2_back = keras.layers.LSTM(rnn_units[1],
                                   kernel_initializer="he_normal",
                                   go_backwards=True,
                                   return_sequences=True,
                                   name='lstm_11_back')(rnn_1_add)
    x = keras.layers.Concatenate()([rnn_2_forward, rnn_2_back])
    backbone = keras.models.Model(inputs=inputs, outputs=x)
    x = keras.layers.Dropout(dropout, name='dropout')(x)
    x = keras.layers.Dense(len(alphabet) + 1,
                           kernel_initializer='he_normal',
                           activation='softmax',
                           name='fc_12')(x)
    x = keras.layers.Lambda(lambda x: x[:, rnn_steps_to_discard:])(x)
    model = keras.models.Model(inputs=inputs, outputs=x)

    prediction_model = keras.models.Model(inputs=inputs, outputs= model.output)#CTCDecoder()(model.output))
    labels = keras.layers.Input(name='labels', shape=[model.output_shape[1]], dtype='float32')
    label_length = keras.layers.Input(shape=[1])
    input_length = keras.layers.Input(shape=[1])
    loss = keras.layers.Lambda(lambda inputs: keras.backend.ctc_batch_cost(
        y_true=inputs[0], y_pred=inputs[1], input_length=inputs[2], label_length=inputs[3]))(
            [labels, model.output, input_length, label_length])
    training_model = keras.models.Model(inputs=[model.input, labels, input_length, label_length],
                                        outputs=loss)
    return backbone, model, training_model, prediction_model



class Pipeline:
    """A wrapper for a combination of detector and recognizer.

    Args:
        detector: The detector to use
        recognizer: The recognizer to use
        scale: The scale factor to apply to input images
        max_size: The maximum single-side dimension of images for
            inference.
    """
    def __init__(self, detector=None, scale=2, max_size=2048):
        if detector is None:
            detector = detection.Detector()
        #if recognizer is None:
        #    recognizer = recognition.Recognizer()
        self.scale = scale
        self.detector = detector
        #self.recognizer = recognizer
        self.max_size = max_size
        #chengbin modified
        self.build_params = PRETRAINED_WEIGHTS['kurapan']['build_params']
        self.alphabet = PRETRAINED_WEIGHTS['kurapan']['alphabet']
        self.blank_label_idx = len(self.alphabet)
        self.backbone, self.model, self.training_model, self.prediction_model = build_model(
            alphabet=self.alphabet, **self.build_params)
        weights_dict = PRETRAINED_WEIGHTS['kurapan']
        if self.alphabet == weights_dict['alphabet']:
            self.model.load_weights(
                    tools.download_and_verify(url=weights_dict['weights']['top']['url'],
                                              filename=weights_dict['weights']['top']['filename'],
                                              sha256=weights_dict['weights']['top']['sha256']))

    def recognize(self, images, detection_kwargs=None, recognition_kwargs=None):
        """Run the pipeline on one or multiples images.

        Args:
            images: The images to parse (can be a list of actual images or a list of filepaths)
            detection_kwargs: Arguments to pass to the detector call
            recognition_kwargs: Arguments to pass to the recognizer call

        Returns:
            A list of lists of (text, box) tuples.
        """

        # Make sure we have an image array to start with.
        if not isinstance(images, np.ndarray):#chengbin: If images are not array. we read image from the file path.
            images = [tools.read(image) for image in images] #chengbin: list of numpy array.
        # This turns images into (image, scale) tuples temporarily
        images = [tools.resize_image(image, max_scale=self.scale, max_size=self.max_size) for image in images]#chengbin: image has to resize: max_size: 2048, scale: 2
        
        max_height, max_width = np.array([image.shape[:2] for image, scale in images]).max(axis=0)
        scales = [scale for _, scale in images]
        images = np.array(
            [tools.pad(image, width=max_width, height=max_height) for image, _ in images])
        if detection_kwargs is None:
            detection_kwargs = {}
        if recognition_kwargs is None:
            recognition_kwargs = {}
        box_groups = self.detector.detect(images=images, **detection_kwargs)
        #assert len(box_groups) == len(images), 'You must provide the same number of box groups as images.'
        
        crops = []
        start_end = []
        
        
        for image, boxes in zip(images, box_groups):
            image = tools.read(image)
            if self.prediction_model.input_shape[-1] == 1 and image.shape[-1] == 3:
                # Convert color to grayscale
                image = cv2.cvtColor(image, code=cv2.COLOR_RGB2GRAY)
            print("This is prediction model input shape",self.prediction_model.input_shape)
            for box in boxes:
                crops.append(
                    tools.warpBox(image=image,
                                  box=box,
                                  target_height=self.model.input_shape[1],
                                  target_width=self.model.input_shape[2]))
            start = 0 if not start_end else start_end[-1][1]
            start_end.append((start, start + len(boxes)))
        if not crops:
            return [[] for image in images]
        print("this is crops",crops,np.asarray(crops).shape)
        X = np.float32(crops) / 255
        if len(X.shape) == 3:
            X = X[..., np.newaxis]
        #predictions = [''.join([self.alphabet[idx] for idx in row if idx not in [self.blank_label_idx, -1]]) for row in self.prediction_model.predict(X, **recognition_kwargs)]
        rows = self.prediction_model.predict(np.asarray([X[0]]))
        #print("length of X",len(X[0]),"This is prediciton rows.",rows[0][0])
        for r in rows[0]:
            maxrid = 0
            maxr = r[0]
            for i in range(len(r)):
                if r[i] > maxr:
                    maxr = r[i]
                    maxrid = i
            print("max row value",maxr,"max r id", maxrid)
        return rows#[predictions[start:end] for start, end in start_end]
        #prediction_groups = self.recognizer.recognize_from_boxes(images=images,box_groups=box_groups, **recognition_kwargs)
        #box_groups = [
        #    tools.adjust_boxes(boxes=boxes, boxes_format='boxes', scale=1 /
        #                       scale) if scale != 1 else boxes
        #    for boxes, scale in zip(box_groups, scales)
        #]
        #return [
        #    list(zip(predictions, boxes))
        #    for predictions, boxes in zip(prediction_groups, box_groups)
        #]
def showimage(image):
    import matplotlib.pyplot as plt
    plt.figure()
    plt.imshow(image[0])
    plt.show()


if __name__ == "__main__":
    p = Pipeline()
    images = ["./test.jpg"]
    prediction = p.recognize(images)
    print(prediction)
