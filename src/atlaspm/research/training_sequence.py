"""Optional PyTorch sequence regressors; deterministic CPU research by default."""
from __future__ import annotations

SEQUENCE_MODELS = ("lstm","gru","transformer")


def fit_sequence(name, train, y, test, seed, *, epochs=12, device="cpu"):
    import numpy as np
    import torch
    from torch import nn
    if name not in SEQUENCE_MODELS:
        raise ValueError("UNKNOWN_SEQUENCE_MODEL")
    if type(epochs) is not int or not 1 <= epochs <= 200:
        raise ValueError("INVALID_SEQUENCE_EPOCHS")
    if device not in {"cpu","cuda"} or (device=="cuda" and not torch.cuda.is_available()):
        raise ValueError("REQUESTED_SEQUENCE_DEVICE_UNAVAILABLE")
    if train.ndim!=3 or test.ndim!=3 or train.shape[1:]!=test.shape[1:]:
        raise ValueError("INVALID_SEQUENCE_SHAPE")
    if not np.isfinite(train).all() or not np.isfinite(test).all():
        raise ValueError("INVALID_SEQUENCE_VALUES")
    # Every sequence ends at its own signal date. Fit scaling on training only.
    mean=train.mean(axis=(0,1))
    scale=np.maximum(train.std(axis=(0,1)),1e-8)
    ymean=float(y.mean())
    yscale=max(float(y.std()),1e-8)
    features=train.shape[2]
    length=train.shape[1]
    hidden=16

    class Recurrent(nn.Module):
        def __init__(self):
            super().__init__()
            cls=nn.LSTM if name=="lstm" else nn.GRU
            self.encoder=cls(features,hidden,batch_first=True)
            self.head=nn.Linear(hidden,1)
        def forward(self,x):
            states,_=self.encoder(x)
            return self.head(states[:,-1]).squeeze(-1)

    class Attention(nn.Module):
        def __init__(self):
            super().__init__()
            self.embed=nn.Linear(features,hidden)
            self.position=nn.Parameter(torch.zeros(1,length,hidden))
            block=nn.TransformerEncoderLayer(hidden,4,32,dropout=0.,batch_first=True)
            self.encoder=nn.TransformerEncoder(block,1)
            self.head=nn.Linear(hidden,1)
        def forward(self,x):
            # Full attention is permitted inside the observed trailing window;
            # no token after the signal date exists in the input.
            states=self.encoder(self.embed(x)+self.position)
            return self.head(states[:,-1]).squeeze(-1)

    previous_threads=torch.get_num_threads()
    try:
        torch.set_num_threads(1)
        with torch.random.fork_rng(devices=[] if device=="cpu" else [torch.cuda.current_device()]):
            torch.manual_seed(seed)
            model=(Attention() if name=="transformer" else Recurrent()).to(device)
            optimizer=torch.optim.AdamW(model.parameters(),lr=.003)
            loss_fn=nn.MSELoss()
            x=torch.as_tensor((train-mean)/scale,dtype=torch.float32,device=device)
            target=torch.as_tensor((y-ymean)/yscale,dtype=torch.float32,device=device)
            xt=torch.as_tensor((test-mean)/scale,dtype=torch.float32,device=device)
            generator=torch.Generator().manual_seed(seed)
            model.train()
            for _ in range(epochs):
                for ids in torch.randperm(len(x),generator=generator).split(64):
                    ids=ids.to(device)
                    optimizer.zero_grad()
                    loss=loss_fn(model(x[ids]),target[ids])
                    if not torch.isfinite(loss):
                        raise ValueError("NONFINITE_SEQUENCE_LOSS")
                    loss.backward()
                    nn.utils.clip_grad_norm_(model.parameters(),1.)
                    optimizer.step()
            model.eval()
            with torch.no_grad():
                result=model(xt).cpu().numpy().astype(float)*yscale+ymean
    finally:
        torch.set_num_threads(previous_threads)
    if result.shape!=(len(test),) or not np.isfinite(result).all():
        raise ValueError("NONFINITE_SEQUENCE_PREDICTION")
    return result,dict(epochs=epochs,device=device,hidden=hidden,length=length,
                       seed=seed,learning_rate=.003,batch_size=64,
                       feature_mean=mean.tolist(),feature_scale=scale.tolist(),
                       target_mean=ymean,target_scale=yscale,
                       deterministic_gpu_guaranteed=False)
