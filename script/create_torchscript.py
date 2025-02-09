import argparse

import mmcv
import numpy as np
import torch
import torch._C
import torch.serialization
from mmcv.runner import load_checkpoint
from mmseg.apis import inference_segmentor, init_segmentor, show_result_pyplot
from torch import nn
import argparse

from mmseg.models import build_segmentor

from script.config import *

torch.manual_seed(3)

def digit_version(version_str):
    digit_version = []
    for x in version_str.split('.'):
        if x.isdigit():
            digit_version.append(int(x))
        elif x.find('rc') != -1:
            patch_version = x.split('rc')
            digit_version.append(int(patch_version[0]) - 1)
            digit_version.append(int(patch_version[1]))
    return digit_version


def check_torch_version():
    torch_minimum_version = '1.8.0'
    torch_version = digit_version(torch.__version__)

    assert (torch_version >= digit_version(torch_minimum_version)), \
        f'Torch=={torch.__version__} is not support for converting to ' \
        f'torchscript. Please install pytorch>={torch_minimum_version}.'


def _convert_batchnorm(module):
    module_output = module
    if isinstance(module, torch.nn.SyncBatchNorm):
        module_output = torch.nn.BatchNorm2d(module.num_features, module.eps,
                                             module.momentum, module.affine,
                                             module.track_running_stats)
        if module.affine:
            module_output.weight.data = module.weight.data.clone().detach()
            module_output.bias.data = module.bias.data.clone().detach()
            # keep requires_grad unchanged
            module_output.weight.requires_grad = module.weight.requires_grad
            module_output.bias.requires_grad = module.bias.requires_grad
        module_output.running_mean = module.running_mean
        module_output.running_var = module.running_var
        module_output.num_batches_tracked = module.num_batches_tracked
    for name, child in module.named_children():
        module_output.add_module(name, _convert_batchnorm(child))
    del module
    return module_output


def _demo_mm_inputs(input_shape, num_classes):
    """Create a superset of inputs needed to run test or train batches.
    Args:
        input_shape (tuple):
            input batch dimensions
        num_classes (int):
            number of semantic classes
    """
    (N, C, H, W) = input_shape
    rng = np.random.RandomState(0)
    imgs = rng.rand(*input_shape)
    segs = rng.randint(
        low=0, high=num_classes - 1, size=(N, 1, H, W)).astype(np.uint8)
    img_metas = [{
        'img_shape': (H, W, C),
        'ori_shape': (H, W, C),
        'pad_shape': (H, W, C),
        'filename': '<demo>.png',
        'scale_factor': 1.0,
        'flip': False,
    } for _ in range(N)]
    mm_inputs = {
        'imgs': torch.FloatTensor(imgs).requires_grad_(True).cuda().half(),
        'img_metas': img_metas,
        'gt_semantic_seg': torch.LongTensor(segs).cuda().half()
    }
    return mm_inputs


def pytorch2libtorch(model,
                     input_shape,
                     show=False,
                     output_file='tmp.pt',
                     verify=False):
    """Export Pytorch model to TorchScript model and verify the outputs are
    same between Pytorch and TorchScript.
    Args:
        model (nn.Module): Pytorch model we want to export.
        input_shape (tuple): Use this input shape to construct
            the corresponding dummy input and execute the model.
        show (bool): Whether print the computation graph. Default: False.
        output_file (string): The path to where we store the
            output TorchScript model. Default: `tmp.pt`.
        verify (bool): Whether compare the outputs between
            Pytorch and TorchScript. Default: False.
    """
    if isinstance(model.decode_head, nn.ModuleList):
        num_classes = model.decode_head[-1].num_classes
    else:
        num_classes = model.decode_head.num_classes

    mm_inputs = _demo_mm_inputs(input_shape, num_classes)

    imgs = mm_inputs.pop('imgs')

    # replace the orginal forword with forward_dummy
    model.forward = model.forward_dummy
    model.eval()
    traced_model = torch.jit.trace(
        model,
        example_inputs=imgs,
        check_trace=verify,
    )

    if show:
        print(traced_model.graph)

    traced_model.save(output_file)
    print('Successfully exported TorchScript model: {}'.format(output_file))

if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('--in_model', type=str)
    parser.add_argument('--out_model', type=str)
    args = parser.parse_args()

    checkpoint = torch.load(args.in_model)
    checkpoint['meta']['PALETTE'] = ([128, 128, 128], [129, 127, 38], [120, 69, 125], [53, 125, 34], 
           [0, 11, 123])
    torch.save(checkpoint, args.in_model)

    # build the model from a config file and a checkpoint file
    model = init_segmentor(cfg, args.in_model, device='cuda:0')
    model.half()


    pytorch2libtorch(
        model,
        (1,3,512,512),
        show=True,
        output_file=args.out_model,
        verify=True)