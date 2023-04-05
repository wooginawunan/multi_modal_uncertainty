# -*- coding: utf-8 -*-
"""
Simple data getters. Each returns iterator for train and dataset for test/valid
training: 60,000
testing: 10,000
20% val: 12,000 

"""

import numpy as np
import os
import logging
logging.getLogger(__name__)

import torch
from torchvision import datasets, transforms
from torchvision.transforms import functional as F_trans

class QuarterCrop(object):
    """
    Input size is 28*28 for fasion MNIST    
    """

    def __init__(self, expected_size):
        self.expected_size = expected_size
        self.crop_size_w = int(self.expected_size[0]/2)
        self.crop_size_h = int(self.expected_size[1]/2)

    def __call__(self, img):
        """
        Args:
            img (PIL Image): Image to be cropped and resized.

        Returns:
            PIL Image: Randomly cropped and resized image.
        """
        w, h = img.size
        assert w == self.expected_size[0] and h == self.expected_size[1]
        starts_x = [0, 0, self.crop_size_w, self.crop_size_h]
        starts_y = [0, self.crop_size_h, 0, self.crop_size_h]
        
        
        return [F_trans.crop(img, i, j, self.crop_size_h, self.crop_size_w) for i, j in zip(starts_x, starts_y)]

def get_fmnist(
        datapath = os.environ['DATA_DIR'], 
        batch_size=128,
        download = False, 
        shuffle = True,
        sample_size = None,
        seed=777):
    '''
    if in_quarter is True, img in batch will be cropped into four quarters,
    and the shape of each batch will be [batch_size, 4, 1, 14, 14], 
        0 - upper left
        1 - upper right
        2 - lower left
        3 - lower right
    
    '''
    print(datapath)

    transform_to_quarter = transforms.Compose([
        QuarterCrop((28, 28)), 
        transforms.Lambda(lambda crops: torch.stack([transforms.ToTensor()(crop) for crop in crops]))
    ])

    torch.manual_seed(seed)
    training = datasets.FashionMNIST(datapath, 
                                 download=download,
                                 train=True,
                                 transform=transform_to_quarter)
    
    testing = datasets.FashionMNIST(datapath,
                                    download=download,
                                    train=False,
                                    transform=transform_to_quarter)
    
    test_loader = torch.utils.data.DataLoader(testing,
                                               batch_size=batch_size,
                                               shuffle=False)
    num_train = len(training)
    indices = list(range(num_train))
    training_idx = indices
    valid_loader = None

    if sample_size is None:
        sample_size = len(training_idx)
    training_idx = training_idx[:sample_size]

    training_sub = torch.utils.data.Subset(training, training_idx)

    training_loader = torch.utils.data.DataLoader(training_sub,
                                                   batch_size=batch_size,
                                                   shuffle=shuffle
                                                   ) 

    print('training_loader LENGTH:', len(training_loader))
    dd = training.train_data[training_idx].float()

        
    return training_loader, test_loader, None