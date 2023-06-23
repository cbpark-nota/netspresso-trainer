DEFAULT_CROP_PCT = 0.95 #0.875 
IMAGENET_DEFAULT_MEAN = (0.485, 0.456, 0.406)
IMAGENET_DEFAULT_STD = (0.229, 0.224, 0.225)
IMAGENET_INCEPTION_MEAN = (0.5, 0.5, 0.5)
IMAGENET_INCEPTION_STD = (0.5, 0.5, 0.5)
IMAGENET_DPN_MEAN = (124 / 255, 117 / 255, 104 / 255)
IMAGENET_DPN_STD = tuple([1 / (.0167 * 255)] * 3)

IMG_EXTENSIONS = ('.png', '.jpg', '.jpeg',
                  '.bmp', '.tif', '.tiff', '.dng', '.webp', '.mpo')