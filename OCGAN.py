# %%
# import packages
import time
import pickle
import matplotlib.pyplot as plt
import torch
import torch.nn as nn
import torchvision.transforms as transforms
import numpy as np
import pandas as pd
import random
import os
from torchsummary import summary
device = torch.device("cuda" if torch.cuda.is_available() else "cpu")
device = 'cpu'
# %%
# define dataset


class elliptic_dataset(torch.utils.data.Dataset):
    def __init__(self, path):
        # read features
        feature_path = os.path.join(path, "elliptic_txs_features.csv")
        df = pd.read_csv(feature_path, header=None)
        fdf = df.to_numpy()
        num_features = 93
        self.features = torch.zeros(
            (len(fdf), num_features), dtype=torch.float32)
        self.timestepidx = []
        current_time = 0
        for i, feature in enumerate(fdf):
            if current_time < feature[1]:
                current_time += 1
                self.timestepidx.append(i)

            self.features[i] = torch.tensor(
                feature[2:num_features+2], dtype=torch.float32)
        print("features read!")
        # read classes
        class_path = os.path.join(path, "elliptic_txs_classes.csv")
        df = pd.read_csv(class_path)
        self.IdToidx = {}
        for idx, Id in enumerate(df["txId"].to_numpy()):
            self.IdToidx[Id] = idx
        label = df["class"].str.replace("unknown", '3')
        label = label.astype(np.float).to_numpy()
        self.totalnode = len(label)
        # 1 bad 2 good 3 unknown
        self.label = []
        for idx in range(len(self.timestepidx)):
            try:
                self.label.append(
                    label[self.timestepidx[idx]:self.timestepidx[idx+1]])
            except:
                self.label.append(label[self.timestepidx[idx]:])
        print("class read!")
        # read edge
        edge_path = os.path.join(path, "elliptic_txs_edgelist.csv")
        df = pd.read_csv(edge_path)
        self.adjlist = []
        for i in range(len(self.timestepidx)):
            self.adjlist.append([])
            try:
                for _ in range(self.timestepidx[i+1]-self.timestepidx[i]):
                    self.adjlist[i].append([])
            except:
                for _ in range(self.totalnode-self.timestepidx[i]):
                    self.adjlist[i].append([])
        froms = df["txId1"]
        tos = df["txId2"]
        for (id1, id2) in zip(froms, tos):
            current_time = int(fdf[self.IdToidx[id1]][1])
            self.adjlist[current_time-1][self.IdToidx[id1] -
                                         self.timestepidx[current_time-1]].append(self.IdToidx[id2] -
                                                                                  self.timestepidx[current_time-1])
            self.adjlist[current_time-1][self.IdToidx[id2] -
                                         self.timestepidx[current_time-1]].append(self.IdToidx[id1] -
                                                                                  self.timestepidx[current_time-1])
        print("edge read!")

    def __getitem__(self, idx):
        return (self.adjlist[idx], self.features[idx], self.label[idx])

    def __len__(self):
        return len(self.label)


# %%
dataset = elliptic_dataset("dataset/elliptic_bitcoin_dataset")
# split dataset by timestep
# %%
# define model


class MultiGATLayer(nn.Module):
    def __init__(self, f_in, f_out, num_heads):
        super(MultiGATLayer, self).__init__()
        self.f_in = f_in
        self.f_out = f_out
        self.num_heads = num_heads
        self.W=nn.ModuleList()
        self.a=nn.ModuleList()
        for _ in range(self.num_heads):
            self.W.append(nn.Linear(f_in, f_out))
        for _ in range(self.num_heads):
            self.a.append(nn.Linear(f_out*2, 1))
        self.leakyrelu = nn.LeakyReLU(0.2)
        self.softmax = nn.Softmax()

    def forward(self, adjlist, features):
        output_features = torch.zeros(
            len(features), self.f_out*self.num_heads, device=device)
        for node in range(len(features)):
            neighbors = adjlist[node].copy()
            neighbors.append(node)
            node_features = [features[neighbor] for neighbor in neighbors]
            node_features = torch.stack(node_features).clone().detach().to(device)
            attentionkey = [self.W[i](node_features)
                            for i in range(self.num_heads)]
            transformed_features = torch.zeros(
                self.num_heads, len(neighbors), self.f_out*2, device=device)
            for k in range(self.num_heads):
                for i in range(len(neighbors)):
                    row = torch.cat(
                        [attentionkey[k][-1], attentionkey[k][i]], dim=0)
                    transformed_features[k, i, :] = row
            att_weights = [self.leakyrelu(self.a[k](transformed_features[k]))
                           for k in range(self.num_heads)]
            att_weights = [self.softmax(torch.transpose(att_weights[k], 0, 1))
                           for k in range(self.num_heads)]
            output = [torch.matmul(att_weights[k], attentionkey[k])
                      for k in range(self.num_heads)]
            output = torch.cat(output, dim=1)
            output_features[node] = output
        return output_features


