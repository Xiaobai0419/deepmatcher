import pdb

import torch.optim as optim
from torch.nn.utils import clip_grad_norm

# This file is taken mostly from the ONMT project.


class Optimizer(object):
    """
    Controller class for optimization. Mostly a thin
    wrapper for `optim`, but also useful for implementing
    rate scheduling beyond what is currently available.
    Also implements necessary methods for training RNNs such
    as grad manipulations.

    Args:
      method (:obj:`str`): one of [sgd, adagrad, adadelta, adam]
      lr (float): learning rate
      lr_decay (float, optional): learning rate decay multiplier
      start_decay_at (int, optional): epoch to start learning rate decay
      beta1, beta2 (float, optional): parameters for adam
      adagrad_accum (float, optional): initialization parameter for adagrad
      lr_decay_method (str, option): custom decay options
      warmup_steps (int, option): parameter for `noam` decay
      model_size (int, option): parameter for `noam` decay
    """

    # We use the default parameters for Adam that are suggested by
    # the original paper https://arxiv.org/pdf/1412.6980.pdf
    # These values are also used by other established implementations,
    # e.g. https://www.tensorflow.org/api_docs/python/tf/train/AdamOptimizer
    # https://keras.io/optimizers/
    # Recently there are slightly different values used in the paper
    # "Attention is all you need"
    # https://arxiv.org/pdf/1706.03762.pdf, particularly the value beta2=0.98
    # was used there however, beta2=0.999 is still arguably the more
    # established value, so we use that here as well
    def __init__(self,
                 method='adam',
                 lr=0.001,
                 max_grad_norm=5,
                 start_decay_at=None,
                 beta1=0.9,
                 beta2=0.999,
                 adagrad_accum=0.0,
                 lr_decay=0.8,
                 lr_decay_method=None,
                 warmup_steps=4000,
                 model_size=None):
        self.last_acc = None
        self.lr = lr
        self.original_lr = lr
        self.max_grad_norm = max_grad_norm
        self.method = method
        self.lr_decay = lr_decay
        self.start_decay_at = start_decay_at
        self.start_decay = False
        self._step = 0
        self.betas = [beta1, beta2]
        self.adagrad_accum = adagrad_accum
        self.lr_decay_method = lr_decay_method
        self.warmup_steps = warmup_steps
        self.model_size = model_size
        self.params = None

    def set_parameters(self, params):
        self.params = []
        self.sparse_params = []
        for k, p in params:
            if p.requires_grad:
                if self.method != 'sparseadam' or "embed" not in k:
                    self.params.append(p)
                else:
                    self.sparse_params.append(p)
        if self.method == 'sgd':
            self.base_optimizer = optim.SGD(self.params, lr=self.lr)
        elif self.method == 'adagrad':
            self.base_optimizer = optim.Adagrad(self.params, lr=self.lr)
            for group in self.base_optimizer.param_groups:
                for p in group['params']:
                    self.base_optimizer.state[p]['sum'] = self.base_optimizer\
                        .state[p]['sum'].fill_(self.adagrad_accum)
        elif self.method == 'adadelta':
            self.base_optimizer = optim.Adadelta(self.params, lr=self.lr)
        elif self.method == 'adam':
            self.base_optimizer = optim.Adam(self.params, lr=self.lr,
                                        betas=self.betas, eps=1e-9)
        elif self.method == 'sparseadam':
            self.base_optimizer = MultipleOptimizer(
                [optim.Adam(self.params, lr=self.lr,
                            betas=self.betas, eps=1e-8),
                 optim.SparseAdam(self.sparse_params, lr=self.lr,
                                  betas=self.betas, eps=1e-8)])
        else:
            raise RuntimeError("Invalid optim method: " + self.method)

    def _set_rate(self, lr):
        self.lr = lr
        pdb.set_trace()
        self.base_optimizer.param_groups[0]['lr'] = self.lr

    def step(self):
        """Update the model parameters based on current gradients.

        Optionally, will employ gradient modification or update learning
        rate.
        """
        self._step += 1

        # Decay method used in tensor2tensor.
        if self.lr_decay_method == "noam":
            self._set_rate(self.original_lr *
                           (self.model_size**
                            (-0.5) * min(self._step**
                                         (-0.5), self._step * self.warmup_steps**(-1.5))))

        if self.max_grad_norm:
            clip_grad_norm(self.params, self.max_grad_norm)
        self.base_optimizer.step()

    def update_learning_rate(self, acc, epoch):
        """
        Decay learning rate if val perf does not improve
        or we hit the start_decay_at limit.
        """

        self.start_decay = True
        if self.start_decay_at is not None and epoch >= self.start_decay_at:
            self.start_decay = True
        if self.last_acc is not None and acc < self.last_acc:
            self.start_decay = True

        if self.start_decay:
            self.lr = self.lr * self.lr_decay
            print("Decaying learning rate to %g" % self.lr)

        self.last_acc = acc
        self.base_optimizer.param_groups[0]['lr'] = self.lr