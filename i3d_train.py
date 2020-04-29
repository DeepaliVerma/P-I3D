import os
os.environ['KERAS_BACKEND'] = 'tensorflow'
#os.environ['CUDA_VISIBLE_DEVICES'] = "2,3"
from keras.layers import Dense, Flatten, Dropout, Reshape
from keras import regularizers
from keras.preprocessing import image
from keras.models import Model, load_model
from keras.applications.vgg16 import preprocess_input
from keras.utils import to_categorical
from keras.optimizers import SGD
from i3d_inception import Inception_Inflated3d, conv3d_bn
from keras.callbacks import ReduceLROnPlateau, ModelCheckpoint, CSVLogger, Callback
from keras.utils import Sequence, multi_gpu_model

import random
import sys
from multiprocessing import cpu_count
import numpy as np
import glob
from skimage.io import imread
import cv2
from loader import *
import argparse

def parse():
    parser = argparse.ArgumentParser(description='Test temporal model')
    parser.add_argument('dataset', help='Dataset')
    parser.add_argument('protocol', help='Protocol')
    parser.add_argument('part', help='full_body/left_hand/right_hand')
    parser.add_argument('num_classes', help='Number of classes')
    parser.add_argument('batch_size', type=int, help='Batch size')
    parser.add_argument('epochs', type=int, help='Epochs')
    args = parser.parse_args()
    return args

class i3d_modified:
    def __init__(self, weights = 'rgb_imagenet_and_kinetics'):
        self.model = Inception_Inflated3d(include_top = True, weights= weights)
        
    def i3d_flattened(self, num_classes = 60):
        i3d = Model(inputs = self.model.input, outputs = self.model.get_layer(index=-4).output)
        x = conv3d_bn(i3d.output, num_classes, 1, 1, 1, padding='same', use_bias=True, use_activation_fn=False, use_bn=False, name='Conv3d_6a_1x1')
        num_frames_remaining = int(x.shape[1])
        x = Flatten()(x)
        predictions = Dense(num_classes, activation = 'softmax', kernel_regularizer=regularizers.l2(0.01), activity_regularizer=regularizers.l1(0.01))(x)
        new_model  = Model(inputs = i3d.input, outputs = predictions)
        
        #for layer in i3d.layers:
        #    layer.trainable = False
        
        return new_model

class CustomModelCheckpoint(Callback):

    def __init__(self, model_parallel, path):

        super(CustomModelCheckpoint, self).__init__()
        self.save_model = model_parallel
        self.path = path
        self.nb_epoch = 0
        if not os.path.exists(path):
            os.makedirs(path)

    def on_epoch_end(self, epoch, logs=None):
        self.nb_epoch += 1
        self.save_model.save(self.path + str(self.nb_epoch) + '.hdf5')

if __name__ == '__main__':
    args = parse()

    i3d = i3d_modified(weights = 'rgb_imagenet_and_kinetics')
    model = i3d.i3d_flattened(num_classes = args.num_classes)
    optim = SGD(lr = 0.01, momentum = 0.9)
    
    # Callbacks
    reduce_lr = ReduceLROnPlateau(monitor='val_loss', factor = 0.1, patience = 10)
    csvlogger = CSVLogger('logs/i3d_{}_{}_{}.csv'.format(args.dataset, args.protocol, args.part)
    
    parallel_model = multi_gpu_model(model, gpus=4)
    parallel_model.compile(loss = 'categorical_crossentropy', optimizer = optim, metrics = ['accuracy'])
    model.compile(loss = 'categorical_crossentropy', optimizer = optim, metrics = ['accuracy'])
    
    model_checkpoint = CustomModelCheckpoint(model, 'weights/weights_{}_{}_{}/epoch_'.format(args.dataset, args.protocol, args.part))
    
    part_patch = 'data/{}/{}/'.format(args.dataset, args.part)
    train_generator = DataLoader('splits/{}/train_{}.txt'.format(args.dataset, args.protocol), part_patch, args.protocol, batch_size = args.batch_size)
    val_generator = DataLoader('splits/{}/validation_{}.txt'.format(args.dataset, args.protocol), part_patch, args.protocol, batch_size = args.batch_size)
    test_generator = DataLoader('splits/{}/test_{}.txt'.format(args.dataset, args.protocol), part_patch, args.protocol, batch_size = args.batch_size)
    
    parallel_model.fit_generator(
        generator = train_generator, 
        validation_data=val_generator,
        epochs = args.epochs, 
        callbacks = [csvlogger, reduce_lr, model_checkpoint],
        max_queue_size = 48,
        workers = cpu_count() - 2,
        use_multiprocessing = True,
    )
    
    print(parallel_model.evaluate_generator(generator = test_generator, use_multiprocessing=True, max_queue_size = 48, workers=cpu_count() - 2))
