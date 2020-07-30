import argparse
import datetime
import time

import torch
import torch.distributed as dist
import torch.multiprocessing as mp
import torch.nn as nn
import torch.optim as optim
import torch.utils.data.distributed
import torchvision.transforms as transforms
from model import pyramidnet
from torch.optim import lr_scheduler
from torch.utils.data import DataLoader
from torchvision.datasets import CIFAR10

parser = argparse.ArgumentParser(description='cifar10 classification models')
parser.add_argument('--lr', default=0.1, help='')
parser.add_argument('--batch-size', type=int, default=64, help='')
parser.add_argument('--max-epochs', type=int, default=4, help='')
parser.add_argument('--num-workers', type=int, default=4, help='')
parser.add_argument("--gpu-devices", type=int, nargs='+', required=False, default=None, help="")
parser.add_argument('--init-method', default='tcp://127.0.0.1:13456', type=str, help='')
parser.add_argument('--dist-backend', default='nccl', type=str, help='')
parser.add_argument('--rank', default=0, type=int, help='')
parser.add_argument('--world-size', default=1, type=int, help='')
parser.add_argument('--distributed', action='store_true', help='')


def main():
    args = parser.parse_args()
    ngpus_per_node = torch.cuda.device_count()
    mp.spawn(main_worker, nprocs=ngpus_per_node, args=(ngpus_per_node, args))


def main_worker(gpu, ngpus_per_node, args):
    # init the process group
    dist.init_process_group(backend=args.dist_backend, init_method=args.init_method,
                            world_size=args.world_size, rank=args.rank)

    torch.cuda.set_device(gpu)

    print("From Rank: {}, Use GPU: {} for training".format(args.rank, gpu))

    print('From Rank: {}, ==> Making model..'.format(args.rank))
    net = pyramidnet()
    net.cuda(gpu)
    args.batch_size = int(args.batch_size / ngpus_per_node)
    # args.num_workers = int(args.num_workers / args.world_size)
    net = torch.nn.parallel.DistributedDataParallel(net, device_ids=[gpu], output_device=gpu)
    num_params = sum(p.numel() for p in net.parameters() if p.requires_grad)
    print('From Rank: {}, The number of parameters of model is'.format(args.rank), num_params)

    print('From Rank: {}, ==> Preparing data..'.format(args.rank))
    transforms_train = transforms.Compose([
        transforms.RandomCrop(32, padding=4),
        transforms.RandomHorizontalFlip(),
        transforms.ToTensor(),
        transforms.Normalize((0.4914, 0.4822, 0.4465), (0.2023, 0.1994, 0.2010))])

    dataset_train = CIFAR10(root='/home/zhaopp5', train=True, download=True,
                            transform=transforms_train)
    train_sampler = torch.utils.data.distributed.DistributedSampler(dataset_train)
    train_loader = DataLoader(dataset_train, batch_size=args.batch_size,
                              shuffle=(train_sampler is None), num_workers=2,
                              sampler=train_sampler)

    # there are 10 classes so the dataset name is cifar-10
    classes = ('plane', 'car', 'bird', 'cat', 'deer',
               'dog', 'frog', 'horse', 'ship', 'truck')

    criterion = nn.CrossEntropyLoss().cuda(gpu)
    optimizer = optim.SGD(net.parameters(), lr=args.lr,
                          momentum=0.9, weight_decay=1e-4)

    scheduler = lr_scheduler.StepLR(optimizer, step_size=2, gamma=0.1)

    for epoch in range(args.max_epochs):
        train(epoch, net, criterion, optimizer, train_loader, args.rank)
        scheduler.step()

    # if args.rank == 0:
    torch.save(net.module.state_dict(), "final_model_rank_{}.pth".format(args.rank))
    print("From Rank: {}, model saved.".format(args.rank))


def train(epoch, net, criterion, optimizer, train_loader, rank):
    net.train()

    train_loss = 0
    correct = 0
    total = 0

    epoch_start = time.time()
    for batch_idx, (inputs, targets) in enumerate(train_loader):
        start = time.time()

        inputs = inputs.cuda()
        targets = targets.cuda()
        outputs = net(inputs)
        loss = criterion(outputs, targets)

        optimizer.zero_grad()
        loss.backward()
        optimizer.step()

        train_loss += loss.item()
        _, predicted = outputs.max(1)
        total += targets.size(0)
        correct += predicted.eq(targets).sum().item()

        acc = 100 * correct / total

        batch_time = time.time() - start

        if batch_idx % 20 == 0:
            print('From Rank: {}, Epoch:[{}][{}/{}]| '
                  'loss: {:.3f} | acc: {:.3f} | batch time: {:.3f}s '.format(
                rank, epoch, batch_idx, len(train_loader),
                train_loss / (batch_idx + 1), acc, batch_time), flush=True)

    elapse_time = time.time() - epoch_start
    elapse_time = datetime.timedelta(seconds=elapse_time)
    print("From Rank: {}, Training time {}".format(rank, elapse_time))


if __name__ == '__main__':
    main()
