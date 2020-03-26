import os
import argparse
import torch
from torch.utils.data import DataLoader
from dataset import Dataset
from torch import nn, optim
from model.net import SMNet
from utils import batch_PSNR
import visdom
import numpy as np


#  python train_rgb_blind.py --batchsize 8 -checkpoint 0
parser =argparse.ArgumentParser(description="SMNet Train")
parser.add_argument("--batchsize", type=int, default=8, help="Training batch size")
parser.add_argument("--epochs", type=int,  default=100, help="Number of training epochs")
parser.add_argument("--checkpoint", "-c", type=int, default=0, help="checkpoint of training")
opt = parser.parse_args()



def evaluate(model, dataset_val):
    """

    :param model: network model
    :param dataset_val:
    :return: psnr_val
    """
    with torch.no_grad():
        model.eval()
        # validate
        psnr_val = 0
        for k in range(len(dataset_val)):
            img_val = torch.unsqueeze(dataset_val[k], 0)
            torch.manual_seed(0)
            noise = torch.FloatTensor(img_val.size()).normal_(mean=0, std=opt.noiseL / 255.)

            imgn_val = img_val + noise
            img_val, imgn_val = img_val.cuda(), imgn_val.cuda()
            out_val = model(imgn_val)
            out_val = torch.clamp(out_val, 0., 1.)
            psnr_val += batch_PSNR(out_val, img_val, 1.)
        psnr_val /= len(dataset_val)
    return psnr_val



def main():

    device = torch.device("cuda:0")
    model = SMNet(in_channels=3)
    model = torch.nn.DataParallel(model, device_ids=[0, 1, 2, 3]).to(device)
    optimier = optim.Adam(model.parameters(), lr=5e-4)
    scheduler = optim.lr_scheduler.MultiStepLR(optimier, milestones=[10, 40], gamma=0.1)
    criterion_sr = nn.MSELoss(reduction='mean')

    dataset_train = Dataset(train=True, data_root='train_data.h5')
    loader_train = DataLoader(dataset_train, batch_size=opt.batchsize, num_workers=8, shuffle=True, drop_last=True)
    dataset_step = len(loader_train.dataset)/opt.batchsize

    dataset_val = Dataset(train=False)

    viz = visdom.Visdom()
    noiseL_B = [0, 55]
    weight_path = "weights"
    if opt.checkpoint != 0:
        viz.line([0.0001], [dataset_step * opt.checkpoint], win='rgb_loss', opts=dict(title='rgb_loss'))
        viz.line([33], [dataset_step * opt.checkpoint], win='rgb_val_psnr', opts=dict(title='rgb_val_psnr'))
        viz.line([33], [opt.checkpoint], win='rgb_train_psnr', opts=dict(title='rgb_train_psnr'))
    print('check point:%d' %(opt.checkpoint))


    if opt.checkpoint == 0:
        viz.line([0.0001], [dataset_step*opt.checkpoint], win='rgb_loss', opts=dict(title='rgb_loss'))
        viz.line([33], [dataset_step*opt.checkpoint], win='rgb_val_psnr', opts=dict(title='rgb_val_psnr'))
        viz.line([33], [opt.checkpoint], win='rgb_train_psnr', opts=dict(title='rgb_train_psnr'))
    global_step = dataset_step*opt.checkpoint

    model.train()
    for epoch in range(opt.checkpoint, opt.epochs):
        train_psnr_all = 0
        for step, x in enumerate(loader_train):
            x = x.to(device)
            noise = torch.FloatTensor(x.size()).to(device)
            stdN = np.random.uniform(noiseL_B[0], noiseL_B[1], size=noise.size()[0])
            for n in range(noise.size()[0]):
                sizeN = noise[0, :, :, :].size()
                noise[n, :, :, :] = torch.FloatTensor(sizeN).normal_(mean=0, std=stdN[n] / 255.)

            x_noise = x + noise
            x_hat = model(x_noise)

            loss_sr = criterion_sr(x, x_hat)
            loss = 1.0 * loss_sr

            optimier.zero_grad()
            loss.backward()
            optimier.step()

            out_train = torch.clamp(x_hat, 0., 1.)
            psnr_train = batch_PSNR(out_train, x, 1.)
            train_psnr_all += psnr_train

            if step % 100 == 0 and step != 0:
                out_train = torch.clamp(x_hat, 0., 1.)
                psnr_train = batch_PSNR(out_train, x, 1.)
                print("[epoch %d][%d/%d] PSNR_train: %.6f \n all_loss: %.6f \n" %
                      (epoch + 1, step, dataset_step, psnr_train, loss.item()))
                viz.line([loss.item()], [global_step], win='rgb_loss', update='append')

            if step % 400 == 0 and step != 0:
                val_psnr = evaluate(model, dataset_val)
                print("****************************\nepoch:{} val_psnr:{}\n***************************************\n".format(
                        epoch + 1, val_psnr))
                viz.line([val_psnr], [global_step], win='rgb_val_psnr', update='append')
                with open('log_rgb_B.txt', 'a+') as f:
                    f.write("[epoch %d][%d/%d] PSNR_train: %.6f \n all_loss: %.6f \n" %
                            (epoch + 1, step, dataset_step, psnr_train, loss.item()))
                    f.write("****************************\nepoch:{} val_psnr:{}\n***************************************\n".format(
                            epoch + 1, val_psnr))
                model.train()

            global_step +=1

        if epoch % 1 == 0:
            print("****************************\nepoch:{} train_psnr:{}\n***************************************\n".format(
                    epoch + 1, train_psnr_all/step))
            val_psnr = evaluate(model, dataset_val)
            print("****************************\nepoch:{} val_psnr:{}\n***************************************\n".format(
                    epoch + 1, val_psnr))
            viz.line([val_psnr], [global_step], win='rgb_val_psnr', update='append')
            viz.line([train_psnr_all/step], [epoch+1], win='rgb_train_psnr', update='append')
            torch.save(model.state_dict(), os.path.join(weight_path, "model_rgb_B_%d.pth" %(epoch+1)))
            scheduler.step()


if __name__ == '__main__':
    main()