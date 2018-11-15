from __future__ import division
from __future__ import absolute_import
from __future__ import print_function

import tensorflow as tf
import numpy as np
from tensorflow.python.ops import control_flow_ops
from tensorflow.python.training import moving_averages

from dltk.core.modules import *


class Upscore(AbstractModule):
    """Upscore module according to J. Long.
    """
    def __init__(self, out_filters, strides, name='upscore'):
        """Constructs an Upscore module
        Parameters
        ----------
        out_filters : int
            number of output filters
        strides : list or tuple
            strides to use for upsampling
        name : string
            name of the module
        """
        self.out_filters = out_filters
        self.strides = strides
        self.in_filters = None
        super(Upscore, self).__init__(name)

    def _build(self, x, x_up, is_training=True):
        """Applies the upscore operation
        Parameters
        ----------
        x : tf.Tensor
            tensor to be upsampled
        x_up : tf.Tensor
            tensor from the same scale to be convolved and added to the upsampled tensor
        is_training : bool
            flag for specifying whether this is training - passed to batch normalization
        Returns
        -------
        tf.Tensor
            output of the upscore operation
        """

        # Compute an up-conv shape dynamically from the input tensor. Input filters are required to be static.
        if self.in_filters is None:
            self.in_filters = x.get_shape().as_list()[-1]
        assert(self.in_filters == x.get_shape().as_list()[-1], 'Module was initialised for a different input shape')

        # Account for differences in input and output filters
        if self.in_filters != self.out_filters:
            x = Convolution(self.out_filters, name='up_score_filter_conv')(x)
            #print('upscore', x.get_shape())
            
        t_conv = BilinearUpsample(strides=self.strides)(x)
        #print('t_conv', t_conv.get_shape())
        
        conv = Convolution(self.out_filters, 1)(x_up)
        conv = BatchNorm()(conv, is_training)
        #print('conv', conv.get_shape())
        return tf.add(t_conv, conv)



class ResNetFCN(AbstractModule):
    """FCN module with residual encoder

    This module builds a FCN for segmentation using a residual encoder.
    """
        
    def __init__(self, num_classes=[1], num_residual_units=3, filters=(16, 64, 128, 256, 512),
                 strides=((1, 1, 1), (2, 2, 2), (2, 2, 2), (2, 2, 2), (1, 1, 1)), relu_leakiness=0.1,
                 name='resnetfcn'):
        """Builds a residual FCN for segmentation

        Parameters
        ----------
        num_classes : int
            number of classes to segment
        num_residual_units : int
            number of residual units per scale
        filters : tuple or list
            number of filters per scale. The first is used for the initial convolution without residual connections
        strides : tuple or list
            strides per scale. The first is used for the initial convolution without residual connections
        relu_leakiness : float
            leakiness of the relus used
        name : string
            name of the network
        """
        self.num_classes = num_classes
        self.num_residual_units = num_residual_units
        self.filters = filters
        self.strides = strides
        self.relu_leakiness = relu_leakiness
        self.rank = None
        super(ResNetFCN, self).__init__(name)

    def _build(self, inp, inp2, is_training=True):
        """Constructs a ResNetFCN using the input tensor

        Parameters
        ----------
        inp : tf.Tensor
            input tensor
        is_training : bool
            flag to specify whether this is training - passed to batch normalization

        Returns
        -------
        dict
            output dictionary containing:
                - `logits` - logits of the classification
                - `y_prob` - classification probabilities
                - `y_` - prediction of the classification

        """
        outputs = {}
        filters = self.filters
        strides = self.strides

        assert len(strides) == len(filters)

        if self.rank is None:
            self.rank = len(strides[0])
        assert len(inp.get_shape().as_list()) == self.rank + 2, \
            'Stride gives rank {} input is rank {}'.format(self.rank, len(inp.get_shape().as_list()) - 2)

        print('encoder 1:')
        x = inp
        outputs['x'] = x
        print('input', x.get_shape())
        
        x = Convolution(filters[0], strides=strides[0])(x)
        print(x.get_shape())

        # Residual feature encoding blocks with num_residual_units at different scales via strided convolutions
        scales = [x]
        saved_strides = []
        with tf.variable_scope('Encoder1'):               
            for scale in range(1, len(filters)):
                
                with tf.variable_scope('unit_%d_0' % (scale)):
                    x = VanillaResidualUnit(filters[scale], stride=strides[scale])(x, is_training=is_training)
                saved_strides.append(strides[scale])
                
                for i in range(1, self.num_residual_units):
                    with tf.variable_scope('unit_%d_%d' % (scale, i)):
                        x = VanillaResidualUnit(filters[scale])(x, is_training=is_training)                        
                scales.append(x)
                encoder_out = x
                print('enc 1 out', encoder_out.get_shape())
                
        print('encoder 2:')
        
        x2 = inp2
        outputs['x2'] = x2
        print('input 2', x2.get_shape())
        
        x2 = Convolution(filters[0], strides=strides[0])(x2)
        print(x2.get_shape())

        # Residual feature encoding blocks with num_residual_units at different scales via strided convolutions
        scales = [x2]
        saved_strides = []
        with tf.variable_scope('Encoder2'):               
            for scale in range(1, len(filters)):
                
                with tf.variable_scope('unit_%d_0' % (scale)):
                     x2 = VanillaResidualUnit(filters[scale], stride=strides[scale])(x2, is_training=is_training)
                saved_strides.append(strides[scale])
                
                for i in range(1, self.num_residual_units):
                    with tf.variable_scope('unit_%d_%d' % (scale, i)):
                        x2 = VanillaResidualUnit(filters[scale])(x2, is_training=is_training)
                        
                scales.append(x2)
                encoder_out2 = x2
                print('enc 2 out', encoder_out2.get_shape())
            
        x = encoder_out + encoder_out2
        # Decoder / upscore
        for scale in range(len(filters) - 2, -1, -1):
            with tf.variable_scope('upscore_%d' % scale):
                print(x.name)
                tf.logging.info('Building upsampling for scale %d with x (%s) x_up (%s) stride (%s)'
                                % (scale, x.get_shape().as_list(), scales[scale].get_shape().as_list(),
                                   saved_strides[scale]))
                x = Upscore(self.num_classes, saved_strides[scale])(x, scales[scale], is_training=is_training)
            
            tf.logging.info('up_%d shape %s', scale, x.get_shape())
            print(x.get_shape())

        with tf.variable_scope('last') as scope:
            x = Convolution(self.num_classes, 1, strides=[1] * self.rank)(x)
            print(x.name)

            
        outputs['logits'] = x
        tf.logging.info('last conv shape %s', x.get_shape())

        with tf.variable_scope('pred'):
            y_prob = tf.nn.softmax(x)
            outputs['y_prob'] = y_prob
            y_ = tf.argmax(x, axis=-1) if self.num_classes > 1 else tf.cast(tf.greater_equal(x[..., 0], 0.5), tf.int32)
            outputs['y_'] = y_

        return outputs