# %%


class MultiGAT(nn.Module):
    def __init__(self, f_in, f_out, num_heads, num_layers):
        super(MultiGAT, self).__init__()
        self.num_heads = num_heads
        self.f_in = f_in
        self.f_out = f_out
        self.num_layers = num_layers
        self.GATlayers = nn.ModuleList()
        self.sigmoid = nn.Sigmoid()
        for i in range(num_layers):
            self.GATlayers.append(MultiGATLayer(
                f_in, f_out, num_heads))
            f_in = f_out*num_heads
            if (i<num_layers):
                self.GATlayers.append(nn.LeakyReLU(0.2))

    def forward(self, adjlist, features):
        hidden_features = features
        for layer in self.GATlayers:
            try:
                hidden_features=layer(adjlist, hidden_features)
            except:
                hidden_features=layer(hidden_features)
        output = self.sigmoid(hidden_features.mean(axis=1))
        return output


# %%
model = MultiGAT(93, 30, 4, 4).to(device)
#paralist = []
#for layer in model.GATlayers:
#    for p in layer.a:
#        paralist.append({'params': p.parameters()})
#    for p in layer.W:
#        paralist.append({'params': p.parameters()})
optimizer = torch.optim.Adam(model.parameters(), lr=0.002)


class WeightedBCELoss(nn.Module):
    def __init__(self, weighted):
        super(WeightedBCELoss, self).__init__()
        self.BCELoss = nn.BCELoss()
        self.weighted = weighted

    def forward(self, y_pred, y_label):
        loss = self.BCELoss(y_pred, y_label)
        return (self.weighted*y_label+1-y_label)*loss


# %%
# training
traininglist = range(10)
criterion = WeightedBCELoss(13)

EPOCH = 30
for epoch in range(EPOCH):
    total_positive = 0
    total_negative = 0
    total_true_positive = 0
    total_false_positive = 0
    total_acc = 0
    for timestep in random.sample(traininglist, len(traininglist)):
        starttime = time.time()
        positive = 0
        negative = 0
        true_positive = 0
        false_positive = 0
        loss = 0
        acc = 0
        start = dataset.timestepidx[timestep]
        try:
            end = dataset.timestepidx[timestep+1]
        except:
            end = len(dataset.features)
        output = model(dataset.adjlist[timestep],
                       dataset.features[start:end].to(device))
        for idx, label in enumerate(dataset.label[timestep]):
            if (label == 1):
                loss += criterion(output[idx],
                                  torch.tensor((1), dtype=torch.float32, device=device))
                positive += 1
                if(output[idx] >= 0.5):
                    acc += 1
                    true_positive += 1

            elif (label == 2):
                loss += criterion(output[idx],
                                  torch.tensor((0), dtype=torch.float32, device=device))
                negative += 1
                if(output[idx] < 0.5):
                    acc += 1
                else:
                    false_positive += 1
        total_acc += acc
        total_positive += positive
        total_negative += negative
        total_true_positive += true_positive
        total_false_positive += false_positive
        loss /= negative+positive
        optimizer.zero_grad()
        loss.backward()
        optimizer.step()
        recall = true_positive/positive
        try:
            precision = true_positive/(true_positive+false_positive)
            f1 = 2/(1/precision+1/recall)
        except:
            precision = 0
            f1 = 0
        print(
            f"[{timestep+1}/{len(traininglist)}] loss={loss:.4f} acc={acc/(negative+positive):.4f} precision={precision:.4f} recall={recall:.4f} f1={f1:.4f} time={time.time()-starttime:.4f}")
    recall = total_true_positive/total_positive
    try:
        precision = total_true_positive / \
            (total_true_positive+total_false_positive)
        f1 = 2/(1/precision+1/recall)
    except:
        total_precision = 0
        total_f1 = 0
    print(f"[{epoch+1}/{EPOCH}] acc={total_acc/(total_negative+total_positive):.4f} precision={precision:.4f} recall={recall:.4f} f1={f1:.4f}")

# %%
torch.save(model, "./models/test_model.bin")

# %%
model = torch.load("./models/test_model.bin")
# %%