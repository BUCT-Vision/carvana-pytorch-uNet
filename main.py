import os 
import time
import argparse
import numpy as np
import pandas as pd
import torch.optim as optim
from data_util import *
from model import *
from tensorboard import SummaryWriter
from datetime import datetime
from torch.utils.data import DataLoader,Dataset
from torch.autograd import Variable
from torch.utils.data.sampler import SubsetRandomSampler

"""
https://www.kaggle.com/c/carvana-image-masking-challenge
"""

parser = argparse.ArgumentParser(description='Carvance')
parser.add_argument('--batch_size', type=int, default=1,
                    help='input batch size for training (default: 8)')
parser.add_argument('--lr', type=float, default=1e-3,
                    help='initial learning rate')
parser.add_argument('--test-batch-size', type=int, default=12, metavar='N',
                    help='input batch size for testing (default: 1000)')
parser.add_argument('--start-epoch', type=int, default=0, 
                    help='start epoch')
parser.add_argument('--epochs', type=int, default=30, metavar='N',
                    help='number of epochs to train (default: 20)')
parser.add_argument('--seed', type=int, default=212,
                    metavar='S', help='random seed (default: 1)')
parser.add_argument('--log-interval', type=int, default=10, metavar='N',
                    help='how many batches to wait before logging training status')
parser.add_argument('--resume', type=str, default=None,
                    help='resume training')
args = parser.parse_args()
args.cuda =torch.cuda.is_available()
if args.cuda:
    torch.cuda.manual_seed(args.seed)


def train(epoch, model, optimizer, train_loader, writer, iters):
    model.train()
    criterion=nn.NLLLoss2d(torch.FloatTensor(CLASS_WEIGHT)).cuda()
    dice_co=0
    count=0
    for batch_idx,(data,target) in enumerate(train_loader):
        data = Variable(data.cuda())
        target = Variable(target.cuda())
        output = model(data)
        optimizer.zero_grad()
        _, pred = torch.max(output, 1)
        
        dice_coef=compute_dice(pred,target)
        dice_co += dice_coef

        loss = criterion(output, target)+Variable(torch.FloatTensor([10.0-10.0*dice_coef]).cuda())
        loss.backward()
        optimizer.step()

        count += torch.sum(pred.data[0] == target.data[0])

        wrong = torch.ones(pred.data[0].size()).cuda()
        nonMatch = torch.eq(pred.data[0], target.data[0])
        wrong[nonMatch] = 0
        
        if batch_idx % args.log_interval == 0 and not batch_idx==0 :
            
            print('Train Epoch:{}/{} [{}/{} ({:.0f}%)]  Loss:{:.4f} acc:{:.2f}% ave dice coef:{:.4f}'.format(
                epoch, args.epochs, batch_idx *
                len(data), len(train_loader.dataset),
                100.0 * batch_idx / len(train_loader), loss.data[0], 100.0 *
                count / args.log_interval / torch.numel(target.data[0]),
                dice_co/args.log_interval
            ))
            # add to tensorboard
            writer.add_scalar('loss', loss.data[0], iters)
            writer.add_scalar('dice_coef', dice_co, iters)
            writer.add_image('image', data.data[0], iters)
            writer.add_image('pred', pred.data[0].float().expand_as(data.data[0]), iters)
            writer.add_image('ground truth', target.data[0].float().expand_as(data.data[0]),iters)
            writer.add_image('wrong prediction',wrong.expand_as(data.data[0]),iters)
            iters += 1
            dice_co = 0
            count=0
    return loss.data[0],iters

def compute_dice(pred,target):
    """
    compute dice coefficient
    """
    dice_count = torch.sum(pred.data[0].type(torch.ByteTensor)
                            & target.data[0].type(torch.ByteTensor))
    dice_sum = (1.0 * torch.sum(target.data[0].type(torch.ByteTensor)) +
                     1.0 * torch.sum(pred.data[0].type(torch.ByteTensor)))
    return (2 * dice_count+1.0)/(1.0 + dice_sum)


def save_checkpoint(state, is_best, filename='checkpoint.pth.tar'):
    """
    save checkpoint 
    """
    torch.save(state, filename)
    if is_best:
        shutil.copyfile(filename, 'model_best.pth.tar')

def resume(ckpt,model):
    """
    resume training 
    """
    if os.path.isfile(ckpt):
        print('==> loading checkpoint {}'.format(ckpt))
        checkpoint = torch.load(ckpt)
        args.start_epoch = checkpoint['epoch']
        best_loss = checkpoint['loss']
        model.load_state_dict(checkpoint['state_dict'])
        optimizer = checkpoint['optimizer']
        iters=checkpoint['iters']
        print("==> loaded checkpoint '{}' (epoch {})".format(args.resume, checkpoint['epoch']))
        return model,optimizer,args.start_epoch,best_loss,iters
    else:
        print("==> no checkpoint found at '{}'".format(args.resume))
    
def adjust_lr(optimizer,epoch,decay=20):
    """
        adjust the learning rate initial lr decayed 10 every 20 epoch
    """
    lr=args.lr*(0.1**(epoch//decay))
    for param in optimizer.param_groups:
        param['lr']=lr

def main():
    kwargs = {'num_workers': 1, 'pin_memory': True} if args.cuda else {}
    CarSet = CarDataSet(ROOT, TRAIN, MASK)
    # split train val 
    # train_idx, valid_idx = augmented_train_valid_split(CarSet, test_size = 0.15,shuffle = True ,random_seed=args.seed)
    # train_sampler = SubsetRandomSampler(train_idx)
    # val_samper = SubsetRandomSampler(valid_idx)
    
    train_loader = DataLoader(CarSet,
                            #   sampler=train_sampler,
                              shuffle=True,
                              batch_size=args.batch_size,
                              **kwargs)
    # val_loader = DataLoader(CarSet,
    #                         sampler=val_samper,
    #                         batch_size=2,
    #                         **kwargs)
    model = uNet(NUM_CLASS)
    if args.cuda:
        model.cuda()
    optimizer=optim.Adam(model.parameters(),lr=args.lr,betas=(0.9, 0.999))
    writer=SummaryWriter('logs/'+datetime.now().strftime('%B-%d'))
    best_loss=1e+5
    iters=0
    # resume training 
    if args.resume:
        model,optimizer,args.start_epoch,best_loss,iters = resume(args.resume,model)

    for epoch in range(args.start_epoch ,args.epochs):
        adjust_lr(optimizer,epoch,decay=5)
        t1=time.time()
        loss, iters = train(epoch,
                            model,
                            optimizer,
                            train_loader,
                            writer,
                            iters)
        is_best = loss < best_loss
        best_loss = min(best_loss, loss)
        state={
            'epoch':epoch,
            'state_dict':model.state_dict(),
            'optimizer':optimizer,
            'loss':best_loss,
            'iters': iters,
        }
        save_checkpoint(state, is_best)
    writer.close()
    





if __name__ == '__main__':
    main()
    
