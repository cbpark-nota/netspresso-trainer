from typing import Callable, Dict

from .custom import ColorJitter, Pad, RandomCrop, RandomHorizontalFlip, RandomResizedCrop, RandomVerticalFlip, Resize, RandomMixup, RandomCutmix
TRANSFORM_DICT: Dict[str, Callable] = {
    'colorjitter': ColorJitter,
    'pad': Pad,
    'randomcrop': RandomCrop,
    'randomresizedcrop': RandomResizedCrop,
    'randomhorizontalflip': RandomHorizontalFlip,
    'randomverticalflip': RandomVerticalFlip,
    'resize': Resize,
    'mixup': RandomMixup,
    'cutmix': RandomCutmix
}
