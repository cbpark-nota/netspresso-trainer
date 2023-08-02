import os

import numpy as np

from ..base import BaseHFDataset
from ..segmentation.transforms import generate_edge, reduce_label
from ...utils.logger import set_logger

logger = set_logger('data', level=os.getenv('LOG_LEVEL', default='INFO'))


class SegmentationHFDataset(BaseHFDataset):

    def __init__(
            self,
            args,
            idx_to_class,
            split,
            huggingface_dataset,
            transform=None,
            with_label=True
    ):
        root = args.data.metadata.repo
        super(SegmentationHFDataset, self).__init__(
            args,
            root,
            split,
            with_label
        )
        
        self.transform = transform
        self.idx_to_class = idx_to_class
        self.samples = huggingface_dataset
        
        self.image_feature_name = args.data.metadata.features.image
        self.label_feature_name = args.data.metadata.features.label

    @property
    def num_classes(self):
        return len(self.idx_to_class)

    @property
    def class_map(self):
        return self.idx_to_class

    def __len__(self):
        return self.samples.num_rows

    def __getitem__(self, index):
        
        img_name =  f"{index:06d}"
        img = self.samples[index][self.image_feature_name]
        label = self.samples[index][self.label_feature_name] if self.label_feature_name in self.samples[index] else None

        org_img = img.copy()

        w, h = img.size

        if label is None:
            out = self.transform(self.args.augment)(image=img)
            return {'pixel_values': out['image'], 'name': img_name, 'org_img': org_img, 'org_shape': (h, w)}

        outputs = {}

        if self.args.model.architecture.full == 'pidnet':
            edge = generate_edge(np.array(label))
            out = self.transform(self.args.augment)(image=img, mask=label, edge=edge)
            outputs.update({'pixel_values': out['image'], 'labels': out['mask'], 'edges': out['edge'].float(), 'name': img_name})
        else:
            out = self.transform(self.args.augment)(image=img, mask=label)
            outputs.update({'pixel_values': out['image'], 'labels': out['mask'], 'name': img_name})

        if self._split in ['train', 'training']:
            return outputs

        assert self._split in ['val', 'valid', 'test']
        # outputs.update({'org_img': org_img, 'org_shape': (h, w)})  # TODO: return org_img with batch_size > 1
        outputs.update({'org_shape': (h, w)})
        return outputs