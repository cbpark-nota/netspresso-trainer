from pathlib import Path
from typing import Callable, Dict, List, Type

import torch.nn as nn

from .backbones import cspdarknet, efficientformer, mobilenetv3_small, mobilevit, resnet50, segformer, vit, mixnet
from .full import pidnet
from .heads.classification import fc
from .heads.detection import faster_rcnn, yolox_head
from .heads.segmentation import all_mlp_decoder

MODEL_BACKBONE_DICT: Dict[str, Callable[..., nn.Module]] = {
    'resnet50': resnet50,
    'mobilenetv3_small': mobilenetv3_small,
    'segformer': segformer,
    'mobilevit': mobilevit,
    'vit': vit,
    'efficientformer': efficientformer,
    'cspdarknet': cspdarknet,
    'mixnet': mixnet,
}

MODEL_HEAD_DICT: Dict[str, Callable[..., nn.Module]] = {
    'classification': {
        'fc': fc,
    },
    'segmentation': {
        'all_mlp_decoder': all_mlp_decoder,
    },
    'detection': {
        'faster_rcnn': faster_rcnn,
        'yolox_head': yolox_head
    },
}

MODEL_FULL_DICT = {
    'pidnet': pidnet
}

SUPPORTING_MODEL_LIST = list(MODEL_BACKBONE_DICT.keys()) + list(MODEL_FULL_DICT.keys())
SUPPORTING_TASK_LIST: List[str] = ['classification', 'segmentation', 'detection']
