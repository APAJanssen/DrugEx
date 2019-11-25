#!/usr/bin/env python
# -*- coding: utf-8 -*-

"""This file is used for generator training under reinforcement learning framework.

It is implemented by integrating exploration strategy into REINFORCE algorithm.
The deep learning code is implemented by PyTorch ( >= version 1.0)
"""

import os

import click
import numpy as np
import torch
from rdkit import rdBase
from torch.utils.data import DataLoader, TensorDataset
from tqdm import trange

from drugex import model, util
from drugex.api.agent.agents import DrugExAgent
from drugex.api.agent.callbacks import BasicAgentMonitor
from drugex.api.agent.policy import PolicyGradient
from drugex.api.environ.models import FileEnvDeserializer, Environ
from drugex.api.model.callbacks import BasicMonitor
from drugex.api.pretrain.generators import BasicGenerator, PolicyAwareGenerator

class PG(PolicyGradient):

    def __call__(self, environ: Environ, exploit: PolicyAwareGenerator, explore=None):
        """Training generator under reinforcement learning framework,
        The rewoard is only the final reward given by environment (predictor).

        agent (model.Generator): the exploitation network for SMILES string generation
        environ (util.Activity): the environment provide the final reward for each SMILES
        explore (model.Generator): the exploration network for SMILES string generation,
            it has the same architecture with the agent.
        """

        smiles, valids, seqs = exploit.sample(
            self.batch_size
            , explore=explore
            , epsilon=self.epsilon
            , include_tensors=True
            , mc=self.mc
        )

        # obtaining the reward
        preds = environ.predictSMILES(smiles)
        preds[valids == False] = 0
        preds -= self.beta
        preds = torch.Tensor(preds.reshape(-1, 1)).to(util.dev)

        ds = TensorDataset(seqs, preds)
        loader = DataLoader(ds, batch_size=self.batch_size)

        # Training Loop
        for seq, pred in loader:
            exploit.policyUpdate(seq, pred)


def rollout_pg(agent, environ, explore=None, *, batch_size, baseline, mc, epsilon):
    """Training generator under reinforcement learning framework.

    The reward is given for each token in the SMILES, which is generated by
    Monte Carlo Tree Search based on final reward given by the environment.

    Arguments:

        agent (model.Generator): the exploitation network for SMILES string generation
        environ (util.Activity): the environment provide the final reward for each SMILES
        explore (model.Generator): the exploration network for SMILES string generation,
            it has the same architecture with the agent.
    """

    agent.optim.zero_grad()
    seqs = agent.sample(batch_size, explore=explore, epsilon=epsilon)
    batch_size = seqs.size(0)
    seq_len = seqs.size(1)
    rewards = np.zeros((batch_size, seq_len))
    smiles, valids = util.check_smiles(seqs, agent.voc)
    preds = environ(smiles) - baseline
    preds[valids == False] = - baseline
    scores, hiddens = agent.likelihood(seqs)

    # Monte Carlo Tree Search for step rewards generation
    for _ in trange(mc):
        for i in range(seq_len):
            if (seqs[:, i] != 0).any():
                h = hiddens[:, :, i, :]
                subseqs = agent.sample(batch_size, inits=(seqs[:, i], h, i + 1, None))
                subseqs = torch.cat([seqs[:, :i+1], subseqs], dim=1)
                subsmile, subvalid = util.check_smiles(subseqs, voc=agent.voc)
                subpred = environ(subsmile) - baseline
                subpred[1 - subvalid] = -baseline
            else:
                subpred = preds
            rewards[:, i] += subpred
    loss = agent.PGLoss(scores, seqs, torch.FloatTensor(rewards / mc))
    loss.backward()
    agent.optim.step()
    return 0, valids.mean(), smiles, preds


def _main_helper(*, epsilon, baseline, batch_size, mc, vocabulary_path, output_dir):
    #: File path of predictor in the environment
    environ_path = os.path.join(output_dir, 'RF_cls_ecfp6.pkg')

    # Environment (predictor)
    des = FileEnvDeserializer(environ_path)
    environ = des.getModel()

    # Agent (generator, exploitation network)
    exploit_monitor = BasicMonitor(output_dir, "pr")
    exploit = BasicGenerator(initial_state=exploit_monitor)

    # exploration network
    explore_monitor = BasicMonitor(output_dir, "ex")
    explore = BasicGenerator(initial_state=explore_monitor)

    policy = PG(batch_size, mc, epsilon, beta=baseline)
    identifier = 'e_%.2f_%.1f_%dx%d' % (policy.epsilon, policy.beta, policy.batch_size, policy.mc)
    agent_monitor = BasicAgentMonitor(output_dir, identifier)
    agent = DrugExAgent(
        agent_monitor
        , environ
        , exploit
        , policy
        , explore
        , {"n_epochs" : 1000}
    )
    agent.train()


@click.command()
@click.option('-d', '--input-directory', type=click.Path(file_okay=False, dir_okay=True), show_default=True, default="data")
@click.option('-o', '--output-directory', type=click.Path(file_okay=False, dir_okay=True), show_default=True, default="output")
@click.option('--mc', type=int, default=10, show_default=True)
@click.option('-s', '--batch-size', type=int, default=512, show_default=True)
@click.option('-t', '--num-threads', type=int, default=1, show_default=True)
@click.option('-e', '--epsilon', type=float, default=0.1, show_default=True)
@click.option('-b', '--baseline', type=float, default=0.1, show_default=True)
@click.option('-g', '--gpu', type=int, default=0)
def main(input_directory, output_directory, mc, batch_size, num_threads, epsilon, baseline, gpu):
    rdBase.DisableLog('rdApp.error')
    torch.set_num_threads(num_threads)
    if torch.cuda.is_available() and gpu:
        torch.cuda.set_device(gpu)
    _main_helper(
        baseline=baseline,
        batch_size=batch_size,
        mc=mc,
        epsilon=epsilon,
        vocabulary_path=os.path.join(input_directory, "voc.txt"),
        output_dir=output_directory,
    )


if __name__ == "__main__":
    main()
